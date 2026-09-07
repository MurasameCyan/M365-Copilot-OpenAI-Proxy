from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .call_log_store import append_call_log, record_response_text
from .config import Settings
from .models import OpenAIResponsesRequest
from .response_helpers import (
    _REQUIRED_TOOL_CHOICE_ERROR,
    _resolve_responses_tool_calls,
    _responses_function_call_items,
    _responses_message_item,
    _responses_object,
    _responses_required_tool_retry_prompt,
    _responses_stream,
    _responses_stream_with_tools,
)
from .routes_api_common import (
    TOOL_CALLING_HEADER,
    apply_request_model,
    effective_run_permission,
    effective_tool_planning_mode,
    request_model_alias,
    upstream_http_error,
)
from .routes_media_proxy import request_media_rewriter
from .session_helpers import (
    _decode_responses_response_claims,
    _decode_responses_session_id,
    _encode_responses_session_id,
    _namespaced_session,
    _persistent_session,
    _responses_session_key,
    _responses_store_key,
    _responses_store_key_belongs_to_request,
    _studio_session_namespace,
    _studio_session_for_context,
)
from .substrate_client import SubstrateCopilotClient, SubstrateCopilotError
from .sse_stream import keepalive_stream, merge_sse_headers
from .studio_planner import (
    PlannerTurn,
    ordered_or_answered,
)
from .studio_agent_discovery import ensure_studio_client_snapshot
from .tone_resolver import normalized_session_model
from .tool_call_parser import (
    _RETRY_INSTRUCTION,
    _has_read_only_intent,
    _looks_like_fake_file_claim,
    planner_fallback_needed,
    _strip_tool_call_blocks,
    split_no_tool_marker,
)
from .translator import (
    _responses_content_text,
    _responses_last_action_index,
    responses_tool_config,
    tool_description_lines,
    translate_responses_request,
)
from .tool_router import build_router_prompt, routed_or_answered
from .usage_store import estimate_upstream_input_tokens


def _router_context_from_view(view, parent: dict | None = None) -> dict:
    """Private, encrypted context needed to close a Router-planned tool loop."""
    prompt = str(view.prompt or "")
    additional_context = [
        str(item) for item in view.additional_context if isinstance(item, str)
    ]
    if not isinstance(parent, dict):
        return {"prompt": prompt, "additional_context": additional_context}
    parent_prompt = str(parent.get("prompt") or "")
    parent_context = parent.get("additional_context")
    merged = [
        str(item)
        for item in (parent_context if isinstance(parent_context, list) else [])
        if isinstance(item, str)
    ]
    if parent_prompt:
        merged.append(f"Previous task or continuation:\n{parent_prompt}")
    merged.extend(additional_context)
    return {"prompt": prompt, "additional_context": merged}


def _router_context_conversation(context: dict) -> str:
    prompt = str(context.get("prompt") or "")
    additional_context = context.get("additional_context")
    parts = [
        str(item)
        for item in (additional_context if isinstance(additional_context, list) else [])
        if isinstance(item, str) and item
    ]
    if prompt:
        parts.append(f"User: {prompt}")
    return "\n\n".join(parts)


def _restore_router_answer_context(translated, parent: dict):
    """Give the answer turn the original request as well as the new tool output."""
    restored = _router_context_conversation(parent)
    if not restored:
        return translated
    return translated.model_copy(
        update={
            "additional_context": [
                f"Original routed task context:\n{restored}",
                *translated.additional_context,
            ]
        }
    )


class _ResponsesStreamingResponse(StreamingResponse):
    """Own the request lock until the SSE transport has fully terminated."""

    def __init__(
        self,
        stream: AsyncIterator[str],
        *,
        on_request_done: Callable[[bool], None],
        response_lock=None,
        **kwargs,
    ) -> None:
        super().__init__(stream, **kwargs)
        self._on_request_done = on_request_done
        self._response_lock = response_lock

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    await close()
            finally:
                try:
                    self._on_request_done(False)
                finally:
                    if self._response_lock is not None:
                        self._response_lock.release()


def register_responses_routes(
    app: FastAPI,
    get_settings: Callable[[], Settings],
    get_copilot_client: Callable[[Request], SubstrateCopilotClient],
) -> None:
    @app.post("/v1/responses")
    async def openai_responses(
        raw: Request,
        settings: Settings = Depends(get_settings),
    ):
        log = logging.getLogger("copilot_proxy")
        model_alias = request_model_alias(app, raw, settings)
        try:
            body = await raw.json()
            request = OpenAIResponsesRequest.model_validate(body)
            choice, tools = responses_tool_config(
                request.tools,
                request.tool_choice,
                request.parallel_tool_calls,
            )
            tool_names = {
                tool.function.name
                for tool in tools
                if tool.function is not None
            }
            tool_namespaces = {
                tool.function.name: tool.function.namespace
                for tool in tools
                if tool.function is not None
                and isinstance(getattr(tool.function, "namespace", None), str)
                and tool.function.namespace.strip()
            }
            strict_tool_schemas = {
                tool.function.name: tool.function.parameters or {"type": "object"}
                for tool in tools
                if tool.function is not None
                and getattr(tool.function, "strict", False) is True
            }
            # Apply the provider-specific upstream selector: M365 tone or
            # Consumer mode. Session suffix normalization remains M365-specific.
            client, resolved_tone, is_consumer = apply_request_model(
                app, raw, get_copilot_client, request.model
            )
            key_obj = getattr(raw.state, "api_key_obj", None)
            key_system_prompt = (
                (key_obj.system_prompt if key_obj is not None else "") or ""
            ).strip()
            system_override = key_system_prompt or getattr(
                app.state, "system_prompt", ""
            )
            run_permission = effective_run_permission(app, key_obj)
            user_texts = (
                [request.input]
                if isinstance(request.input, str)
                else [
                    _responses_content_text(item.get("content"))
                    for item in request.input
                    if isinstance(item, dict) and item.get("role") == "user"
                ]
            )
            latest_user_text = user_texts[-1] if user_texts else ""
            read_only_guard = (
                run_permission == "read_only"
                or _has_read_only_intent(latest_user_text)
            )
            previous_session_key = _decode_responses_session_id(
                request.previous_response_id,
                app.state.media_proxy_secret,
            )
            previous_session = (
                app.state.session_store.get_existing(previous_session_key)
                if (
                    not is_consumer
                    and previous_session_key is not None
                    and _responses_store_key_belongs_to_request(
                        raw, previous_session_key
                    )
                )
                else None
            )
            response_claims = (
                _decode_responses_response_claims(
                    request.previous_response_id,
                    app.state.media_proxy_secret,
                )
                if not is_consumer
                else None
            )
            if not is_consumer and request.previous_response_id is not None:
                if (
                    previous_session is None
                    or response_claims is None
                    or response_claims[0] != previous_session_key
                ):
                    raise ValueError(
                        "Invalid or expired Responses previous_response_id."
                    )
            incremental_output_ids = {
                str(item.get("call_id") or "").strip()
                for item in request.input
                if isinstance(request.input, list)
                and isinstance(item, dict)
                and item.get("type") == "function_call_output"
            }
            incremental_output_ids.discard("")
            last_action_index = _responses_last_action_index(request.input)
            is_tool_output_continuation = (
                last_action_index is not None
                and isinstance(request.input[last_action_index], dict)
                and request.input[last_action_index].get("type")
                == "function_call_output"
            )
            translated = translate_responses_request(
                request,
                system_override=system_override,
                consumer_tool_max_chars=(
                    settings.consumer_prompt_max_chars if is_consumer else None
                ),
                allow_unmatched_function_call_outputs=(
                    previous_session is not None
                ),
            )
            planning_mode = effective_tool_planning_mode(app, key_obj)
            actual_planning = planning_mode
            studio_client = None
            studio_session = None
            studio_translated = None
            studio_snapshot = None
            studio_fallback = ""
            router_prompt = ""
            router_response_context: dict | None = None
            parent_router_context: dict | None = None
            if tool_names and planning_mode in {"studio", "router"}:
                account = getattr(raw.state, "account", None)
                if is_consumer:
                    actual_planning = "router"
                    studio_fallback = "unsupported_provider"
                else:
                    studio_snapshot = await ensure_studio_client_snapshot(app, account)
                if not is_consumer and studio_snapshot is None:
                    actual_planning = "router"
                    studio_fallback = "not_ready"
                elif not is_consumer:
                    studio_token, studio_agent_id = studio_snapshot
                    studio_client = get_copilot_client(
                        raw,
                        studio_agent_id=studio_agent_id,
                        token_override=studio_token,
                    )
                    studio_client._tone = resolved_tone
                    actual_planning = planning_mode

            if tool_names and (
                actual_planning == "router" or studio_client is not None
            ):
                full_view = translate_responses_request(
                    request.model_copy(update={"tools": None, "tool_choice": None}),
                    system_override=system_override,
                    consumer_tool_max_chars=(
                        settings.consumer_prompt_max_chars if is_consumer else None
                    ),
                    allow_unmatched_function_call_outputs=(
                        previous_session is not None
                    ),
                )
                parent_router_context = (
                    previous_session.response_context(
                        request.previous_response_id or ""
                    )
                    if previous_session is not None
                    else None
                )
                if parent_router_context is not None:
                    translated = _restore_router_answer_context(
                        translated, parent_router_context
                    )
                router_response_context = _router_context_from_view(
                    full_view, parent_router_context
                )
                if not is_tool_output_continuation:
                    router_prompt = build_router_prompt(
                        _router_context_conversation(router_response_context),
                        tool_description_lines(tools),
                        choice,
                    )
            required_tool_retry_prompt = _responses_required_tool_retry_prompt(
                choice,
                tool_names,
                translated.prompt,
            )
            # A function_call_output is the result of a tool the host already
            # executed. The next Studio turn is the answer/next-action turn; an
            # ordinary final answer must not be mistaken for a failed first
            # planner and sent through Router again. Initial tool-bearing turns
            # still use the normal planner fallback predicate.
            planner_predicate = (
                (lambda candidate: planner_fallback_needed(candidate, set(tool_names)))
                if tool_names and not is_tool_output_continuation
                else None
            )
            if (
                previous_session is None
                and read_only_guard
                and required_tool_retry_prompt
                and not any(
                    name.lower()
                    in {"read", "grep", "glob", "ls", "searchcodebase"}
                    for name in tool_names
                )
            ):
                raise ValueError(
                    "Required tool_choice conflicts with read-only permission."
                )
            media_rewriter = request_media_rewriter(app, raw)
            session_key = None
            session = None
            if not is_consumer:
                if previous_session is not None:
                    session = previous_session
                    session_key = previous_session_key
                else:
                    fallback_key = _responses_session_key(request)
                    if (
                        request.previous_response_id is None
                        and not (raw.headers.get("x-m365-session-id") or "").strip()
                        and not normalized_session_model(request.model).endswith(":persist")
                    ):
                        fallback_key = f"responses_{uuid.uuid4().hex}"
                    session = _persistent_session(
                        app,
                        raw,
                        normalized_session_model(request.model),
                        fallback_key,
                    )
                    session_key = _responses_store_key(app, session)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        continuation_id = (
            request.previous_response_id
            if not is_consumer and previous_session is not None
            else None
        )
        resp_id = (
            _encode_responses_session_id(
                session_key,
                app.state.media_proxy_secret,
            )
            if session_key
            else f"resp_{uuid.uuid4().hex}"
        )
        pending_response_calls: list[str] | None = None
        pending_response_context: dict | None = None
        continuation_reservation: str | None = None
        router_classified_call = False

        def mark_router_classified_call() -> None:
            nonlocal router_classified_call
            router_classified_call = True
        response_lock = session.response_lock if session is not None else None
        lock_owned = False

        try:
            if response_lock is not None:
                await response_lock.acquire()
                lock_owned = True

            if previous_session is not None:
                if previous_session.latest_response_id != request.previous_response_id:
                    if request.previous_response_id in previous_session.consumed_response_ids:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                "Responses previous_response_id has already been "
                                "submitted."
                            ),
                        )
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "M365 previous_response_id must reference the latest "
                            "response."
                        ),
                    )
                if not previous_session.allows_response_outputs(
                    request.previous_response_id or "",
                    incremental_output_ids,
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Responses function_call_output does not match the issued "
                            "previous_response_id; all issued outputs must be "
                            "submitted together."
                        ),
                    )
                if is_tool_output_continuation:
                    read_only_guard = (
                        read_only_guard
                        or previous_session.response_is_read_only(
                            request.previous_response_id or ""
                        )
                    )

            if read_only_guard and required_tool_retry_prompt and not any(
                name.lower() in {"read", "grep", "glob", "ls", "searchcodebase"}
                for name in tool_names
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Required tool_choice conflicts with read-only permission.",
                )

            if continuation_id is not None:
                continuation_reservation = (
                    previous_session.begin_response_continuation(continuation_id)
                )
                if continuation_reservation is None:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "Responses previous_response_id has already been submitted."
                        ),
                    )

            if studio_client is not None:
                studio_session = _namespaced_session(
                    app,
                    raw,
                    session,
                    _studio_session_namespace(studio_agent_id, resolved_tone),
                )
                studio_session = _studio_session_for_context(
                    app, studio_session, continuation_id,
                )
                try:
                    studio_translated = translate_responses_request(
                        request,
                        incremental=(
                            studio_session is not None and studio_session.turn_count > 0
                        ),
                        system_override=system_override,
                        consumer_tool_max_chars=(
                            settings.consumer_prompt_max_chars if is_consumer else None
                        ),
                        allow_unmatched_function_call_outputs=(
                            previous_session is not None
                        ),
                    )
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
                if (
                    studio_session is not None
                    and studio_session.turn_count == 0
                    and parent_router_context is not None
                ):
                    # A changed tone or namespace starts without upstream history;
                    # previous_response_id may carry only the new tool output.
                    studio_translated = _restore_router_answer_context(
                        studio_translated, parent_router_context
                    )

            def record_issued_response(response_id: str, call_ids: list[str]) -> None:
                nonlocal pending_response_calls, pending_response_context
                if session is None:
                    return
                if studio_session is not None and actual_planning == "studio":
                    studio_session.record_studio_context(response_id)
                response_context = (
                    router_response_context
                    if isinstance(router_response_context, dict)
                    and (
                        router_classified_call
                        or studio_client is not None
                        or is_tool_output_continuation
                    )
                    else None
                )
                if continuation_id is None:
                    session.record_response(
                        response_id,
                        call_ids,
                        read_only_guard,
                        response_context=response_context,
                    )
                    return
                pending_response_calls = list(call_ids)
                pending_response_context = response_context

            def finish_continuation(success: bool) -> None:
                if continuation_id is None or continuation_reservation is None:
                    return
                if success and pending_response_calls is not None:
                    previous_session.complete_response_continuation(
                        continuation_id,
                        continuation_reservation,
                        resp_id,
                        pending_response_calls,
                        read_only_guard,
                        child_response_context=pending_response_context,
                    )
                    return
                previous_session.finish_response_continuation(
                    continuation_id,
                    continuation_reservation,
                    False,
                )

            call_record = {
                "api": "responses",
                "endpoint": "/v1/responses",
                "time": time.strftime("%H:%M:%S"),
                "ts": time.time(),
                "stream": request.stream,
                "tools": sorted(tool_names),
                "tool_choice": choice[0],
                "parallel_tool_calls": choice[2],
                "messages": len(request.input) if isinstance(request.input, list) else 1,
                "model": request.model,
                "tone": resolved_tone,
                "run_permission": run_permission,
                "read_only_guard": read_only_guard,
                "tool_calls_result": None if request.stream else [],
                "usage_input_tokens": estimate_upstream_input_tokens(
                    (studio_translated or translated).prompt,
                    (studio_translated or translated).additional_context,
                ),
            }
            if studio_client is not None:
                call_record["tool_planning"] = planning_mode
            elif router_prompt:
                call_record["tool_planning"] = "router"
            if studio_fallback:
                call_record["studio_fallback"] = studio_fallback

            def note_planning_stage(stage: str) -> None:
                nonlocal actual_planning
                actual_planning = stage
                call_record["tool_planning"] = stage

            def note_studio_fallback(reason: str) -> None:
                call_record["studio_fallback"] = reason

            def note_router_fallback(reason: str) -> None:
                call_record["router_fallback"] = reason

            # Echo the canonical Responses value. Input validation accepts harmless
            # case/whitespace variations, but the SDK only accepts the lower-case
            # enum (or the named-function object).
            response_tool_choice = (
                {"type": "function", "name": choice[1]}
                if choice[0] == "tool"
                else choice[0]
            )
            response_tools = request.tools or []

            if request.stream:
                call_record["streaming"] = True
                append_call_log(app.state, call_record)
                if tool_names:
                    stream = _responses_stream_with_tools(
                        model_alias,
                        client,
                        translated.prompt,
                        translated.additional_context,
                        session,
                        on_text_done=lambda text: record_response_text(
                            app.state, call_record, text
                        ),
                        text_transform=media_rewriter,
                        response_id=resp_id,
                        images=translated.images,
                        response_tools=response_tools,
                        tool_choice=response_tool_choice,
                        parallel_tool_calls=choice[2],
                        previous_response_id=request.previous_response_id,
                        instructions=request.instructions,
                        tool_names=tool_names,
                        strict_tool_schemas=strict_tool_schemas,
                        tool_namespaces=tool_namespaces,
                        read_only_guard=read_only_guard,
                        call_record=call_record,
                        required_tool_retry_prompt=required_tool_retry_prompt,
                        on_response_issued=record_issued_response,
                        on_request_done=finish_continuation,
                        router_prompt=router_prompt,
                        on_router_call=mark_router_classified_call,
                        studio_turn=(
                            PlannerTurn(
                                studio_client,
                                (studio_translated or translated).prompt,
                                (studio_translated or translated).additional_context,
                                studio_session,
                                (studio_translated or translated).images,
                            )
                            if studio_client is not None
                            else None
                        ),
                        prefer_router=planning_mode == "router",
                        should_fallback=planner_predicate,
                        on_stage=note_planning_stage,
                        on_studio_fallback=note_studio_fallback,
                        on_router_fallback=note_router_fallback,
                        skip_router_fallback=is_tool_output_continuation,
                    )
                else:
                    stream = _responses_stream(
                        model_alias,
                        client,
                        translated.prompt,
                        translated.additional_context,
                        session,
                        on_text_done=lambda text: record_response_text(
                            app.state, call_record, text
                        ),
                        text_transform=media_rewriter,
                        response_id=resp_id,
                        images=translated.images,
                        response_tools=response_tools,
                        tool_choice=response_tool_choice,
                        parallel_tool_calls=choice[2],
                        previous_response_id=request.previous_response_id,
                        instructions=request.instructions,
                        call_record=call_record,
                        on_response_issued=record_issued_response,
                        on_request_done=finish_continuation,
                    )
                stream = keepalive_stream(stream)
                response = _ResponsesStreamingResponse(
                    stream,
                    on_request_done=finish_continuation,
                    response_lock=response_lock,
                    media_type="text/event-stream",
                    headers=merge_sse_headers(
                        {
                            TOOL_CALLING_HEADER: actual_planning,
                        }
                        if tool_names and planning_mode in {"studio", "router"}
                        else None
                    ),
                )
                lock_owned = False
                return response

            response = await _complete_nonstream_response(
                app=app,
                client=client,
                translated=translated,
                session=session,
                model_alias=model_alias,
                resp_id=resp_id,
                request=request,
                response_tools=response_tools,
                response_tool_choice=response_tool_choice,
                tool_names=tool_names,
                strict_tool_schemas=strict_tool_schemas,
                tool_namespaces=tool_namespaces,
                read_only_guard=read_only_guard,
                choice=choice,
                required_tool_retry_prompt=required_tool_retry_prompt,
                media_rewriter=media_rewriter,
                call_record=call_record,
                record_issued_response=record_issued_response,
                finish_continuation=finish_continuation,
                log=log,
                router_prompt=router_prompt,
                on_router_call=mark_router_classified_call,
                studio_turn=(
                    PlannerTurn(
                        studio_client,
                        (studio_translated or translated).prompt,
                        (studio_translated or translated).additional_context,
                        studio_session,
                        (studio_translated or translated).images,
                    )
                    if studio_client is not None
                    else None
                ),
                prefer_router=planning_mode == "router",
                should_fallback=planner_predicate,
                on_stage=note_planning_stage,
                on_studio_fallback=note_studio_fallback,
                on_router_fallback=note_router_fallback,
                skip_router_fallback=is_tool_output_continuation,
            )
            if tool_names and planning_mode in {"studio", "router"}:
                response.headers[TOOL_CALLING_HEADER] = call_record.get(
                    "tool_planning", actual_planning
                )
            return response
        finally:
            if lock_owned:
                if continuation_id is not None and continuation_reservation is not None:
                    previous_session.finish_response_continuation(
                        continuation_id,
                        continuation_reservation,
                        False,
                    )
                response_lock.release()


async def _complete_nonstream_response(
    *,
    app,
    client,
    translated,
    session,
    model_alias,
    resp_id,
    request,
    response_tools,
    response_tool_choice,
    tool_names,
    strict_tool_schemas,
    tool_namespaces,
    read_only_guard,
    choice,
    required_tool_retry_prompt,
    media_rewriter,
    call_record,
    record_issued_response,
    finish_continuation,
    log,
    router_prompt,
    on_router_call,
    studio_turn,
    prefer_router,
    should_fallback,
    on_stage,
    on_studio_fallback,
    on_router_fallback,
    skip_router_fallback,
):
    router_decided = False

    def note_router_decision() -> None:
        nonlocal router_decided
        router_decided = True

    try:
        async def inline_answer() -> str:
            return await client.chat(
                translated.prompt,
                translated.additional_context,
                session,
                translated.images,
            )

        async def router_answer(fallback_turn) -> str:
            call_record["tool_planning"] = "router"
            return await routed_or_answered(
                client,
                router_prompt,
                translated.prompt,
                translated.additional_context,
                session,
                translated.images,
                on_router_call=note_router_decision,
                should_fallback=should_fallback,
                fallback_turn=fallback_turn,
                on_router_fallback=on_router_fallback,
            )

        raw_text = await ordered_or_answered(
            studio_turn=studio_turn,
            router_turn=router_answer,
            inline_turn=inline_answer,
            prefer_router=prefer_router,
            should_fallback=should_fallback,
            on_stage=on_stage,
            on_studio_fallback=on_studio_fallback,
            skip_router_fallback=skip_router_fallback,
        )
    except SubstrateCopilotError as exc:
        finish_continuation(False)
        call_record["error"] = str(exc)
        call_record["tool_calls_result"] = []
        record_response_text(app.state, call_record, "")
        append_call_log(app.state, call_record)
        raise upstream_http_error(exc) from exc

    raw_text, declined = split_no_tool_marker(raw_text)
    tool_calls = _resolve_responses_tool_calls(
        raw_text,
        tool_names,
        read_only_guard,
        choice[2],
        strict_tool_schemas,
        tool_namespaces,
        declined,
    )
    if router_decided and tool_calls:
        on_router_call()
    if not tool_calls and required_tool_retry_prompt:
        try:
            retry_client = (
                studio_turn.client
                if studio_turn is not None
                and call_record.get("tool_planning") == "studio"
                else client
            )
            retry_context = (
                studio_turn.additional_context
                if retry_client is not client
                else translated.additional_context
            )
            retry_session = (
                studio_turn.session if retry_client is not client else session
            )
            raw_text = await retry_client.chat(
                required_tool_retry_prompt,
                retry_context,
                retry_session,
                translated.images,
            )
        except SubstrateCopilotError as exc:
            finish_continuation(False)
            call_record["retried"] = True
            call_record["error"] = str(exc)
            call_record["tool_calls_result"] = []
            record_response_text(app.state, call_record, raw_text)
            append_call_log(app.state, call_record)
            raise upstream_http_error(exc) from exc
        raw_text, declined = split_no_tool_marker(raw_text)
        tool_calls = _resolve_responses_tool_calls(
            raw_text,
            tool_names,
            read_only_guard,
            choice[2],
            strict_tool_schemas,
            tool_namespaces,
            declined,
        )
        call_record["retried"] = True
        if not tool_calls:
            finish_continuation(False)
            call_record["tool_calls_result"] = []
            record_response_text(app.state, call_record, raw_text)
            append_call_log(app.state, call_record)
            raise HTTPException(
                status_code=502,
                detail=_REQUIRED_TOOL_CHOICE_ERROR,
            )
    elif (
        not tool_calls
        and tool_names
        and not read_only_guard
        and not declined
        and _looks_like_fake_file_claim(raw_text)
    ):
        log.info("  fake file claim detected, forcing corrective retry")
        try:
            retry_prompt = (
                f"{_RETRY_INSTRUCTION}\n\nOriginal request:\n{translated.prompt}"
            )
            retry_client = (
                studio_turn.client
                if studio_turn is not None
                and call_record.get("tool_planning") == "studio"
                else client
            )
            retry_context = (
                studio_turn.additional_context
                if retry_client is not client
                else translated.additional_context
            )
            retry_session = (
                studio_turn.session if retry_client is not client else session
            )
            retry_text = await retry_client.chat(
                retry_prompt,
                retry_context,
                retry_session,
                translated.images,
            )
            retry_calls = _resolve_responses_tool_calls(
                retry_text,
                tool_names,
                read_only_guard,
                choice[2],
                strict_tool_schemas,
                tool_namespaces,
            )
            if retry_calls:
                raw_text, tool_calls = retry_text, retry_calls
                call_record["retried"] = True
        except SubstrateCopilotError:
            pass

    call_record["tool_calls_result"] = [
        (call.get("function") or {}).get("name")
        for call in tool_calls
    ]
    record_response_text(app.state, call_record, raw_text)
    append_call_log(app.state, call_record)

    text = _strip_tool_call_blocks(raw_text) if tool_names else raw_text
    text = media_rewriter(text)
    output: list[dict] = []
    if text or not tool_calls:
        output.append(_responses_message_item(text))
    output.extend(_responses_function_call_items(tool_calls))
    if session is not None:
        record_issued_response(
            resp_id,
            [
                item["call_id"]
                for item in output
                if item["type"] == "function_call"
            ],
        )
    finish_continuation(True)
    return JSONResponse(_responses_object(
        resp_id,
        model_alias,
        int(time.time()),
        "completed",
        output,
        response_tools=response_tools,
        tool_choice=response_tool_choice,
        parallel_tool_calls=choice[2],
        previous_response_id=request.previous_response_id,
        instructions=request.instructions,
        include_usage=True,
        usage=call_record.get("usage"),
    ))
