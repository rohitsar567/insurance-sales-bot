"""Regression tests — fresh no-cookie session must NOT inherit a stranger's
profile via a weak/shared name key (privacy audit, 2026-05-16).

AUDIT FINDING
-------------
A live test opened a FRESH browser context with zero cookies and was still
greeted "Welcome back, Rahul! … Based on our last conversation…" — the
backend restored a prior on-disk profile into a brand-new session.

ROOT CAUSE
----------
The turn-1 recall path was keyed on the user-STATED NAME, not the session:

    single_brain.handle_turn (turn 1)
      -> profile_persistence.try_recall_by_name(session, name)
      -> session_state.rehydrate_by_name(session, name)
      -> profile_store.load_profile(name)        # <-- name-slug keyed file
         + a directory scan that returned ANY persona-id file whose stored
           display-name matched the slug.

So a second real user on a shared browser/IP — or anyone who simply states
a common first name ("I'm Rahul") — was AUTO-MERGED a stranger's captured
profile and greeted "Welcome back" with no confirmation.

INTENDED SAFE DESIGN (KI-196 / ADR-041, specced but never wired)
----------------------------------------------------------------
`SessionState.pending_profile_recall` — a name match is STAGED, not merged.
The profile is only applied after an EXPLICIT user affirmation. A fresh
session is NEVER silently greeted with a stored stranger profile.

These tests pin:

  1. try_recall_by_name does NOT auto-merge a stored profile into a fresh
     session; it stages it on `session.pending_profile_recall`.
  2. After staging, `session.profile` still has NO recalled fields → the
     "Welcome back" / is_returning_user signal stays False.
  3. An explicit AFFIRM (apply_pending_recall) merges the staged profile —
     legitimate returning-user continuity is preserved.
  4. An explicit DENY discards the staged profile and leaves the session
     blank.
  5. Same-session continuity (slots captured within THIS conversation)
     is untouched — only cross-session NAME recall is gated.
  6. load_profile no longer leaks a stranger's persona-id file via the
     display-name directory scan (cross-identity leak vector removed).

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \\
        tests/test_profile_recall_session_isolation.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.needs_finder import Profile  # noqa: E402


def _write_stored_profile(profiles_dir: Path, slug: str, prof_fields: dict,
                          *, display: str, persona_id: str | None = None):
    """Write a profile JSON the way profile_store.save_profile would."""
    profiles_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{persona_id}.json" if persona_id else f"{slug}.json"
    payload = {
        "name_display": display,
        "name_slug": slug,
        "persona_id": persona_id,
        "profile": {**{f.name: getattr(Profile(), f.name)
                       for f in Profile.__dataclass_fields__.values()},
                     **prof_fields},
        "first_seen": "2026-05-15T00:00:00Z",
        "last_seen": "2026-05-15T00:00:00Z",
        "sessions": ["stranger_sess_1"],
    }
    (profiles_dir / fname).write_text(json.dumps(payload, indent=2, default=str))


class _ProfilesDirMixin(unittest.TestCase):
    """Point profile_store at an isolated temp profiles dir for the test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._profiles_dir = Path(self._tmp.name) / "profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        # profile_store reads module-level _PROFILES_DIR for every path op.
        import backend.profile_store as ps
        self._ps = ps
        self._patch = mock.patch.object(ps, "_PROFILES_DIR", self._profiles_dir)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()


class TestFreshSessionDoesNotInheritStrangerProfile(_ProfilesDirMixin):
    """Core privacy guarantee."""

    def _fresh_session(self):
        from backend.session_state import SessionState
        return SessionState(session_id=f"fresh_{uuid.uuid4().hex[:8]}")

    def test_try_recall_by_name_does_not_auto_merge(self):
        """A stranger named 'Rahul' has a stored profile. A FRESH session
        states 'Rahul' on turn 1. The stored profile must NOT be merged
        into session.profile, and must NOT flag a returning user."""
        _write_stored_profile(
            self._profiles_dir, "rahul",
            {"name": "Rahul", "age": 41, "dependents": "self+spouse+kids",
             "location_tier": "metro", "income_band": "10L-25L"},
            display="Rahul",
        )

        from backend.profile_persistence import try_recall_by_name

        sess = self._fresh_session()
        result = try_recall_by_name(sess, "Rahul")

        # NOT auto-applied → caller must not treat as returning user.
        self.assertFalse(
            result,
            "REGRESSION (privacy): try_recall_by_name auto-merged a stored "
            "stranger profile into a fresh session. It must stage, not merge.",
        )
        # session.profile must still be blank — no leaked PII.
        self.assertIsNone(sess.profile.age)
        self.assertIsNone(sess.profile.dependents)
        self.assertIsNone(sess.profile.location_tier)
        self.assertIn(
            sess.profile.name, (None, ""),
            "REGRESSION (privacy): stranger name leaked onto fresh session.",
        )

    def test_match_is_staged_for_confirmation(self):
        """The match IS detected — it's staged on pending_profile_recall so
        the brain can ASK 'are you Rahul?' rather than silently greet."""
        _write_stored_profile(
            self._profiles_dir, "rahul",
            {"name": "Rahul", "age": 41, "location_tier": "metro"},
            display="Rahul",
        )
        from backend.profile_persistence import try_recall_by_name

        sess = self._fresh_session()
        try_recall_by_name(sess, "Rahul")

        self.assertIsNotNone(
            sess.pending_profile_recall,
            "Expected the name match to be STAGED on pending_profile_recall.",
        )
        self.assertEqual(sess.pending_profile_recall["name"], "Rahul")
        # Staged summary carries identity hints for the confirm prompt, but
        # they are NOT on the live profile.
        self.assertIn("summary", sess.pending_profile_recall)
        self.assertIsNone(sess.profile.age)

    def test_no_match_stages_nothing(self):
        """Distinct identity with no stored file → no staging, no leak
        (mirrors the Zenobia control from the audit)."""
        from backend.profile_persistence import try_recall_by_name

        sess = self._fresh_session()
        result = try_recall_by_name(sess, "Zenobia")
        self.assertFalse(result)
        self.assertIsNone(sess.pending_profile_recall)
        self.assertIsNone(sess.profile.age)


class TestExplicitConfirmationFlow(_ProfilesDirMixin):
    """Legitimate returning-user continuity must still work — on opt-in."""

    def test_affirm_applies_staged_profile(self):
        _write_stored_profile(
            self._profiles_dir, "priya",
            {"name": "Priya", "age": 33, "dependents": "self+spouse",
             "location_tier": "tier1"},
            display="Priya",
        )
        from backend.profile_persistence import try_recall_by_name
        from backend.session_state import SessionState, apply_pending_recall

        sess = SessionState(session_id="ret_user_1")
        try_recall_by_name(sess, "Priya")
        self.assertIsNotNone(sess.pending_profile_recall)

        # User explicitly confirms "yes, that's me".
        applied = apply_pending_recall(sess, confirmed=True)
        self.assertTrue(applied)
        self.assertEqual(sess.profile.name, "Priya")
        self.assertEqual(sess.profile.age, 33)
        self.assertEqual(sess.profile.dependents, "self+spouse")
        self.assertEqual(sess.profile.location_tier, "tier1")
        # Staging cleared after apply.
        self.assertIsNone(sess.pending_profile_recall)

    def test_deny_discards_staged_profile(self):
        _write_stored_profile(
            self._profiles_dir, "rahul",
            {"name": "Rahul", "age": 41, "location_tier": "metro"},
            display="Rahul",
        )
        from backend.profile_persistence import try_recall_by_name
        from backend.session_state import SessionState, apply_pending_recall

        sess = SessionState(session_id="not_rahul_1")
        try_recall_by_name(sess, "Rahul")
        self.assertIsNotNone(sess.pending_profile_recall)

        # User says "no, I'm not Rahul" (or just continues with new facts).
        applied = apply_pending_recall(sess, confirmed=False)
        self.assertFalse(applied)
        self.assertIsNone(sess.pending_profile_recall)
        self.assertIsNone(sess.profile.age)
        self.assertIn(sess.profile.name, (None, ""))


class TestSameSessionContinuityPreserved(_ProfilesDirMixin):
    """Slots captured within THIS conversation must never be gated/lost."""

    def test_in_conversation_capture_untouched(self):
        from backend.session_state import get_session, reset_session

        sid = f"same_sess_{uuid.uuid4().hex[:8]}"
        reset_session(sid)
        sess = get_session(sid)
        # Simulate brain_tools.save_profile_field within this conversation.
        sess.profile.name = "Anjali"
        sess.profile.age = 29
        sess.profile.location_tier = "tier2"

        # Same-session continuity is in-memory and must persist verbatim.
        again = get_session(sid)
        self.assertIs(again, sess)
        self.assertEqual(again.profile.name, "Anjali")
        self.assertEqual(again.profile.age, 29)
        self.assertEqual(again.profile.location_tier, "tier2")
        self.assertIsNone(again.pending_profile_recall)
        reset_session(sid)


class TestLoadProfileNoCrossIdentityScan(_ProfilesDirMixin):
    """The display-name directory scan in load_profile was a pure leak
    vector — a fresh visitor stating a common first name pulled a random
    persona-id file. It must no longer cross identities."""

    def test_persona_id_file_not_returned_by_bare_name(self):
        # Stranger stored under a persona-id filename (different age/city),
        # NOT under the bare name slug.
        _write_stored_profile(
            self._profiles_dir, "rohit",
            {"name": "Rohit", "age": 52, "location_tier": "tier3"},
            display="Rohit", persona_id="deadbeef1234",
        )
        # No rohit.json (bare slug) exists.
        self.assertFalse((self._profiles_dir / "rohit.json").exists())

        loaded = self._ps.load_profile("Rohit")  # no persona_id supplied
        self.assertIsNone(
            loaded,
            "REGRESSION (privacy): load_profile returned a stranger's "
            "persona-id-keyed profile via the display-name directory scan. "
            "Bare-name lookup must NOT cross persona identities.",
        )

    def test_exact_slug_file_still_loads(self):
        """Legitimate same-name slug file (the user's own) still resolves —
        we only removed the cross-persona scan, not the direct slug path."""
        _write_stored_profile(
            self._profiles_dir, "meera",
            {"name": "Meera", "age": 38},
            display="Meera",
        )
        loaded = self._ps.load_profile("Meera")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.age, 38)


if __name__ == "__main__":
    unittest.main(verbosity=2)
