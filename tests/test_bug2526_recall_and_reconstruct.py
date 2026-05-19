"""Regression tests for the live bugs #25 and #26 (2026-05-19).

#25 — returning user NEVER recognised: the recall probe was gated to
`_current_turn == 1`, but the fact-find asks for the name in the bot's
FIRST reply, so the name lands on turn >=2 and the probe was skipped
entirely. Compounded by (b) `extract_potential_name` only matching an
"I'm X / my name is X" preamble (a bare "rohit sar" → None) and (c) a
multi-token name slugging to "rohit-sar", missing the stored
"rohit.json". The fix: probe whenever the LLM-captured
`session.profile.name` is first known (any turn), with a first-name
slug fallback, one-shot guarded — STILL privacy-safe (STAGE + explicit
confirm; no auto-merge).

#26 — profile lost mid-conversation: in-memory sessions
(_TTL_SECONDS=1h, KI-118 removed disk persistence) get evicted on an HF
container restart / idle, so get_session() returns a BLANK session and
the bot says "I seem to have lost some of your profile information.
What's your name?". Fix (user-chosen): when the live profile is blank
but the client still carries chat_history, inject STATE-RECOVERY MODE so
the model silently re-captures the facts from history and continues —
never re-asking the name / never admitting a loss.

Each test is written so it FAILS on the pre-fix code (the exact gap
that let the bug ship) and passes only with the fix.
"""
import asyncio
import os
import random
import string
import unittest
import uuid
from unittest import mock

from backend import single_brain
from backend.session_state import SessionState, apply_pending_recall
from backend.profile_persistence import try_recall_by_name  # noqa: F401
from backend.profile_store import save_profile, _normalise_name, _PROFILES_DIR
from backend.needs_finder import Profile


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class _FirstNameStoredFixture(unittest.TestCase):
    """Stores a profile under a SINGLE-token name (like the real
    40-data/profiles/rohit.json) so a later two-token capture
    ("Rohit Sar") must use the first-name slug fallback to resolve it."""

    def setUp(self):
        self.first = "Firstonly" + "".join(
            random.choices(string.ascii_lowercase, k=7))
        self.full = f"{self.first} Lastname"           # what the user types
        self.slug = _normalise_name(self.first)
        p = Profile()
        p.name = self.first
        p.age = 41
        p.dependents = "self+spouse+1 kid"
        p.location_tier = "metro"
        p.income_band = "10L-25L"
        p.primary_goal = "first_buy"
        p.health_conditions = ["none"]
        self.assertTrue(save_profile(self.first, p),
                        "fixture save_profile failed")
        self._env = mock.patch.dict(
            os.environ, {"GOOGLE_API_KEY": "test-key"})
        self._env.start()
        self.sys_prompts = []
        self.si_kwargs = []

        async def _fake_gemini(*_a, **_k):
            self.sys_prompts.append(
                (_k.get("system_instruction") or {})
                .get("parts", [{}])[0].get("text", ""))
            return _text_payload("ok")

        self._gp = mock.patch.object(
            single_brain, "_gemini_call", _fake_gemini)
        self._gp.start()

        # Spy on _system_instruction WITHOUT changing behaviour, to assert
        # the #26 reconstruct flag wiring end-to-end.
        _real_si = single_brain._system_instruction

        def _spy_si(*a, **k):
            self.si_kwargs.append(k)
            return _real_si(*a, **k)

        self._sp = mock.patch.object(
            single_brain, "_system_instruction", _spy_si)
        self._sp.start()

    def tearDown(self):
        self._sp.stop()
        self._gp.stop()
        self._env.stop()
        try:
            import json
            for fp in _PROFILES_DIR.glob("*.json"):
                try:
                    d = json.loads(fp.read_text())
                except Exception:
                    continue
                if d.get("name_slug") == self.slug or \
                   (d.get("profile") or {}).get("name") == self.first:
                    fp.unlink(missing_ok=True)
        except Exception:
            pass


class TestBug25RecallAfterTurn1(_FirstNameStoredFixture):
    def test_name_captured_on_later_turn_stages_recall(self):
        """The exact #25 flow: turn 1 has no name; the LLM captures the
        name (save_profile_field) so by a later turn session.profile.name
        is a TWO-token string. Old code: `elif _current_turn == 1`
        skipped the probe AND the slug missed the stored file → recall
        NEVER fired. Fixed: staged + confirm prompt injected."""
        sess = SessionState(session_id=f"b25_{uuid.uuid4().hex[:8]}")
        # Turn 1 — user states intent only, NO name.
        _run(single_brain.handle_turn(sess, "I want a health policy"))
        self.assertIsNone(getattr(sess, "pending_profile_recall", None),
                           "no name yet → must not stage on turn 1")
        # The LLM captured the name via save_profile_field on turn 2;
        # replicate that exact server state (two-token name).
        sess.profile.name = self.full
        # A normal later fact-find turn (turn 3) — bare answer, NOT a
        # name preamble, NOT turn 1: the precise pre-fix dead zone.
        _run(single_brain.handle_turn(sess, "no pre-existing conditions"))
        pr = getattr(sess, "pending_profile_recall", None)
        self.assertTrue(
            pr, "#25: recall not staged when name known after turn 1")
        self.assertEqual(pr["name"], self.first,
                         "#25: first-name slug fallback did not resolve "
                         "the stored profile")
        self.assertIn("RETURNING-USER CHECK", self.sys_prompts[-1],
                      "#25: confirm block not injected")
        self.assertTrue(getattr(sess, "recall_probe_done", False),
                        "#25: one-shot guard not set")

    def test_explicit_yes_then_merges(self):
        sess = SessionState(session_id=f"b25y_{uuid.uuid4().hex[:8]}")
        sess.profile.name = self.full
        _run(single_brain.handle_turn(sess, "just me"))
        self.assertTrue(sess.pending_profile_recall)
        r2 = _run(single_brain.handle_turn(sess, "yes, that's me"))
        self.assertTrue(r2.returning_user_recalled)
        self.assertEqual(sess.profile.age, 41,
                         "stored profile not merged on explicit yes")

    def test_declined_recall_not_reoffered(self):
        sess = SessionState(session_id=f"b25n_{uuid.uuid4().hex[:8]}")
        sess.profile.name = self.full
        _run(single_brain.handle_turn(sess, "just me"))
        self.assertTrue(sess.pending_profile_recall)
        apply_pending_recall(sess, confirmed=False)
        self.assertTrue(sess.recall_probe_done)
        # A later turn must NOT re-stage the declined recall.
        _run(single_brain.handle_turn(sess, "income 25L+"))
        self.assertIsNone(getattr(sess, "pending_profile_recall", None),
                          "#25: declined recall was re-offered")

    def test_unknown_name_no_false_recall_later_turn(self):
        sess = SessionState(session_id=f"b25u_{uuid.uuid4().hex[:8]}")
        sess.profile.name = "Zzqxnobodyhasthis Lastname"
        _run(single_brain.handle_turn(sess, "no pre-existing conditions"))
        self.assertIsNone(getattr(sess, "pending_profile_recall", None))
        self.assertNotIn("RETURNING-USER CHECK", self.sys_prompts[-1])


class TestBug26ReconstructFromHistory(_FirstNameStoredFixture):
    def _hist(self):
        return [
            {"role": "user", "content": "I want a health policy"},
            {"role": "assistant", "content": "Sure — your name and age?"},
            {"role": "user", "content": "Rohit, 29, Bangalore"},
            {"role": "assistant", "content": "Thanks Rohit. Income band?"},
        ]

    def test_blank_session_with_history_triggers_reconstruction(self):
        """Evicted/blank session BUT the client still carries the
        conversation → STATE-RECOVERY MODE, not "What's your name?"."""
        sess = SessionState(session_id=f"b26_{uuid.uuid4().hex[:8]}")
        _run(single_brain.handle_turn(
            sess, "income 25L+", chat_history=self._hist()))
        self.assertTrue(
            self.si_kwargs[-1].get("reconstruct_from_history"),
            "#26: reconstruction not triggered for blank+history")
        self.assertIn("STATE-RECOVERY MODE", self.sys_prompts[-1])
        self.assertNotIn("lost some of your profile",
                         self.sys_prompts[-1].lower())

    def test_genuine_first_turn_no_reconstruction(self):
        sess = SessionState(session_id=f"b26f_{uuid.uuid4().hex[:8]}")
        _run(single_brain.handle_turn(sess, "I want a health policy"))
        self.assertFalse(
            self.si_kwargs[-1].get("reconstruct_from_history"),
            "#26: false recovery on a genuine first turn")
        self.assertNotIn("STATE-RECOVERY MODE", self.sys_prompts[-1])

    def test_populated_session_no_reconstruction(self):
        sess = SessionState(session_id=f"b26p_{uuid.uuid4().hex[:8]}")
        sess.profile.name = "Asha"
        sess.profile.age = 30
        _run(single_brain.handle_turn(
            sess, "income 25L+", chat_history=self._hist()))
        self.assertFalse(
            self.si_kwargs[-1].get("reconstruct_from_history"),
            "#26: reconstruction wrongly fired on a populated session")


if __name__ == "__main__":
    unittest.main()
