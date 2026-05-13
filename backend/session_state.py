"""Per-session state for multi-turn fact-find continuity.

The orchestrator was originally stateless — each user turn re-classified
intent from scratch. That broke fact-find: after the bot asked "what's
your age?", the user's "39 years old" wasn't matched by intent_classifier
and got routed to RAG retrieval (which then refused). This module fixes that.

State is in-memory (process-local). v2 → Redis for multi-instance.

Public API:
    get_session(session_id) -> SessionState
    SessionState.profile, .asked, .awaiting (question id pending answer)
    SessionState.set_awaiting(qid)
    SessionState.record_answer(qid, raw_answer) → also clears awaiting
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock

from backend.needs_finder import Profile, record_answer


@dataclass
class SessionState:
    session_id: str
    profile: Profile = field(default_factory=Profile)
    awaiting_question_id: str | None = None  # if set, next user message answers this
    free_form_session: bool = False           # user explicitly opted out of fact-find
    last_touched: float = field(default_factory=time.time)

    def set_awaiting(self, question_id: str | None) -> None:
        self.awaiting_question_id = question_id
        self.last_touched = time.time()

    def record_user_answer(self, raw_answer: str) -> str | None:
        """If we're awaiting an answer, parse + store it. Returns the answered question_id."""
        if not self.awaiting_question_id:
            return None
        qid = self.awaiting_question_id
        record_answer(self.profile, qid, raw_answer)
        self.awaiting_question_id = None
        self.last_touched = time.time()
        return qid


_sessions: dict[str, SessionState] = {}
_lock = Lock()
_TTL_SECONDS = 60 * 60  # 1h idle → garbage-collect


def get_session(session_id: str) -> SessionState:
    with _lock:
        now = time.time()
        # Garbage-collect idle sessions
        to_kill = [k for k, v in _sessions.items() if now - v.last_touched > _TTL_SECONDS]
        for k in to_kill:
            del _sessions[k]
        if session_id not in _sessions:
            _sessions[session_id] = SessionState(session_id=session_id)
        return _sessions[session_id]


def set_free_form(session_id: str, free_form: bool = True) -> None:
    s = get_session(session_id)
    s.free_form_session = free_form
    s.awaiting_question_id = None
