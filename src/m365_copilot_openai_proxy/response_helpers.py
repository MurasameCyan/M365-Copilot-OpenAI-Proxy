from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping

from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator
from jsonschema.validators import validator_for
from referencing import Registry
from referencing.exceptions import Unresolvable

from .session_store import PersistentSession
from .studio_planner import PlannerTurn, ordered_or_streamed
from .substrate_client import (
    SubstrateCopilotClient,
    SubstrateCopilotError,
    SubstrateThrottled,
    _dedupe_repeated_delta,
)
from .tool_call_parser import (
    _RETRY_INSTRUCTION,
    _extract_prose_write,
    _extract_tool_calls,
    _filter_read_only_tool_calls,
    _looks_like_fake_file_claim,
    split_no_tool_marker,
    _strip_tool_call_blocks,
)
from .tool_router import routed_or_streamed
from .usage_store import (
    anthropic_usage,
    openai_usage,
    responses_usage,
    usage_for_record,
)


def _transform_complete_text(full_text: str, text_transform: Callable[[str], str] | None) -> str:
    return text_transform(full_text) if text_transform is not None else full_text


def _json_err(status: int, message: str, error_type: str = "error") -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"message": message, "type": error_type}},
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def _openai_stream(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
    on_text_done: Callable[[str], None] | None = None,
    text_transform: Callable[[str], str] | None = None,
    images: list | None = None,
    call_record: dict | None = None,
    on_response_done: Callable[[dict], None] | None = None,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl_{uuid.uuid4().hex}"
    created = int(time.time())
    first_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_alias,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first_chunk)}\n\n"
    raw_text = ""
    full_text = ""
    try:
        async for delta in client.chat_stream(prompt, additional_context, session, images):
            delta = _dedupe_repeated_delta(raw_text, delta)
            if not delta:
                continue
            raw_text += delta
            if text_transform is not None:
                continue
            full_text += delta
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_alias,
                "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
    except SubstrateCopilotError as exc:
        # A mid-stream upstream failure (most often a mode M365 will not serve for
        # this account) must reach the client as readable assistant text, NOT a
        # bare {"error": ...} frame: strict OpenAI clients index
        # choices[0].delta.content and render an error-only chunk as
        # "null: [object Object]". Deliver the message as a normal content delta
        # plus a stop finish, mirroring the anthropic stream path.
        error_text = f"⚠️ 上游错误：{exc}"
        if call_record is not None:
            call_record["error"] = str(exc)
        logging.getLogger("copilot_proxy").warning("openai stream upstream error: %s", exc)
        sep = "\n\n" if full_text else ""
        err_delta = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_alias,
            "choices": [{"index": 0, "delta": {"content": sep + error_text}, "finish_reason": None}],
        }
        if isinstance(exc, SubstrateThrottled):
            err_delta["m365_error"] = {
                "type": "rate_limit_error",
                "message": str(exc),
            }
        yield f"data: {json.dumps(err_delta)}\n\n"
        stop_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_alias,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        if on_text_done is not None:
            on_text_done(full_text + sep + error_text)
        stop_chunk["usage"] = openai_usage(usage_for_record(call_record))
        yield f"data: {json.dumps(stop_chunk)}\n\n"
        yield "data: [DONE]\n\n"
        return
    if text_transform is not None:
        full_text = _transform_complete_text(raw_text, text_transform)
        if full_text:
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_alias,
                "choices": [{"index": 0, "delta": {"content": full_text}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
    if on_text_done is not None:
        on_text_done(full_text)
    if on_response_done is not None:
        on_response_done({"role": "assistant", "content": full_text})
    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_alias,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        "usage": openai_usage(usage_for_record(call_record)),
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


def _responses_usage(usage: dict | None = None) -> dict:
    return responses_usage(usage)


def _responses_message_item(
    text: str,
    item_id: str | None = None,
    status: str = "completed",
) -> dict:
    return {
        "id": item_id or f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{
            "type": "output_text",
            "text": text,
            "annotations": [],
            "logprobs": [],
        }],
    }


def _responses_function_call_items(tool_calls: list[dict]) -> list[dict]:
    items: list[dict] = []
    for call in tool_calls:
        function = call.get("function") or {}
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False)
        item = {
            "id": f"fc_{uuid.uuid4().hex}",
            "type": "function_call",
            "call_id": call.get("id") or f"call_{uuid.uuid4().hex[:24]}",
            "name": function.get("name") or "tool",
            "arguments": arguments,
            "status": "completed",
        }
        namespace = call.get("namespace")
        if isinstance(namespace, str) and namespace.strip():
            item["namespace"] = namespace.strip()
        items.append(item)
    return items


def _responses_object(
    response_id: str,
    model_alias: str,
    created: int,
    status: str,
    output: list[dict],
    *,
    response_tools: list[dict] | None = None,
    tool_choice=None,
    parallel_tool_calls: bool = True,
    previous_response_id: str | None = None,
    instructions: str | None = None,
    error: dict | None = None,
    include_usage: bool = False,
    usage: dict | None = None,
) -> dict:
    response = {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "model": model_alias,
        "status": status,
        "output": output,
        "parallel_tool_calls": parallel_tool_calls,
        "tool_choice": tool_choice if tool_choice is not None else "auto",
        "tools": response_tools or [],
    }
    if previous_response_id is not None:
        response["previous_response_id"] = previous_response_id
    if instructions is not None:
        response["instructions"] = instructions
    if error is not None:
        response["error"] = error
    if status == "completed":
        response["completed_at"] = int(time.time())
    if include_usage:
        response["usage"] = _responses_usage(usage)
    return response


def _resolve_responses_tool_calls(
    text: str,
    tool_names: set[str],
    read_only_guard: bool,
    allow_parallel: bool,
    strict_tool_schemas: Mapping[str, dict] | None = None,
    tool_namespaces: Mapping[str, str] | None = None,
    declined: bool = False,
) -> list[dict]:
    if not tool_names:
        return []
    calls = []
    for call in _extract_tool_calls(text):
        function = call.get("function") or {}
        if function.get("name") not in tool_names:
            continue
        try:
            arguments = json.loads(function.get("arguments", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(arguments, dict):
            continue
        calls.append(call)
    if read_only_guard and calls:
        calls = _filter_read_only_tool_calls(calls)
    # A model that answered the contract with the explicit no-action token did not
    # forget to call a tool, so guessing a Write out of its prose would fabricate a
    # file it deliberately declined to write. The chat and Anthropic resolvers have
    # always skipped the fallback on `declined`; this path used to discard the flag.
    if not calls and not read_only_guard and not declined:
        calls = _extract_prose_write(text, tool_names)
    if strict_tool_schemas:
        valid_calls = []
        for call in calls:
            function = call.get("function") or {}
            schema = strict_tool_schemas.get(function.get("name"))
            if schema is None:
                valid_calls.append(call)
                continue
            try:
                arguments = json.loads(function.get("arguments", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            try:
                is_valid = (
                    isinstance(arguments, dict)
                    and validator_for(
                        schema,
                        default=Draft202012Validator,
                    )(
                        schema,
                        registry=Registry(),
                    ).is_valid(arguments)
                )
            except (RecursionError, Unresolvable):
                is_valid = False
            if is_valid:
                valid_calls.append(call)
        calls = valid_calls
    if not allow_parallel:
        calls = calls[:1]
    if tool_namespaces:
        for call in calls:
            function = call.get("function") or {}
            namespace = tool_namespaces.get(function.get("name"))
            if namespace:
                call["namespace"] = namespace
    return calls


_REQUIRED_TOOL_CHOICE_ERROR = (
    "Upstream model did not return a valid function call for required "
    "tool_choice after one retry."
)


def _responses_required_tool_retry_prompt(
    choice: tuple[str, str | None, bool],
    tool_names: set[str],
    original_prompt: str,
) -> str | None:
    """Build one corrective turn for Responses required/named tool choices."""
    mode, name, _allow_parallel = choice
    if mode not in {"required", "tool"}:
        return None
    target = (
        f"the function named {name}"
        if mode == "tool" and name
        else "one of these declared functions: " + ", ".join(sorted(tool_names))
    )
    return (
        "Your previous response did not satisfy the required tool_choice. "
        f"You must call {target} now. Emit a valid fenced tool_call JSON block "
        "and do not answer in prose instead.\n\n"
        f"Original request:\n{original_prompt}"
    )


def _responses_event(payload: dict, sequence_number: int) -> str:
    return (
        f"event: {payload['type']}\n"
        f"data: {json.dumps({**payload, 'sequence_number': sequence_number})}\n\n"
    )


async def _collect_deduped_stream(stream: AsyncIterator[str]) -> str:
    text = ""
    async for delta in stream:
        text += _dedupe_repeated_delta(text, delta)
    return text


async def _responses_stream(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
    on_text_done: Callable[[str], None] | None = None,
    text_transform: Callable[[str], str] | None = None,
    response_id: str | None = None,
    images: list | None = None,
    response_tools: list[dict] | None = None,
    tool_choice=None,
    parallel_tool_calls: bool = True,
    previous_response_id: str | None = None,
    instructions: str | None = None,
    call_record: dict | None = None,
    on_response_issued: Callable[[str, list[str]], None] | None = None,
    on_request_done: Callable[[bool], None] | None = None,
) -> AsyncIterator[str]:
    resp_id = response_id or f"resp_{uuid.uuid4().hex}"
    item_id = f"msg_{uuid.uuid4().hex}"
    created = int(time.time())
    sequence = 0

    in_progress = _responses_object(
        resp_id, model_alias, created, "in_progress", [],
        response_tools=response_tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        previous_response_id=previous_response_id,
        instructions=instructions,
    )
    yield _responses_event({"type": "response.created", "response": in_progress}, sequence)
    sequence += 1
    yield _responses_event({"type": "response.in_progress", "response": in_progress}, sequence)
    sequence += 1
    yield _responses_event({
        "type": "response.output_item.added",
        "output_index": 0,
        "item": {
            "id": item_id,
            "type": "message",
            "status": "in_progress",
            "role": "assistant",
            "content": [],
        },
    }, sequence)
    sequence += 1
    yield _responses_event({
        "type": "response.content_part.added",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "part": {"type": "output_text", "text": "", "annotations": [], "logprobs": []},
    }, sequence)
    sequence += 1

    raw_text = ""
    full_text = ""
    try:
        async for delta in client.chat_stream(prompt, additional_context, session, images):
            delta = _dedupe_repeated_delta(raw_text, delta)
            if not delta:
                continue
            raw_text += delta
            if text_transform is not None:
                continue
            full_text += delta
            yield _responses_event({
                "type": "response.output_text.delta",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": delta,
                "logprobs": [],
            }, sequence)
            sequence += 1
    except SubstrateCopilotError as exc:
        if call_record is not None:
            call_record["error"] = str(exc)
            call_record["tool_calls_result"] = []
        if on_text_done is not None:
            on_text_done(raw_text)
        error_code = "rate_limit_error" if isinstance(exc, SubstrateThrottled) else "server_error"
        yield _responses_event({
            "type": "error",
            "code": error_code,
            "message": str(exc),
            "param": None,
        }, sequence)
        sequence += 1
        failed = _responses_object(
            resp_id, model_alias, created, "failed", [],
            response_tools=response_tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            previous_response_id=previous_response_id,
            instructions=instructions,
            error={
                "message": str(exc),
                "code": "rate_limit_exceeded"
                if isinstance(exc, SubstrateThrottled)
                else error_code,
            },
            usage=(call_record or {}).get("usage"),
        )
        if on_request_done is not None:
            on_request_done(False)
        yield _responses_event({"type": "response.failed", "response": failed}, sequence)
        return

    if text_transform is not None:
        full_text = _transform_complete_text(raw_text, text_transform)
        if full_text:
            yield _responses_event({
                "type": "response.output_text.delta",
                "item_id": item_id,
                "output_index": 0,
                "content_index": 0,
                "delta": full_text,
                "logprobs": [],
            }, sequence)
            sequence += 1
    if on_text_done is not None:
        on_text_done(full_text)
    message_item = _responses_message_item(full_text, item_id)
    yield _responses_event({
        "type": "response.output_text.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
        "logprobs": [],
    }, sequence)
    sequence += 1
    yield _responses_event({
        "type": "response.content_part.done",
        "item_id": item_id,
        "output_index": 0,
        "content_index": 0,
        "part": message_item["content"][0],
    }, sequence)
    sequence += 1
    yield _responses_event({
        "type": "response.output_item.done",
        "output_index": 0,
        "item": message_item,
    }, sequence)
    sequence += 1
    completed = _responses_object(
        resp_id, model_alias, created, "completed", [message_item],
        response_tools=response_tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        previous_response_id=previous_response_id,
        instructions=instructions,
        include_usage=True,
        usage=(call_record or {}).get("usage"),
    )
    if on_response_issued is not None:
        on_response_issued(resp_id, [])
    if on_request_done is not None:
        on_request_done(True)
    yield _responses_event({"type": "response.completed", "response": completed}, sequence)


async def _responses_stream_with_tools(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
    on_text_done: Callable[[str], None] | None = None,
    text_transform: Callable[[str], str] | None = None,
    response_id: str | None = None,
    images: list | None = None,
    response_tools: list[dict] | None = None,
    tool_choice=None,
    parallel_tool_calls: bool = True,
    previous_response_id: str | None = None,
    instructions: str | None = None,
    tool_names: set[str] | None = None,
    strict_tool_schemas: Mapping[str, dict] | None = None,
    tool_namespaces: Mapping[str, str] | None = None,
    read_only_guard: bool = False,
    call_record: dict | None = None,
    required_tool_retry_prompt: str | None = None,
    on_response_issued: Callable[[str, list[str]], None] | None = None,
    on_request_done: Callable[[bool], None] | None = None,
    router_prompt: str = "",
    on_router_call: Callable[[], None] | None = None,
    studio_turn: PlannerTurn | None = None,
    prefer_router: bool = False,
    should_fallback: Callable[[str], bool] | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_studio_fallback: Callable[[str], None] | None = None,
    on_router_fallback: Callable[[str], None] | None = None,
    skip_router_fallback: bool = False,
    allow_final_answer: bool = False,
) -> AsyncIterator[str]:
    resp_id = response_id or f"resp_{uuid.uuid4().hex}"
    created = int(time.time())
    sequence = 0
    names = tool_names or set()
    full_text = ""
    in_progress = _responses_object(
        resp_id, model_alias, created, "in_progress", [],
        response_tools=response_tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        previous_response_id=previous_response_id,
        instructions=instructions,
    )
    yield _responses_event({"type": "response.created", "response": in_progress}, sequence)
    sequence += 1
    yield _responses_event({"type": "response.in_progress", "response": in_progress}, sequence)
    sequence += 1

    try:
        router_decided = False

        def note_router_decision() -> None:
            nonlocal router_decided
            router_decided = True

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
                on_router_call=note_router_decision,
                should_fallback=should_fallback,
                fallback_turn=fallback_turn,
                on_router_fallback=on_router_fallback,
            ):
                yield delta

        stream = ordered_or_streamed(
            studio_turn=studio_turn,
            router_turn=router_stream,
            inline_turn=inline_stream,
            prefer_router=prefer_router,
            should_fallback=should_fallback,
            on_stage=on_stage,
            on_studio_fallback=on_studio_fallback,
            skip_router_fallback=skip_router_fallback,
        )
        full_text = await _collect_deduped_stream(stream)
        full_text, declined = split_no_tool_marker(full_text)
        tool_calls = _resolve_responses_tool_calls(
            full_text,
            names,
            read_only_guard,
            parallel_tool_calls,
            strict_tool_schemas,
            tool_namespaces,
            declined,
        )
        if router_decided and tool_calls and on_router_call is not None:
            on_router_call()
        if not tool_calls and required_tool_retry_prompt:
            if call_record is not None:
                call_record["retried"] = True
            retry_client = (
                studio_turn.client
                if studio_turn is not None
                and (
                    call_record is None
                    or call_record.get("tool_planning") == "studio"
                )
                else client
            )
            retry_context = (
                studio_turn.additional_context
                if retry_client is not client
                else additional_context
            )
            retry_session = (
                studio_turn.session if retry_client is not client else session
            )
            full_text = await _collect_deduped_stream(
                retry_client.chat_stream(
                    required_tool_retry_prompt,
                    retry_context,
                    retry_session,
                    images,
                )
            )
            full_text, declined = split_no_tool_marker(full_text)
            tool_calls = _resolve_responses_tool_calls(
                full_text,
                names,
                read_only_guard,
                parallel_tool_calls,
                strict_tool_schemas,
                tool_namespaces,
                declined,
            )
        elif (
            not tool_calls
            and names
            and not read_only_guard
            and not declined
            and not allow_final_answer
            and _looks_like_fake_file_claim(full_text)
        ):
            retry_prompt = (
                f"{_RETRY_INSTRUCTION}\n\nOriginal request:\n{prompt}"
            )
            retry_client = (
                studio_turn.client
                if studio_turn is not None
                and (
                    call_record is None
                    or call_record.get("tool_planning") == "studio"
                )
                else client
            )
            retry_context = (
                studio_turn.additional_context
                if retry_client is not client
                else additional_context
            )
            retry_session = (
                studio_turn.session if retry_client is not client else session
            )
            retry_text = await _collect_deduped_stream(
                retry_client.chat_stream(
                    retry_prompt, retry_context, retry_session, images
                )
            )
            retry_calls = _resolve_responses_tool_calls(
                retry_text,
                names,
                read_only_guard,
                parallel_tool_calls,
                strict_tool_schemas,
                tool_namespaces,
            )
            if retry_calls:
                full_text, tool_calls = retry_text, retry_calls
                if call_record is not None:
                    call_record["retried"] = True
    except SubstrateCopilotError as exc:
        if call_record is not None:
            call_record["error"] = str(exc)
            call_record["tool_calls_result"] = []
        if on_text_done is not None:
            on_text_done(full_text)
        error_code = "rate_limit_error" if isinstance(exc, SubstrateThrottled) else "server_error"
        yield _responses_event({
            "type": "error",
            "code": error_code,
            "message": str(exc),
            "param": None,
        }, sequence)
        sequence += 1
        failed = _responses_object(
            resp_id, model_alias, created, "failed", [],
            response_tools=response_tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            previous_response_id=previous_response_id,
            instructions=instructions,
            error={
                "message": str(exc),
                "code": "rate_limit_exceeded"
                if isinstance(exc, SubstrateThrottled)
                else error_code,
            },
            usage=(call_record or {}).get("usage"),
        )
        if on_request_done is not None:
            on_request_done(False)
        yield _responses_event({"type": "response.failed", "response": failed}, sequence)
        return

    if required_tool_retry_prompt and not tool_calls:
        if call_record is not None:
            call_record["tool_calls_result"] = []
        if on_text_done is not None:
            on_text_done(full_text)
        yield _responses_event({
            "type": "error",
            "code": "server_error",
            "message": _REQUIRED_TOOL_CHOICE_ERROR,
            "param": "tool_choice",
        }, sequence)
        sequence += 1
        failed = _responses_object(
            resp_id, model_alias, created, "failed", [],
            response_tools=response_tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel_tool_calls,
            previous_response_id=previous_response_id,
            instructions=instructions,
            error={
                "message": _REQUIRED_TOOL_CHOICE_ERROR,
                "code": "server_error",
            },
            usage=(call_record or {}).get("usage"),
        )
        if on_request_done is not None:
            on_request_done(False)
        yield _responses_event({"type": "response.failed", "response": failed}, sequence)
        return

    if call_record is not None:
        call_record["tool_calls_result"] = [
            (call.get("function") or {}).get("name") for call in tool_calls
        ]
    if on_text_done is not None:
        on_text_done(full_text)

    text_out = _strip_tool_call_blocks(full_text) if names else full_text
    if text_transform is not None:
        text_out = text_transform(text_out)
    output: list[dict] = []
    if text_out or not tool_calls:
        output.append(_responses_message_item(text_out))
    output.extend(_responses_function_call_items(tool_calls))
    issued_call_ids = [
        item["call_id"] for item in output if item["type"] == "function_call"
    ]
    if on_response_issued is not None:
        on_response_issued(resp_id, issued_call_ids)

    for output_index, item in enumerate(output):
        if item["type"] == "message":
            added = {
                "id": item["id"],
                "type": "message",
                "status": "in_progress",
                "role": "assistant",
                "content": [],
            }
            yield _responses_event({
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": added,
            }, sequence)
            sequence += 1
            yield _responses_event({
                "type": "response.content_part.added",
                "item_id": item["id"],
                "output_index": output_index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": [], "logprobs": []},
            }, sequence)
            sequence += 1
            if text_out:
                yield _responses_event({
                    "type": "response.output_text.delta",
                    "item_id": item["id"],
                    "output_index": output_index,
                    "content_index": 0,
                    "delta": text_out,
                    "logprobs": [],
                }, sequence)
                sequence += 1
            yield _responses_event({
                "type": "response.output_text.done",
                "item_id": item["id"],
                "output_index": output_index,
                "content_index": 0,
                "text": text_out,
                "logprobs": [],
            }, sequence)
            sequence += 1
            yield _responses_event({
                "type": "response.content_part.done",
                "item_id": item["id"],
                "output_index": output_index,
                "content_index": 0,
                "part": item["content"][0],
            }, sequence)
            sequence += 1
        else:
            added = {**item, "arguments": "", "status": "in_progress"}
            yield _responses_event({
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": added,
            }, sequence)
            sequence += 1
            yield _responses_event({
                "type": "response.function_call_arguments.delta",
                "item_id": item["id"],
                "output_index": output_index,
                "delta": item["arguments"],
            }, sequence)
            sequence += 1
            yield _responses_event({
                "type": "response.function_call_arguments.done",
                "item_id": item["id"],
                "output_index": output_index,
                "name": item["name"],
                "arguments": item["arguments"],
            }, sequence)
            sequence += 1
        yield _responses_event({
            "type": "response.output_item.done",
            "output_index": output_index,
            "item": item,
        }, sequence)
        sequence += 1

    completed = _responses_object(
        resp_id, model_alias, created, "completed", output,
        response_tools=response_tools,
        tool_choice=tool_choice,
        parallel_tool_calls=parallel_tool_calls,
        previous_response_id=previous_response_id,
        instructions=instructions,
        include_usage=True,
        usage=(call_record or {}).get("usage"),
    )
    if on_request_done is not None:
        on_request_done(True)
    yield _responses_event({"type": "response.completed", "response": completed}, sequence)


async def _anthropic_stream(
    model_alias: str,
    client: SubstrateCopilotClient,
    prompt: str,
    additional_context: list[str],
    session: PersistentSession | None = None,
    on_text_done: Callable[[str], None] | None = None,
    text_transform: Callable[[str], str] | None = None,
    images: list | None = None,
    call_record: dict | None = None,
    on_response_done: Callable[[dict], None] | None = None,
) -> AsyncIterator[str]:
    msg_id = f"msg_{uuid.uuid4().hex}"

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    yield sse("message_start", {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model_alias, "stop_reason": None, "stop_sequence": None, "usage": anthropic_usage(usage_for_record(call_record))}})
    yield sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    yield sse("ping", {"type": "ping"})

    raw_text = ""
    full_text = ""
    try:
        async for delta in client.chat_stream(prompt, additional_context, session, images):
            delta = _dedupe_repeated_delta(raw_text, delta)
            if not delta:
                continue
            raw_text += delta
            if text_transform is not None:
                continue
            full_text += delta
            yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": delta}})
    except SubstrateCopilotError as exc:
        error_text = f"⚠️ 上游错误：{exc}"
        if call_record is not None:
            call_record["error"] = str(exc)
        if isinstance(exc, SubstrateThrottled):
            if text_transform is not None and raw_text:
                full_text = _transform_complete_text(raw_text, text_transform)
                if full_text:
                    yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": full_text}})
            if on_text_done is not None:
                on_text_done(full_text)
            yield sse("error", {
                "type": "error",
                "error": {
                    "type": "rate_limit_error",
                    "message": str(exc),
                },
            })
            return
        if text_transform is not None and raw_text:
            full_text = _transform_complete_text(raw_text, text_transform)
            if full_text:
                yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": full_text}})
        yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": error_text}})
        yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        if on_text_done is not None:
            on_text_done(full_text + error_text)
        yield sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": anthropic_usage(usage_for_record(call_record))})
        yield sse("message_stop", {"type": "message_stop"})
        return

    if text_transform is not None:
        full_text = _transform_complete_text(raw_text, text_transform)
        if full_text:
            yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": full_text}})
    if on_text_done is not None:
        on_text_done(full_text)
    if on_response_done is not None:
        on_response_done({"role": "assistant", "content": full_text})
    yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": anthropic_usage(usage_for_record(call_record))})
    yield sse("message_stop", {"type": "message_stop"})
