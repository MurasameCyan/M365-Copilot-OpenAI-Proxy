from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from typing import Any

from fastapi import Request

from .auth_helpers import constant_time_equals
from .history_index import normalize_history
from .models import AnthropicMessagesRequest, OpenAIChatRequest, OpenAIResponsesRequest
from .session_store import PersistentSession
from .translator import flatten_content

_PERSIST_MODEL_SUFFIX = ":persist"
_SESSION_ID_HEADER = "x-m365-session-id"
_RESP_ID_PREFIX = "resp_"


def _studio_session_namespace(agent_id: str | None, tone: str) -> str:
    """Return an opaque per-Agent and per-tone Studio conversation namespace.

    The raw Agent ID is tenant metadata and must not become part of a session
    key. Rebinding the Agent or selecting another tone starts a separate thread.
    The version also isolates conversations started by the old fixed-Magic path.
    """
    normalized = str(agent_id or "").strip()
    scope = json.dumps([normalized, tone], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    return f"studio-v2-{digest}"


def _studio_history_context_id(
    messages: list[Any], *, before_turn: bool = False,
) -> str | None:
    canonical = [
        item.model_dump(mode="json", exclude_none=True)
        if hasattr(item, "model_dump") else dict(item)
        for item in messages
    ]
    if before_turn:
        last_assistant = max(
            (i for i, item in enumerate(canonical) if item.get("role") == "assistant"),
            default=-1,
        )
        if last_assistant < 0:
            return None
        canonical = canonical[:last_assistant + 1]
    for item in canonical:
        if item.get("tool_calls"):
            # Streaming clients may retain the transport-only call index.
            item["tool_calls"] = [
                {key: value for key, value in call.items() if key != "index"}
                for call in item["tool_calls"]
            ]
    payload = json.dumps(normalize_history(canonical), ensure_ascii=False, separators=(",", ":"))
    return "history-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _studio_session_for_context(
    app: Any, session: PersistentSession | None, context_id: str | None,
) -> PersistentSession | None:
    if (
        session is None
        or session.turn_count == 0
        or context_id is None
        or session.studio_context_id == context_id
    ):
        return session
    # Do not reset an existing object's conversation/lock: an in-flight request
    # may still own it. Replacing the store entry gives this history a fresh owner.
    key = app.state.session_store.key_for(session)
    return app.state.session_store.reset(key) if key else PersistentSession()


def _request_tenant(raw_request: Request) -> str:
    key_obj = getattr(raw_request.state, "api_key_obj", None)
    account = getattr(raw_request.state, "account", None)
    key_id = str(getattr(key_obj, "id", "") or "")
    account_id = str(getattr(account, "id", "") or "")
    if key_id and account_id:
        return f"{key_id}:{account_id}"
    return key_id or account_id or "global"


def _detect_conversation_session(request: OpenAIChatRequest) -> tuple[str, str]:
    for msg in request.messages:
        if msg.role == "user":
            text = flatten_content(msg.content).strip()
            if text:
                sid = "conv_" + hashlib.sha256(text.encode()).hexdigest()[:12]
                title = text[:60].replace("\n", " ")
                return sid, title
    return "conv_" + uuid.uuid4().hex[:12], "New conversation"


def _encode_responses_session_id(
    session_key: str,
    secret: str | None = None,
    call_ids: set[str] | None = None,
) -> str:
    """Encode a session key into a Responses `resp_...` id so the client can
    echo it back as `previous_response_id` on the next turn. A random suffix
    keeps each id unique (per OpenAI semantics) while the encoded prefix stays
    stable across the conversation."""
    payload = json.dumps(
        {"session": session_key, "calls": sorted(call_ids or set())},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    nonce = uuid.uuid4().hex[:8]
    if not secret:
        return f"{_RESP_ID_PREFIX}{token}.{nonce}"
    signature = hmac.new(
        secret.encode(), f"{token}.{nonce}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{_RESP_ID_PREFIX}{token}.{nonce}.{signature}"


def _decode_responses_session_id(
    resp_id: str | None,
    secret: str | None = None,
) -> str | None:
    """Recover the session key previously encoded by
    `_encode_responses_session_id`. Returns None for ids that were not produced
    by us (e.g. plain random ids) so callers fall back to other keys."""
    if not isinstance(resp_id, str) or not resp_id.startswith(_RESP_ID_PREFIX):
        return None
    parts = resp_id[len(_RESP_ID_PREFIX):].split(".")
    token = parts[0] if parts else ""
    if not token:
        return None
    if secret:
        if len(parts) != 3:
            return None
        expected = hmac.new(
            secret.encode(), f"{parts[0]}.{parts[1]}".encode(), hashlib.sha256
        ).hexdigest()[:32]
        # constant_time_equals, not hmac.compare_digest: parts[2] comes from the
        # request body's previous_response_id, so one non-ASCII character there
        # would raise TypeError instead of failing the signature check.
        if not constant_time_equals(parts[2], expected):
            return None
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(decoded, str):
        return decoded or None
    session_key = decoded.get("session") if isinstance(decoded, dict) else None
    return session_key if isinstance(session_key, str) and session_key else None


def _decode_responses_response_claims(
    resp_id: str | None,
    secret: str,
) -> tuple[str, set[str]] | None:
    """Verify an issued Responses id and recover its session + tool call ids."""
    session_key = _decode_responses_session_id(resp_id, secret)
    if session_key is None or not isinstance(resp_id, str):
        return None
    token = resp_id[len(_RESP_ID_PREFIX):].split(".", 1)[0]
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    calls = decoded.get("calls") if isinstance(decoded, dict) else None
    if not isinstance(calls, list) or any(not isinstance(item, str) for item in calls):
        return None
    return session_key, set(calls)


def _responses_session_key(request: OpenAIResponsesRequest) -> str | None:
    prev = _decode_responses_session_id(getattr(request, "previous_response_id", None))
    if prev:
        return prev
    user = getattr(request, "user", None)
    if isinstance(user, str) and user.strip():
        return user.strip()
    text = json.dumps(request.input, ensure_ascii=False, sort_keys=True)
    if text:
        return "responses_" + hashlib.sha256(text.encode()).hexdigest()[:12]
    return None


def _responses_store_key(app: Any, session: PersistentSession | None) -> str | None:
    """Return the actual tenant-qualified store key selected for this request."""
    if session is None:
        return None
    return app.state.session_store.key_for(session)


def _responses_store_key_belongs_to_request(
    raw_request: Request,
    store_key: str,
) -> bool:
    return store_key.startswith(f"{_request_tenant(raw_request)}:")


def _messages_session_key(request: AnthropicMessagesRequest) -> str | None:
    for msg in request.messages:
        if msg.role == "user":
            text = flatten_content(msg.content).strip()
            if text:
                return "messages_" + hashlib.sha256(text.encode()).hexdigest()[:12]
    return None


def _auto_session(
    app: Any,
    tenant: str,
    request: OpenAIChatRequest | AnthropicMessagesRequest,
) -> PersistentSession:
    """Pick the session for a conversation the client did not name itself.

    Prefers the exact history index (longest strict prefix of the messages just
    sent), which keeps two conversations that open with the same text on separate
    upstream threads instead of resetting each other. A history miss with
    assistant messages starts a fresh local session: after a restart or a client
    history rewrite, reusing the first-user-message key could merge two
    conversations that share an opener. The fresh session has ``turn_count == 0``
    so the translator sends the complete client history upstream.
    """
    index = getattr(app.state, "history_index", None)
    pairs = normalize_history(request.messages) if index is not None else []
    if pairs and any(m.role == "assistant" for m in request.messages):
        matched = index.match(tenant, pairs)
        if matched is not None:
            session = app.state.session_store.get_existing(matched)
            if session is not None:
                index.record(tenant, pairs, matched)
                return session
    sid, _title = _detect_conversation_session(request)
    # An unnamed first turn is not enough to distinguish a retry from a new
    # Cherry conversation: both can carry the same templated opener. Reusing a
    # deterministic first-message key would let the newer request overwrite the
    # older session and leak its upstream context. Allocate a unique owner and
    # rely on the exact-history index for later continuations; callers that need
    # retry/idempotency semantics can send the explicit session header.
    key = f"{tenant}:auto:{sid}:{uuid.uuid4().hex[:12]}"
    # Never fall back to the first-user-message key after an exact-history miss.
    # Two Cherry conversations commonly share a templated opener; the old fallback
    # merged them after a restart or client-side history rewrite.
    session = app.state.session_store.reset(key)
    if pairs:
        index.record(tenant, pairs, key)
    return session


def record_auto_session_response(
    app: Any,
    raw_request: Request,
    request: OpenAIChatRequest | AnthropicMessagesRequest,
    session: PersistentSession | None,
    assistant: Any,
) -> None:
    """Bind a successful client-visible assistant message to an auto session.

    The request-side index records the history the client sent.  Without this
    response-side entry, two conversations with the same opener cannot be
    distinguished on their next turn: neither full ``user + assistant`` prefix
    exists in the index.  Only auto sessions participate; named/persist sessions
    already have an explicit selector and must not become discoverable by an
    unrelated unnamed request.
    """
    if session is None:
        return
    key = app.state.session_store.key_for(session)
    if not key:
        return
    if key.startswith(f"{_request_tenant(raw_request)}:studio-v2-"):
        session.record_studio_context(
            _studio_history_context_id([*request.messages, assistant]) or ""
        )
    if ":auto:" not in key:
        return
    index = getattr(app.state, "history_index", None)
    if index is None:
        return
    pairs = normalize_history([*request.messages, assistant])
    # The namespace is part of the tenant string used by `_auto_session`.
    # Deriving it from the actual store key also handles Studio's parallel
    # session without exposing raw account/agent metadata to the index API.
    tenant = key.split(":auto:", 1)[0]
    index.record(tenant, pairs, key)


def _persistent_session(
    app: Any,
    raw_request: Request,
    model: str,
    fallback_key: str | None = None,
    request: OpenAIChatRequest | AnthropicMessagesRequest | None = None,
    namespace: str = "",
) -> PersistentSession | None:
    tenant = _request_tenant(raw_request)
    namespace = str(namespace or "").strip()
    if namespace:
        tenant = f"{tenant}:{namespace}"
    header_key = (raw_request.headers.get(_SESSION_ID_HEADER) or "").strip()
    if header_key:
        return app.state.session_store.get(f"{tenant}:header:{header_key}")
    if model.endswith(_PERSIST_MODEL_SUFFIX):
        return app.state.session_store.get(f"{tenant}:model:{fallback_key or 'default'}")
    if request is not None:
        return _auto_session(app, tenant, request)
    if fallback_key:
        return app.state.session_store.get(f"{tenant}:auto:{fallback_key}")
    return None


def _namespaced_session(
    app: Any,
    raw_request: Request,
    session: PersistentSession | None,
    namespace: str,
) -> PersistentSession | None:
    """Return a parallel session under the same tenant and a named namespace."""
    if session is None:
        return None
    namespace = str(namespace or "").strip()
    if not namespace:
        return session
    key = app.state.session_store.key_for(session)
    if not key:
        return None
    tenant = _request_tenant(raw_request)
    prefix = f"{tenant}:"
    suffix = key[len(prefix):] if key.startswith(prefix) else key
    return app.state.session_store.get(f"{tenant}:{namespace}:{suffix}")
