from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .session_store import PersistentSession
from .substrate_client import SubstrateCopilotError, SubstrateThrottled


class ChatStreamClient(Protocol):
    def chat_stream(
        self,
        prompt: str,
        additional_context: list[str],
        session: PersistentSession | None = None,
        images: list | None = None,
    ) -> AsyncIterator[str]: ...


@dataclass(frozen=True, slots=True)
class PlannerTurn:
    client: ChatStreamClient
    prompt: str
    additional_context: list[str]
    session: PersistentSession | None = None
    images: list | None = None


AnswerFallback = Callable[[], Awaitable[str]]
StreamFallback = Callable[[], AsyncIterator[str]]
RouterAnswer = Callable[[AnswerFallback | None], Awaitable[str]]
RouterStream = Callable[[StreamFallback | None], AsyncIterator[str]]
NeedsFallback = Callable[[str], bool]


async def planned_or_answered(
    *,
    studio_turn: PlannerTurn,
    fallback_turn: AnswerFallback,
    should_fallback: NeedsFallback | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> str:
    chunks: list[str] = []
    yielded_any = False
    try:
        async for chunk in studio_turn.client.chat_stream(
            studio_turn.prompt,
            studio_turn.additional_context,
            studio_turn.session,
            studio_turn.images,
        ):
            if chunk:
                yielded_any = True
            chunks.append(chunk)
    except SubstrateThrottled:
        raise
    except SubstrateCopilotError:
        if yielded_any:
            raise
        if studio_turn.session is not None:
            studio_turn.session.reset_conversation()
        if on_fallback is not None:
            on_fallback("upstream_error")
        return await fallback_turn()
    text = "".join(chunks)
    if should_fallback is not None and should_fallback(text):
        if on_fallback is not None:
            on_fallback("no_tool_call")
        return await fallback_turn()
    return text


async def planned_or_streamed(
    *,
    studio_turn: PlannerTurn,
    fallback_turn: StreamFallback,
    should_fallback: NeedsFallback | None = None,
    on_fallback: Callable[[str], None] | None = None,
) -> AsyncIterator[str]:
    # Tool-bearing callers buffer the whole upstream turn before emitting a
    # protocol response. When a predicate is supplied, buffer here too so a
    # no-call Studio answer can be replaced without duplicating visible text.
    buffered: list[str] | None = [] if should_fallback is not None else None
    yielded_any = False
    try:
        async for chunk in studio_turn.client.chat_stream(
            studio_turn.prompt,
            studio_turn.additional_context,
            studio_turn.session,
            studio_turn.images,
        ):
            if chunk:
                yielded_any = True
            if buffered is None:
                yield chunk
            else:
                buffered.append(chunk)
    except SubstrateThrottled:
        raise
    except SubstrateCopilotError:
        if yielded_any:
            raise
        if studio_turn.session is not None:
            studio_turn.session.reset_conversation()
        if on_fallback is not None:
            on_fallback("upstream_error")
        async for chunk in fallback_turn():
            yield chunk
        return
    if buffered is not None:
        text = "".join(buffered)
        if should_fallback is not None and should_fallback(text):
            if on_fallback is not None:
                on_fallback("no_tool_call")
            async for chunk in fallback_turn():
                yield chunk
            return
        for chunk in buffered:
            yield chunk


async def ordered_or_answered(
    *,
    studio_turn: PlannerTurn | None,
    router_turn: RouterAnswer,
    inline_turn: AnswerFallback,
    prefer_router: bool,
    should_fallback: NeedsFallback | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_studio_fallback: Callable[[str], None] | None = None,
    skip_router_fallback: bool = False,
) -> str:
    """Run the requested planner order, ending at one ordinary inline turn.

    ``router_turn`` is supplied by the route because it owns the provider client
    and router prompt.  Keeping the order here makes the three API protocols use
    the same fallback contract:

    * Studio mode: Studio -> Router -> inline
    * Router mode: Router -> Studio -> inline
    * no Studio client (including Consumer): Router -> inline

    An explicit router ``NO_TOOL_NEEDED`` verdict is already an ordinary answer,
    so the router helper must not be asked to retry it a second time.
    """
    async def inline_layer() -> str:
        if on_stage is not None:
            on_stage("inline")
        return await inline_turn()

    async def studio_layer(fallback: AnswerFallback) -> str:
        if studio_turn is None:
            return await inline_layer()
        if on_stage is not None:
            on_stage("studio")
        return await planned_or_answered(
            studio_turn=studio_turn,
            fallback_turn=fallback,
            should_fallback=should_fallback,
            on_fallback=on_studio_fallback,
        )

    if prefer_router:
        if on_stage is not None:
            on_stage("router")
        return await router_turn(
            (lambda: studio_layer(inline_layer))
            if studio_turn is not None
            else None
        )
    if studio_turn is not None:
        fallback = (
            inline_layer
            if skip_router_fallback
            else lambda: _router_answer(router_turn, inline_layer, on_stage)
        )
        return await studio_layer(fallback)
    if on_stage is not None:
        on_stage("router")
    return await router_turn(None)


async def _router_answer(router_turn, fallback, on_stage):
    if on_stage is not None:
        on_stage("router")
    return await router_turn(fallback)


async def ordered_or_streamed(
    *,
    studio_turn: PlannerTurn | None,
    router_turn: RouterStream,
    inline_turn: StreamFallback,
    prefer_router: bool,
    should_fallback: NeedsFallback | None = None,
    on_stage: Callable[[str], None] | None = None,
    on_studio_fallback: Callable[[str], None] | None = None,
    skip_router_fallback: bool = False,
) -> AsyncIterator[str]:
    """Streaming counterpart to :func:`ordered_or_answered`."""
    async def inline_layer() -> AsyncIterator[str]:
        if on_stage is not None:
            on_stage("inline")
        async for chunk in inline_turn():
            yield chunk

    async def studio_layer(fallback: StreamFallback) -> AsyncIterator[str]:
        if studio_turn is None:
            async for chunk in inline_layer():
                yield chunk
            return
        if on_stage is not None:
            on_stage("studio")
        async for chunk in planned_or_streamed(
            studio_turn=studio_turn,
            fallback_turn=fallback,
            should_fallback=should_fallback,
            on_fallback=on_studio_fallback,
        ):
            yield chunk

    if prefer_router:
        if on_stage is not None:
            on_stage("router")
        fallback = (
            (lambda: studio_layer(inline_layer))
            if studio_turn is not None
            else None
        )
        async for chunk in router_turn(fallback):
            yield chunk
        return
    if studio_turn is not None:
        fallback = (
            inline_layer
            if skip_router_fallback
            else lambda: _router_stream(router_turn, inline_layer, on_stage)
        )
        async for chunk in studio_layer(fallback):
            yield chunk
        return
    if on_stage is not None:
        on_stage("router")
    async for chunk in router_turn(None):
        yield chunk


async def _router_stream(router_turn, fallback, on_stage):
    if on_stage is not None:
        on_stage("router")
    async for chunk in router_turn(fallback):
        yield chunk
