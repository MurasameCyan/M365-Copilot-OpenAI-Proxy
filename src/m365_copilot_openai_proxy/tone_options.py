from __future__ import annotations

# Conversation tone (mode) options discovered from M365 Copilot's mode picker.
# The `tone` field in the Substrate chat payload controls which model/mode is used.
# Display labels become /v1/models ids (spaces → underscores via normalize_tone_options).
#
# Which of these an account may actually use is decided on the M365 side, not here:
# `Claude_Fable` and `Claude_Opus` are real modes that this tenant is currently
# refused on (measured 2026-08-02 with scan_tones.py) and are listed on purpose, so
# they come back by themselves once Microsoft rolls them out -- do not "clean them
# up". A refused mode now surfaces as an upstream error naming the mode rather than
# as a silent canned reply (see substrate_client._M365_REFUSAL_TEXTS).
#
# `Gpt_5_6_Chat` is the rollout moving the other way: refused on 2026-08-02, it
# answered normally on 2026-08-28 (.probe/tone_candidates.py), so it was promoted out
# of scan_tones.CANDIDATE_TONES into the picker. That same run re-probed a list of
# model names reported working on someone else's tenant; the four with no counterpart
# here -- gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, gpt-image-2 -- came back "empty
# response twice" across 12 spellings (bare codename, versioned, _Chat/_Reasoning
# suffixed). Matching is case-insensitive upstream, so those are absences, not casing
# slips: they are not tone values on this tenant and adding them would only route
# traffic to a mode that answers nothing. Re-probe rather than re-add on faith.
# Gpt_6_Astra completed a fresh direct M365 turn on 2026-09-07. The selector is
# usable; the response does not attest the underlying model identity. See
# docs/gpt6-tone-verification-2026-09-07.md. Gpt_6_Reasoning is listed by request
# after successful Studio tool workflows, but ordinary calls still fail and
# Router fails on the tool-result continuation. See README's GPT-6 restrictions.
TONE_OPTIONS = [
    {"value": "Magic", "label": "Copilot_自动", "label_zh": "Copilot_自动", "label_en": "Copilot_自动"},
    {"value": "Chat", "label": "Copilot_快速答复", "label_zh": "Copilot_快速答复", "label_en": "Copilot_快速答复"},
    {"value": "Reasoning", "label": "Copilot_深度思考", "label_zh": "Copilot_深度思考", "label_en": "Copilot_深度思考"},
    {"value": "Claude_Sonnet", "label": "claude-sonnet-4-6", "label_zh": "claude-sonnet-4-6", "label_en": "claude-sonnet-4-6"},
    {"value": "Claude_Sonnet_Reasoning", "label": "claude-sonnet-4-5", "label_zh": "claude-sonnet-4-5", "label_en": "claude-sonnet-4-5"},
    {"value": "Claude_Fable", "label": "claude-fable-5", "label_zh": "claude-fable-5", "label_en": "claude-fable-5"},
    {"value": "Claude_Opus", "label": "claude-opus", "label_zh": "claude-opus", "label_en": "claude-opus"},
    {"value": "Gpt_6_Astra", "label": "gpt-6_Chat", "label_zh": "gpt-6_Chat", "label_en": "gpt-6_Chat"},
    {"value": "Gpt_6_Reasoning", "label": "gpt-6", "label_zh": "gpt-6", "label_en": "gpt-6"},
    {"value": "Gpt_5_6_Chat", "label": "gpt-5.6_Chat", "label_zh": "gpt-5.6_Chat", "label_en": "gpt-5.6_Chat"},
    {"value": "Gpt_5_6_Reasoning", "label": "gpt-5.6", "label_zh": "gpt-5.6", "label_en": "gpt-5.6"},
    {"value": "Gpt_5_5_Chat", "label": "gpt-5.5_Chat", "label_zh": "gpt-5.5_Chat", "label_en": "gpt-5.5_Chat"},
    {"value": "Gpt_5_5_Reasoning", "label": "gpt-5.5", "label_zh": "gpt-5.5", "label_en": "gpt-5.5"},
    {"value": "Gpt_5_4_Chat", "label": "gpt-5.4_Chat", "label_zh": "gpt-5.4_Chat", "label_en": "gpt-5.4_Chat"},
    {"value": "Gpt_5_4_Reasoning", "label": "gpt-5.4", "label_zh": "gpt-5.4", "label_en": "gpt-5.4"},
    {"value": "Gpt_5_3_Chat", "label": "gpt-5.3_Chat", "label_zh": "gpt-5.3_Chat", "label_en": "gpt-5.3_Chat"},
    {"value": "Gpt_5_3_Reasoning", "label": "gpt-5.3", "label_zh": "gpt-5.3", "label_en": "gpt-5.3"},
    {"value": "Gpt_5_2_Chat", "label": "gpt-5.2_Chat", "label_zh": "gpt-5.2_Chat", "label_en": "gpt-5.2_Chat"},
    {"value": "Gpt_5_2_Reasoning", "label": "gpt-5.2", "label_zh": "gpt-5.2", "label_en": "gpt-5.2"},
]
TONE_VALUES = {option["value"] for option in TONE_OPTIONS}

# Whether a tone honours the injected tool-calling contract is a property of the
# tone, not of this proxy: prompt injection (translator._format_tools_prompt) ->
# fenced-block parse (tool_call_parser) -> tool_calls is the same pipeline for
# every tone. Measured 2026-08-18 against this tenant, one real upstream turn per
# cell, with the Read / bash / Write tools:
#   Claude_Sonnet, Claude_Sonnet_Reasoning -> 3/3 correct tool_calls each
#   Magic, Reasoning, Gpt_5_6_Reasoning,
#   Gpt_5_5_Reasoning, Gpt_5_5_Chat        -> 0 tool_calls. The turn either refuses
#       ("I cannot reach your local files, attach them instead") or runs the
#       command in Microsoft's own server-side interpreter and returns its output
#       as prose. Forcing tool_choice does not change this.
#   Claude_Fable, Claude_Opus              -> tone refused outright, untestable
# Only directly measured tones are listed. Everything else is "unknown" and is
# never flagged: tone behaviour drifts with Microsoft's rollout (see the same
# caveat above for mode availability), so an untested tone must not be advertised
# as broken. This map is advisory only -- the hard failure in
# routes_api_common.required_tool_call_error keys on the actual outcome, not this.
TONE_TOOL_CALLING = {
    "Claude_Sonnet": "verified",
    "Claude_Sonnet_Reasoning": "verified",
    "Magic": "unsupported",
    "Reasoning": "unsupported",
    "Gpt_5_6_Reasoning": "unsupported",
    "Gpt_5_5_Reasoning": "unsupported",
    "Gpt_5_5_Chat": "unsupported",
}

# The server-side interpreter, measured per tone -- NOT per family. First pass
# (2026-08-25, .probe/ci_ab.py): tone=Magic returned the SHA-256 of a nonce minted at
# probe time and an exact 12x12-digit product, with GeneratedCode frames on the wire;
# Claude_Sonnet emitted no GeneratedCode frame and hallucinated the digest.
# Second pass the same day (.probe/reasoning_interpreter_frames.py) killed the
# "Claude tones cannot compute" generalisation that reading looked like: on both
# oracles Claude_Sonnet_Reasoning was exactly right WITH GeneratedCode/python frames,
# while Claude_Sonnet failed the same nonce in the same session ("I'll compute this
# directly from my knowledge of the SHA-256 algorithm"). So the split is the tone, and
# Claude_Sonnet_Reasoning is the one selector measured to have BOTH halves -- it is
# also "verified" for the tool contract above, which makes it the remedy to point a
# user at rather than a Copilot tone that cannot tool-call.
# What remains true is the narrow version: Claude_Sonnet honours the tool contract and
# cannot compute, so a user asking it for a hash gets a confident wrong answer.
# Half of that is caught on turns that carry tools: the exact-computation rule in
# translator._DEFAULT_TOOL_SYSTEM_PROMPT turns the invented digest into "I cannot
# compute this exactly here" when nothing declared can run code. A turn with no tools
# carries no contract, so substrate_parse._combine_text appends one sentence for the
# tones below instead -- which is why this map has to stay measurement-only. Telling
# a tone that DOES have the interpreter it cannot execute would suppress a working
# capability, so "unknown" must keep meaning "say nothing" here, exactly as above.
TONE_SERVER_INTERPRETER = {
    "Magic": "verified",
    "Claude_Sonnet": "absent",
    "Claude_Sonnet_Reasoning": "verified",
    # Sweep of every remaining tone, 2026-08-25, one oracle turn each
    # (.probe/interpreter_scan.py). All nine returned the fresh nonce's digest exactly,
    # so Claude_Sonnet is the ONLY tone measured to fabricate -- the sentence covers
    # one cell, and that is the whole population of the problem, not a sampling gap.
    # Chat / Gpt_5_5_* / Gpt_5_4_* / Gpt_5_3_Chat / Gpt_5_2_Chat also showed a
    # GeneratedCode frame; Reasoning and Gpt_5_6_Reasoning were exactly right WITHOUT
    # one reaching the frame recorder. A fresh nonce cannot be recalled, so the correct
    # answer is what earns "verified" here and the frame is corroboration only.
    "Chat": "verified",
    "Reasoning": "verified",
    "Gpt_5_6_Reasoning": "verified",
    "Gpt_5_5_Chat": "verified",
    "Gpt_5_5_Reasoning": "verified",
    "Gpt_5_4_Chat": "verified",
    "Gpt_5_4_Reasoning": "verified",
    "Gpt_5_3_Chat": "verified",
    "Gpt_5_2_Chat": "verified",
    # 2026-08-28: Gpt_5_3_Reasoning, which the 08-25 sweep could not test (the turn
    # errored with InternalError), now answers -- and returned a fresh nonce's digest
    # with a GeneratedCode frame on the wire. That was availability moving, not
    # capability, exactly as the note below predicted.
    "Gpt_5_3_Reasoning": "verified",
    # Gpt_5_6_Chat is the first tone measured to do BOTH: it fabricated a fresh nonce's
    # digest on one turn, then returned two other fresh nonces correctly (2/3, no
    # GeneratedCode frame in any of the three). "absent" would be false -- it provably
    # computes -- and "verified" would promise a correctness it does not hold, so it
    # reuses the "flaky" status the sibling Consumer map already defines for a selector
    # that both complies and does not. Practically this means silence: the only reader
    # (substrate_parse._combine_text) keys on "absent", so flaky appends nothing, which
    # is the conservative half. A user who needs an exact value here still has to
    # declare a tool that executes -- the tools-turn rule covers that path.
    "Gpt_5_6_Chat": "flaky",
    # Deliberately absent from this map: Gpt_5_2_Reasoning, which after 120s streamed
    # its own tool call as prose -- {"code": "import hashlib\n..."} -- so it neither
    # computed nor invented. "absent" would only add a sentence it does not need, and
    # that failure is not one a prompt can fix.
}


def tone_server_interpreter(tone: str | None) -> str:
    """Measured server-side code execution: verified / flaky / absent / unknown.

    Only "absent" changes behaviour; "flaky" is recorded so a tone measured to both
    compute and fabricate is not filed under either clean answer.
    """
    return TONE_SERVER_INTERPRETER.get(str(tone or ""), "unknown")

# Same question for the Consumer provider, whose selector is a mode rather than a
# tone (see _BUILTIN_CONSUMER_MODE_OPTIONS). Measured 2026-08-19 against the live
# account, three rounds over all nine Consumer model names, streaming, with a Read
# tool; the counts below pool the names that share a mode:
#   search 3/3, research 3/3, coco 3/3    -> honoured every time
#   reasoning 5/6 (copilot-reasoning + copilot-thinking) -> one miss in six
#   chat 1/3, smart 1/6 (copilot + copilot-smart)        -> complied once
#   study 0/3                                            -> never
# Unlike the tones, Consumer is NOT binary: the same mode both refused ("I can't
# access files on your machine, paste the contents") and emitted a correct call
# across rounds, so "flaky" is its own status -- calling smart/chat unsupported
# would be false (they provably can) and calling them verified would be worse.
# Keys are modes, not model names: two model names can share one mode, and the
# routes carry the resolved mode.
CONSUMER_MODE_TOOL_CALLING: dict[str, str] = {
    "search": "verified",
    "research": "verified",
    "coco": "verified",
    "reasoning": "verified",
    "chat": "flaky",
    "smart": "flaky",
    "study": "unsupported",
}


# Whether a Consumer mode actually draws when asked for a picture. Measured
# 2026-08-25 against the live account, one turn per mode, by tapping `drain_json`
# so the record is the frames the shipped client received:
#   smart, chat, search   -> generatingImage + partialImageGenerated (a real JPEG)
#   reasoning, study, research, coco -> not one image frame
# The four that do not draw are not all the same failure, and none of them is
# ours to fix:
#   reasoning claims success in prose ("已为你生成一张...") having sent nothing --
#     the reported bug, and the reason it is recorded here rather than argued
#     about: the proxy has no image to lose, and Copilot's own web UI on this
#     account answers the same prompt the same way
#   study refuses by design (it wants to teach, not draw)
#   research answers with a web image-search stub (`<vs?i=...>`)
#   coco asked a safety question about the prompt instead of drawing
# So a mode missing an image is upstream behaviour, not a delivery bug. Keys are
# modes for the same reason as the map above.
CONSUMER_MODE_IMAGE_GENERATION: dict[str, str] = {
    "smart": "verified",
    "chat": "verified",
    "search": "verified",
    "reasoning": "absent",
    "study": "absent",
    "research": "absent",
    "coco": "absent",
}


def consumer_mode_image_generation(mode: str | None) -> str:
    """Measured image generation for a Consumer mode: verified / absent / unknown.

    ``absent`` is a statement about upstream, not about this proxy: the mode was
    asked for a picture on a live account and sent no image frame at all.
    """
    return CONSUMER_MODE_IMAGE_GENERATION.get(str(mode or ""), "unknown")


def tone_tool_calling(tone: str | None) -> str:
    """Measured tool-calling status: verified / flaky / unsupported / unknown.

    Also answers for a Consumer mode, because the routes carry whichever selector
    the provider uses in the same variable (see apply_request_model). Tone values
    are CamelCase and Consumer modes are lower_snake, so the two namespaces cannot
    collide.
    """
    key = str(tone or "")
    return TONE_TOOL_CALLING.get(key) or CONSUMER_MODE_TOOL_CALLING.get(key, "unknown")


# How a tools-bearing turn is planned; see tool_router.py for what each mode does.
# Kept in this leaf module rather than next to the router because runtime_settings
# needs to normalize the persisted value, and importing the router there would
# close a cycle (runtime_settings <- media_proxy <- substrate_client <- router).
TOOL_PLANNING_MODES = {"auto", "native", "router", "studio"}


def tool_planning_mode(raw: str | None) -> str:
    """Normalize a stored/requested planning mode, defaulting to ``auto``."""
    value = str(raw or "").strip().lower()
    return value if value in TOOL_PLANNING_MODES else "auto"


def router_applies(mode: str | None, tone: str) -> bool:
    """Whether this turn should be planned by a dedicated classification turn.

    ``auto`` keys on the measured status rather than a hardcoded tone list, so a
    tone that starts honouring the native contract stops paying for the extra
    turn without a code change -- and an unmeasured tone keeps today's behaviour
    instead of silently doubling its upstream cost. ``flaky`` routes too: a
    selector that complied 1-in-6 natively is one an agent client cannot build on,
    and the router turn is a deterministic replacement for that coin flip.
    """
    normalized = tool_planning_mode(mode)
    if normalized == "native":
        return False
    if normalized in {"router", "studio"}:
        return True
    return tone_tool_calling(tone) in {"unsupported", "flaky"}


def effective_tool_calling(tone: str | None, planning_mode: str | None) -> str:
    """Measured status, plus "router" when this turn's tools go through the router.

    What a client needs to know is whether sending ``tools`` will produce calls,
    and with router mode on it does even for a tone that ignores the inline
    contract. Reporting the bare measured status there is a pessimistic lie that
    makes a gating client withhold tools it would have got calls for.
    """
    status = tone_tool_calling(tone)
    if status != "verified" and router_applies(planning_mode, str(tone or "")):
        return "router"
    return status
