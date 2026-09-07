from __future__ import annotations

import asyncio
import atexit
import copy
import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .account_crypto import AccountCipher, load_or_create_key
from .runtime_flags import elog


@dataclass(frozen=True)
class CopilotTurn:
    conversation_id: str
    client_session_id: str
    is_start_of_session: bool


_MAX_SESSIONS = 1000

# Write-coalescing window in seconds. Every turn of every conversation mutates a
# session, and one write rewrites the WHOLE map, so a busy pool was rewriting up
# to _MAX_SESSIONS sessions' worth of JSON per turn. 0 keeps the historical
# write-through behaviour; production passes a real interval (see state_init).
_FLUSH_INTERVAL_SECONDS = 0.0


@dataclass
class PersistentSession:
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_count: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    response_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    last_accessed: float = field(default_factory=time.time)
    issued_response_calls: dict[str, list[str]] = field(default_factory=dict)
    issued_response_read_only: dict[str, bool] = field(default_factory=dict)
    issued_response_contexts: dict[str, dict[str, Any]] = field(default_factory=dict)
    consumed_response_ids: list[str] = field(default_factory=list)
    latest_response_id: str | None = None
    pending_response_ids: dict[str, str] = field(default_factory=dict, repr=False)
    # Studio alone uses an opaque history digest/response id to detect turns
    # answered on another tone's parallel thread. Never store message text here.
    studio_context_id: str = ""
    # Called after turn_count changes so the store can persist to disk.
    _on_change: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def reserve_turn(self) -> CopilotTurn:
        turn = CopilotTurn(
            conversation_id=self.conversation_id,
            client_session_id=self.client_session_id,
            is_start_of_session=self.turn_count == 0,
        )
        self.turn_count += 1
        self.last_accessed = time.time()
        if self._on_change is not None:
            self._on_change()
        return turn

    def record_studio_context(self, context_id: str) -> None:
        self.studio_context_id = context_id
        if self._on_change is not None:
            self._on_change()

    def reset_conversation(self) -> None:
        """Abandon the current M365 conversation and start a fresh one in place.

        A reused persistent conversation can rot: after enough turns the upstream
        begins refusing every continuation (turnState=Failed / canned refusal) even
        though the same tone answers in a brand-new conversation. Rebinding the
        identity here (new conversation_id / client_session_id, turn_count back to
        0) lets the very next turn -- and every turn after it -- run on a clean
        conversation, so the caller never has to open a new chat by hand. Mutated
        in place (same object and lock) so the store keeps handing this session back
        under its existing key; unlike ``PersistentSessionStore.reset`` this needs
        no key and preserves the per-session lock a caller may already hold.
        """
        self.conversation_id = str(uuid.uuid4())
        self.client_session_id = str(uuid.uuid4())
        self.turn_count = 0
        self.studio_context_id = ""
        self.last_accessed = time.time()
        if self._on_change is not None:
            self._on_change()

    def record_response(
        self,
        response_id: str,
        call_ids: list[str],
        read_only: bool = False,
        response_context: dict[str, Any] | None = None,
    ) -> None:
        """Remember issued Responses ids and their function calls for continuation."""
        self.issued_response_calls[response_id] = list(dict.fromkeys(call_ids))
        self.issued_response_read_only[response_id] = read_only
        if isinstance(response_context, dict):
            self.issued_response_contexts[response_id] = copy.deepcopy(response_context)
        else:
            self.issued_response_contexts.pop(response_id, None)
        self.latest_response_id = response_id
        while len(self.issued_response_calls) > 64:
            expired = next(iter(self.issued_response_calls))
            self.issued_response_calls.pop(expired)
            self.issued_response_read_only.pop(expired, None)
            self.issued_response_contexts.pop(expired, None)
        self.last_accessed = time.time()
        if self._on_change is not None:
            self._on_change()

    def allows_response_outputs(self, response_id: str, call_ids: set[str]) -> bool:
        issued = self.issued_response_calls.get(response_id)
        return (
            response_id == self.latest_response_id
            and issued is not None
            and call_ids == set(issued)
        )

    def response_is_read_only(self, response_id: str) -> bool:
        return self.issued_response_read_only.get(response_id, False)

    def response_context(self, response_id: str) -> dict[str, Any] | None:
        """Return an isolated copy of private continuation context, if any."""
        context = self.issued_response_contexts.get(response_id)
        return copy.deepcopy(context) if context is not None else None

    def begin_response_continuation(self, response_id: str) -> str | None:
        """Reserve an issued response id for one in-flight continuation."""
        if response_id not in self.issued_response_calls:
            return None
        if response_id != self.latest_response_id:
            return None
        if response_id in self.consumed_response_ids:
            return None
        if response_id in self.pending_response_ids:
            return None
        reservation = uuid.uuid4().hex
        self.pending_response_ids[response_id] = reservation
        return reservation

    def finish_response_continuation(
        self,
        response_id: str,
        reservation: str,
        success: bool,
    ) -> None:
        """Commit a successful linear continuation, or release a failed one."""
        if self.pending_response_ids.get(response_id) != reservation:
            return
        del self.pending_response_ids[response_id]
        if not success:
            return
        self.consumed_response_ids.append(response_id)
        while len(self.consumed_response_ids) > 64:
            self.consumed_response_ids.pop(0)
        self.last_accessed = time.time()
        if self._on_change is not None:
            self._on_change()

    def complete_response_continuation(
        self,
        parent_response_id: str,
        reservation: str,
        child_response_id: str,
        child_call_ids: list[str],
        child_read_only: bool = False,
        child_response_context: dict[str, Any] | None = None,
    ) -> bool:
        """Atomically record the child response and consume its linear parent."""
        if self.pending_response_ids.get(parent_response_id) != reservation:
            return False
        del self.pending_response_ids[parent_response_id]
        self.consumed_response_ids.append(parent_response_id)
        while len(self.consumed_response_ids) > 64:
            self.consumed_response_ids.pop(0)
        self.issued_response_contexts.pop(parent_response_id, None)
        self.issued_response_calls[child_response_id] = list(
            dict.fromkeys(child_call_ids)
        )
        self.issued_response_read_only[child_response_id] = child_read_only
        if isinstance(child_response_context, dict):
            self.issued_response_contexts[child_response_id] = copy.deepcopy(
                child_response_context
            )
        else:
            self.issued_response_contexts.pop(child_response_id, None)
        self.latest_response_id = child_response_id
        while len(self.issued_response_calls) > 64:
            expired = next(iter(self.issued_response_calls))
            self.issued_response_calls.pop(expired)
            self.issued_response_read_only.pop(expired, None)
            self.issued_response_contexts.pop(expired, None)
        self.last_accessed = time.time()
        if self._on_change is not None:
            self._on_change()
        return True


class PersistentSessionStore:
    def __init__(
        self,
        max_sessions: int = _MAX_SESSIONS,
        persist_path: str | Path | None = None,
        flush_interval: float = _FLUSH_INTERVAL_SECONDS,
        encryption_key_path: str | Path | None = None,
    ):
        self._sessions: OrderedDict[str, PersistentSession] = OrderedDict()
        self._lock = threading.RLock()
        self._max_sessions = max_sessions
        self._persist_path = Path(persist_path) if persist_path else None
        self._cipher = AccountCipher(
            load_or_create_key(encryption_key_path)
            if encryption_key_path is not None
            else None
        )
        self._flush_interval = max(0.0, float(flush_interval))
        self._dirty = False
        self._flush_timer: threading.Timer | None = None
        # Counters for the admin cache panel: `changes` is how many times a
        # session asked to be persisted, `writes` how many disk writes that cost.
        self.changes = 0
        self.writes = 0
        self.last_write_at = 0.0
        if self._persist_path is not None:
            self._load()
        if self._persist_path is not None and self._flush_interval > 0:
            # A coalesced write is still pending when the process exits, so flush
            # there too: that keeps the loss window at "the last flush_interval"
            # instead of "everything since the last timer fired".
            atexit.register(self.flush)

    def _load(self) -> None:
        """Restore sessions from disk so conversations survive container restarts."""
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError, OSError):
            return
        if not isinstance(data, dict):
            return
        for key, s in data.items():
            if not isinstance(s, dict):
                continue
            issued_response_calls = s.get("issued_response_calls", {}) or {}
            if not isinstance(issued_response_calls, dict):
                issued_response_calls = {}
            clean_issued_response_calls = {
                str(response_id): [
                    str(call_id)
                    for call_id in call_ids
                    if isinstance(call_id, str)
                ]
                for response_id, call_ids in issued_response_calls.items()
                if isinstance(response_id, str) and isinstance(call_ids, list)
            }
            issued_response_read_only = s.get("issued_response_read_only", {}) or {}
            if not isinstance(issued_response_read_only, dict):
                issued_response_read_only = {}
            issued_response_contexts = s.get("issued_response_contexts", {}) or {}
            if not isinstance(issued_response_contexts, dict):
                issued_response_contexts = {}
            clean_issued_response_contexts: dict[str, dict[str, Any]] = {}
            for response_id, encrypted_context in issued_response_contexts.items():
                if (
                    not self._cipher.enabled
                    or not self._cipher.is_envelope(encrypted_context)
                    or not isinstance(response_id, str)
                    or response_id not in clean_issued_response_calls
                ):
                    continue
                try:
                    context = self._cipher.decrypt_value(encrypted_context)
                except ValueError:
                    continue
                if isinstance(context, dict):
                    clean_issued_response_contexts[response_id] = context
            consumed_response_ids = s.get("consumed_response_ids", []) or []
            if not isinstance(consumed_response_ids, list):
                consumed_response_ids = []
            latest_response_id = s.get("latest_response_id")
            if latest_response_id not in clean_issued_response_calls:
                latest_response_id = next(reversed(clean_issued_response_calls), None)
            studio_context_id = s.get("studio_context_id")
            if not isinstance(studio_context_id, str):
                studio_context_id = ""
            try:
                session = PersistentSession(
                    conversation_id=s["conversation_id"],
                    client_session_id=s["client_session_id"],
                    turn_count=int(s.get("turn_count", 0)),
                    last_accessed=float(s.get("last_accessed", time.time())),
                    issued_response_calls=clean_issued_response_calls,
                    issued_response_read_only={
                        response_id: value
                        for response_id, value in issued_response_read_only.items()
                        if (
                            isinstance(response_id, str)
                            and response_id in clean_issued_response_calls
                            and isinstance(value, bool)
                        )
                    },
                    issued_response_contexts=clean_issued_response_contexts,
                    consumed_response_ids=[
                        str(response_id)
                        for response_id in consumed_response_ids
                        if isinstance(response_id, str)
                    ][-64:],
                    latest_response_id=latest_response_id,
                    studio_context_id=studio_context_id,
                )
            except (KeyError, TypeError, ValueError):
                continue
            session._on_change = self._save
            self._sessions[key] = session

    def _save(self) -> None:
        """Note that the map changed and get it to disk, coalescing bursts.

        ponytail: with a coalescing window, a hard kill (SIGKILL skips atexit)
        can lose up to `flush_interval` seconds of turn bookkeeping -- at worst a
        conversation resumes a turn behind, or a session created inside the
        window starts over. The alternative was rewriting the entire session map
        on every single turn, so the trade is deliberate; flush_interval=0
        restores write-through for anyone who wants it.
        """
        if self._persist_path is None:
            return
        with self._lock:
            self.changes += 1
            self._dirty = True
            if self._flush_interval > 0:
                self._schedule_flush()
                return
        self.flush()

    def _schedule_flush(self) -> None:
        """Arm the single pending flush timer. Caller holds the lock."""
        if self._flush_timer is not None:
            return
        timer = threading.Timer(self._flush_interval, self._on_flush_timer)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _on_flush_timer(self) -> None:
        with self._lock:
            self._flush_timer = None
        self.flush()

    def flush(self) -> None:
        """Write pending changes now. A no-op when nothing changed."""
        with self._lock:
            timer, self._flush_timer = self._flush_timer, None
            if timer is not None:
                timer.cancel()
            if not self._dirty:
                return
            self._dirty = False
        self._write_now()

    def stats(self) -> dict:
        """Persistence/occupancy counters for the admin cache panel."""
        with self._lock:
            return {
                "sessions": len(self._sessions),
                "max_sessions": self._max_sessions,
                "changes": self.changes,
                "writes": self.writes,
                "coalesced": max(0, self.changes - self.writes),
                "flush_interval": self._flush_interval,
                "pending": self._dirty,
            }

    def _write_now(self) -> None:
        """Atomically write the session map to disk (best-effort)."""
        if self._persist_path is None:
            return
        with self._lock:
            data = {
                key: {
                    "conversation_id": s.conversation_id,
                    "client_session_id": s.client_session_id,
                    "turn_count": s.turn_count,
                    "last_accessed": s.last_accessed,
                    "issued_response_calls": s.issued_response_calls,
                    "issued_response_read_only": s.issued_response_read_only,
                    "issued_response_contexts": self._encrypted_response_contexts(s),
                    "consumed_response_ids": s.consumed_response_ids,
                    "latest_response_id": s.latest_response_id,
                    "studio_context_id": s.studio_context_id,
                }
                for key, s in self._sessions.items()
            }
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self._persist_path)
        except OSError:
            # Persistence is best-effort; never break a request over a disk error.
            # Stay dirty so the next change (or flush) retries instead of dropping
            # everything written since the last successful write.
            with self._lock:
                self._dirty = True
            return
        with self._lock:
            self.writes += 1
            self.last_write_at = time.time()

    def _encrypted_response_contexts(
        self, session: PersistentSession
    ) -> dict[str, Any]:
        """Serialize private Responses context without risking request failure."""
        if not self._cipher.enabled:
            return {}
        encrypted: dict[str, Any] = {}
        for response_id, context in session.issued_response_contexts.items():
            try:
                encrypted[response_id] = self._cipher.encrypt_value(context)
            except (TypeError, ValueError):
                # Context is auxiliary continuation state. If an unexpected
                # non-JSON value slips in, omit it instead of breaking chat.
                continue
        return encrypted

    def _evict_overflow(self) -> None:
        """Drop least-recently-used sessions over the cap. Caller holds the lock."""
        while len(self._sessions) > self._max_sessions:
            evicted, _ = self._sessions.popitem(last=False)
            elog(f"session dropped: {evicted} (LRU, over max_sessions={self._max_sessions})")

    def get(self, key: str) -> PersistentSession:
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = PersistentSession()
                session._on_change = self._save
                self._sessions[key] = session
                # Evict oldest session if over limit
                self._evict_overflow()
                self._save()
            else:
                # Move to end (most recently used)
                self._sessions.move_to_end(key)
                session.last_accessed = time.time()
            return session

    def key_for(self, session: PersistentSession) -> str | None:
        """Return the storage key for an existing session object."""
        with self._lock:
            return next(
                (key for key, candidate in self._sessions.items() if candidate is session),
                None,
            )

    def get_existing(self, key: str) -> PersistentSession | None:
        """Return an existing session without creating an attacker-chosen key."""
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                self._sessions.move_to_end(key)
                session.last_accessed = time.time()
            return session

    def items(self) -> list[tuple[str, PersistentSession]]:
        """Snapshot of (key, session) pairs, newest-used last (LRU order).

        Returned as a list so the management views can iterate without holding
        the store lock while they do slow work (e.g. cloud lookups).
        """
        with self._lock:
            return list(self._sessions.items())

    def remove(self, key: str) -> bool:
        """Forget one session. Returns False when the key was already gone."""
        with self._lock:
            if self._sessions.pop(key, None) is None:
                return False
            self._save()
            elog(f"session dropped: {key} (explicit delete)")
            return True

    def prune(
        self,
        prefix: str = "",
        older_than: float = 0.0,
        keep_newest: int = 0,
        protected: set[str] | None = None,
    ) -> list[str]:
        """Drop sessions under `prefix` by idle age and/or count, keeping the
        newest ones. Returns the removed keys.

        `older_than` (seconds since last use) and `keep_newest` are independent:
        0 disables that rule, so passing neither removes nothing. Keys listed in
        `protected` are never removed -- that is the whitelist a caller uses to
        pin a conversation it still wants. `prefix` is what scopes a cleanup to
        one tenant ("<key_id>:"); empty means every tenant.
        """
        keep = protected or set()
        now = time.time()
        with self._lock:
            scoped = [
                (key, session)
                for key, session in self._sessions.items()
                if key.startswith(prefix) and key not in keep
            ]
            scoped.sort(key=lambda item: item[1].last_accessed, reverse=True)
            removed = [
                key
                for index, (key, session) in enumerate(scoped)
                if (older_than > 0 and now - session.last_accessed > older_than)
                or (keep_newest > 0 and index >= keep_newest)
            ]
            for key in removed:
                del self._sessions[key]
            if removed:
                self._save()
                rules = " ".join(
                    part
                    for part in (
                        f"idle>{older_than:.0f}s" if older_than > 0 else "",
                        f"keep_newest={keep_newest}" if keep_newest > 0 else "",
                    )
                    if part
                )
                # A session that simply vanishes is indistinguishable from a bug
                # after the fact, so every removal path says who went and why:
                # scope, rule, how many were in scope, and the keys themselves.
                elog(
                    f"session prune: dropped {len(removed)}/{len(scoped)} under "
                    f"{prefix or '*'} by [{rules}], {len(keep)} protected: {removed}"
                )
            return removed

    def reset(self, key: str) -> PersistentSession:
        """Discard any existing session under key and start a fresh one.

        Used when the auto-detected conversation key collides (e.g. two different
        conversations that happen to share the same first user message): a new
        conversation's first turn must NOT reuse the previous M365 thread, or the
        model receives stale context and hallucinates. A fresh session gets a new
        conversation_id / client_session_id and turn_count=0.
        """
        with self._lock:
            session = PersistentSession()
            session._on_change = self._save
            self._sessions[key] = session
            self._sessions.move_to_end(key)
            self._evict_overflow()
            self._save()
            return session
