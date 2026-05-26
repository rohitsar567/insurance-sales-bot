"""Per-session state for multi-turn fact-find continuity (in-memory only).

The orchestrator was originally stateless — each user turn re-classified
intent from scratch. That broke fact-find: after the bot asked "what's
your age?", the user's "39 years old" wasn't matched by intent_classifier
and got routed to RAG retrieval (which then refused). This module fixes that.

Persistence model (ADR-043, 2026-05-27 — REMOVAL of cross-session recall):
  - In-memory dict ONLY. No disk persistence anywhere.
  - Sessions are evicted from memory after `_TTL_SECONDS = 60 * 60` idle.
  - There is no cross-session memory. Closing the tab (or letting the
    session go idle for an hour) discards the profile permanently.
  - The previous cross-session recall design (ADR-041 + ADR-042 with
    name-slug pointers under `40-data/profiles/` plus a confirmation
    gate, redacted prompts, match-before-merge guards, two-fact gate
    and same-turn extractors) was removed. The complexity tax was high
    relative to the use case (insurance is a rare-purchase, return
    sessions are uncommon), the privacy surface — name-only key with
    slug-pointer collisions across distinct users — required four
    sequential hardening passes to keep contained, and the recall path
    became a recurring bug source. Minimum-data-retention now matches
    the simpler "stateless advisor" mental model.

Public API:
    get_session(session_id) -> SessionState
    SessionState.profile, .awaiting (question id pending answer)
    SessionState.set_awaiting(qid)
    SessionState.record_user_answer(raw_answer) → also clears awaiting
    SessionState.update_profile_field(name, value)
    reset_session(session_id) / clear_session(session_id) — evict in-memory
    set_free_form(session_id, free_form) — bypass fact-find for this session
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, Optional

from backend.needs_finder import Profile, record_answer

_log = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_id: str
    profile: Profile = field(default_factory=Profile)
    awaiting_question_id: Optional[str] = None  # if set, next user message answers this
    free_form_session: bool = False              # user explicitly opted out of fact-find
    last_touched: float = field(default_factory=time.time)
    # KI-224 — most-recent recommendation policy_ids the brain cited on the
    # last user-visible recommendation/comparison turn. Populated by
    # single_brain after a clean closer reply. Lets the NEXT turn route
    # follow-ups like "tell me more about #2" without re-retrieving from
    # scratch. Empty list = no active shortlist on this session.
    last_recommendation_ids: list = field(default_factory=list)
    # X7 (admin Recommendation History — conversation_turn column).
    # Monotonically incremented at the START of every single_brain.handle_turn
    # call so the policy-event writer can stamp `turn_idx` on each event dict.
    turn_idx: int = 0
    # Set True after the first successful single_brain turn; a later
    # SingleBrainError on the same session then emits a graceful retry
    # prompt instead of switching handlers, so the session stays on
    # single_brain (see ADR-042 retry policy in single_brain._gemini_call).
    single_brain_sticky: bool = False
    # Post-recap pricing & family-history bundle re-ask gate
    # (brain_tools.retrieve_policies):
    #   pricing_bundle_reasked — one-shot guard; set True the first time
    #     the gate re-asks an unresolved bundle slot so the next
    #     recommendation retrieve proceeds even if the user skips.
    #   pricing_bundle_skipped — set True by single_brain when the user
    #     explicitly declines the pricing inputs; bypasses the re-ask.
    pricing_bundle_reasked: bool = False
    pricing_bundle_skipped: bool = False

    def _flush(self) -> None:
        """No-op. Session state lives only in the in-memory dict; the
        method is kept so callers' write paths don't have to change.
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
    """Return the live in-memory SessionState for `session_id`, creating
    a fresh blank one on miss. Idle sessions older than _TTL_SECONDS are
    evicted lazily on every call. Disk is never consulted — see ADR-043.
    """
    with _lock:
        now = time.time()
        # Evict idle entries from the hot cache.
        to_kill = [k for k, v in _sessions.items() if now - v.last_touched > _TTL_SECONDS]
        for k in to_kill:
            del _sessions[k]
        if session_id in _sessions:
            return _sessions[session_id]
        _sessions[session_id] = SessionState(session_id=session_id)
        return _sessions[session_id]


def set_free_form(session_id: str, free_form: bool = True) -> None:
    s = get_session(session_id)
    s.free_form_session = free_form
    s.awaiting_question_id = None
    s.last_touched = time.time()


def reset_session(session_id: str) -> bool:
    """Delete a session — evict from in-memory cache.
    Returns True if anything was actually deleted.

    KI-020 (2026-05-14) — backs the user-facing "Clear chat / start fresh"
    toggle. KI-118 (2026-05-15) removed disk persistence; in-memory
    eviction is the only side effect.
    """
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return True
    return False


def clear_session(session_id: str) -> bool:
    """Wipe in-memory state for one session_id.

    Semantically identical to `reset_session` (both just evict the
    in-memory entry). Kept as a distinct symbol so the call-site intent
    at `POST /api/session/clear` is self-documenting and so future
    divergence (e.g. partial-state wipes) doesn't require touching the
    legacy KI-020 caller.
    """
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            return True
    return False


def purge_old_files() -> int:
    """No-op. Disk persistence was removed in KI-118 (2026-05-15) and
    cross-session profile recall was removed in ADR-043 (2026-05-27).
    Kept as a stub so any existing scheduled-task caller (cron / startup
    hook) doesn't crash on attribute miss.
    """
    return 0
