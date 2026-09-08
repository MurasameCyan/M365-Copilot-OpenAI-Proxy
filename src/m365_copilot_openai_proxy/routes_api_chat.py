from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .call_log_store import append_call_log, record_response_text
from .config import Settings
from .models import OpenAIChatRequest
from .response_helpers import _openai_stream
from .routes_api_common import (
    REQUIRED_NO_CALL_OUTCOME,
    REQUIRED_REJECTED_CALL_OUTCOME,
    TOOL_CALLING_HEADER,
    TOOL_OUTCOME_HEADER,
    apply_request_model,
    _consumer_mode_options,
    build_consumer_models_list,
    effective_run_permission,
    effective_tool_planning_mode,
    no_tool_calls_note,
    prose_with_reason,
    request_model_alias,
    required_tool_call_error,
    upstream_http_error,
)
from .routes_media_proxy import request_media_rewriter
from .session_helpers import (
    _persistent_session,
    _studio_history_context_id,
    _studio_session_for_context,
    _studio_session_namespace,
    record_auto_session_response,
)
from .tone_resolver import build_models_list, normalized_session_model
from .tone_options import effective_tool_calling, tone_tool_calling
from .session_store import PersistentSession
from .sse_stream import keepalive_stream, merge_sse_headers
from .substrate_client import SubstrateCopilotClient, SubstrateCopilotError, SubstrateThrottled
from .studio_planner import (
    PlannerTurn,
    ordered_or_answered,
    ordered_or_streamed,
)
from .studio_agent_discovery import ensure_studio_client_snapshot
from .tool_call_parser import (
    _RETRY_INSTRUCTION,
    _extract_prose_write,
    _extract_tool_calls,
    _filter_read_only_tool_calls,
    _filter_schema_valid_tool_calls,
    _read_only_intent_for_messages,
    _looks_like_fake_file_claim,
    planner_fallback_needed,
    _strip_tool_call_blocks,
    split_no_tool_marker,
)
from .usage_store import estimate_text_tokens, estimate_upstream_input_tokens, openai_usage, usage_for_record
from .translator import effective_tools, normalize_tool_choice, tool_description_lines, translate_openai_request
from .tool_router import build_router_prompt, routed_or_answered, routed_or_streamed, router_applies


def register_chat_routes(
    app: FastAPI,
    get_settings: Callable[[], Settings],
    get_copilot_client: Callable[..., SubstrateCopilotClient],
) -> None:
    @app.get("/v1/models")
    async def list_models(raw_request: Request, settings: Settings = Depends(get_settings)) -> dict:
        created = int(time.time())
        account = getattr(raw_request.state, "account", None)
        # Per-key override first: the list advertises what THIS key's tools-bearing
        # turns will actually do, so a user who picked router mode is not told the
        # global default's (pessimistic) status.
        planning_mode = effective_tool_planning_mode(
            app, getattr(raw_request.state, "api_key_obj", None)
        )
        if getattr(account, "provider", "m365") == "consumer":
            models = build_consumer_models_list(
                _consumer_mode_options(app), created, planning_mode,
            )
        else:
            tone_options = getattr(app.state, "tone_options", None) or []
            models = build_models_list(tone_options, created, planning_mode)
        return {
            "object": "list",
            "data": models,
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(
        raw_request: Request,
        request: OpenAIChatRequest,
        settings: Settings = Depends(get_settings),
    ):
        _log = logging.getLogger("copilot_proxy")
        model_alias = request_model_alias(app, raw_request, settings)
        # Resolve tool_choice once. `tools` below is the effective list: empty for
        # tool_choice="none" (so parsing, the prose fallback and the corrective
        # retry are all disabled together), narrowed to one entry when a specific
        # tool was demanded.
        choice = normalize_tool_choice(request.tool_choice, getattr(request, "parallel_tool_calls", None))
        tools = effective_tools(request.tools, choice)
        is_tool_result_continuation = bool(
            tools and request.messages and request.messages[-1].role == "tool"
        )
        allow_final_answer = is_tool_result_continuation and choice[0] == "auto"
        _log.info("[/v1/chat/completions] stream=%s tools=%d messages=%d model=%s tool_choice=%s",
                  request.stream, len(tools) if tools else 0,
                  len(request.messages), request.model, choice[0])
        if tools:
            for t in tools:
                _log.info("  tool: %s", t.function.name if t.function else "?")
        # Record call for web UI
        call_record = {
            "api": "chat",
            "endpoint": "/v1/chat/completions",
            "time": time.strftime("%H:%M:%S"),
            "ts": time.time(),
            "stream": request.stream,
            "tools": [t.function.name for t in tools] if tools else [],
            "tool_choice": choice[0],
            "messages": len(request.messages),
            "model": request.model,
            "tool_calls_result": None,
        }
        try:
            # Apply the provider-specific upstream selector. M365 receives a tone;
            # Consumer receives a mode. Persistent-session normalization remains
            # M365-only because the Consumer adapter resends the full transcript.
            client, resolved_tone, is_consumer = apply_request_model(
                app, raw_request, get_copilot_client, request.model
            )
            session = None
            if not is_consumer:
                session = _persistent_session(
                    app,
                    raw_request,
                    normalized_session_model(request.model),
                    request.user,
                    request,
                )
            # Whenever we reuse a persistent M365 session that already has history
            # (both auto mode and explicit :persist mode), the server remembers the
            # prior turns — so only send the incremental turn instead of resending the
            # whole transcript on every request.
            incremental = (
                session is not None
                and session.turn_count > 0
            )
            # Diagnostics: surface in the web call-log so we can see whether the
            # incremental optimization actually kicks in across turns.
            call_record["incremental"] = incremental
            call_record["turn_count"] = session.turn_count if session is not None else None
            _key_obj = getattr(raw_request.state, "api_key_obj", None)
            call_record["tone"] = resolved_tone
            # System prompt: the key's own override wins; if the key hasn't set one,
            # fall back to the global system prompt (admin's "系统提示词（全局）").
            _key_sp = ((_key_obj.system_prompt if _key_obj is not None else "") or "").strip()
            _system_override = _key_sp or getattr(app.state, 'system_prompt', '')
            run_permission = effective_run_permission(app, _key_obj)
            read_only_guard = run_permission == "read_only" or _read_only_intent_for_messages(
                (m.role, m.content) for m in request.messages
            )
            call_record["run_permission"] = run_permission
            call_record["read_only_guard"] = read_only_guard
            # Tool calling is tone-dependent (see tone_options.TONE_TOOL_CALLING),
            # so record and advertise the status: a degraded turn used to be
            # indistinguishable from a working one that merely answered in prose.
            # The header carries the EFFECTIVE status while the log and call record
            # keep the measured one -- the client needs to know whether tools work
            # this turn (they do, via the router), the operator needs to know which
            # of the two shapes got them there.
            planning_mode = effective_tool_planning_mode(app, _key_obj)
            planner_chain = bool(tools) and planning_mode in {"studio", "router"}
            tool_status = tone_tool_calling(resolved_tone) if tools else ""
            actual_planning = planning_mode
            studio_client = None
            studio_session = None
            studio_translated = None
            studio_snapshot = None
            if tools and planning_mode in {"studio", "router"}:
                account = getattr(raw_request.state, "account", None)
                if is_consumer:
                    actual_planning = "router"
                    call_record["studio_fallback"] = "unsupported_provider"
                else:
                    studio_snapshot = await ensure_studio_client_snapshot(app, account)
                if not is_consumer and studio_snapshot is None:
                    actual_planning = "router"
                    call_record["studio_fallback"] = "not_ready"
                elif not is_consumer:
                    studio_token, studio_agent_id = studio_snapshot
                    studio_client = get_copilot_client(
                        raw_request,
                        studio_agent_id=studio_agent_id,
                        token_override=studio_token,
                    )
                    studio_client._tone = resolved_tone
                    studio_session = _persistent_session(
                        app,
                        raw_request,
                        normalized_session_model(request.model),
                        request.user,
                        request,
                        namespace=_studio_session_namespace(studio_agent_id, resolved_tone),
                    )
                    studio_session = _studio_session_for_context(
                        app, studio_session,
                        _studio_history_context_id(request.messages, before_turn=True),
                    )
                    call_record["tool_planning"] = planning_mode
            extra_headers = None
            shortfall_note = ""
            declined_note = ""
            tool_schemas: dict[str, dict | None] = {}
            if tools:
                # name -> declared JSON Schema, for validating what we parse back
                # out of prose. Doubles as the set of offered names.
                tool_schemas = {
                    t.function.name: t.function.parameters for t in tools if t.function
                }
                shortfall_note = no_tool_calls_note(
                    app,
                    model_str=request.model,
                    tone=resolved_tone,
                    choice=choice,
                    tool_count=len(tools),
                    read_only_guard=read_only_guard,
                    planning_mode=actual_planning,
                )
                # Both variants are computed up front because only the generator
                # learns whether the model declined, and by then app/tone are gone.
                declined_note = no_tool_calls_note(
                    app,
                    model_str=request.model,
                    tone=resolved_tone,
                    choice=choice,
                    tool_count=len(tools),
                    read_only_guard=read_only_guard,
                    declined=True,
                    planning_mode=actual_planning,
                )
            if tools:
                call_record["tool_calling"] = tool_status
            planner_predicate = (
                (lambda candidate: planner_fallback_needed(candidate, set(tool_schemas)))
                if planner_chain and not is_tool_result_continuation
                else None
            )
            translated = translate_openai_request(
                request,
                incremental=incremental,
                system_override=_system_override,
                consumer_tool_max_chars=(
                    settings.consumer_prompt_max_chars if is_consumer else None
                ),
            )
            call_record["usage_input_tokens"] = estimate_upstream_input_tokens(
                translated.prompt,
                translated.additional_context,
            )
            if studio_client is not None:
                studio_translated = translate_openai_request(
                    request,
                    incremental=(
                        studio_session is not None
                        and studio_session.turn_count > 0
                    ),
                    system_override=_system_override,
                )
                call_record["usage_input_tokens"] = estimate_upstream_input_tokens(
                    studio_translated.prompt,
                    studio_translated.additional_context,
                )
            media_rewriter = request_media_rewriter(app, raw_request)
            # Router mode: plan the turn with a dedicated classification prompt
            # instead of asking a tone that ignores the inline contract to embed a
            # fenced block mid-answer. The conversation handed to the router is a
            # NON-incremental view with the tool contract stripped -- the router
            # turn is a throwaway fresh conversation, so it has no server-side
            # history to build on, and it must not also be told to answer in the
            # native shape it is replacing.
            # Consumer included: its hard prompt ceiling is already enforced one
            # layer down (ConsumerClientAdapter.chat compacts anything over the
            # budget), so the router needs no size handling of its own.
            router_prompt = ""
            if (
                tools
                and router_applies(actual_planning, resolved_tone)
                and not is_tool_result_continuation
            ):
                full_view = translate_openai_request(
                    request.model_copy(update={"tools": None, "tool_choice": None})
                )
                router_prompt = build_router_prompt(
                    "\n\n".join([*full_view.additional_context, f"User: {full_view.prompt}"]),
                    tool_description_lines(tools),
                    choice,
                )
                if studio_client is None:
                    # ``auto`` also resolves to a real router turn for measured
                    # unsupported/flaky tones.  Record the path that actually ran,
                    # while a ready Studio turn keeps "studio" until/unless its
                    # buffered planner fallback changes the record later.
                    actual_planning = "router"
                    call_record["tool_planning"] = "router"
            if tool_status in {"unsupported", "flaky"}:
                # Logged after planning, so the operator sees whether the measured
                # shortfall was actually routed around this turn.
                _log.warning(
                    "  %s is measured %s the tool-calling contract "
                    "(%d tool(s) requested)%s", resolved_tone,
                    "NOT to honour" if tool_status == "unsupported" else "to honour only sometimes",
                    len(tools),
                    "; planning this turn with a router turn instead"
                    if router_prompt and actual_planning == "router"
                    else "",
                )

            def note_planning_stage(stage: str) -> None:
                nonlocal actual_planning, shortfall_note, declined_note
                actual_planning = stage
                call_record["tool_planning"] = stage
                source = studio_translated if stage == "studio" else translated
                if source is not None:
                    call_record["usage_input_tokens"] = estimate_upstream_input_tokens(
                        source.prompt, source.additional_context
                    )
                note_mode = "native" if stage == "inline" else stage
                if not tools:
                    return
                shortfall_note = no_tool_calls_note(
                    app,
                    model_str=request.model,
                    tone=resolved_tone,
                    choice=choice,
                    tool_count=len(tools),
                    read_only_guard=read_only_guard,
                    planning_mode=note_mode,
                )
                declined_note = no_tool_calls_note(
                    app,
                    model_str=request.model,
                    tone=resolved_tone,
                    choice=choice,
                    tool_count=len(tools),
                    read_only_guard=read_only_guard,
                    declined=True,
                    planning_mode=note_mode,
                )

            def note_studio_fallback(reason: str) -> None:
                call_record["studio_fallback"] = reason

            def note_router_fallback(reason: str) -> None:
                call_record["router_fallback"] = reason
            if request.stream:
                # Save call record for streaming (tool_calls_result resolved later)
                call_record["streaming"] = True
                append_call_log(app.state, call_record)
                if tools:
                    stream = _openai_stream_with_tools(
                        model_alias,
                        client,
                        translated.prompt,
                        translated.additional_context,
                        session,
                        call_log=app.state.call_log,
                        call_record=call_record,
                        on_record_update=lambda text: record_response_text(
                            app.state, call_record, text
                        ),
                        on_response_done=lambda assistant: record_auto_session_response(
                            app,
                            raw_request,
                            request,
                            (
                                studio_session
                                if call_record.get("tool_planning") == "studio"
                                else session
                            ),
                            assistant,
                        ),
                        tool_names={t.function.name for t in tools if t.function},
                        tool_schemas=tool_schemas,
                        read_only_guard=read_only_guard,
                        text_transform=media_rewriter,
                        images=translated.images,
                        shortfall_note=shortfall_note,
                        declined_note=declined_note,
                        router_shortfall_note=no_tool_calls_note(
                            app,
                            model_str=request.model,
                            tone=resolved_tone,
                            choice=choice,
                            tool_count=len(tools),
                            read_only_guard=read_only_guard,
                            planning_mode="router",
                        ),
                        router_declined_note=no_tool_calls_note(
                            app,
                            model_str=request.model,
                            tone=resolved_tone,
                            choice=choice,
                            tool_count=len(tools),
                            read_only_guard=read_only_guard,
                            declined=True,
                            planning_mode="router",
                        ),
                        inline_shortfall_note=no_tool_calls_note(
                            app,
                            model_str=request.model,
                            tone=resolved_tone,
                            choice=choice,
                            tool_count=len(tools),
                            read_only_guard=read_only_guard,
                            planning_mode="native",
                        ),
                        inline_declined_note=no_tool_calls_note(
                            app,
                            model_str=request.model,
                            tone=resolved_tone,
                            choice=choice,
                            tool_count=len(tools),
                            read_only_guard=read_only_guard,
                            declined=True,
                            planning_mode="native",
                        ),
                        router_prompt=router_prompt,
                        studio_turn=(
                            PlannerTurn(
                                studio_client,
                                studio_translated.prompt,
                                studio_translated.additional_context,
                                studio_session,
                                studio_translated.images,
                            )
                            if studio_client is not None
                            else None
                        ),
                        prefer_router=planning_mode == "router",
                        should_fallback=planner_predicate,
                        on_stage=note_planning_stage if planner_chain else None,
                        on_studio_fallback=note_studio_fallback,
                        on_router_fallback=note_router_fallback,
                        skip_router_fallback=is_tool_result_continuation,
                        allow_final_answer=allow_final_answer,
                    )
                    header_status = (
                        actual_planning
                        if planning_mode in {"studio", "router"}
                        else effective_tool_calling(resolved_tone, planning_mode)
                    )
                    extra_headers = {TOOL_CALLING_HEADER: header_status}
                    # When tools are present, buffer the full stream then parse tool_calls
                    return StreamingResponse(
                        keepalive_stream(stream),
                        media_type="text/event-stream",
                        headers=merge_sse_headers(extra_headers),
                    )
                return StreamingResponse(
                    keepalive_stream(
                        _openai_stream(
                            model_alias,
                            client,
                            translated.prompt,
                            translated.additional_context,
                            session,
                            on_text_done=lambda text: record_response_text(app.state, call_record, text),
                            text_transform=media_rewriter,
                            images=translated.images,
                            call_record=call_record,
                            on_response_done=lambda assistant: record_auto_session_response(
                                app, raw_request, request, session, assistant
                            ),
                        )
                    ),
                    media_type="text/event-stream",
                    headers=merge_sse_headers(),
                )
            # Keep the RAW model text for parsing; the media rewriter is applied
            # at delivery time below. Rewriting first base64-encodes the source
            # URL into a ?u= parameter, erasing the file extension that
            # _looks_like_fake_file_claim needs to detect a natively generated
            # file, so the corrective retry never fired. Deferring it also keeps
            # the rewriter away from a Write tool_call's file content.
            async def inline_answer() -> str:
                return await client.chat(
                    translated.prompt,
                    translated.additional_context,
                    session,
                    translated.images,
                )

            async def router_answer(fallback_turn) -> str:
                return await routed_or_answered(
                    client,
                    router_prompt,
                    translated.prompt,
                    translated.additional_context,
                    session,
                    translated.images,
                    should_fallback=planner_predicate,
                    fallback_turn=fallback_turn,
                    on_router_fallback=note_router_fallback,
                )

            text = await ordered_or_answered(
                studio_turn=(
                    PlannerTurn(
                        studio_client,
                        studio_translated.prompt,
                        studio_translated.additional_context,
                        studio_session,
                        studio_translated.images,
                    )
                    if studio_client is not None and studio_translated is not None
                    else None
                ),
                router_turn=router_answer,
                inline_turn=inline_answer,
                prefer_router=planning_mode == "router",
                should_fallback=planner_predicate,
                on_stage=note_planning_stage if planner_chain else None,
                on_studio_fallback=note_studio_fallback,
                skip_router_fallback=is_tool_result_continuation,
            )
            header_status = (
                call_record.get("tool_planning", actual_planning)
                if planning_mode in {"studio", "router"}
                else effective_tool_calling(resolved_tone, planning_mode)
            )
            extra_headers = {TOOL_CALLING_HEADER: header_status} if tools else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SubstrateCopilotError as exc:
            # The request reached the upstream attempt, so retain one failed
            # call in both diagnostics and the site-wide estimated usage total.
            # Persisting telemetry is best-effort and must not mask the actual
            # upstream error response.
            call_record["error"] = str(exc)
            record_response_text(app.state, call_record, "")
            append_call_log(app.state, call_record)
            raise upstream_http_error(exc) from exc

        # If request included tools, parse model output for tool_call blocks
        # The explicit no-action token is stripped before anything else reads the
        # text: it is protocol chatter, not part of the answer, and `declined`
        # decides below whether the fallbacks are even appropriate.
        declined = False
        if tools:
            text, declined = split_no_tool_marker(text)
        tool_calls = _extract_tool_calls(text) if tools else []
        if read_only_guard and tool_calls:
            blocked = len(tool_calls)
            tool_calls = _filter_read_only_tool_calls(tool_calls)
            if len(tool_calls) != blocked:
                _log.info("  read-only guard filtered mutating tool_call(s)")
        if not tool_calls and tools and not read_only_guard and not declined:
            # Prose fallback: model described "save as <path>" + code block
            tool_names = {t.function.name for t in tools if t.function}
            tool_calls = _extract_prose_write(text, tool_names)
            if tool_calls:
                _log.info("  prose fallback synthesized Write tool_call")
        # Corrective retry: M365 sometimes "creates" a file via its native
        # attachment feature (hosted URL) instead of a tool_call. If it claims a
        # file but emitted none, force one retry demanding a real tool_call.
        # A model that explicitly declined is exempt: it did answer the contract,
        # so re-asking would only bully it into a call the user never wanted.
        if not tool_calls and tools and not read_only_guard and not declined and not allow_final_answer and _looks_like_fake_file_claim(text):
            _log.info("  fake file claim detected, forcing corrective retry")
            try:
                retry_uses_studio = (
                    studio_client is not None and actual_planning == "studio"
                )
                retry_text = await (
                    studio_client if retry_uses_studio else client
                ).chat(
                    _RETRY_INSTRUCTION,
                    (
                        studio_translated.additional_context
                        if retry_uses_studio
                        else translated.additional_context
                    ),
                    studio_session if retry_uses_studio else session,
                )
                retry_calls = _extract_tool_calls(retry_text)
                if not retry_calls:
                    tool_names = {t.function.name for t in tools if t.function}
                    retry_calls = _extract_prose_write(retry_text, tool_names)
                if retry_calls:
                    _log.info("  retry produced %d tool_call(s)", len(retry_calls))
                    text, tool_calls = retry_text, retry_calls
                    call_record["retried"] = True
            except SubstrateCopilotError:
                pass  # Keep original response if retry fails
        # Last: only calls we would otherwise deliver are worth judging, and a
        # synthesized Write has to clear the client's schema like any other call.
        rejected: list[str] = []
        if tool_calls:
            tool_calls, rejected = _filter_schema_valid_tool_calls(tool_calls, tool_schemas)
            if rejected:
                _log.warning("  dropped unusable tool_call(s): %s", "; ".join(rejected))
        _log.info("[/v1/chat/completions] response len=%d tool_calls=%d", len(text), len(tool_calls))
        if tool_calls:
            _log.info("  parsed tool_calls: %s", [tc["function"]["name"] for tc in tool_calls])
        # Save call record
        call_record["response_len"] = len(text)
        call_record["usage_output_tokens"] = estimate_text_tokens(text)
        call_record["response_text"] = text[:8000]
        call_record["response_repr"] = repr(text[:2000])
        call_record["tool_calls_result"] = [tc["function"]["name"] for tc in tool_calls] if tool_calls else []
        if declined:
            call_record["tool_declined"] = True
        if rejected:
            call_record["tool_calls_rejected"] = rejected
        append_call_log(app.state, call_record)
        # The client demanded a call that never came: a 200 with prose would be the
        # silent failure this whole path exists to remove.
        required_error = required_tool_call_error(
            app,
            model_str=request.model,
            tone=resolved_tone,
            choice=choice,
            tool_calls=tool_calls,
            read_only_guard=read_only_guard,
            declined=declined,
            rejected=rejected,
            planning_mode=actual_planning,
        )
        if required_error:
            error_headers = dict(extra_headers or {})
            if tools and choice[0] == "required":
                error_headers[TOOL_OUTCOME_HEADER] = (
                    REQUIRED_REJECTED_CALL_OUTCOME
                    if rejected
                    else REQUIRED_NO_CALL_OUTCOME
                )
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": required_error,
                        "type": "invalid_request_error",
                    }
                },
                headers=error_headers,
            )
        if tool_calls:
            remaining = media_rewriter(_strip_tool_call_blocks(text))
            msg = {"role": "assistant", "content": remaining or None, "tool_calls": tool_calls}
            record_auto_session_response(
                app, raw_request, request,
                studio_session if actual_planning == "studio" else session,
                msg,
            )
            return JSONResponse({
                "id": f"chatcmpl_{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model_alias,
                "choices": [
                    {
                        "index": 0,
                        "message": msg,
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": openai_usage(call_record.get("usage")),
            }, headers=extra_headers)

        # Tools were offered but no call is being delivered: say why -- a dropped
        # call, a deliberate decline, or a tone that ignores the contract. Appended,
        # never substituted: the model's own answer is still the response.
        delivered = prose_with_reason(
            media_rewriter(text),
            shortfall_note="" if allow_final_answer and text.strip() else shortfall_note,
            declined_note=declined_note,
            declined=declined,
            rejected=rejected,
        )
        record_auto_session_response(
            app,
            raw_request,
            request,
            studio_session if actual_planning == "studio" else session,
            {"role": "assistant", "content": delivered},
        )
        return JSONResponse({
            "id": f"chatcmpl_{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_alias,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": delivered},
                    "finish_reason": "stop",
                }
            ],
            "usage": openai_usage(call_record.get("usage")),
        }, headers=extra_headers)


async def _openai_stream_with_tools(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
    call_log: list | None = None,
    call_record: dict | None = None,
    on_record_update: Callable[[str], None] | None = None,
    on_response_done: Callable[[dict], None] | None = None,
    tool_names: set | None = None,
    tool_schemas: dict | None = None,
    read_only_guard: bool = False,
    text_transform: Callable[[str], str] | None = None,
    images: list | None = None,
    shortfall_note: str = "",
    declined_note: str = "",
    router_shortfall_note: str = "",
    router_declined_note: str = "",
    inline_shortfall_note: str = "",
    inline_declined_note: str = "",
    router_prompt: str = "",
    studio_turn: PlannerTurn | None = None,
    prefer_router: bool = False,
    should_fallback: Callable[[str], bool] | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_studio_fallback: Callable[[str], None] | None = None,
    on_router_fallback: Callable[[str], None] | None = None,
    skip_router_fallback: bool = False,
    allow_final_answer: bool = False,
) -> AsyncIterator[str]:
    """Buffer full stream, then emit as tool_calls if found, else normal content stream.

    ``shortfall_note``/``declined_note`` are appended to the delivered text only
    when the turn ends up with no tool_calls: headers are long gone by then, so the
    reason a tools-bearing request produced prose has to travel as readable
    content. Which one applies depends on the turn itself, so the caller precomputes
    both and this generator picks.
    """
    _log = logging.getLogger("copilot_proxy")
    chunks: list[str] = []
    try:
        async def inline_stream() -> AsyncIterator[str]:
            async for delta in client.chat_stream(
                prompt, additional_context, session, images
            ):
                yield delta

        async def router_stream(fallback_turn) -> AsyncIterator[str]:
            async for delta in routed_or_streamed(
                client,
                router_prompt,
                prompt,
                additional_context,
                session,
                images,
                should_fallback=should_fallback,
                fallback_turn=fallback_turn,
                on_router_fallback=on_router_fallback,
            ):
                yield delta

        def note_stage(stage: str) -> None:
            nonlocal shortfall_note, declined_note
            if stage == "router":
                shortfall_note = router_shortfall_note
                declined_note = router_declined_note
            elif stage == "inline":
                shortfall_note = inline_shortfall_note
                declined_note = inline_declined_note
            if on_stage is not None:
                on_stage(stage)

        stream = ordered_or_streamed(
            studio_turn=studio_turn,
            router_turn=router_stream,
            inline_turn=inline_stream,
            prefer_router=prefer_router,
            should_fallback=should_fallback,
            on_stage=note_stage if on_stage is not None else None,
            on_studio_fallback=on_studio_fallback,
            skip_router_fallback=skip_router_fallback,
        )
        async for delta in stream:
            chunks.append(delta)
    except SubstrateCopilotError as exc:
        # Deliver the upstream failure as readable assistant text rather than a
        # bare {"error": ...} frame (strict OpenAI clients render an error-only
        # chunk as "null: [object Object]"). Any buffered text captured before the
        # failure is preserved ahead of the error note.
        error_text = f"⚠️ 上游错误：{exc}"
        _log.warning("stream_with_tools upstream error: %s", exc)
        buffered = "".join(chunks)
        if text_transform is not None:
            buffered = text_transform(buffered)
        sep = "\n\n" if buffered else ""
        delivered = buffered + sep + error_text
        if call_record is not None:
            call_record["response_len"] = len(delivered)
            call_record["response_text"] = delivered[:8000]
            call_record["response_repr"] = repr(delivered[:2000])
            call_record["error"] = str(exc)
        if on_record_update is not None:
            on_record_update(delivered)
        err_id = f"chatcmpl_{uuid.uuid4().hex}"
        err_created = int(time.time())
        yield f"data: {json.dumps({'id': err_id, 'object': 'chat.completion.chunk', 'created': err_created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        error_chunk = {
            "id": err_id,
            "object": "chat.completion.chunk",
            "created": err_created,
            "model": model_alias,
            "choices": [{"index": 0, "delta": {"content": delivered}, "finish_reason": None}],
        }
        if isinstance(exc, SubstrateThrottled):
            error_chunk["m365_error"] = {
                "type": "rate_limit_error",
                "message": str(exc),
            }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield f"data: {json.dumps({'id': err_id, 'object': 'chat.completion.chunk', 'created': err_created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': openai_usage(usage_for_record(call_record))})}\n\n"
        yield "data: [DONE]\n\n"
        return
    # Raw text for parsing; the media rewriter runs at delivery time below. This
    # path previously never applied it at all, so media links reached streaming
    # clients unrewritten.
    full_text = "".join(chunks)

    full_text, declined = split_no_tool_marker(full_text)
    tool_calls = _extract_tool_calls(full_text)
    if read_only_guard and tool_calls:
        blocked = len(tool_calls)
        tool_calls = _filter_read_only_tool_calls(tool_calls)
        if len(tool_calls) != blocked:
            _log.info("  read-only guard filtered mutating tool_call(s)")
    if not tool_calls and tool_names and not read_only_guard and not declined:
        # Prose fallback: model described "save as <path>" + code block
        tool_calls = _extract_prose_write(full_text, tool_names)
        if tool_calls:
            _log.info("  prose fallback synthesized Write tool_call")
    # Corrective retry: M365 native file-gen (hosted URL) instead of a tool_call.
    # Skipped when the model explicitly declined -- it answered the contract.
    if not tool_calls and tool_names and not read_only_guard and not declined and not allow_final_answer and _looks_like_fake_file_claim(full_text):
        _log.info("  fake file claim detected, forcing corrective retry")
        try:
            retry_chunks: list[str] = []
            retry_uses_studio = (
                studio_turn is not None
                and (
                    call_record is None
                    or call_record.get("tool_planning") == "studio"
                )
            )
            retry_client = studio_turn.client if retry_uses_studio else client
            retry_context = (
                studio_turn.additional_context
                if retry_uses_studio
                else additional_context
            )
            retry_session = studio_turn.session if retry_uses_studio else session
            async for delta in retry_client.chat_stream(
                _RETRY_INSTRUCTION, retry_context, retry_session
            ):
                retry_chunks.append(delta)
            retry_text = "".join(retry_chunks)
            retry_calls = _extract_tool_calls(retry_text)
            if not retry_calls:
                retry_calls = _extract_prose_write(retry_text, tool_names)
            if retry_calls:
                _log.info("  retry produced %d tool_call(s)", len(retry_calls))
                full_text, tool_calls = retry_text, retry_calls
                if call_record is not None:
                    call_record["retried"] = True
        except SubstrateCopilotError:
            pass  # Keep original response if retry fails
    rejected: list[str] = []
    if tool_calls and tool_schemas is not None:
        tool_calls, rejected = _filter_schema_valid_tool_calls(tool_calls, tool_schemas)
        if rejected:
            _log.warning("  dropped unusable tool_call(s): %s", "; ".join(rejected))
    _log.info("[stream_with_tools] full_text len=%d tool_calls=%d", len(full_text), len(tool_calls))
    if tool_calls:
        _log.info("  parsed tool_calls: %s", [tc["function"]["name"] for tc in tool_calls])
    # Update call record with results
    if call_record is not None:
        call_record["response_len"] = len(full_text)
        call_record["response_text"] = full_text[:8000]
        call_record["response_repr"] = repr(full_text[:2000])
        call_record["tool_calls_result"] = [tc["function"]["name"] for tc in tool_calls] if tool_calls else []
        if declined:
            call_record["tool_declined"] = True
        if rejected:
            call_record["tool_calls_rejected"] = rejected
    if on_record_update is not None:
        on_record_update(full_text)
    completion_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = int(time.time())

    if tool_calls:
        remaining = _strip_tool_call_blocks(full_text)
        if text_transform is not None:
            remaining = text_transform(remaining)
        if on_response_done is not None:
            on_response_done(
                {
                    "role": "assistant",
                    "content": remaining or None,
                    "tool_calls": tool_calls,
                }
            )
        # Emit role chunk
        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        # Emit remaining text content if any
        if remaining:
            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {'content': remaining}, 'finish_reason': None}]})}\n\n"
        # Emit tool_calls chunks — one per tool call
        for i, tc in enumerate(tool_calls):
            delta_tc = [{"index": i, "id": tc["id"], "type": "function", "function": {"name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}}]
            yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {'tool_calls': delta_tc}, 'finish_reason': None}]})}\n\n"
        # Final chunk with finish_reason
        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}], 'usage': openai_usage(usage_for_record(call_record))})}\n\n"
        yield "data: [DONE]\n\n"
    else:
        # No tool calls found — re-stream as normal content
        delivered = prose_with_reason(
            text_transform(full_text) if text_transform is not None else full_text,
            shortfall_note="" if allow_final_answer and full_text.strip() else shortfall_note,
            declined_note=declined_note,
            declined=declined,
            rejected=rejected,
        )
        if on_response_done is not None:
            on_response_done({"role": "assistant", "content": delivered})
        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {'content': delivered}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_alias, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}], 'usage': openai_usage(usage_for_record(call_record))})}\n\n"
        yield "data: [DONE]\n\n"
