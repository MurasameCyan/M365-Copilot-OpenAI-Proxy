from __future__ import annotations

import json
import re as _re
import uuid
from collections.abc import Iterable, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, best_match
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from .media_proxy import references_m365_media

_READ_ONLY_INTENT_RE = _re.compile(
    r"(只分析|仅分析|只读|不要修改|不要改|不要写|不要保存|不要创建|不要删除|不要执行|不要运行|不修改文件|不改文件|"
    r"analy[sz]e only|read[- ]only|do not modify|don't modify|no changes|do not write|don't write|do not save|don't save|do not run|don't run)",
    _re.IGNORECASE,
)
_READ_ONLY_TOOL_NAMES = {"read", "grep", "glob", "ls", "searchcodebase"}
_SYSTEM_REMINDER_RE = _re.compile(
    r"<system-reminder\b[^>]*>(.*?)</system-reminder\s*>",
    _re.IGNORECASE | _re.DOTALL,
)
_PLAN_MODE_RE = _re.compile(
    r"^[ \t]*(?:\#{1,6}[ \t]*)?"
    r"(?:(?P<active>Plan mode (?:is|still) active\b)|You have exited plan mode\b|Exited Plan Mode\b)",
    _re.IGNORECASE | _re.MULTILINE,
)


def _has_read_only_intent(*parts: str) -> bool:
    return any(_READ_ONLY_INTENT_RE.search(part or "") for part in parts)


def _intent_content_text(content: object) -> str:
    """Preserve text-block boundaries and exclude tool-result payloads."""
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else ():
        if isinstance(part, Mapping):
            kind, text = part.get("type"), part.get("text")
        else:
            kind, text = getattr(part, "type", None), getattr(part, "text", None)
        if kind in ("text", "input_text", "output_text") and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _read_only_intent_for_messages(
    messages: Iterable[tuple[str, object]], *, instructions: object = "",
    continuation_read_only: bool = False,
) -> bool:
    """Infer this task's intent without treating client context as permissions.

    CC places CLAUDE.md and memory in user-side system-reminder blocks. A rule
    about one setting (e.g. "do not modify user.email") must not ban every Write.
    The original messages still go to the model unchanged; this extraction only
    scopes our coarse all-or-nothing tool filter. Configured permissions remain
    a separate ceiling at each API entry point.

    Tool-result-only turns have no user text, so they retain the most recent
    real request's intent. A new request replaces that intent. Explicit CC plan
    mode signals are tracked separately, including those sent as system text.
    """
    latest_user_read_only = continuation_read_only
    user_plan_mode = False
    system_plan_mode = None
    for role, content in [*messages, ("system", instructions)]:
        if role not in ("user", "system", "developer"):
            continue
        text = _intent_content_text(content)
        if not text:
            continue
        # A plan reminder is a real execution constraint. Do not mistake the
        # word "read-only" in a skill/agent description for such a constraint.
        plan_text = _SYSTEM_REMINDER_RE.sub(lambda match: "\n" + match[1] + "\n", text)
        for mode_match in _PLAN_MODE_RE.finditer(plan_text):
            active = mode_match.group("active") is not None
            if role == "user":
                user_plan_mode = active
            else:
                system_plan_mode = active
        if role == "user":
            user_text = _SYSTEM_REMINDER_RE.sub("", text).strip()
            if user_text:
                latest_user_read_only = _has_read_only_intent(user_text)
    # Current system instructions outrank older user-side mode reminders.
    plan_mode = system_plan_mode if system_plan_mode is not None else user_plan_mode
    return plan_mode or latest_user_read_only


def _tool_call_name(tool_call: dict) -> str:
    try:
        return str(tool_call.get("function", {}).get("name", "")).strip()
    except AttributeError:
        return ""


def _filter_read_only_tool_calls(tool_calls: list[dict]) -> list[dict]:
    return [tc for tc in tool_calls if _tool_call_name(tc).lower() in _READ_ONLY_TOOL_NAMES]


def _filter_schema_valid_tool_calls(
    tool_calls: list[dict], schemas: Mapping[str, dict | None]
) -> tuple[list[dict], list[str]]:
    """Drop calls the client cannot execute, returning ``(kept, reasons)``.

    Both checks are against what the client itself declared: the function has to
    be one it offered, and the arguments have to satisfy its own JSON Schema. Our
    tool_calls are *parsed out of prose*, so a hallucinated name or a missing
    required argument is routine -- and forwarding one just moves the failure to
    the client, where it surfaces as a validation error with no hint that the
    model, not the client, got it wrong. ``reasons`` exists so the caller reports
    the drop instead of turning it into another silent degradation.

    Deliberately permissive about anything it cannot judge: a tool with no
    declared schema, and a schema jsonschema refuses to compile or resolve, both
    pass through. Rejecting on our own uncertainty would break tool calling for a
    client whose schema we merely failed to understand. The Responses route makes
    the opposite call in ``_resolve_responses_tool_calls`` and is right to: it
    gates schemas at request time (400 on unresolvable/too-deep) and only enforces
    tools the client marked ``strict``, so there a compile failure is impossible
    rather than unjudgeable.

    ponytail: arguments that are not JSON at all are kept unchecked -- an
    unusable call, but a different defect from this one. Fix it by making
    _coerce_tool_call reject non-object arguments outright.
    """
    kept: list[dict] = []
    reasons: list[str] = []
    for call in tool_calls:
        name = _tool_call_name(call)
        if name not in schemas:
            offered = ", ".join(sorted(schemas)) or "（本轮未声明任何工具）"
            reasons.append(
                f"{name or '(未命名)'} 不在本轮声明的工具里（可用：{offered}）"
            )
            continue
        schema = schemas.get(name)
        if not isinstance(schema, dict) or not schema:
            kept.append(call)
            continue
        try:
            arguments = json.loads(call.get("function", {}).get("arguments") or "")
        except (json.JSONDecodeError, TypeError, ValueError):
            kept.append(call)
            continue
        try:
            validator = validator_for(schema, default=Draft202012Validator)(
                schema, registry=Registry()
            )
            error = best_match(validator.iter_errors(arguments))
        except (RecursionError, Unresolvable, SchemaError, TypeError, AttributeError):
            kept.append(call)
            continue
        if error is None:
            kept.append(call)
            continue
        where = "/".join(str(part) for part in error.absolute_path) or "arguments"
        reasons.append(f"{name} 的参数不符合声明的 schema（{where}：{error.message}）")
    return kept, reasons


# Explicit "I considered the tools and none is needed" signal, borrowed from
# HEXUXIU/M365-Copilot2API's router prompt. Absence of tool_calls alone cannot
# tell a deliberate no-action answer apart from a tone that ignored the injected
# contract entirely, and those two need opposite advice: the first is a correct
# turn, the second means "switch models". Models decorate the token
# (**NO_TOOL_NEEDED**, trailing period), so match loosely and strip what we match.
# The leading class is decoration-only and deliberately excludes whitespace: it used
# to be [*_`\s]* , which is greedy and unanchored, so a reply that closed a fenced
# code block immediately before the token ("```\n\nNO_TOOL_NEEDED") had the closing
# fence eaten along with it. That corrupted the delivered text on every surface and
# silently disabled the prose-Write fallback, which needs a complete fence to match.
# Whatever whitespace is left behind is handled by the .strip() in the splitter.
# The boundaries are lookarounds rather than \b because _ is a word character, so
# \b never matched _NO_TOOL_NEEDED_ -- the flag still got set (that is a plain
# substring test below) but nothing was stripped, leaking protocol chatter into the
# answer. Excluding only ASCII alphanumerics keeps the token strippable when it is
# glued to CJK text, which has no spaces to rely on.
_NO_TOOL_MARKER = "NO_TOOL_NEEDED"
_NO_TOOL_MARKER_RE = _re.compile(
    r"[*_`]*(?<![A-Za-z0-9])" + _NO_TOOL_MARKER + r"(?![A-Za-z0-9])[*_`]*[.。!！]?",
    _re.IGNORECASE,
)


def split_no_tool_marker(text: str) -> tuple[str, bool]:
    """Split the explicit no-action signal off a turn: ``(text_without, declined)``.

    ``declined`` is only ever additional certainty -- a model that forgets the
    token leaves us exactly where we were before, i.e. "unknown".
    """
    if not text or _NO_TOOL_MARKER.lower() not in text.lower():
        return text, False
    stripped = _NO_TOOL_MARKER_RE.sub("", text).strip()
    # A reply that is *nothing but* the token is not a deliberate no-action answer,
    # it is a malformed turn (the model answered the protocol instead of the user).
    # Leave it visible so the existing shortfall reporting still fires.
    if not stripped:
        return text, False
    return stripped, True


# Primary: fenced ```tool_call blocks. Fallback: ```json blocks that look like a tool call.
# We only match the OPENING fence + optional language tag with a regex; the JSON
# body is parsed with json.JSONDecoder.raw_decode (balanced-brace) rather than a
# `\{.*?\}```  regex. The old regex terminated at the first ``` it saw, so any ```
# sequence or nested braces INSIDE the JSON — common when Write's `content` is
# Markdown/code — truncated the payload into invalid JSON and dropped the whole
# tool_call. Balanced-brace decoding tolerates backticks/braces inside the JSON.
_TOOL_CALL_FENCE_RE = _re.compile(r"```tool_call[ \t]*\r?\n?", _re.IGNORECASE)
_JSON_FENCE_RE = _re.compile(r"```(?:json)?[ \t]*\r?\n?", _re.IGNORECASE)
_CLOSING_FENCE_RE = _re.compile(r"\s*```")
_DECODER = json.JSONDecoder()


def _parse_fenced_json_blocks(text: str, fence_re) -> list[tuple[dict, int, int]]:
    """Find fenced blocks whose body is a JSON object.

    Returns a list of (obj, block_start, block_end) where block_start/block_end
    delimit the full span to strip (from the fence opener through the closing
    ``` if one is present). The JSON is located by finding the first `{` after
    the fence opener and decoded with raw_decode, so ``` inside the JSON string
    values does not prematurely end the block.
    """
    results: list[tuple[dict, int, int]] = []
    for m in fence_re.finditer(text):
        body_start = m.end()
        brace = text.find("{", body_start)
        if brace == -1:
            continue
        # If another fence appears before the opening brace, this opener has no
        # JSON body of its own — skip it.
        if "```" in text[body_start:brace]:
            continue
        try:
            obj, end = _DECODER.raw_decode(text, brace)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        tail = _CLOSING_FENCE_RE.match(text, end)
        block_end = tail.end() if tail else end
        results.append((obj, m.start(), block_end))
    return results


def _coerce_tool_call(obj: dict) -> dict | None:
    """Turn a parsed JSON object into an OpenAI tool_call dict if it looks like one."""
    if not isinstance(obj, dict):
        return None
    # Accept {"name": ..., "arguments": {...}} or common variants
    name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or obj.get("function")
    if not name or not isinstance(name, str):
        return None
    arguments = obj.get("arguments")
    if arguments is None:
        arguments = obj.get("parameters")
    if arguments is None:
        # Treat remaining keys (minus name markers) as the arguments
        arguments = {k: v for k, v in obj.items()
                     if k not in ("name", "tool", "tool_name", "function")}
    if isinstance(arguments, dict):
        arguments = json.dumps(arguments, ensure_ascii=False)
    elif not isinstance(arguments, str):
        arguments = str(arguments)
    return {
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _scan_unfenced_tool_json(text: str) -> list[tuple[dict, int, int]]:
    """Find balanced JSON objects in free text that look like tool calls.

    Used when the model emits bare ``{"name":...,"arguments":...}`` without a
    markdown fence (common after prompt pressure / partial compliance). Skips
    objects that already sit inside a matched fenced span (caller filters).
    """
    results: list[tuple[dict, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        # Cheap prefilter: tool-shaped keys must appear soon after the brace.
        window = text[i : i + 240]
        if '"name"' not in window and '"tool"' not in window and '"function"' not in window:
            i += 1
            continue
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except (json.JSONDecodeError, ValueError):
            i += 1
            continue
        if isinstance(obj, dict) and _coerce_tool_call(obj):
            results.append((obj, i, end))
            i = end
            continue
        i += 1
    return results


def _extract_tool_calls(text: str) -> list[dict]:
    """Parse tool_call JSON blocks from model text output into OpenAI tool_calls format.

    Tolerant to several formats the M365 Copilot model may emit:
    1. ```tool_call fenced blocks (preferred)
    2. ```json (or bare ```) fenced blocks whose JSON has a "name" key
    3. Unfenced balanced JSON objects with name/arguments (last resort)
    """
    calls = []
    matched_spans: list[tuple[int, int]] = []

    # 1. Preferred tool_call blocks
    for obj, start, end in _parse_fenced_json_blocks(text, _TOOL_CALL_FENCE_RE):
        tc = _coerce_tool_call(obj)
        if tc:
            calls.append(tc)
            matched_spans.append((start, end))

    # 2. Fallback: json/plain fenced blocks that look like tool calls
    for obj, start, end in _parse_fenced_json_blocks(text, _JSON_FENCE_RE):
        # Skip if this span overlaps an already-matched tool_call block
        if any(s <= start < e for s, e in matched_spans):
            continue
        tc = _coerce_tool_call(obj)
        if tc:
            calls.append(tc)
            matched_spans.append((start, end))

    # 3. Unfenced tool-shaped JSON outside already-matched spans.
    for obj, start, end in _scan_unfenced_tool_json(text):
        if any(s <= start < e or s < end <= e or (start <= s and end >= e) for s, e in matched_spans):
            continue
        tc = _coerce_tool_call(obj)
        if tc:
            calls.append(tc)
            matched_spans.append((start, end))

    return calls


def planner_fallback_needed(text: str, tool_names: set[str] | None = None) -> bool:
    """Whether a planner produced no usable declared tool call.

    An explicit ``NO_TOOL_NEEDED`` is a valid planner verdict, so it never
    escalates to another planner. Otherwise only calls the client actually
    declared count as success; malformed or unrelated tool-shaped prose leaves
    the caller free to try its next planning layer.
    """
    raw = text or ""
    # The route-level parser deliberately treats a marker-only answer as a
    # malformed user-facing turn (there is no answer to deliver).  A planner
    # chain has a different question: the explicit verdict still means the
    # planner considered the tools and declined, so do not spend another
    # planner attempt on it.
    if raw.strip().strip("*_` .。!！").casefold() == _NO_TOOL_MARKER.casefold():
        return False
    clean, declined = split_no_tool_marker(raw)
    calls = _extract_tool_calls(clean)
    if declined and not calls:
        return False
    # Let each route's existing corrective file retry run before changing
    # planners; otherwise the retry would be bypassed by an early chain hop.
    # A delivered image is the same kind of stop: it is an answer, not a planning
    # failure, so hopping planners would only redraw it -- another turn, another
    # image quota unit on consumer -- and still not produce a tool_call.
    if not calls and (
        _looks_like_fake_file_claim(clean) or _delivered_media(clean)
    ):
        return False
    if tool_names:
        return not any(
            (call.get("function") or {}).get("name") in tool_names
            for call in calls
        )
    return not calls


# Prose fallback: model writes "save as `<path>`" then a fenced code block,
# instead of emitting a tool_call. Synthesize a Write tool_call ONLY when the
# code block's language tag matches the target file's extension — this avoids
# mistaking a usage example (e.g. ```bash python foo.py```) for the file content.
_PROSE_PATH_RE = _re.compile(
    r"`([A-Za-z]:[\\/][^`\n]+?\.[A-Za-z0-9]{1,8}|/[^`\n]+?\.[A-Za-z0-9]{1,8})`"
)
# Capture the language tag (group 1) and the body (group 2).
_PROSE_CODE_RE = _re.compile(r"```([A-Za-z0-9_+#.\-]*)[ \t]*\n(.*?)```", _re.DOTALL)

# Map a file extension to the set of fenced-code-block language tags that count
# as matching content for that extension.
_EXT_LANG = {
    "py": {"python", "py", "python3"},
    "pyw": {"python", "py"},
    "bat": {"bat", "batch", "cmd", "dos", "bat文件"},
    "cmd": {"bat", "batch", "cmd", "dos"},
    "sh": {"bash", "sh", "shell", "zsh"},
    "bash": {"bash", "sh", "shell"},
    "ps1": {"powershell", "ps1", "pwsh", "posh"},
    "js": {"javascript", "js", "node", "jsx"},
    "mjs": {"javascript", "js", "node"},
    "cjs": {"javascript", "js", "node"},
    "ts": {"typescript", "ts", "tsx"},
    "tsx": {"typescript", "tsx", "ts"},
    "jsx": {"javascript", "jsx", "js"},
    "json": {"json", "json5", "jsonc"},
    "html": {"html", "htm", "xhtml"},
    "htm": {"html", "htm"},
    "css": {"css"},
    "scss": {"scss", "sass", "css"},
    "less": {"less", "css"},
    "java": {"java"},
    "kt": {"kotlin", "kt"},
    "c": {"c"},
    "h": {"c", "cpp", "c++"},
    "cpp": {"cpp", "c++", "cxx", "cc"},
    "cc": {"cpp", "c++", "cc"},
    "cs": {"csharp", "cs", "c#"},
    "go": {"go", "golang"},
    "rs": {"rust", "rs"},
    "rb": {"ruby", "rb"},
    "php": {"php"},
    "swift": {"swift"},
    "yml": {"yaml", "yml"},
    "yaml": {"yaml", "yml"},
    "xml": {"xml"},
    "sql": {"sql"},
    "md": {"markdown", "md"},
    "txt": {"text", "txt", "plaintext", ""},
    "toml": {"toml"},
    "ini": {"ini", "cfg", "conf"},
    "cfg": {"ini", "cfg", "conf"},
    "conf": {"ini", "cfg", "conf"},
    "env": {"dotenv", "env", "bash", "sh", ""},
    "dockerfile": {"dockerfile", "docker"},
    "vue": {"vue", "html"},
    "r": {"r"},
    "lua": {"lua"},
    "pl": {"perl", "pl"},
    "scala": {"scala"},
    "dart": {"dart"},
    "gradle": {"gradle", "groovy"},
    "groovy": {"groovy"},
    "makefile": {"makefile", "make"},
}


def _extract_prose_write(text: str, tool_names: set[str]) -> list[dict]:
    """Fallback: synthesize a Write tool_call from a 'save as <path>' + code block prose.

    Strict matching to avoid corrupting files:
    - A Write-like tool must be available.
    - A LOCAL file path (drive letter or absolute unix path, not a URL) with an
      extension must be present.
    - A fenced code block whose language tag matches the file's extension must
      exist. This prevents usage-example blocks (```bash, ```text) from being
      mistaken for the file content and overwriting a correctly written file.
    """
    if not any(n.lower() == "write" for n in tool_names):
        return []

    # Collect candidate local paths (skip URLs).
    file_path = None
    target_ext = None
    for path_m in _PROSE_PATH_RE.finditer(text):
        candidate = path_m.group(1).strip()
        if "://" in candidate or candidate.lower().startswith("http"):
            continue
        ext = candidate.rsplit(".", 1)[-1].lower() if "." in candidate else ""
        if not ext:
            continue
        file_path = candidate
        target_ext = ext
        break
    if not file_path or not target_ext:
        return []

    allowed_langs = _EXT_LANG.get(target_ext)

    # Find a code block whose language matches the target extension.
    best_content = None
    for code_m in _PROSE_CODE_RE.finditer(text):
        lang = (code_m.group(1) or "").strip().lower()
        body = code_m.group(2)
        if allowed_langs is not None:
            if lang in allowed_langs:
                best_content = body
                break
        else:
            # Unknown extension: only accept an exactly-matching language tag.
            if lang == target_ext:
                best_content = body
                break
    if best_content is None:
        return []

    # Trim a single trailing newline that fenced blocks usually carry.
    if best_content.endswith("\n"):
        best_content = best_content[:-1]
    if not best_content.strip():
        return []

    write_name = next((n for n in tool_names if n.lower() == "write"), "Write")
    arguments = json.dumps({"file_path": file_path, "content": best_content}, ensure_ascii=False)
    return [{
        "id": f"call_{uuid.uuid4().hex[:24]}",
        "type": "function",
        "function": {"name": write_name, "arguments": arguments},
    }]


def _strip_tool_call_blocks(text: str) -> str:
    """Remove tool_call code blocks from text, keeping surrounding content.

    Uses the same balanced-brace scanner as extraction so the removed span
    matches exactly what was parsed as a tool_call (including ``` inside the
    JSON body). Only blocks that decode into a tool_call are stripped.
    Also strips unfenced tool-shaped JSON objects for consistency with
    ``_extract_tool_calls``.
    """
    spans: list[tuple[int, int]] = []
    for obj, start, end in _parse_fenced_json_blocks(text, _TOOL_CALL_FENCE_RE):
        if _coerce_tool_call(obj):
            spans.append((start, end))
    for obj, start, end in _parse_fenced_json_blocks(text, _JSON_FENCE_RE):
        if any(s <= start < e for s, e in spans):
            continue
        if _coerce_tool_call(obj):
            spans.append((start, end))
    for obj, start, end in _scan_unfenced_tool_json(text):
        if any(s <= start < e or s < end <= e or (start <= s and end >= e) for s, e in spans):
            continue
        if _coerce_tool_call(obj):
            spans.append((start, end))
    if not spans:
        return text.strip()
    spans.sort()
    out = []
    cursor = 0
    for start, end in spans:
        if start < cursor:
            continue
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    return "".join(out).strip()


# M365 Copilot has a native "generate a file" feature that hosts the file on its
# own object storage (asyncgw/Teams) and returns a download URL, instead of
# emitting our tool_call. From the model's view the task is "done", so prompt
# rules alone can't stop it. We detect this pattern and force a corrective retry.
_FILE_CLAIM_URL_RE = _re.compile(
    r"https?://[^\s`)]+?\.(?:py|js|ts|tsx|jsx|json|txt|md|html?|css|sh|bat|ps1|"
    r"java|kt|c|cpp|cc|h|cs|go|rs|rb|php|swift|ya?ml|xml|sql|ini|toml|cfg)\b",
    _re.IGNORECASE,
)
# Phrases that claim a file was produced (zh + en).
_FILE_CLAIM_PHRASE_RE = _re.compile(
    r"已生成|已创建|已保存|已写入|已经生成|已经创建|生成脚本|生成了|创建了|保存到|"
    r"file (?:created|saved|generated|written)|created the file|saved to|generated the",
    _re.IGNORECASE,
)
# A delivered image: an inline data uri, or markdown pointing at something the
# client can actually fetch. Markdown aimed at a bare filesystem path is NOT
# delivered -- "已生成 ![chart](chart.png)" with no tool_call is exactly the fake
# claim the retry exists to catch.
_DELIVERED_IMAGE_RE = _re.compile(
    r"data:image/[\w.+-]+;base64,|!\[[^\]]*\]\(\s*(?:https?://|/v1/m365-media\?)",
    _re.IGNORECASE,
)


def _delivered_media(text: str) -> bool:
    """True if the reply already carries the artifact its prose talks about.

    Two families: consumer inlines the image as a data uri, M365 hands back a
    hosted source (designer/asyncgw) that our media proxy signs and serves. The
    M365 half has to be recognised in its RAW shape, because the routes run this
    before the media rewriter on purpose -- there the image is still a backticked
    or bare host url, not the markdown the rewriter would emit.
    """
    return bool(_DELIVERED_IMAGE_RE.search(text)) or references_m365_media(text)


def _looks_like_fake_file_claim(text: str) -> bool:
    """True if the model claims to have produced a file but emitted no tool_call.

    Two triggers:
    1. A hosted attachment URL pointing at a code/text file (M365 native file gen).
    2. A "file created/生成" style phrase.
    The caller only invokes this when NO tool_call was parsed from the response.
    """
    if not text:
        return False
    if _FILE_CLAIM_URL_RE.search(text):
        return True
    if _delivered_media(text):
        # 已生成/生成了 is also how both providers word an image turn, and the
        # image in the same reply is the artifact the phrase refers to -- the
        # claim is not fake. Retrying it spent a second upstream turn (on
        # consumer, another image quota unit) and, when that turn produced a
        # Write call, handed the client that call instead of the picture: the
        # reply said the image was ready and carried none. The url branch above
        # still fires, so a hosted code-file link sitting beside an image is
        # unaffected -- that link is direct evidence, the phrase circumstantial.
        return False
    if _FILE_CLAIM_PHRASE_RE.search(text):
        return True
    return False


_RETRY_INSTRUCTION = (
    "[SYSTEM] Your previous reply did NOT create any file on the host. "
    "You may have used a hosted attachment link or an out-of-band file feature — that does NOT work here; "
    "the host only creates files when you emit a tool_call block. "
    "Re-do the task NOW: output ONLY a fenced ```tool_call block whose JSON is "
    '{"name": "Write", "arguments": {"file_path": "<the exact path the user gave>", "content": "<the FULL file body>"}}. '
    "No prose, no links, no usage examples — just the tool_call block with the complete file content.[/SYSTEM]"
)
