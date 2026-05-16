"""Regression tests — confirmation-gated cross-session recall in single_brain.

CONTEXT
-------
The privacy fix (2026-05-16) made `session_state.rehydrate_by_name` STAGE a
name match on `session.pending_profile_recall` and ALWAYS return False (it no
longer auto-merges a stranger's profile). That closed the leak but a genuine
returning user had to re-enter everything.

This restores legitimate returning-user continuity SAFELY inside
`single_brain.handle_turn`:

  STEP 1 (turn 1): if a name match is staged AND not yet asked, the brain
          short-circuits with a one-line "Welcome back — are you the same
          <name>? (yes/no)" prompt. NOTHING is merged. Only the name (which
          the user themselves stated) is surfaced — never the stored PII.
  STEP 2 (next turn): the user's answer is classified.
          affirmative → session_state.apply_pending_recall(confirmed=True)
                         (fills empty slots; live-conversation slots win).
          anything else → apply_pending_recall(confirmed=False) (discard,
                          fail closed). Staging is cleared either way so the
                          brain asks ONLY ONCE.

TEST STRATEGY
-------------
STEP 1 short-circuits with NO Gemini call, so we assert directly on its
TurnResult. STEP 2 resolves the staged recall (apply / discard) BEFORE the
Gemini path runs; we mock `single_brain._gemini_call` to raise so the turn
stops right after the recall block, letting us assert the profile / staging
side effects deterministically (independent of any real GOOGLE_API_KEY).

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \\
        tests/test_recall_confirm_flow.py -v
"""

from __future__ import annotations

import asyncio
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
from backend import single_brain  # noqa: E402


def _write_stored_profile(profiles_dir: Path, slug: str, prof_fields: dict,
                          *, display: str, persona_id: str | None = None):
    """Write a profile JSON the way profile_store.save_profile would
    (mirrors tests/test_profile_recall_session_isolation.py)."""
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


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _boom(*_a, **_k):
    """Stand-in for _gemini_call — raises so handle_turn stops right after
    the recall block. STEP 1 never reaches this (it short-circuits); STEP 2
    resolves the staged recall BEFORE this fires, so the profile/staging
    side effects are observable on the session afterwards."""
    raise single_brain.SingleBrainError("stubbed: no Gemini in unit test")


class _RecallTestBase(unittest.TestCase):
    """Isolated temp profiles dir + a stubbed Gemini boundary."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._profiles_dir = Path(self._tmp.name) / "profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        import backend.profile_store as ps
        self._ps = ps
        self._pp = mock.patch.object(ps, "_PROFILES_DIR", self._profiles_dir)
        self._pp.start()
        # Stub the only network boundary so STEP 2 / non-recall turns stop
        # deterministically right after the recall block.
        self._gp = mock.patch.object(single_brain, "_gemini_call", _boom)
        self._gp.start()

    def tearDown(self):
        self._gp.stop()
        self._pp.stop()
        self._tmp.cleanup()

    def _fresh_session(self):
        from backend.session_state import SessionState
        return SessionState(session_id=f"fresh_{uuid.uuid4().hex[:8]}")


class TestClassifyRecallAnswer(unittest.TestCase):
    """Pure yes/no classifier — affirm ONLY on a clear yes; fail closed."""

    def test_affirmatives(self):
        from backend.single_brain import _classify_recall_answer
        for s in ("yes", "Yes", "yep", "yeah", "yup", "haan",
                  "yes, that's me", "Yes that's me!", "correct",
                  "ya that's right", "sure", "ok", "it's me",
                  "yes I'm the same person", "the same"):
            self.assertTrue(
                _classify_recall_answer(s),
                f"expected affirmative for {s!r}",
            )

    def test_negatives_and_ambiguous_fail_closed(self):
        from backend.single_brain import _classify_recall_answer
        for s in ("no", "nope", "nah", "not me", "I'm someone else",
                  "no thanks, I'm new", "", "   ", "no, that's me",
                  "different person", "wrong person",
                  "I want health insurance for my family",
                  "actually I have a question about premiums"):
            self.assertFalse(
                _classify_recall_answer(s),
                f"expected non-affirm (fail closed) for {s!r}",
            )


class TestStep1AsksConfirmationNoAutoMerge(_RecallTestBase):
    """Turn 1: a staged match must produce a one-line confirm prompt and
    must NOT merge the stored profile or leak its PII."""

    def test_turn1_asks_and_does_not_merge(self):
        _write_stored_profile(
            self._profiles_dir, "rahul",
            {"name": "Rahul", "age": 41, "dependents": "self+spouse+kids",
             "location_tier": "metro", "income_band": "10L-25L"},
            display="Rahul",
        )
        sess = self._fresh_session()

        res = _run(single_brain.handle_turn(sess, "Hi, I'm Rahul"))

        # One-line confirmation prompt, short-circuited (no Gemini call —
        # _boom was NOT hit).
        self.assertEqual(res.intent, "recall_confirm")
        self.assertIn("Rahul", res.reply_text)
        self.assertIn("yes/no", res.reply_text.lower())
        self.assertEqual(res.citations, [])

        # NOTHING merged onto the live profile (privacy: no leaked PII).
        self.assertIsNone(sess.profile.age)
        self.assertIsNone(sess.profile.dependents)
        self.assertIsNone(sess.profile.location_tier)
        self.assertIsNone(sess.profile.income_band)
        self.assertIn(sess.profile.name, (None, ""))

        # Match still staged, now marked prompted (ask-once guard armed).
        self.assertIsNotNone(sess.pending_profile_recall)
        self.assertTrue(sess.pending_profile_recall.get("prompted"))

        # The reply must NOT contain stored PII (only the user-stated name).
        self.assertNotIn("41", res.reply_text)
        self.assertNotIn("metro", res.reply_text.lower())
        self.assertNotIn("10L-25L", res.reply_text)

    def test_no_stored_match_no_confirm_prompt(self):
        """No stored file → no staging, no confirm short-circuit; the turn
        falls through to the (stubbed) brain."""
        sess = self._fresh_session()
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sess, "Hi, I'm Zenobia"))
        self.assertIsNone(sess.pending_profile_recall)
        self.assertIn(sess.profile.name, (None, ""))


class TestStep2YesApplies(_RecallTestBase):
    """The next turn's 'yes' applies the staged profile (continuity)."""

    def test_yes_applies_staged_profile(self):
        _write_stored_profile(
            self._profiles_dir, "priya",
            {"name": "Priya", "age": 33, "dependents": "self+spouse",
             "location_tier": "tier1", "income_band": "10L-25L"},
            display="Priya",
        )
        sess = self._fresh_session()

        r1 = _run(single_brain.handle_turn(sess, "Hi I'm Priya"))
        self.assertEqual(r1.intent, "recall_confirm")
        self.assertIsNotNone(sess.pending_profile_recall)

        # Turn 2: user affirms. apply_pending_recall runs BEFORE the stubbed
        # brain → SingleBrainError, with the profile side effect persisted.
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sess, "yes that's me"))

        self.assertEqual(sess.profile.name, "Priya")
        self.assertEqual(sess.profile.age, 33)
        self.assertEqual(sess.profile.dependents, "self+spouse")
        self.assertEqual(sess.profile.location_tier, "tier1")
        self.assertEqual(sess.profile.income_band, "10L-25L")
        # Staging cleared (resolved).
        self.assertIsNone(sess.pending_profile_recall)


class TestStep2NoDiscards(_RecallTestBase):
    """The next turn's 'no' (or anything ambiguous) discards the staged
    profile and continues as a fresh, blank profile (fail closed)."""

    def test_no_discards_staged_profile(self):
        _write_stored_profile(
            self._profiles_dir, "rahul",
            {"name": "Rahul", "age": 41, "location_tier": "metro"},
            display="Rahul",
        )
        sess = self._fresh_session()

        r1 = _run(single_brain.handle_turn(sess, "Hi I'm Rahul"))
        self.assertEqual(r1.intent, "recall_confirm")
        self.assertIsNotNone(sess.pending_profile_recall)

        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sess, "no, I'm a new user"))

        # Nothing merged; staging cleared.
        self.assertIsNone(sess.pending_profile_recall)
        self.assertIsNone(sess.profile.age)
        self.assertIsNone(sess.profile.location_tier)
        self.assertIn(sess.profile.name, (None, ""))

    def test_ambiguous_answer_fails_closed(self):
        """A non-yes/no message (e.g. just asking a question) must DISCARD
        the staged stranger profile — never silently merge it."""
        _write_stored_profile(
            self._profiles_dir, "rahul",
            {"name": "Rahul", "age": 41, "location_tier": "metro"},
            display="Rahul",
        )
        sess = self._fresh_session()
        _run(single_brain.handle_turn(sess, "Hi I'm Rahul"))
        self.assertIsNotNone(sess.pending_profile_recall)

        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(
                sess, "What's the cheapest family floater you have?"))

        self.assertIsNone(sess.pending_profile_recall)
        self.assertIsNone(sess.profile.age)
        self.assertIn(sess.profile.name, (None, ""))


class TestAskOnlyOnce(_RecallTestBase):
    """The confirm prompt must fire exactly ONCE, then resolve."""

    def test_second_turn_is_resolution_not_a_reprompt(self):
        _write_stored_profile(
            self._profiles_dir, "meera",
            {"name": "Meera", "age": 38, "location_tier": "tier2"},
            display="Meera",
        )
        sess = self._fresh_session()

        r1 = _run(single_brain.handle_turn(sess, "Hello, my name is Meera"))
        self.assertEqual(r1.intent, "recall_confirm")
        self.assertTrue(sess.pending_profile_recall.get("prompted"))

        # Turn 2 must NOT be another recall_confirm prompt — it resolves the
        # staging (then proceeds, hitting the stubbed brain).
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sess, "yes"))
        self.assertIsNone(sess.pending_profile_recall)
        self.assertEqual(sess.profile.name, "Meera")
        self.assertEqual(sess.profile.age, 38)

    def test_live_slot_wins_over_stored_on_apply(self):
        """apply_pending_recall fills only EMPTY slots — a value captured in
        THIS conversation must NOT be overwritten by the stored one."""
        _write_stored_profile(
            self._profiles_dir, "arjun",
            {"name": "Arjun", "age": 41, "location_tier": "metro"},
            display="Arjun",
        )
        sess = self._fresh_session()
        _run(single_brain.handle_turn(sess, "Hi I'm Arjun"))
        self.assertIsNotNone(sess.pending_profile_recall)

        # User corrected their age live before confirming continuity.
        sess.profile.age = 29

        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sess, "yes"))

        # Live age preserved; only the EMPTY slot (location_tier) filled.
        self.assertEqual(sess.profile.age, 29)
        self.assertEqual(sess.profile.location_tier, "metro")
        self.assertEqual(sess.profile.name, "Arjun")


class TestSameSessionContinuityUnaffected(_RecallTestBase):
    """A normal in-conversation capture (turn_idx > 1, no staged recall)
    must never trigger the confirm prompt or be gated."""

    def test_no_pending_recall_no_confirm_prompt(self):
        sess = self._fresh_session()
        # Simulate slots captured earlier in THIS conversation.
        sess.profile.name = "Anjali"
        sess.profile.age = 29
        sess.profile.location_tier = "tier2"
        sess.turn_idx = 3  # mid-conversation, not turn 1

        # No pending_profile_recall → no recall_confirm short-circuit; the
        # turn falls through to the stubbed brain.
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sess, "I have diabetes"))

        # In-conversation profile untouched, no staging introduced.
        self.assertEqual(sess.profile.name, "Anjali")
        self.assertEqual(sess.profile.age, 29)
        self.assertEqual(sess.profile.location_tier, "tier2")
        self.assertIsNone(sess.pending_profile_recall)


if __name__ == "__main__":
    unittest.main(verbosity=2)
