"""Per-session state for multi-turn fact-find continuity, persisted to disk.

The orchestrator was originally stateless — each user turn re-classified
intent from scratch. That broke fact-find: after the bot asked "what's
your age?", the user's "39 years old" wasn't matched by intent_classifier
and got routed to RAG retrieval (which then refused). This module fixes that.

Persistence model (changed 2026-05-14):
  - In-memory dict for hot reads (avoids hitting disk every turn).
  - JSON file per session at 40-data/sessions/<session_id>.json — survives
    Space restarts so a user returning after HF hibernation finds their
    profile intact.
  - Loaded lazily on first get_session(); flushed on every state mutation.
  - Same 1h idle TTL applies but ONLY garbage-collects the in-memory cache.
    The on-disk file lives until session_state.purge_old_files() runs (called
    daily by a cron, or on startup). 30-day disk TTL.

The previous architecture (in-memory only) was lost on every Space cold-
start. With HF Spaces hibernating after ~30 min of idleness, every returning
user was getting a fresh blank profile.

Public API:
    get_session(session_id) -> SessionState
    SessionState.profile, .asked, .awaiting (question id pending answer)
    SessionState.set_awaiting(qid)
    SessionState.record_answer(qid, raw_answer) → also clears awaiting + flushes
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from typing import Optional

from backend.needs_finder import Profile, record_answer

# On-disk storage root. Created on first write.
_DATA_ROOT = Path(__file__).resolve().parent.parent / "40-data" / "sessions"


@dataclass
class SessionState:
    session_id: str
    profile: Profile = field(default_factory=Profile)
    awaiting_question_id: Optional[str] = None  # if set, next user message answers this
    free_form_session: bool = False              # user explicitly opted out of fact-find
    last_touched: float = field(default_factory=time.time)

    def _flush(self) -> None:
        """Atomic write to 40-data/sessions/<id>.json so a restart doesn't lose state."""
        try:
            _DATA_ROOT.mkdir(parents=True, exist_ok=True)
            target = _DATA_ROOT / f"{self.session_id}.json"
            payload = {
                "session_id": self.session_id,
                "profile": asdict(self.profile),
                "awaiting_question_id": self.awaiting_question_id,
                "free_form_session": self.free_form_session,
                "last_touched": self.last_touched,
            }
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(target)
        except Exception as e:
            # KI-002 — Log silent failures so HF Space logs reveal them.
            # Don't crash the request — disk hiccups shouldn't kill chat.
            import logging
            logging.warning(
                "session_state flush failed for %s: %s: %s",
                self.session_id, type(e).__name__, str(e)[:200],
            )

    def set_awaiting(self, question_id: Optional[str]) -> None:
        self.awaiting_question_id = question_id
        self.last_touched = time.time()
        self._flush()

    def record_user_answer(self, raw_answer: str) -> Optional[str]:
        """If we're awaiting an answer, parse + store it. Returns the answered question_id."""
        if not self.awaiting_question_id:
            return None
        qid = self.awaiting_question_id
        record_answer(self.profile, qid, raw_answer)
        self.awaiting_question_id = None
        self.last_touched = time.time()
        self._flush()
        return qid

    def update_profile_field(self, name: str, value) -> None:
        """Set a Profile attribute + flush. Used by /api/profile."""
        if hasattr(self.profile, name):
            setattr(self.profile, name, value)
            self.last_touched = time.time()
            self._flush()


def _load_from_disk(session_id: str) -> Optional[SessionState]:
    """Rehydrate from 40-data/sessions/<id>.json if it exists."""
    target = _DATA_ROOT / f"{session_id}.json"
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text())
        raw_profile_dict = raw.get("profile", {}) or {}
        # Profile may have new fields added since this file was written —
        # filter to only what the current Profile dataclass accepts so we
        # never crash on schema drift.
        valid_fields = {f for f in Profile.__dataclass_fields__.keys()}
        dropped = set(raw_profile_dict.keys()) - valid_fields
        if dropped:
            # KI-095 — log schema-drift drops so silent data loss is visible.
            import logging
            logging.warning(
                "session_state load_from_disk dropped %d unknown profile keys for %s: %s",
                len(dropped), session_id, sorted(dropped),
            )
        prof_dict = {k: v for k, v in raw_profile_dict.items() if k in valid_fields}
        return SessionState(
            session_id=raw["session_id"],
            profile=Profile(**prof_dict),
            awaiting_question_id=raw.get("awaiting_question_id"),
            free_form_session=bool(raw.get("free_form_session", False)),
            last_touched=float(raw.get("last_touched", time.time())),
        )
    except Exception as e:
        # KI-003 — Log schema-drift / corrupt-JSON failures. The user will
        # get a fresh session either way, but the log lets us detect when
        # the Profile dataclass evolves in a way that breaks old sessions.
        import logging
        logging.warning(
            "session_state load_from_disk failed for %s: %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )
        return None


_sessions: dict[str, SessionState] = {}
_lock = Lock()
_TTL_SECONDS = 60 * 60         # 1h idle → evict from in-memory cache (still on disk)
_DISK_TTL_SECONDS = 30 * 86400  # 30 days → delete the JSON file


def get_session(session_id: str) -> SessionState:
    with _lock:
        now = time.time()
        # Evict idle entries from the hot cache (file on disk survives)
        to_kill = [k for k, v in _sessions.items() if now - v.last_touched > _TTL_SECONDS]
        for k in to_kill:
            del _sessions[k]
        if session_id in _sessions:
            return _sessions[session_id]
        # Try disk first — survives Space restarts
        rehydrated = _load_from_disk(session_id)
        if rehydrated is None:
            rehydrated = SessionState(session_id=session_id)
        _sessions[session_id] = rehydrated
        return _sessions[session_id]


def set_free_form(session_id: str, free_form: bool = True) -> None:
    s = get_session(session_id)
    s.free_form_session = free_form
    s.awaiting_question_id = None
    s.last_touched = time.time()
    s._flush()


def reset_session(session_id: str) -> bool:
    """Delete a session — evict from in-memory cache and remove the disk file.
    Returns True if anything was actually deleted.
    KI-020 (2026-05-14) — backs the user-facing "Clear chat / start fresh" toggle."""
    deleted_any = False
    with _lock:
        if session_id in _sessions:
            del _sessions[session_id]
            deleted_any = True
    target = _DATA_ROOT / f"{session_id}.json"
    if target.exists():
        try:
            target.unlink()
            deleted_any = True
        except Exception as e:
            import logging
            logging.warning(
                "reset_session unlink failed for %s: %s: %s",
                session_id, type(e).__name__, str(e)[:200],
            )
    return deleted_any


def purge_old_files() -> int:
    """Delete on-disk session files older than _DISK_TTL_SECONDS. Returns count."""
    if not _DATA_ROOT.exists():
        return 0
    now = time.time()
    purged = 0
    for f in _DATA_ROOT.glob("*.json"):
        try:
            if (now - f.stat().st_mtime) > _DISK_TTL_SECONDS:
                f.unlink()
                purged += 1
        except Exception:
            continue
    return purged
