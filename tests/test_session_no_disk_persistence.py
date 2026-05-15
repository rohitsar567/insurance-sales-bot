"""Regression test for KI-118 (2026-05-15) — session_state has NO disk persistence.

The pre-KI-118 architecture wrote `40-data/sessions/<session_id>.json` on
every state mutation so a Space restart wouldn't lose state. That disk-write
side was:

  1. The root of the Chroma corruption incident (the legacy
     `profile_anonymous` chunk that poisoned every subsequent query).
  2. A privacy and operational liability (orphan files accumulate, schema
     drift hits old files, cross-session memory leak surface).

KI-118 rip-out: session_state is in-memory only. Cross-session memory is
strictly name-based (returning user provides their name → fact_find brain
captures it → `rehydrate_by_name` pulls the named profile from
`40-data/profiles/`).

This test pins the contract: a session lifecycle (get, mutate, set_awaiting,
record_answer, update_profile_field, reset) MUST NOT create ANY file under
`40-data/sessions/`. If a future change reintroduces disk persistence, this
test fires.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest tests/test_session_no_disk_persistence.py -v
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


_SESSIONS_DIR = _REPO_ROOT / "40-data" / "sessions"


def _file_count() -> int:
    if not _SESSIONS_DIR.exists():
        return 0
    return sum(1 for _ in _SESSIONS_DIR.glob("*.json"))


class TestSessionNoDiskPersistence(unittest.TestCase):
    """KI-118 — session lifecycle must not touch 40-data/sessions/."""

    def setUp(self):
        # Capture the pre-test file count so we only assert on the delta.
        # Other tests / contributors may legitimately have files in this
        # dir; we only care that this test's session id doesn't write one.
        self.before_count = _file_count()
        self.session_id = f"ki118_no_disk_{uuid.uuid4().hex[:10]}"

    def tearDown(self):
        # Defensive cleanup: if some other code path DID create our file,
        # remove it so we don't pollute the working dir.
        target = _SESSIONS_DIR / f"{self.session_id}.json"
        if target.exists():
            try:
                target.unlink()
            except OSError:
                pass

    def test_session_lifecycle_creates_no_disk_file(self):
        from backend.session_state import (
            get_session,
            reset_session,
        )

        # 1. get_session → in-memory
        sess = get_session(self.session_id)
        self.assertEqual(sess.session_id, self.session_id)

        # 2. mutate every public path that USED to flush to disk pre-KI-118
        sess.profile.age = 35
        sess.profile.name = "Rohit"
        sess.set_awaiting("dependents")
        sess.update_profile_field("income_band", "10-25L")
        sess.free_form_session = True
        sess._flush()  # legacy callers still invoke this — must be a no-op

        # 3. Reset
        reset_session(self.session_id)

        # 4. Assert: no new file in 40-data/sessions/ AND specifically no
        #    file under our session id.
        target = _SESSIONS_DIR / f"{self.session_id}.json"
        self.assertFalse(
            target.exists(),
            f"REGRESSION (KI-118): session lifecycle created {target}. "
            "session_state.py was refactored to be in-memory only; a disk "
            "write was reintroduced.",
        )

        # The directory may still exist if it was already there pre-test,
        # but our file count delta must be 0.
        after = _file_count()
        self.assertEqual(
            after, self.before_count,
            f"REGRESSION (KI-118): session lifecycle created {after - self.before_count} "
            f"new file(s) under {_SESSIONS_DIR}. Expected zero.",
        )

    def test_flush_is_a_noop(self):
        """SessionState._flush() is kept for backwards-compat with existing
        callers (single_brain + fact_find_brain). It must do nothing — no
        I/O, no exceptions on missing dir, no return value."""
        from backend.session_state import get_session
        sess = get_session(self.session_id)
        sess.profile.age = 35
        # Must not raise even if the parent dir doesn't exist.
        result = sess._flush()
        self.assertIsNone(result, "_flush() must be a no-op returning None")

    def test_purge_old_files_is_noop(self):
        """KI-118 — purge_old_files is a stub now; must return 0 and never
        crash, even if 40-data/sessions/ doesn't exist."""
        from backend.session_state import purge_old_files
        result = purge_old_files()
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
