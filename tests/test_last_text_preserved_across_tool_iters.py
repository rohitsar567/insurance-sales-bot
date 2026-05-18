"""Regression test (2026-05-18) — spoken prose emitted in an EARLY
tool-call iteration must NOT be erased by a LATER iteration that returns
only function calls and no text.

THE BUG (user-reported live symptom):
  "did not speak any of the words after just saying profile now; did not
   present the policies — in speech form."

  After the profile completes the model often emits, on ONE iteration, a
  text part ("Great, your profile is complete!") TOGETHER with a tool
  call (save_profile_field). The next iteration is pure tool calls
  (retrieve_policies + mark_recommendation) with NO text part. If the
  conversation then ends without a further free-text iteration, the final
  reply must still be the prose captured earlier.

ROOT CAUSE:
  single_brain.handle_turn's tool-call loop ended every CASE-B iteration
  with an UNCONDITIONAL `last_text = text`. `text` is "" on a pure
  tool-call iteration, so iteration 2 overwrote the iteration-1 prose with
  "". `reply_text = last_text or _HONEST_EMPTY_REPLY` then surfaced the
  honest-empty fallback (or nothing), and main.py skips TTS when
  reply_text is empty/fallback → the bot went SILENT right after profile
  completion / policy presentation.

THE CONTRACT THIS PINS:
  An iteration that produces NO text part must not destroy prose captured
  in a PRIOR iteration. A non-empty text on any iteration still updates
  last_text (so the MAX_ITERATIONS-with-text safety the original comment
  referred to is preserved); an empty one is a no-op.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest -q \\
        tests/test_last_text_preserved_across_tool_iters.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import brain_tools, single_brain  # noqa: E402
from backend.single_brain import _HONEST_EMPTY_REPLY  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fc_part(name, args):
    return {"functionCall": {"name": name, "args": args}}


def _text_and_fc_payload(text, parts):
    """A single model turn that carries BOTH a text part AND function-call
    parts — the realistic 'Great, your profile is complete!' + a tool call
    shape that triggers the bug."""
    return {"candidates": [{"content": {"parts": [{"text": text}] + parts}}]}


def _tool_payload(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _enriched(pid, name, slug, score, cid, *, grade="A", overall=85):
    return {
        "chunk_id": cid, "policy_id": pid, "policy_name": name,
        "insurer_slug": slug, "doc_type": "policy",
        "source_url": f"https://example.com/{pid}.pdf", "score": score,
        "_grade": grade, "_overall_score": overall,
    }


class _Harness(unittest.TestCase):
    """Drives single_brain.handle_turn for real; only the Gemini and vector
    seams are stubbed (same pattern as test_recommendation_transparency)."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ,
                                    {"GOOGLE_API_KEY": "test-key"})
        self._env.start()
        self._gemini_script: list = []
        self._retrieve_chunks: list = []

        async def _fake_gemini(*_a, **_k):
            if not self._gemini_script:
                return _text_payload("(no more scripted turns)")
            return self._gemini_script.pop(0)

        async def _fake_retrieve(*_a, **_k):
            chunks = list(self._retrieve_chunks)
            sess = _k.get("session")
            if sess is not None:
                sess.last_retrieved_chunks = list(chunks)
                sess.slug_to_insurer = {
                    c["policy_id"]: c["insurer_slug"] for c in chunks
                }
            return {"chunks": chunks, "count": len(chunks)}

        self._gp = mock.patch.object(single_brain, "_gemini_call",
                                     _fake_gemini)
        self._rp = mock.patch.object(brain_tools, "retrieve_policies",
                                     _fake_retrieve)
        self._gp.start()
        self._rp.start()

    def tearDown(self):
        self._rp.stop()
        self._gp.stop()
        self._env.stop()

    def _ready_session(self):
        from backend.session_state import SessionState
        sess = SessionState(session_id=f"t_{uuid.uuid4().hex[:8]}")
        sess.profile.name = "Asha"
        sess.profile.age = 35
        sess.profile.dependents = "self+spouse"
        sess.profile.location_tier = "metro"
        sess.profile.income_band = "10L-25L"
        sess.profile.primary_goal = "first_buy"
        sess.profile.health_conditions = ["none"]
        sess.pricing_bundle_skipped = True
        return sess


class TestEarlyProseSurvivesLaterPureToolIters(_Harness):
    def test_profile_complete_prose_then_pure_tool_iters_is_spoken(self):
        """User's exact symptom: prose comes WITH a tool call in iter 1;
        iters 2-3 are pure tool calls with NO text; the turn then ends.
        reply_text MUST still be the iter-1 prose, NOT _HONEST_EMPTY_REPLY
        (which makes main.py skip TTS → the bot goes silent)."""
        sess = self._ready_session()
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.81, "c1"),
            _enriched("niva-bupa__reassure-3", "ReAssure 3.0",
                      "niva-bupa", 0.74, "c2"),
        ]
        spoken = (
            "Great, your profile is complete! Based on it, here are two "
            "strong options: 1. my:Optima Secure (HDFC ERGO) "
            "2. ReAssure 3.0 (Niva Bupa). Want me to compare them?"
        )
        self._gemini_script = [
            # iter 1 — TEXT (the spoken prose) + a tool call in the SAME turn.
            _text_and_fc_payload(
                spoken,
                [_fc_part("save_profile_field",
                          {"field": "smoker", "value": "no"})]),
            # iter 2 — PURE tool calls, NO text part.
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro family comprehensive"})]),
            # iter 3 — PURE tool calls, NO text part. Loop ends here (CASE A
            # is never reached because the model keeps tool-calling and the
            # script then yields no further text turn before the loop's
            # natural termination on the empty-script sentinel below).
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure",
                               "niva-bupa__reassure-3"]})]),
            # iter 4 — model emits NO text (empty text part). This is the
            # documented near-zero "LLM returned nothing" tail; the correct
            # behaviour is to fall back to the EARLIER spoken prose, not to
            # _HONEST_EMPTY_REPLY.
            _text_payload(""),
        ]

        r = _run(single_brain.handle_turn(sess, "I don't smoke"))

        self.assertNotEqual(
            r.reply_text, _HONEST_EMPTY_REPLY,
            "BUG: an empty later tool-call/empty-text iteration erased the "
            "iter-1 spoken prose, so reply_text became the honest-empty "
            "fallback and main.py would skip TTS (bot goes silent).")
        self.assertNotEqual(
            r.reply_text.strip(), "",
            "reply_text must not be empty — main.py skips TTS on empty text.")
        self.assertIn(
            "your profile is complete", r.reply_text,
            "the spoken prose captured in iter 1 must survive to reply_text "
            "so it is read aloud (TTS).")
        self.assertIn("my:Optima Secure", r.reply_text,
                       "the presented policies must be in the spoken reply.")

    def test_nonempty_later_text_still_wins(self):
        """Guard the original intent: if a LATER iteration DOES emit fresh
        prose, that newer prose is what gets spoken (the fix must not pin
        stale text)."""
        sess = self._ready_session()
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.81, "c1"),
        ]
        self._gemini_script = [
            _text_and_fc_payload(
                "One moment while I pull plans for you...",
                [_fc_part("retrieve_policies", {"query": "metro family"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure"]})]),
            # Final free-text iteration — newer prose must replace the
            # interim "one moment" line.
            _text_payload(
                "Here is my:Optima Secure (HDFC ERGO) — a strong "
                "comprehensive plan for your family."),
        ]
        r = _run(single_brain.handle_turn(sess, "show me options"))
        self.assertIn("Here is my:Optima Secure", r.reply_text)
        self.assertNotIn("One moment while I pull", r.reply_text,
                          "the fresher final prose must win over the interim "
                          "holding line.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
