"""Regression test for bug #45 (2026-05-19) — the REAL cause of the
recurring "returning user not recognised" complaint.

`profile_persistence.auto_persist_session()` (saves the named profile to
disk + Chroma) was ORPHANED by the orchestrator→single-LLM rewrite:
nothing on the chat path called it, so a user who completed fact-find
PURELY BY CHAT was never persisted to the named store (save_profile()
only ran from the POST /api/profile builder UI). The recall LOOKUP fix
(#25) was correct but its verification used a pre-existing rohit.json,
masking this write-side gap. A live audit with a brand-new chat-only
"Auditkumar Verma" then a fresh same-name session got NO recall —
because nothing wrote auditkumar*.json.

Fix: single_brain.handle_turn now awaits auto_persist_session(session)
at end-of-turn. These tests pin the write-side end-to-end and FAIL on
the pre-fix code.
"""
import asyncio
import os
import random
import string
import unittest
from unittest import mock

from backend import single_brain
from backend.session_state import SessionState
from backend.profile_store import load_profile, _normalise_name, _PROFILES_DIR


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class TestBug45ChatProfilePersistence(unittest.TestCase):
    def setUp(self):
        self.first = "Persisttester" + "".join(
            random.choices(string.ascii_lowercase, k=7))
        self.slug = _normalise_name(self.first)
        self._env = mock.patch.dict(
            os.environ, {"GOOGLE_API_KEY": "test-key"})
        self._env.start()

        async def _fake_gemini(*_a, **_k):
            return _text_payload("Thanks — noted.")

        self._gp = mock.patch.object(
            single_brain, "_gemini_call", _fake_gemini)
        self._gp.start()

    def tearDown(self):
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

    def _completed_profile_session(self):
        """A session whose profile the LLM has fully captured by chat
        (exactly the end state of a normal conversational fact-find)."""
        s = SessionState(session_id=f"b45_{self.first.lower()}")
        p = s.profile
        p.name = self.first
        p.age = 38
        p.dependents = "self+spouse+1 kid"
        p.location_tier = "metro"
        p.income_band = "25L+"
        p.primary_goal = "first_buy"
        p.health_conditions = ["none"]
        return s

    def test_chat_turn_persists_named_profile_to_disk(self):
        """The exact #45 gap: a chat-only completed profile must be
        written to the named store at end-of-turn so a return visit can
        recall it. Pre-fix: auto_persist_session was never called →
        load_profile() is None."""
        self.assertIsNone(load_profile(self.first),
                          "fixture leaked a pre-existing profile")
        sess = self._completed_profile_session()
        _run(single_brain.handle_turn(sess, "yes that's all correct"))
        stored = load_profile(self.first)
        self.assertIsNotNone(
            stored,
            "#45: chat fact-find did NOT persist the named profile "
            "(auto_persist_session still orphaned)")
        self.assertEqual(getattr(stored, "age", None), 38,
                         "#45: persisted profile is missing captured slots")

    def test_anonymous_turn_does_not_persist(self):
        """No name → never write to disk (KI-118 privacy gate intact)."""
        s = SessionState(session_id="b45_anon")
        s.profile.age = 30
        _run(single_brain.handle_turn(s, "I want a health policy"))
        # nothing with our unique slug should have been created
        self.assertIsNone(load_profile(self.first))

    def test_persist_then_fresh_session_recall_fires(self):
        """End-to-end: chat-only persist (#45 write) THEN a brand-new
        session giving the same name → recall stages (#25 lookup). This
        is the real-user flow the prior pre-seeded verification skipped."""
        sess = self._completed_profile_session()
        _run(single_brain.handle_turn(sess, "yes that's all correct"))
        self.assertIsNotNone(load_profile(self.first), "precondition: "
                             "profile must be persisted by the chat turn")
        # Brand-new session; user states the same name mid-fact-find.
        s2 = SessionState(session_id="b45_return")
        s2.profile.name = f"{self.first} Kumar"   # two-token, like real
        _run(single_brain.handle_turn(s2, "no pre-existing conditions"))
        pr = getattr(s2, "pending_profile_recall", None)
        self.assertTrue(
            pr, "#45+#25: returning user not recalled even though the "
            "chat-only profile was persisted")
        self.assertEqual(pr["name"], self.first)


if __name__ == "__main__":
    unittest.main()
