"""Per-session state for multi-turn fact-find continuity (in-memory only).

The orchestrator was originally stateless — each user turn re-classified
intent from scratch. That broke fact-find: after the bot asked "what's
your age?", the user's "39 years old" wasn't matched by intent_classifier
and got routed to RAG retrieval (which then refused). This module fixes that.

Persistence model (KI-118, 2026-05-15):
  - In-memory dict ONLY. No disk persistence.
  - Sessions are evicted from memory after `_TTL_SECONDS = 60 * 60` idle.
  - Cross-session memory is name-based: when the user provides a name,
    `rehydrate_by_name(session, name)` pulls the named profile from
    `backend.profile_store.load_profile(name)` (canonical JSON at
    `40-data/profiles/<persona_id>.json`).
  - Anonymous sessions live only in-memory and never leave a trace on disk.

Rationale: insurance shoppers don't multi-session within a browsing window.
Cross-session memory is name-based. The previous disk-write side
(`40-data/sessions/<session_id>.json`) was the root of the Chroma
corruption fought 2026-05-14/15 (profile_anonymous dangling row).

Public API:
    get_session(session_id) -> SessionState
    rehydrate_by_name(session, name) -> bool   # KI-118 cross-session re-entry
    SessionState.profile, .asked, .awaiting (question id pending answer)
    SessionState.set_awaiting(qid)
    SessionState.record_answer(qid, raw_answer) → also clears awaiting
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from backend.needs_finder import Profile, record_answer

_log = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_id: str
    profile: Profile = field(default_factory=Profile)
    awaiting_question_id: Optional[str] = None  # if set, next user message answers this
    free_form_session: bool = False              # user explicitly opted out of fact-find
    last_touched: float = field(default_factory=time.time)

    def _flush(self) -> None:
        """No-op since KI-118 (2026-05-15). Disk persistence was removed; the
        in-memory dict is now the only store. We keep the method so existing
        callers (orchestrator + fact_find_brain + tests) don't have to change
        their write paths.
        """
        return None

    def set_awaiting(self, question_id: Optional[str]) -> None:
        self.awaiting_question_id = question_id
        self.last_touched = time.time()

    def record_user_answer(self, raw_answer: str) -> Optional[str]:
        """If we're awaiting an answer, parse + store it. Returns the answered question_id."""
        if not self.awaiting_question_id:
            return None
        qid = self.awaiting_question_id
        record_answer(self.profile, qid, raw_answer)
        self.awaiting_question_id = None
        self.last_touched = time.time()
        return qid

    def update_profile_field(self, name: str, value) -> None:
        """Set a Profile attribute. Used by /api/profile."""
        if hasattr(self.profile, name):
            setattr(self.profile, name, value)
            self.last_touched = time.time()


_sessions: dict[str, SessionState] = {}
_lock = Lock()
_TTL_SECONDS = 60 * 60         # 1h idle → evict from in-memory cache


def get_session(session_id: str) -> SessionState:
    with _lock:
        now = time.time()
        # Evict idle entries from the hot cache
        to_kill = [k for k, v in _sessions.items() if now - v.last_touched > _TTL_SECONDS]
        for k in to_kill:
            del _sessions[k]
        if session_id in _sessions:
            return _sessions[session_id]
        # KI-118 — no disk lookup; fresh sessions start blank. Cross-session
        # rehydration happens via rehydrate_by_name() when the user provides
        # their name to the fact_find brain.
        _sessions[session_id] = SessionState(session_id=session_id)
        return _sessions[session_id]


def rehydrate_by_name(session: SessionState, name: str) -> bool:
    """KI-118 (2026-05-15) — cross-session re-entry point.

    When a user provides their name (captured in the fact_find brain), look
    up the named profile via `backend.profile_store.load_profile(name)` and
    populate the in-memory session with the stored profile data.

    Returns True if a stored profile was found and merged; False otherwise.
    Failures are logged but never raise — a fresh chat must always proceed.

    Merge semantics: stored fields override current session-state fields IF
    the current field is empty. Already-captured fields on the live session
    win (the user may have corrected themselves mid-conversation).
    """
    if not name or not name.strip():
        return False
    try:
        from backend.profile_store import load_profile
        stored = load_profile(name)
        if stored is None:
            return False
        # Merge: stored value fills any empty slot on the live profile.
        for fld in Profile.__dataclass_fields__.keys():
            try:
                cur = getattr(session.profile, fld, None)
                if cur in (None, "", []):
                    new = getattr(stored, fld, None)
                    if new not in (None, "", []):
                        setattr(session.profile, fld, new)
            except Exception:
                continue
        session.last_touched = time.time()
        return True
    except Exception as e:
        _log.warning(
            "rehydrate_by_name failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )
        return False


def set_free_form(session_id: str, free_form: bool = True) -> None:
    s = get_session(session_id)
    s.free_form_session = free_form
    s.awaiting_question_id = None
    s.last_touched = time.time()


def reset_session(session_id: str) -> bool:
    """Delete a session — evict from in-memory cache.
    Returns True if anything was actually deleted.

    KI-020 (2026-05-14) — backs the user-facing "Clear chat / start fresh" toggle.
    KI-118 (2026-05-15) — no disk file to remove anymore; in-memory eviction
    is the only side effect. Returns True iff the session id was live.
    """
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return True
    return False


def purge_old_files() -> int:
    """KI-118 (2026-05-15) — no-op. Disk persistence was removed; there are
    no files to purge. Kept as a stub so any existing scheduled-task caller
    (cron / startup hook) doesn't crash on attribute miss.
    """
    return 0
