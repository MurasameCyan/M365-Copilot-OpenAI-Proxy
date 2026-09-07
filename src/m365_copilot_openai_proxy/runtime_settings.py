from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .atomic_write import write_text_atomic
from .tone_options import TONE_OPTIONS as _BUILTIN_TONE_OPTIONS
from .tone_options import tool_planning_mode

_log = logging.getLogger("copilot_proxy")
_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_RUN_PERMISSIONS = {"read_only", "full"}
_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h", "socks4", "socks4a"}
# Hosts that must never be proxied: the CDP control channel to the local
# Chromium. websockets>=15 and httpx both consult these env vars by default, so
# without this pin an admin-set proxy would swallow every browser automation
# call and break cookie refresh / token capture.
_PROXY_NEVER = ("localhost", "127.0.0.1", "::1")
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
_MEDIA_SUFFIX_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,39}$")
_DEFAULT_MEDIA_PROXY_SUFFIXES = [
    "png", "jpg", "jpeg", "webp", "gif", "svg", "bmp", "tif", "tiff", "ico", "heic", "heif", "avif",
    "wav", "mp3", "m4a", "ogg", "oga", "flac", "aac", "opus", "wma", "mid", "midi",
    "mp4", "webm", "mov", "mkv", "avi", "m4v", "3gp", "wmv", "flv", "mpeg", "mpg",
    "pdf", "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "xml", "html", "htm", "yaml", "yml", "toml", "ini", "env",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf",
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "tar.gz", "tar.bz2", "tar.xz",
    "py", "pyw", "js", "mjs", "cjs", "ts", "tsx", "jsx", "java", "go", "rs", "c", "h", "cpp", "cxx", "cc", "hpp", "cs",
    "php", "rb", "swift", "kt", "kts", "scala", "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd", "sql", "r", "lua", "pl", "pm",
    "vue", "svelte", "css", "scss", "sass", "less", "dockerfile", "makefile", "cmake", "gradle", "lock", "log", "conf", "cfg",
]
# Preserve the former M365 default catalogue so an untouched persisted default
# adopts the current display names without rewriting an administrator's custom
# tone list or order.
_PREVIOUS_BUILTIN_TONE_OPTIONS = [
    {"value": "Magic", "label": "Copilot_自动", "label_zh": "Copilot_自动", "label_en": "Copilot_自动"},
    {"value": "Chat", "label": "Copilot_快速答复", "label_zh": "Copilot_快速答复", "label_en": "Copilot_快速答复"},
    {"value": "Reasoning", "label": "Copilot_深度思考", "label_zh": "Copilot_深度思考", "label_en": "Copilot_深度思考"},
    {"value": "Claude_Sonnet", "label": "claude-sonnet-4-6", "label_zh": "claude-sonnet-4-6", "label_en": "claude-sonnet-4-6"},
    {"value": "Claude_Sonnet_Reasoning", "label": "claude-sonnet-4-5_Reasoning", "label_zh": "claude-sonnet-4-5_Reasoning", "label_en": "claude-sonnet-4-5_Reasoning"},
    {"value": "Claude_Fable", "label": "claude-fable-5", "label_zh": "claude-fable-5", "label_en": "claude-fable-5"},
    {"value": "Claude_Opus", "label": "claude-opus", "label_zh": "claude-opus", "label_en": "claude-opus"},
    {"value": "Gpt_5_6_Reasoning", "label": "gpt-5.6_Reasoning", "label_zh": "gpt-5.6_Reasoning", "label_en": "gpt-5.6_Reasoning"},
    {"value": "Gpt_5_5_Chat", "label": "gpt-5.5_Chat", "label_zh": "gpt-5.5_Chat", "label_en": "gpt-5.5_Chat"},
    {"value": "Gpt_5_5_Reasoning", "label": "gpt-5.5_Reasoning", "label_zh": "gpt-5.5_Reasoning", "label_en": "gpt-5.5_Reasoning"},
    {"value": "Gpt_5_4_Chat", "label": "gpt-5.4_Chat", "label_zh": "gpt-5.4_Chat", "label_en": "gpt-5.4_Chat"},
    {"value": "Gpt_5_4_Reasoning", "label": "gpt-5.4_Reasoning", "label_zh": "gpt-5.4_Reasoning", "label_en": "gpt-5.4_Reasoning"},
    {"value": "Gpt_5_3_Chat", "label": "gpt-5.3_Chat", "label_zh": "gpt-5.3_Chat", "label_en": "gpt-5.3_Chat"},
    {"value": "Gpt_5_2_Chat", "label": "gpt-5.2_Chat", "label_zh": "gpt-5.2_Chat", "label_en": "gpt-5.2_Chat"},
    {"value": "Gpt_5_2_Reasoning", "label": "gpt-5.2_Reasoning", "label_zh": "gpt-5.2_Reasoning", "label_en": "gpt-5.2_Reasoning"},
]
# Tones added after the rename above, newest first. Every release that measures a
# new tone into _BUILTIN_TONE_OPTIONS must name it here too, because the
# comparison below is byte-exact: a persisted default written by the release
# *before* the tone existed matches no literal otherwise, and the operator's
# picker silently never gains the tone no matter which image they deploy. That is
# how production ended up two tones behind its own image and needed a hand-write.
_TONES_ADDED_SINCE_RENAME = ("Gpt_6_Reasoning", "Gpt_6_Astra", "Gpt_5_6_Chat", "Gpt_5_3_Reasoning")


def _tone_default_without(*values: str) -> list[dict]:
    """The current default minus tones that did not exist in an older release.

    Derived rather than pasted so the historical shapes cannot drift out of sync
    with the labels in _BUILTIN_TONE_OPTIONS the way a copied literal would.
    """
    skip = set(values)
    return [dict(o) for o in _BUILTIN_TONE_OPTIONS if o.get("value") not in skip]


# Every default catalogue we have ever shipped, so an operator who never edited
# the picker adopts the current one. Ordered newest-first only for readability;
# the check is an equality test against each.
_HISTORICAL_BUILTIN_TONE_OPTIONS = (
    _PREVIOUS_BUILTIN_TONE_OPTIONS,
    # _TONES_ADDED_SINCE_RENAME is newest-first, so removing its first N entries
    # reconstructs the catalogue as it stood N additions ago.
    *(
        _tone_default_without(*_TONES_ADDED_SINCE_RENAME[:count])
        for count in range(1, len(_TONES_ADDED_SINCE_RENAME) + 1)
    ),
)
# Historical OpenAI-compatible facade names. Keep this exact list only so an
# untouched persisted default from older releases can move to the current
# tested catalogue; administrators may still configure any model/mode mapping.
_LEGACY_BUILTIN_CONSUMER_MODE_OPTIONS = [
    {"model": "copilot", "mode": "smart", "status": "stable"},
    {"model": "copilot-smart", "mode": "smart", "status": "stable"},
    {"model": "copilot-reasoning", "mode": "reasoning", "status": "experimental"},
    {"model": "copilot-thinking", "mode": "reasoning", "status": "experimental"},
    {"model": "copilot-search", "mode": "search", "status": "experimental"},
    {"model": "copilot-study", "mode": "study", "status": "experimental"},
    {"model": "copilot-chat", "mode": "chat", "status": "experimental"},
    {"model": "copilot-default", "mode": "default", "status": "experimental"},
    {"model": "copilot-research", "mode": "research", "status": "experimental"},
    {"model": "copilot-computer-use", "mode": "computer_use", "status": "experimental"},
    {"model": "copilot-coco", "mode": "coco", "status": "experimental"},
]
# Preserve the former nine-entry default so untouched persisted settings can
# adopt README ordering without reordering an administrator's custom catalog.
_PREVIOUS_BUILTIN_CONSUMER_MODE_OPTIONS = [
    dict(option)
    for option in _LEGACY_BUILTIN_CONSUMER_MODE_OPTIONS
    if option["model"] not in {"copilot-default", "copilot-computer-use"}
]
_BUILTIN_CONSUMER_MODE_OPTIONS = [
    {"model": "copilot-reasoning", "mode": "reasoning", "status": "experimental"},
    {"model": "copilot-thinking", "mode": "reasoning", "status": "experimental"},
    {"model": "copilot-research", "mode": "research", "status": "experimental"},
    {"model": "copilot-coco", "mode": "coco", "status": "experimental"},
    {"model": "copilot-search", "mode": "search", "status": "experimental"},
    {"model": "copilot", "mode": "smart", "status": "stable"},
    {"model": "copilot-smart", "mode": "smart", "status": "stable"},
    {"model": "copilot-chat", "mode": "chat", "status": "experimental"},
    {"model": "copilot-study", "mode": "study", "status": "experimental"},
]
_RUNTIME_SETTINGS_DEFAULTS = {
    "time_zone": "Asia/Shanghai",
    "model_alias": "m365-copilot",
    "auto_refresh": True,
    "refresh_before_seconds": 300,
    "idle_timeout_minutes": 30,
    # Chat WebSocket idle timeout (minutes): max gap between upstream frames before
    # a stalled connection is aborted. Heartbeats/deltas reset it, so this only trips
    # on a genuinely silent upstream. Per-user keys may override (0 => inherit this).
    "ws_idle_timeout_minutes": 5,
    # Cookie keepalive: how often the background loop scans the account pool
    # (minutes), and how long before a cookie's expiry it proactively refreshes
    # (hours). Refreshes are serialised (one Chromium at a time). See RefreshScheduler.
    "keepalive_check_minutes": 5,
    "cookie_keepalive_before_hours": 2,
    # Background reclaim of cold sessions / cloud conversations (see
    # session_autoclean.py). `auto_cleanup_minutes` is only how often the loop
    # wakes up; both thresholds default to 0, i.e. it does nothing until an
    # administrator opts in. session_idle_hours drops local sessions nobody
    # continued (the next turn of one then opens a fresh upstream thread with no
    # history). cloud_cleanup_idle_hours deletes cloud conversations no surviving
    # local session points at -- which on a real person's work account includes
    # the chats they had in the Copilot web UI themselves, hence opt-in.
    "auto_cleanup_minutes": 30,
    "session_idle_hours": 0,
    "cloud_cleanup_idle_hours": 0,
    # Ceiling on simultaneous upstream turns per account (0 = unlimited). One
    # account is one Microsoft identity no matter how many keys are bound to it,
    # and it starts refusing turns well before it starts queueing them. Requests
    # over the ceiling wait for a slot; none are ever rejected.
    "account_concurrency": 8,
    "cdp_port": 9222,
    "account_cdp_port_base": 9322,
    # Self-imposed per-key request ceiling for /v1/ endpoints. M365 publishes no
    # rate-limit headers, so this is the only thing stopping one runaway client on
    # a shared deployment from exhausting the account everyone else is bound to.
    # rpm 0 disables limiting globally; burst is the bucket depth, i.e. how large
    # a momentary spike is absorbed before the rpm average is enforced.
    "rate_limit_rpm": 60,
    "rate_limit_burst": 15,
    # Outbound proxy for everything that leaves the container: the substrate chat
    # WebSocket, media/token HTTP calls, and the refresh Chromium. Empty disables
    # it and falls back to whatever HTTPS_PROXY the deployment set. Needed where
    # M365 is not directly reachable (e.g. a mainland-China host). Applied by
    # apply_proxy_env(); localhost is always exempt (see _PROXY_NEVER).
    "proxy_url": "",
    "log_level": "INFO",
    "call_log_limit": 100,
    "run_permission": "full",
    # How a tools-bearing turn is planned (see tool_router.py). "auto" spends the
    # extra router turn only on tones measured not to honour the inline contract,
    # "native"/"router" pin one shape for diagnosing a report.
    "tool_planning_mode": "auto",
    # User/account runtime log toggles (see runtime_flags.py). verbose gates normal
    # progress logs, errors gates failure logs. Seeded from .env on first boot; the
    # persisted file wins once written, and the admin UI can flip them at runtime.
    "user_log_verbose": True,
    "user_log_errors": True,
    # Suppress high-frequency uvicorn access-log lines (see runtime_flags.py).
    # On by default; the admin UI can flip it at runtime.
    "suppress_access_log": True,
    "media_proxy_suffixes": list(_DEFAULT_MEDIA_PROXY_SUFFIXES),
    # Signed media proxy URL lifetime. The upstream designer/media auth token is
    # refreshed alongside cookies, so the fetch itself always uses the freshest
    # token; this TTL only governs how long a signed URL already stored in a
    # client's chat history stays resolvable. Default 30 days keeps historical
    # images alive far beyond the old 10-minute window.
    "media_proxy_ttl_seconds": 30 * 24 * 60 * 60,
    # Conversation modes shown in the picker. Each entry is
    # {value, label_zh, label_en}; `value` is the raw tone sent to M365 (any
    # string, so future upstream modes work without a code change) and the labels
    # are the editable display names. Defaults to the built-in list.
    "tone_options": [dict(o) for o in _BUILTIN_TONE_OPTIONS],
    # Consumer exposes facade model ids which map to the raw WebSocket `mode`.
    # It is deliberately separate from M365 tones and has no persistent suffix.
    "consumer_mode_options": [dict(o) for o in _BUILTIN_CONSUMER_MODE_OPTIONS],
}

# Max entries / field lengths to keep the pickers and persisted file bounded.
_MAX_TONE_OPTIONS = 40
_MAX_TONE_FIELD_LEN = 80
_MAX_CONSUMER_MODE_OPTIONS = 40
_MAX_CONSUMER_MODE_FIELD_LEN = 80


def normalize_consumer_mode_options(value) -> list[dict]:
    """Validate Consumer facade model mappings as one atomic configuration."""
    raw_items: list[tuple[dict, str]] = []
    if isinstance(value, str):
        for line_number, line in enumerate(value.splitlines(), 1):
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) not in (2, 3):
                raise ValueError(
                    f"consumer_mode_options line {line_number}: expected 2 or 3 "
                    "pipe-separated columns"
                )
            item = {"model": parts[0], "mode": parts[1]}
            if len(parts) == 3:
                item["status"] = parts[2]
            raw_items.append((item, f"line {line_number}"))
        if not raw_items:
            raise ValueError("consumer_mode_options must contain at least one entry")
    elif isinstance(value, list):
        if not value:
            return [dict(option) for option in _BUILTIN_CONSUMER_MODE_OPTIONS]
        raw_items = [(item, f"entry {index}") for index, item in enumerate(value, 1)]
    else:
        raise ValueError("consumer_mode_options must be a string or list")

    if len(raw_items) > _MAX_CONSUMER_MODE_OPTIONS:
        raise ValueError(
            f"consumer_mode_options: maximum {_MAX_CONSUMER_MODE_OPTIONS} entries"
        )

    options: list[dict] = []
    seen: set[str] = set()
    for item, location in raw_items:
        prefix = f"consumer_mode_options {location}"
        if not isinstance(item, dict):
            raise ValueError(f"{prefix}: must be an object")
        for field in ("model", "mode"):
            if field not in item:
                raise ValueError(f"{prefix}: {field} is required")
            if not isinstance(item[field], str):
                raise ValueError(f"{prefix}: {field} must be a string")
        if "status" in item and not isinstance(item["status"], str):
            raise ValueError(f"{prefix}: status must be a string")

        model = item["model"].strip().lower()
        mode = item["mode"].strip()
        status = item.get("status", "stable" if mode == "smart" else "experimental")
        status = status.strip().lower()
        for field, field_value in (("model", model), ("mode", mode), ("status", status)):
            if not field_value:
                raise ValueError(f"{prefix}: {field} must not be empty")
            if len(field_value) > _MAX_CONSUMER_MODE_FIELD_LEN:
                raise ValueError(
                    f"{prefix}: {field} must be at most "
                    f"{_MAX_CONSUMER_MODE_FIELD_LEN} characters"
                )
        if status not in {"stable", "experimental"}:
            raise ValueError(f"{prefix}: status must be stable or experimental")
        if model in seen:
            raise ValueError(f"{prefix}: duplicate model '{model}'")
        seen.add(model)
        options.append({"model": model, "mode": mode, "status": status})
    return options


def _sanitize_tone_label(label: str) -> str:
    """Collapse whitespace to underscores so a display name is safe to use as a
    model id in OpenAI-compatible clients (many clients break on spaces when a
    model is added manually)."""
    return re.sub(r"\s+", "_", label.strip())


def normalize_tone_options(value) -> list[dict]:
    """Coerce admin input into a clean list of {value,label,label_zh,label_en}.

    Accepts either a list of dicts (from JSON) or a newline-delimited string
    (from the textarea editor) where each line is `value | display_name`
    (display name optional; defaults to the tone value). Display names have
    their whitespace collapsed to underscores so each tone can double as a
    model id. Blank/duplicate values are dropped. Falls back to the built-in
    list when nothing valid remains so the picker is never empty.
    """
    raw_items: list = []
    if isinstance(value, str):
        for line in value.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            val = parts[0]
            label = parts[1] if len(parts) > 1 and parts[1] else val
            raw_items.append({"value": val, "label_zh": label})
    elif isinstance(value, list):
        raw_items = value

    options: list[dict] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        val = str(item.get("value") or "").strip()[:_MAX_TONE_FIELD_LEN]
        if not val or val in seen:
            continue
        label = str(item.get("label_zh") or item.get("label") or item.get("label_en") or val).strip()[:_MAX_TONE_FIELD_LEN]
        label = _sanitize_tone_label(label) or val
        seen.add(val)
        # label_en kept equal to label for backward-compatible serialization.
        options.append({"value": val, "label": label, "label_zh": label, "label_en": label})
        if len(options) >= _MAX_TONE_OPTIONS:
            break
    if not options:
        options = [dict(o) for o in _BUILTIN_TONE_OPTIONS]
    return options


def normalize_media_proxy_suffixes(value) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[\s,;]+", value)
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    suffixes: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        suffix = str(item or "").strip().lower().lstrip(".")
        if not suffix or suffix in seen or not _MEDIA_SUFFIX_RE.match(suffix):
            continue
        seen.add(suffix)
        suffixes.append(suffix)
    return suffixes


def normalize_proxy_url(value) -> str:
    """Validate an admin-supplied proxy URL, returning "" when unusable.

    Trust boundary: this string is handed to httpx/websockets and spliced into a
    Chromium argv, so anything with whitespace or control characters is rejected
    outright rather than normalised.
    """
    raw = str(value or "").strip()
    if not raw or len(raw) > 200:
        return ""
    if any(c.isspace() or ord(c) < 0x20 for c in raw):
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme.lower() not in _PROXY_SCHEMES or not parts.hostname:
        return ""
    # A path/query on a proxy URL is meaningless and usually a typo (a pasted
    # subscription link). Credentials and an explicit port are fine.
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return ""
    try:
        if parts.port is None:
            return ""
    except ValueError:
        return ""
    return raw


def _no_proxy_value(existing: str) -> str:
    """NO_PROXY with the local CDP hosts pinned in, preserving admin entries."""
    entries = [e.strip() for e in (existing or "").split(",") if e.strip()]
    lowered = {e.lower() for e in entries}
    entries.extend(h for h in _PROXY_NEVER if h not in lowered)
    return ",".join(entries)


# Environment as the process started, so clearing the setting restores the
# deployment's own proxy vars instead of erasing them.
_BASE_PROXY_ENV = {name: os.environ.get(name) for name in _PROXY_ENV_VARS}


def apply_proxy_env(proxy_url: str) -> None:
    """Publish the configured proxy through the standard env vars.

    httpx (trust_env=True) and websockets>=15 (proxy=True) both read these by
    default, so setting them here routes every outbound call -- including the
    substrate chat WebSocket -- without touching the ~24 individual call sites.

    ponytail: process-global by design, matching the global (not per-account)
    proxy setting. Per-account proxies would need the URL threaded to each call
    site as an explicit argument instead.
    """
    proxy_url = normalize_proxy_url(proxy_url)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        value = proxy_url or _BASE_PROXY_ENV[name]
        for key in (name, name.lower()):
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
    # Pin localhost even with no proxy configured: the deployment may set
    # HTTPS_PROXY itself, and CDP must stay direct either way.
    no_proxy = _no_proxy_value(_BASE_PROXY_ENV["NO_PROXY"] or "")
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy


def _runtime_settings_path(token_dir: str) -> Path:
    return Path(token_dir) / "runtime_settings.json"


def _read_runtime_settings(token_dir: str, env_defaults: dict | None = None) -> dict:
    data = dict(_RUNTIME_SETTINGS_DEFAULTS)
    # .env-provided defaults layer on top of the static defaults but UNDER the
    # persisted file, giving precedence: file > .env > static default.
    if env_defaults:
        data.update({k: env_defaults[k] for k in data.keys() if k in env_defaults})
    try:
        raw = json.loads(_runtime_settings_path(token_dir).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        raw = {}
    if isinstance(raw, dict):
        data.update({k: raw[k] for k in data.keys() if k in raw})
    data["time_zone"] = str(data.get("time_zone") or _RUNTIME_SETTINGS_DEFAULTS["time_zone"]).strip()
    data["model_alias"] = str(data.get("model_alias") or _RUNTIME_SETTINGS_DEFAULTS["model_alias"]).strip()
    data["auto_refresh"] = bool(data.get("auto_refresh"))
    data["refresh_before_seconds"] = max(0, int(data.get("refresh_before_seconds") or 0))
    data["idle_timeout_minutes"] = max(1, int(data.get("idle_timeout_minutes") or 1))
    data["ws_idle_timeout_minutes"] = max(1, int(data.get("ws_idle_timeout_minutes") or _RUNTIME_SETTINGS_DEFAULTS["ws_idle_timeout_minutes"]))
    data["keepalive_check_minutes"] = max(1, int(data.get("keepalive_check_minutes") or _RUNTIME_SETTINGS_DEFAULTS["keepalive_check_minutes"]))
    data["cookie_keepalive_before_hours"] = max(1, int(data.get("cookie_keepalive_before_hours") or _RUNTIME_SETTINGS_DEFAULTS["cookie_keepalive_before_hours"]))
    # 0 disables each of these, so they must not fall through to the default the
    # way the `or`-guarded ints above do.
    for field_name, minimum in (
        ("auto_cleanup_minutes", 0),
        ("session_idle_hours", 0),
        ("cloud_cleanup_idle_hours", 0),
        ("account_concurrency", 0),
    ):
        try:
            data[field_name] = max(minimum, int(data.get(field_name)))
        except (TypeError, ValueError):
            data[field_name] = _RUNTIME_SETTINGS_DEFAULTS[field_name]
    data["cdp_port"] = max(1, int(data.get("cdp_port") or _RUNTIME_SETTINGS_DEFAULTS["cdp_port"]))
    data["account_cdp_port_base"] = max(1, int(data.get("account_cdp_port_base") or _RUNTIME_SETTINGS_DEFAULTS["account_cdp_port_base"]))
    # 0 is meaningful here (disables limiting), so it must not fall through to the
    # default the way the `or`-guarded ints above do.
    try:
        data["rate_limit_rpm"] = max(0, int(data.get("rate_limit_rpm")))
    except (TypeError, ValueError):
        data["rate_limit_rpm"] = _RUNTIME_SETTINGS_DEFAULTS["rate_limit_rpm"]
    try:
        data["rate_limit_burst"] = max(1, int(data.get("rate_limit_burst")))
    except (TypeError, ValueError):
        data["rate_limit_burst"] = _RUNTIME_SETTINGS_DEFAULTS["rate_limit_burst"]
    data["proxy_url"] = normalize_proxy_url(data.get("proxy_url"))
    data["log_level"] = str(data.get("log_level") or _RUNTIME_SETTINGS_DEFAULTS["log_level"]).strip().upper()
    if data["log_level"] not in _LOG_LEVELS:
        data["log_level"] = _RUNTIME_SETTINGS_DEFAULTS["log_level"]
    data["call_log_limit"] = max(1, int(data.get("call_log_limit") or _RUNTIME_SETTINGS_DEFAULTS["call_log_limit"]))
    data["run_permission"] = str(data.get("run_permission") or _RUNTIME_SETTINGS_DEFAULTS["run_permission"]).strip()
    if data["run_permission"] not in _RUN_PERMISSIONS:
        data["run_permission"] = _RUNTIME_SETTINGS_DEFAULTS["run_permission"]
    data["tool_planning_mode"] = tool_planning_mode(data.get("tool_planning_mode"))
    data["user_log_verbose"] = bool(data.get("user_log_verbose"))
    data["user_log_errors"] = bool(data.get("user_log_errors"))
    data["suppress_access_log"] = bool(data.get("suppress_access_log"))
    data["media_proxy_suffixes"] = normalize_media_proxy_suffixes(data.get("media_proxy_suffixes")) or list(_DEFAULT_MEDIA_PROXY_SUFFIXES)
    persisted_tone_options = raw.get("tone_options") if isinstance(raw, dict) else None
    tone_options = data.get("tone_options")
    if persisted_tone_options in _HISTORICAL_BUILTIN_TONE_OPTIONS:
        tone_options = _BUILTIN_TONE_OPTIONS
    data["tone_options"] = normalize_tone_options(tone_options)
    persisted_consumer_options = (
        raw.get("consumer_mode_options") if isinstance(raw, dict) else None
    )
    try:
        consumer_options = data.get("consumer_mode_options")
        if persisted_consumer_options in (
            _LEGACY_BUILTIN_CONSUMER_MODE_OPTIONS,
            _PREVIOUS_BUILTIN_CONSUMER_MODE_OPTIONS,
        ):
            consumer_options = _BUILTIN_CONSUMER_MODE_OPTIONS
        data["consumer_mode_options"] = normalize_consumer_mode_options(
            consumer_options
        )
    except ValueError as exc:
        _log.warning(
            "Invalid persisted consumer_mode_options; using built-in defaults: %s",
            exc,
        )
        data["consumer_mode_options"] = [
            dict(option) for option in _BUILTIN_CONSUMER_MODE_OPTIONS
        ]
    try:
        data["media_proxy_ttl_seconds"] = max(60, int(data.get("media_proxy_ttl_seconds") or 0))
    except (TypeError, ValueError):
        data["media_proxy_ttl_seconds"] = _RUNTIME_SETTINGS_DEFAULTS["media_proxy_ttl_seconds"]
    return data


def _write_runtime_settings(token_dir: str, data: dict) -> None:
    # Atomic: a torn write here reads back as unparseable on the next start, which
    # silently reverts every runtime setting to its default.
    write_text_atomic(
        _runtime_settings_path(token_dir),
        json.dumps(data, ensure_ascii=False, indent=2),
    )
