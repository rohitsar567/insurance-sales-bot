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

from typing import Any, Dict

from backend.needs_finder import Profile, record_answer

_log = logging.getLogger(__name__)


@dataclass
class SessionState:
    session_id: str
    profile: Profile = field(default_factory=Profile)
    awaiting_question_id: Optional[str] = None  # if set, next user message answers this
    free_form_session: bool = False              # user explicitly opted out of fact-find
    last_touched: float = field(default_factory=time.time)
    # KI-196 (ADR-041) — confirmation-gated profile recall. When a fresh
    # session captures a name that matches an on-disk profile, the recall
    # is staged here (NOT auto-merged) and surfaced to the sales_brain as a
    # one-shot "welcome back" prompt. Affirm → merge stored fields into
    # `profile`. Negate → discard. Shape:
    #   {
    #     "name": "Rohit Sarma",
    #     "summary": {age, dependents, location_tier, primary_goal, ...},
    #     "captured_this_turn": {<field>: <value>, ...},  # don't re-extract
    #     "staged_at": <epoch-seconds>,
    #   }
    pending_profile_recall: Optional[Dict[str, Any]] = None
    # KI-224 — most-recent recommendation policy_ids the brain cited on the
    # last user-visible recommendation/comparison turn. Populated by the
    # orchestrator after a clean closer reply. Lets the NEXT turn route
    # follow-ups like "tell me more about #2" without re-retrieving from
    # scratch. Empty list = no active shortlist on this session.
    last_recommendation_ids: list = field(default_factory=list)
    # X7 (admin Recommendation History — conversation_turn column).
    # Monotonically incremented at the START of every orchestrator.handle_turn
    # and single_brain.handle_turn call so the policy-event writer can stamp
    # `turn_idx` on each event dict. Frontend renders this as the
    # "Conversation turn" column in the admin Recommendation History panel
    # (previously showed "—" because no caller populated the field).
    turn_idx: int = 0
    # Z2 fix — Issue 3 (brain election bouncing). Priya's session hopped
    # sales_brain → single_brain → single_brain → sales_brain across 6
    # turns even though USE_SINGLE_BRAIN=true; the Gemini 503 fallback
    # (Z1 retry already softens) was dropping the session back onto the
    # legacy orchestrator mid-stream. Belt-and-suspenders: main.py stamps
    # this True after the FIRST successful single_brain turn, and
    # subsequent SingleBrainError responses on the same session must NOT
    # fall through to the orchestrator — they emit a graceful retry
    # prompt so the session stays sticky on single_brain.
    single_brain_sticky: bool = False

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


# Identity-summary fields surfaced in the "are you <name>?" confirm prompt.
# Enough to let the real owner recognise their own profile, but it is NOT
# applied to the live session until the user explicitly confirms.
_RECALL_SUMMARY_FIELDS: tuple[str, ...] = (
    "age", "dependents", "income_band", "location_tier",
    "primary_goal", "parents_age_max",
)


def rehydrate_by_name(session: SessionState, name: str) -> bool:
    """Cross-session re-entry point — STAGE a name match for confirmation.

    PRIVACY FIX (2026-05-16, audit). Previously this AUTO-MERGED the stored
    named profile into the live session on the very first turn. Because the
    lookup key was the user-stated NAME (not the session / no cookie), a
    second real user on a shared browser/IP — or anyone who simply states a
    common first name — was silently served a stranger's captured profile
    and greeted "Welcome back, <name>!". A fresh, no-cookie session must
    NEVER inherit another session's profile from a weak/shared key.

    Safe design (KI-196 / ADR-041, specced via `pending_profile_recall` but
    previously never wired): a name match is STAGED on
    `session.pending_profile_recall`, NOT merged. `session.profile` is left
    untouched, so `is_returning_user` / RULE-4 "Welcome back" does NOT fire
    on a fresh session. The brain asks the user to confirm ("are you
    <name>?"); only an explicit affirmation calls `apply_pending_recall(
    session, confirmed=True)` to merge the stored fields. An explicit deny
    discards the staged profile.

    Returns:
        False  — always. The stored profile is NEVER auto-applied here, so
                 callers must treat False as "do not flag a returning user
                 / do not greet Welcome back". Whether a match was *staged*
                 is observable via `session.pending_profile_recall`.

    Failures are logged but never raise — a fresh chat must always proceed.
    """
    if not name or not name.strip():
        return False
    try:
        from backend.profile_store import load_profile
        stored = load_profile(name)
        if stored is None:
            return False

        # Build a non-PII-leaking identity summary so the brain can ask
        # "are you <name>?" without putting anything on the live profile.
        summary: Dict[str, Any] = {}
        for fld in _RECALL_SUMMARY_FIELDS:
            v = getattr(stored, fld, None)
            if v not in (None, "", []):
                summary[fld] = v

        # Snapshot the full stored union so apply_pending_recall can merge
        # WITHOUT a second disk read (and without the staged copy being
        # mutated by anything between stage and confirm).
        staged_fields: Dict[str, Any] = {}
        for fld in Profile.__dataclass_fields__.keys():
            v = getattr(stored, fld, None)
            if v not in (None, "", []):
                staged_fields[fld] = v

        session.pending_profile_recall = {
            "name": (getattr(stored, "name", None) or name).strip(),
            "summary": summary,
            "stored_fields": staged_fields,
            "staged_at": time.time(),
        }
        session.last_touched = time.time()
        # Deliberately False: nothing merged, no Welcome-back greeting.
        return False
    except Exception as e:
        _log.warning(
            "rehydrate_by_name failed (name=%r): %s: %s",
            name, type(e).__name__, str(e)[:200],
        )
        return False


def apply_pending_recall(session: SessionState, *, confirmed: bool) -> bool:
    """Resolve a staged cross-session profile recall.

    PRIVACY FIX (2026-05-16). The ONLY path that merges a stored, name-keyed
    profile into a live session. Called after the user explicitly answers
    the "are you <name>?" confirm prompt.

    confirmed=True  — the user affirmed it's them. Stored fields fill any
                      EMPTY slot on the live profile (already-captured slots
                      from THIS conversation win — the user may have given
                      fresher facts). Returns True iff a profile was applied.
    confirmed=False — the user denied / it isn't them. The staged recall is
                      discarded. The live session stays blank. Returns False.

    Idempotent: clears `pending_profile_recall` either way. A no-op (returns
    False) when there is nothing staged.
    """
    pending = getattr(session, "pending_profile_recall", None)
    if not pending:
        return False
    # Resolve the staging regardless of outcome.
    session.pending_profile_recall = None
    if not confirmed:
        return False
    stored_fields = pending.get("stored_fields") or {}
    if not stored_fields:
        return False
    for fld, new in stored_fields.items():
        try:
            if fld not in Profile.__dataclass_fields__:
                continue
            cur = getattr(session.profile, fld, None)
            if cur in (None, "", []) and new not in (None, "", []):
                setattr(session.profile, fld, new)
        except Exception:
            continue
    session.last_touched = time.time()
    return True


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


def clear_session(session_id: str) -> bool:
    """KI-196 (ADR-041) — Wipe in-memory state for one session_id WITHOUT
    touching any on-disk profile JSON under `40-data/profiles/`.

    Semantically identical to `reset_session` today (both just evict the
    in-memory entry; the disk profile has always been independent and lives
    by persona_id / name slug, not session_id). Kept as a distinct symbol so
    the call-site intent at `POST /api/session/clear` is self-documenting and
    so future divergence (e.g. partial-state wipes) doesn't require touching
    the legacy KI-020 caller.

    Returns True iff a live in-memory session was evicted.
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
