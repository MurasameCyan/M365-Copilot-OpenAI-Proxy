"""Probe which M365 conversation tones (models) this account can actually use.

M365 offers no API that lists the modes available to an account, and it never
errors on a ``tone`` it will not serve -- it fails silently, in two distinguishable
ways (measured 2026-08-02): a tone it knows but this account may not use answers
with one canned refusal line, and a tone it does not know at all answers with
nothing. So the only way to find out what works is to send a throwaway turn per
candidate tone and look at the answer. Run this when a mode stops working --
Microsoft moves models in and out of the picker -- to get a paste-ready list for
``/admin`` -> 运行设置 -> 对话模式列表.

Usage (token via env is preferred; a CLI arg lands in shell history):

    $env:M365_ACCESS_TOKEN = "<substrate access token>"
    python scan_tones.py                 # probe the configured tones
    python scan_tones.py --all           # also probe the guessed candidates
    python scan_tones.py --tones Claude_Opus,Gpt_5_7_Chat

Every probe is a real Copilot request against the account: it spends quota and
too many in a row is exactly the traffic pattern that gets an account flagged.
Keep the list short and leave --delay alone unless you have a reason.

The token is the substrate one the userscript pushes (``access_token`` on the
``wss://substrate.office.com`` URL), not an API key of this proxy.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from m365_copilot_openai_proxy.substrate_client import (
    _EMPTY_TURN_MARKER,
    _M365_REFUSAL_TEXTS,
    _REFUSED_TURN_MARKER,
    SubstrateCopilotClient,
    SubstrateCopilotError,
)
from m365_copilot_openai_proxy.tone_options import TONE_OPTIONS

# Guesses to probe with --all. M365 tone values follow the picker's own naming:
# ``Claude_<Family>[_Reasoning]`` and ``Gpt_<major>_<minor>_<Chat|Reasoning>``.
# Unknown values cost one request each; they are harmless but not free.
#
# Trimmed 2026-08-02 after scanning ~115 guesses: what is left is what M365 really
# knows, so a re-scan spends quota only on values that can change. Confirmed to be
# no tone at all -- do not re-add: Claude_Opus_5 / _4_8 / _Reasoning, Claude_Fable_5,
# Claude_Fable_Reasoning, Claude_Mythos(_5), Claude_Haiku, Claude_Sonnet_5,
# Claude_Sonnet_4_6, Claude_Sonnet_Chat, Gpt_5_7_*, Gpt_5_8_*, Gpt_6_*, Gpt_5_1_*,
# Gpt_5_Chat, Gpt_5_Reasoning, Balanced/Creative/Precise_Classic, Auto, Default,
# Pro, Thinking, Deep_Research, Researcher, Analyst, Smart, Think_Deeper, Agent,
# Vision.
# Update 2026-09-07: Gpt_6_Astra now answers and is in TONE_OPTIONS; the earlier
# Gpt_6_* guesses are no longer grounds to exclude all GPT-6 selectors. The exact
# Gpt_6_Reasoning selector returned Failed/InternalError in two fresh direct turns.
#
# The GPT grid is closed on both sides: 5_1 and 5_7 are unknown while every
# 5_2..5_6 x {Chat,Reasoning} pair is real, so a new version appears as a NEW minor
# inside that naming shape, not as 6_x -- re-probe Gpt_5_7_* first when Microsoft
# ships one.
#
# Also settled 2026-08-02: this tenant serves NO third-party models. 36 spellings
# across Mai/Phi (Microsoft), DeepSeek, Gemini, Llama, Mistral, Grok, Qwen,
# Command_R, Nova, Titan, Kimi, Glm, the OpenAI o-series and Sora/Dalle all came
# back unknown. Tone matching is case-insensitive (``magic`` and ``claude_sonnet``
# both answer), so those misses are real absences rather than spelling slips: the
# enum is GPT + Claude + a handful of non-model modes, nothing else.
# Also settled 2026-08-28: gpt-5.6-sol / -terra / -luna and gpt-image-2, reported
# working on another tenant, are not tone values here. 12 spellings (Sol, Terra, Luna,
# Gpt_5_6_Sol, Gpt_5_6_Terra, Gpt_5_6_Luna, Gpt_5_6_Sol_Reasoning, Gpt_Image,
# Gpt_Image_2, Gpt_Image_2_Chat, Image, Image_2) all answered nothing. Do not re-add
# without a new reason: matching is case-insensitive, so these were real absences.
CANDIDATE_TONES = [
    # Real modes that this account was refused on 2026-08-02. Worth re-probing:
    # availability follows Microsoft's rollout, not anything on our side. Gpt_5_6_Chat
    # and Gpt_5_3_Reasoning left this list on 2026-08-28 by starting to answer -- the
    # first is now in TONE_OPTIONS, the second already was.
    "Claude_Opus",
    "Balanced", "Creative", "Precise",
    # Usable on 2026-08-02 but missing from TONE_OPTIONS -- probed until someone
    # adds them to /admin -> 运行设置 -> 对话模式列表.
    "Gpt_5_4_Chat", "Gpt_5_4_Reasoning", "Gpt_5_3_Chat",
]

PROBE_PROMPT = "Reply with the single word: ok"


async def probe(token: str, tone: str, prompt: str, time_zone: str) -> tuple[str, str]:
    """Send one throwaway turn with `tone`. Returns (verdict, detail).

    verdict is one of:
      "ok"      -- the mode answered, so it is usable by this account
      "refused" -- M365 marked the turn failed / sent its canned line => it knows
                   this tone but will not serve it to this account (withdrawn,
                   preview-gated, unlicensed)
      "unknown" -- empty answer => M365 does not recognise this tone value at
                   all, i.e. the spelling is wrong or the mode never existed.
                   Costs TWO requests, not one: the client retries an empty turn
                   once before giving up.
      "error"   -- anything else, e.g. an expired token or a handshake timeout;
                   reported per tone rather than swallowed, so a mid-scan token
                   expiry is visible instead of looking like a wall of dead modes
    """
    try:
        client = SubstrateCopilotClient(token, time_zone=time_zone, tone=tone)
        # session=None => a fresh throwaway conversation, so probes never share
        # history and a refusal cannot be a leftover from the previous tone.
        answer = (await client.chat(prompt, [])).strip()
    except SubstrateCopilotError as exc:
        # substrate_client turns both silent failures into errors; the markers are
        # imported from it so a reworded message cannot quietly land here as "error".
        detail = str(exc)
        if _REFUSED_TURN_MARKER in detail:
            verdict = "refused"
        elif _EMPTY_TURN_MARKER in detail:
            verdict = "unknown"
        else:
            verdict = "error"
        return verdict, detail
    if answer in _M365_REFUSAL_TEXTS:
        return "refused", answer
    return ("ok", answer) if answer else ("unknown", "empty response")


async def scan(token: str, tones: list[str], prompt: str, delay: float, time_zone: str) -> int:
    """Probe tones serially, printing each verdict as it lands. Returns exit code."""
    # Constructing a client validates the token without spending a request, so a
    # bad/expired token fails here instead of once per tone.
    try:
        SubstrateCopilotClient(token, time_zone=time_zone, tone=tones[0])
    except SubstrateCopilotError as exc:
        print(f"token unusable: {exc}")
        return 1
    results: dict[str, list[str]] = {"ok": [], "refused": [], "unknown": [], "error": []}
    for index, tone in enumerate(tones):
        if index:
            await asyncio.sleep(delay)
        verdict, detail = await probe(token, tone, prompt, time_zone)
        results[verdict].append(tone)
        mark = {"ok": "OK      ", "refused": "REFUSED ", "unknown": "UNKNOWN ", "error": "ERROR   "}[verdict]
        suffix = f" -- {detail[:90]}" if verdict == "error" else ""
        print(f"  {mark} {tone}{suffix}", flush=True)
    print(f"\nusable ({len(results['ok'])}) -- paste into 对话模式列表:")
    for tone in results["ok"]:
        print(f"{tone} | {tone}")
    if results["refused"]:
        print(f"\nreal modes this account may not use ({len(results['refused'])}):")
        print("  " + ", ".join(results["refused"]))
    if results["unknown"]:
        print(f"\nnot a tone M365 recognises ({len(results['unknown'])}):")
        print("  " + ", ".join(results["unknown"]))
    if results["error"]:
        print(f"\ncould not be probed ({len(results['error'])}):")
        print("  " + ", ".join(results["error"]))
    # "unknown" is an expected outcome when probing guesses, so it does not fail
    # the run; "error" does, because it usually means the scan itself went wrong.
    return 0 if results["ok"] and not results["error"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--token", default=os.environ.get("M365_ACCESS_TOKEN", ""),
                        help="substrate access token (default: $M365_ACCESS_TOKEN)")
    parser.add_argument("--tones", default="",
                        help="comma-separated tones to probe instead of the configured ones")
    parser.add_argument("--all", action="store_true",
                        help="also probe the guessed candidates (slow, spends quota)")
    parser.add_argument("--prompt", default=PROBE_PROMPT, help="probe prompt")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds between probes (default: 2.0)")
    parser.add_argument("--time-zone", default="Asia/Shanghai", help="time zone sent to Copilot")
    args = parser.parse_args()

    if not args.token:
        parser.error("no token: set $M365_ACCESS_TOKEN or pass --token")

    if args.tones:
        tones = [t.strip() for t in args.tones.split(",") if t.strip()]
    else:
        tones = [str(o["value"]) for o in TONE_OPTIONS]
        if args.all:
            tones += [t for t in CANDIDATE_TONES if t not in tones]
    if not tones:
        parser.error("no tones to probe")

    print(f"probing {len(tones)} tone(s), {args.delay}s apart -- each one is a real Copilot request\n")
    return asyncio.run(scan(args.token, tones, args.prompt, args.delay, args.time_zone))


if __name__ == "__main__":
    sys.exit(main())
