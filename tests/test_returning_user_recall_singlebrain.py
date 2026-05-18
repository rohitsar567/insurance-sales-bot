"""Regression test for the returning-user-by-name recall fix (2026-05-19).

Bug: every visit, the user gave the same name ("Rohit") and the bot NEVER
asked "are you the same Rohit?" / never recalled the stored profile. The
confirmation-gated recall (ADR-041/KI-196) helpers existed and were
unit-tested, but the orchestrator→single-LLM rewrite left them ORPHANED —
nothing on the live path (single_brain.handle_turn) called them, and
`returning_user_recalled` was a hard-coded False. The integration boundary
was exactly what had no test (that's how it shipped).

These tests pin the now-wired chain end-to-end in single_brain:
  turn-1 name sniff → stage (privacy-safe, never auto-merge) → confirm
  prompt injected → explicit "yes" merges + flips returning_user_recalled
  → explicit "no" discards → unknown name is a no-op (no false prompt).
"""
import asyncio
import os
import random
import string
import unittest
import uuid
from unittest import mock

from backend import single_brain
from backend import brain_tools
from backend.session_state import SessionState, apply_pending_recall
from backend.profile_persistence import extract_potential_name, try_recall_by_name
from backend.profile_store import save_profile, _normalise_name, _PROFILES_DIR
from backend.needs_finder import Profile


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class TestAffirmOrDeny(unittest.TestCase):
    """Word-boundary yes/no — must NOT read 'no' inside 'knows'/'now', and
    deny must win ties (privacy fail-closed)."""

    def test_table(self):
        cases = [
            ("yes that is me", True),
            ("yeah, same Rohit", True),
            ("haan bilkul", True),
            ("that is me, carry on", True),
            ("no, I'm a new user", False),
            ("not me, someone else", False),
            ("no, but yes", False),          # deny wins
            ("i don't know", False),         # fail-closed
            ("maybe, who knows", None),      # 'knows' must NOT be 'no'
            ("now what", None),              # 'now' must NOT be 'no'
            ("hmm", None),
            ("", None),
        ]
        for txt, exp in cases:
            self.assertEqual(single_brain._affirm_or_deny(txt), exp, txt)


class _StoredProfileFixture(unittest.TestCase):
    """Creates a deterministic, uniquely-named stored profile so the test
    never couples to the mutable 40-data/profiles/rohit.json."""

    def setUp(self):
        # Alphabetic only — extract_potential_name correctly rejects
        # digit-bearing tokens, so a hex suffix would never be sniffed.
        self.name = "Recalltester" + "".join(
            random.choices(string.ascii_lowercase, k=7))
        self.slug = _normalise_name(self.name)
        p = Profile()
        p.name = self.name
        p.age = 41
        p.dependents = "self+spouse+1 kid"
        p.location_tier = "metro"
        p.income_band = "10L-25L"
        p.primary_goal = "first_buy"
        p.health_conditions = ["none"]
        self.assertTrue(save_profile(self.name, p),
                        "fixture save_profile failed")

    def tearDown(self):
        try:
            for fp in _PROFILES_DIR.glob("*.json"):
                try:
                    import json
                    d = json.loads(fp.read_text())
                except Exception:
                    continue
                if d.get("name_slug") == self.slug or \
                   (d.get("profile") or {}).get("name") == self.name:
                    fp.unlink(missing_ok=True)
        except Exception:
            pass


class TestRecallChain(_StoredProfileFixture):
    def test_stage_inject_merge(self):
        nm = extract_potential_name(f"Hi, I'm {self.name}")
        self.assertTrue(nm and nm.lower().startswith("recalltester"))
        s = SessionState(session_id="rc1")
        try_recall_by_name(s, nm)
        pr = getattr(s, "pending_profile_recall", None)
        self.assertTrue(pr, "name match was not STAGED")
        self.assertEqual(pr["name"], self.name)
        si = single_brain._system_instruction(
            s.profile, pending_recall=pr)["parts"][0]["text"]
        self.assertIn("RETURNING-USER CHECK", si)
        self.assertIn("Welcome back", si)
        self.assertIn(self.name, si)
        # confirm=True merges into empty slots
        self.assertTrue(apply_pending_recall(s, confirmed=True))
        self.assertEqual(s.profile.age, 41)
        self.assertIsNone(getattr(s, "pending_profile_recall", None))

    def test_deny_discards(self):
        s = SessionState(session_id="rc2")
        try_recall_by_name(s, self.name)
        self.assertTrue(s.pending_profile_recall)
        self.assertFalse(apply_pending_recall(s, confirmed=False))
        self.assertIn(getattr(s.profile, "age", None), (None, "", 0))
        self.assertIsNone(s.pending_profile_recall)

    def test_unknown_name_no_stage(self):
        s = SessionState(session_id="rc3")
        try_recall_by_name(s, "Zzqxnobodyhasthisname")
        self.assertIsNone(getattr(s, "pending_profile_recall", None))


class TestHandleTurnIntegration(_StoredProfileFixture):
    """The exact gap that let the bug ship: single_brain.handle_turn
    integration with the recall chain."""

    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ,
                                    {"GOOGLE_API_KEY": "test-key"})
        self._env.start()
        self.sys_prompts = []

        async def _fake_gemini(*_a, **_k):
            self.sys_prompts.append(
                (_k.get("system_instruction") or {})
                .get("parts", [{}])[0].get("text", ""))
            return _text_payload("Welcome back — are you the same person?")

        self._gp = mock.patch.object(single_brain, "_gemini_call",
                                     _fake_gemini)
        self._gp.start()

    def tearDown(self):
        self._gp.stop()
        self._env.stop()
        super().tearDown()

    def test_turn1_stages_and_prompts_then_yes_recalls(self):
        sess = SessionState(session_id=f"hti_{uuid.uuid4().hex[:8]}")
        # Turn 1: user states their (returning) name.
        r1 = _run(single_brain.handle_turn(sess, f"Hi, I'm {self.name}"))
        self.assertTrue(getattr(sess, "pending_profile_recall", None),
                        "turn-1 name sniff did not STAGE the recall")
        self.assertFalse(r1.returning_user_recalled,
                         "must NOT flag recall before the user confirms")
        self.assertIn("RETURNING-USER CHECK", self.sys_prompts[-1],
                      "confirm block not injected into the system prompt")
        # Turn 2: user confirms.
        r2 = _run(single_brain.handle_turn(sess, "yes, that's me"))
        self.assertTrue(r2.returning_user_recalled,
                        "explicit 'yes' must flip returning_user_recalled")
        self.assertEqual(sess.profile.age, 41,
                         "stored profile was not merged on confirm")
        self.assertIsNone(getattr(sess, "pending_profile_recall", None))

    def test_no_keeps_session_blank(self):
        sess = SessionState(session_id=f"hti_{uuid.uuid4().hex[:8]}")
        _run(single_brain.handle_turn(sess, f"Hello, I am {self.name}"))
        self.assertTrue(sess.pending_profile_recall)
        r2 = _run(single_brain.handle_turn(sess, "no, I'm a new user"))
        self.assertFalse(r2.returning_user_recalled)
        self.assertIn(getattr(sess.profile, "age", None), (None, "", 0))
        self.assertIsNone(sess.pending_profile_recall)

    def test_unknown_name_normal_flow(self):
        sess = SessionState(session_id=f"hti_{uuid.uuid4().hex[:8]}")
        r1 = _run(single_brain.handle_turn(
            sess, "Hi, I'm Zzqxnobodyhasthisname"))
        self.assertIsNone(getattr(sess, "pending_profile_recall", None))
        self.assertFalse(r1.returning_user_recalled)
        self.assertNotIn("RETURNING-USER CHECK", self.sys_prompts[-1])


if __name__ == "__main__":
    unittest.main()
