"""Regression — a returning visitor who gives their name IS recognised.

USER REPORT (verbatim, 2026-05-16):
    "I have revisited the page multiple times. Given my name, it has never
     once picked up that I am a returning user. Why?"

ROOT CAUSE (KI-RECALL-FIX, 2026-05-16)
--------------------------------------
Two compounding bugs made the "Welcome back" flow structurally unreachable:

  1. ASYMMETRIC STORE KEY.  `profile_store.save_profile` keyed every file by
     `compute_persona_id(profile)` (hash of name + age + dependents + income
     + location + parents_age) the moment the user gave a name AND any one
     identity fact — which the fact-find asks immediately. So every saved
     file was `<persona_id>.json` and the bare-name slug file `<slug>.json`
     was *deleted* on graduation. `load_profile(name)` on the chat path has
     NO persona_id, the privacy fix (2026-05-16) removed the directory scan,
     so step 2 (slug file) always missed → recall never even staged. Five
     real "Rohit" profiles existed on disk; none recoverable by name.

  2. RECALL WINDOW TOO NARROW.  The name sniff only ran on
     `_current_turn == 1` AND only matched an explicit self-introduction
     ("I'm Priya"). A returning user typically gives their name when
     ANSWERING the bot's "What's your name?" prompt — a bare token
     ("Rohit") on turn 2+ — which the intro regex rejects out of context.

THE FIX
-------
  • `save_profile` ALWAYS also writes a `<slug>.json` recall POINTER to the
    user's most-recent profile under that name (no longer deleted on
    persona-id graduation). `load_profile(name)` resolves it for STAGING
    only — the explicit "are you the same <name>?" confirm gate
    (apply_pending_recall) stays the privacy boundary, so a same-name
    stranger still leaks nothing pre-confirmation.
  • `extract_name_in_context` accepts a bare-name answer when the
    assistant's previous message asked for the name; the sniff runs on any
    turn while the profile has no name (one-shot `_recall_sniff_done`).

These tests pin the real end-to-end path through the live save_profile +
single_brain.handle_turn, NOT a hand-written fixture, so a regression in
the store key or the recall window fails here.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \\
        tests/test_returning_user_recall.py -v
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import single_brain  # noqa: E402
from backend.needs_finder import Profile  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


async def _boom(*_a, **_k):
    """Stub the Gemini boundary so the turn stops right after the recall
    block — STEP 1 short-circuits before this; STEP 2's apply runs before
    it, so the resolved profile is observable on the session afterwards."""
    raise single_brain.SingleBrainError("stubbed: no Gemini in unit test")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._profiles_dir = Path(self._tmp.name) / "profiles"
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        import backend.profile_store as ps
        self._ps = ps
        self._pp = mock.patch.object(ps, "_PROFILES_DIR", self._profiles_dir)
        self._pp.start()
        self._gp = mock.patch.object(single_brain, "_gemini_call", _boom)
        self._gp.start()

    def tearDown(self):
        self._gp.stop()
        self._pp.stop()
        self._tmp.cleanup()

    def _session(self, sid: str | None = None):
        from backend.session_state import SessionState
        return SessionState(session_id=sid or f"s_{uuid.uuid4().hex[:8]}")


class TestStoreKeyAllowsBareNameRecall(_Base):
    """Bug 1 — save graduates to a persona-id file; a returning user with
    NO persona_id (the chat path) must still resolve their own profile."""

    def test_persona_id_save_is_recoverable_by_bare_name(self):
        from backend.profile_store import (
            save_profile, load_profile, compute_persona_id,
        )

        prof = Profile(
            name="Rohit", age="34", dependents="self+spouse+1 kid",
            income_band="10L-25L", location_tier="metro",
            primary_goal="family_protection",
        )
        # The fact-find gives name + identity facts → persona_id derivable.
        pid = compute_persona_id(prof)
        self.assertTrue(pid, "test premise: identity facts → a persona_id")
        self.assertTrue(save_profile("Rohit", prof, session_id="visitA"))

        # Canonical persona-id file AND the bare-name recall pointer exist.
        self.assertTrue((self._profiles_dir / f"{pid}.json").exists())
        self.assertTrue(
            (self._profiles_dir / "rohit.json").exists(),
            "REGRESSION: save_profile did not write the <slug>.json recall "
            "pointer — bare-name recall is structurally dead again.",
        )

        # The chat path calls load_profile WITHOUT a persona_id.
        loaded = load_profile("Rohit")
        self.assertIsNotNone(
            loaded,
            "REGRESSION: a returning user's own profile is unrecoverable by "
            "bare name (the exact user-reported bug).",
        )
        self.assertEqual(loaded.age, "34")
        self.assertEqual(loaded.location_tier, "metro")

    def test_pointer_tracks_most_recent_profile(self):
        """Visit 2 (fuller profile) updates the recall pointer."""
        from backend.profile_store import save_profile, load_profile

        save_profile("Rohit", Profile(name="Rohit", age="34"), session_id="v1")
        save_profile(
            "Rohit",
            Profile(name="Rohit", age="34", location_tier="metro",
                    primary_goal="family_protection"),
            session_id="v2",
        )
        loaded = load_profile("Rohit")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.primary_goal, "family_protection")


class TestEndToEndReturningUserRecognised(_Base):
    """The full user journey through the real save + single_brain path."""

    def test_returning_user_gets_welcome_back_and_profile_restored(self):
        from backend.profile_store import save_profile

        # ---- VISIT A: build + persist a named profile (auto_persist path).
        save_profile(
            "Rohit",
            Profile(
                name="Rohit", age="34", dependents="self+spouse+1 kid",
                income_band="10L-25L", location_tier="metro",
                primary_goal="family_protection",
            ),
            session_id="sessA",
        )

        # ---- VISIT B: brand-new session id (sessionStorage cleared).
        sB = self._session("sessB")

        # B-turn 1: greeting → bot would ask for the name (no recall yet).
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sB, "Hi", chat_history=[]))
        self.assertIsNone(sB.pending_profile_recall)
        self.assertIn(sB.profile.name, (None, ""))

        # B-turn 2: bot asked the name; user types JUST "Rohit" (bare token,
        # turn 2 — the case the old turn-1/intro-only gate missed entirely).
        hist = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! What's your name?"},
        ]
        r2 = _run(single_brain.handle_turn(sB, "Rohit", chat_history=hist))
        self.assertEqual(
            r2.intent, "recall_confirm",
            "REGRESSION: a returning user typing their name on turn 2 was "
            "NOT recognised — the user-reported bug.",
        )
        self.assertIn("Rohit", r2.reply_text)
        self.assertIn("yes/no", r2.reply_text.lower())
        # Privacy boundary: nothing merged pre-confirmation, no PII leaked.
        self.assertIsNone(sB.profile.age)
        self.assertIn(sB.profile.name, (None, ""))
        self.assertNotIn("metro", r2.reply_text.lower())
        self.assertNotIn("34", r2.reply_text)
        self.assertIsNotNone(sB.pending_profile_recall)
        self.assertTrue(sB.pending_profile_recall.get("prompted"))

        # B-turn 3: user confirms "yes that's me" → profile restored AND the
        # deterministic returning_user_recalled flag is set on THIS turn's
        # TurnResult (main.py stamps ChatResponse.returning_user_recalled
        # from it → frontend "Welcome back" banner fires). We can't observe
        # the TurnResult here because apply_pending_recall runs BEFORE the
        # stubbed Gemini boundary; assert it directly via a non-stubbed path.
        self._gp.stop()  # let the confirm turn build a real TurnResult
        try:
            async def _no_text(*_a, **_k):
                # Minimal Gemini stub: a clean final text, no tool calls, so
                # handle_turn reaches the final TurnResult deterministically.
                return {"candidates": [{"content": {"parts": [
                    {"text": "Welcome back, Rohit!"}]}}]}
            with mock.patch.object(single_brain, "_gemini_call", _no_text):
                r3 = _run(single_brain.handle_turn(
                    sB, "yes that's me", chat_history=hist))
        finally:
            self._gp.start()
        self.assertTrue(
            getattr(r3, "returning_user_recalled", False),
            "REGRESSION: the confirm turn did not set returning_user_recalled "
            "— main.py's turn-1-only heuristic can't fire on the confirm "
            "turn, so the 'Welcome back' banner stays unreachable.",
        )
        self.assertEqual(sB.profile.name, "Rohit")
        self.assertEqual(sB.profile.age, "34")
        self.assertEqual(sB.profile.dependents, "self+spouse+1 kid")
        self.assertEqual(sB.profile.income_band, "10L-25L")
        self.assertEqual(sB.profile.location_tier, "metro")
        self.assertEqual(sB.profile.primary_goal, "family_protection")
        self.assertIsNone(sB.pending_profile_recall)

    def test_returning_user_intro_phrasing_turn1_still_works(self):
        """The classic 'Hi I'm Rohit' on turn 1 must keep working too."""
        from backend.profile_store import save_profile

        save_profile(
            "Rohit",
            Profile(name="Rohit", age="34", location_tier="metro"),
            session_id="sessA",
        )
        sB = self._session("sessB2")
        r = _run(single_brain.handle_turn(sB, "Hi, I'm Rohit", chat_history=[]))
        self.assertEqual(r.intent, "recall_confirm")
        self.assertIsNotNone(sB.pending_profile_recall)

    def test_one_shot_guard_no_reprompt_after_decline(self):
        """A user who declines the recall is NOT re-prompted every nameless
        turn (one-shot _recall_sniff_done guard)."""
        from backend.profile_store import save_profile
        from backend.session_state import apply_pending_recall

        save_profile(
            "Rohit", Profile(name="Rohit", age="34", location_tier="metro"),
            session_id="sessA",
        )
        sB = self._session("sessB3")
        hist = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "What is your name?"},
        ]
        r2 = _run(single_brain.handle_turn(sB, "Rohit", chat_history=hist))
        self.assertEqual(r2.intent, "recall_confirm")

        # User denies — apply_pending_recall(confirmed=False) discards it.
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sB, "no, I'm new", chat_history=hist))
        self.assertIsNone(sB.pending_profile_recall)
        self.assertTrue(getattr(sB, "_recall_sniff_done", False))

        # A later nameless turn must NOT re-stage / re-prompt the recall.
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sB, "I want family cover", chat_history=hist))
        self.assertIsNone(
            sB.pending_profile_recall,
            "REGRESSION: recall re-prompted after an explicit decline.",
        )


class TestPrivacyBoundaryHeld(_Base):
    """The fix must NOT reopen the cross-identity leak the 2026-05-16 audit
    closed: a fresh visitor stating a common name gets a confirm prompt,
    never an auto-merge, and nothing until they explicitly affirm."""

    def test_same_name_stranger_must_confirm_and_leaks_nothing(self):
        from backend.profile_store import save_profile

        # A "Rohit" exists on disk (metro, 52). A DIFFERENT person also
        # named Rohit arrives fresh.
        save_profile(
            "Rohit",
            Profile(name="Rohit", age="52", location_tier="tier3"),
            session_id="ownerA",
        )
        sB = self._session("strangerB")
        hist = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi! May I have your name?"},
        ]
        r = _run(single_brain.handle_turn(sB, "Rohit", chat_history=hist))
        # Confirm prompt only — NOT an auto-merge, NO stored PII surfaced.
        self.assertEqual(r.intent, "recall_confirm")
        self.assertIsNone(sB.profile.age)
        self.assertIsNone(sB.profile.location_tier)
        self.assertNotIn("52", r.reply_text)
        self.assertNotIn("tier3", r.reply_text.lower())
        # The different person says "no" → blank session, fail closed.
        with self.assertRaises(single_brain.SingleBrainError):
            _run(single_brain.handle_turn(sB, "no, different person",
                                          chat_history=hist))
        self.assertIsNone(sB.pending_profile_recall)
        self.assertIsNone(sB.profile.age)
        self.assertIn(sB.profile.name, (None, ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
