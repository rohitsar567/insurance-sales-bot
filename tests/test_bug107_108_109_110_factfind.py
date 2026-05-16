"""Bug #107 / #108 / #109 / #110 (2026-05-16) — fact-find / profile
regressions found live by the user.

#107 — On a FACT-FIND / clarifying turn (the assistant is asking for more
       profile info, NOT recommending), the response still carried policy
       citations so the UI rendered a ranked "CITED POLICIES" list directly
       under "Before I recommend… I need more info". A fact-find /
       clarifying turn (profile gate NOT satisfied, or no recommendation
       made) must carry ZERO citations.

#108 — When the user answers SOME but not ALL of the post-recap pricing /
       family-history bundle (SI / budget / co-pay / family history /
       smoker), the bot recommended with the unanswered slot blank. The
       gate must re-ask the still-missing slot(s) ONCE before recommending;
       only proceed once they are filled OR the user explicitly skipped.

#109 — User states a premium budget in chat ("max ₹15,000/yr"); it must be
       persisted to the documented `budget_band` contract field as a
       canonical band so the profile panel can pre-fill it (not stored as
       the raw "15000" string the panel's band→inr switch can't map).

#110 — The fact-find never asked family_medical_history. It must be part
       of the post-recap bundle with the same optional/skip handling, and
       the deterministic gate must re-ask it when unresolved.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest -q \\
        tests/test_bug107_108_109_110_factfind.py
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
from backend.brain_tools import (  # noqa: E402
    _coerce_budget_band,
    _unresolved_pricing_bundle,
    save_profile_field,
)
from backend.needs_finder import Profile  # noqa: E402
from backend.single_brain import (  # noqa: E402
    SYSTEM_PROMPT,
    _user_skipped_pricing_inputs,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fc_part(name, args):
    return {"functionCall": {"name": name, "args": args}}


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _tool_payload(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


def _enriched(pid, name, slug, score, cid, *, grade="B", overall=80):
    return {
        "chunk_id": cid, "policy_id": pid, "policy_name": name,
        "insurer_slug": slug, "doc_type": "policy",
        "source_url": f"https://example.com/{pid}.pdf", "score": score,
        "_grade": grade, "_overall_score": overall,
    }


class _Harness(unittest.TestCase):
    """Drives single_brain.handle_turn for real with Gemini + the vector
    store stubbed. The Bug #107 citation gate, the Bug #108/#110 bundle
    re-ask gate, and the Bug #109 budget coercer all run for real."""

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

        # The vector store is the only thing we hard-stub; the REAL
        # brain_tools.retrieve_policies gate (7-slot profile gate + the Bug
        # #108/#110 post-recap bundle re-ask gate) must run. We wrap the
        # real function so the gate executes for real, but the network /
        # Chroma seam (rag.retrieve) is replaced by canned chunks.
        _real_retrieve_policies = brain_tools.retrieve_policies

        async def _fake_rag_retrieve(*_a, **_k):
            class _C:
                pass
            out = []
            for c in self._retrieve_chunks:
                o = _C()
                for k, v in c.items():
                    setattr(o, k, v)
                o.text = c.get("chunk_text", "")
                out.append(o)
            return out

        async def _fake_retrieve_policies(*a, **k):
            sess = k.get("session")
            with mock.patch("rag.retrieve.retrieve", _fake_rag_retrieve), \
                 mock.patch(
                     "backend.retrieval_filters.filter_pipeline",
                     lambda raw, **_kw: (raw, None)), \
                 mock.patch.object(brain_tools, "_load_policy_facts",
                                   lambda *_x, **_y: {}), \
                 mock.patch.object(brain_tools, "_scorecard_signal",
                                   lambda *_x, **_y: {}):
                res = await _real_retrieve_policies(*a, **k)
            # Mirror the real session bookkeeping the citation builder needs.
            if sess is not None and res.get("chunks"):
                sess.last_retrieved_chunks = list(res["chunks"])
                sess.slug_to_insurer = {
                    c["policy_id"]: c.get("insurer_slug", "")
                    for c in res["chunks"]
                }
            return res

        self._gp = mock.patch.object(single_brain, "_gemini_call",
                                     _fake_gemini)
        self._rp = mock.patch.object(brain_tools, "retrieve_policies",
                                     _fake_retrieve_policies)
        self._gp.start()
        self._rp.start()

    def tearDown(self):
        self._rp.stop()
        self._gp.stop()
        self._env.stop()

    def _fresh_session(self):
        from backend.session_state import SessionState
        return SessionState(session_id=f"t_{uuid.uuid4().hex[:8]}")

    def _required_filled_session(self):
        """7 required slots filled; pricing bundle NOT yet resolved."""
        sess = self._fresh_session()
        sess.profile.name = "Asha"
        sess.profile.age = 35
        sess.profile.dependents = "self+spouse"
        sess.profile.location_tier = "metro"
        sess.profile.income_band = "10L-25L"
        sess.profile.primary_goal = "first_buy"
        sess.profile.health_conditions = ["none"]
        return sess


# ════════════════════════════════════════════════════════════════════════════
# BUG #107 — fact-find / clarifying turns carry NO citations
# ════════════════════════════════════════════════════════════════════════════
class TestBug107NoCitationsOnFactFind(_Harness):
    def test_speculative_retrieve_then_clarifying_question_has_no_cards(self):
        # Profile is INCOMPLETE (name only). The LLM speculatively retrieves
        # then — correctly — asks a clarifying question. The recall dump must
        # NOT flow to the UI as a ranked CITED POLICIES list.
        sess = self._fresh_session()
        sess.profile.name = "Rohit"
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.71, "c1"),
            _enriched("niva-bupa__reassure-3", "ReAssure 3.0",
                      "niva-bupa", 0.66, "c2"),
        ]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "family floater metro"})]),
            _text_payload(
                "Before I recommend, I need a bit more — how old are you, "
                "and who should the cover include?"),
        ]
        r = _run(single_brain.handle_turn(sess, "I need health insurance"))
        self.assertEqual(
            r.citations, [],
            "Bug #107: a clarifying / fact-find turn (profile gate not "
            "satisfied) must carry ZERO policy citations")

    def test_recommendation_turn_with_complete_profile_still_cites(self):
        # Control: a genuine recommendation turn (profile complete, bundle
        # skipped) still attaches the cited cards — the gate must not break
        # the happy path.
        sess = self._required_filled_session()
        sess.pricing_bundle_skipped = True
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.71, "c1", grade="A", overall=85),
        ]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro family 10L"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure"]})]),
            _text_payload("I recommend my:Optima Secure (HDFC ERGO)."),
        ]
        r = _run(single_brain.handle_turn(sess, "show me options"))
        ids = [c["policy_id"] for c in r.citations]
        self.assertEqual(ids, ["hdfc-ergo__optima-secure"],
                         "recommendation turn must still attach cited cards")


# ════════════════════════════════════════════════════════════════════════════
# BUG #108 + #110 — partial pricing/family-history answer → re-ask gate
# ════════════════════════════════════════════════════════════════════════════
class TestBug108PartialAnswerReask(_Harness):
    def test_unresolved_bundle_includes_family_medical_history(self):
        # Bug #110 — family_medical_history is part of the bundle the gate
        # tracks; with nothing pricing-related captured it's unresolved.
        p = Profile()
        sess = self._fresh_session()
        unresolved = _unresolved_pricing_bundle(p, sess)
        self.assertIn("family_medical_history", unresolved)
        self.assertIn("smoker", unresolved)

    def test_explicit_skip_resolves_the_whole_bundle(self):
        p = Profile()
        sess = self._fresh_session()
        sess.pricing_bundle_skipped = True
        self.assertEqual(_unresolved_pricing_bundle(p, sess), [])

    def test_partial_answer_triggers_one_shot_reask_then_proceeds(self):
        # User answered SI + budget + co-pay but NOT smoker / family
        # history. The gate must re-ask (not recommend); the C/F bundle
        # slots come back in the directive.
        sess = self._required_filled_session()
        sess.profile.desired_sum_insured_inr = 1_000_000
        sess.profile.budget_band = "15k_30k"
        sess.profile.copay_pct = 0
        # smoker + family_medical_history still missing.
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.71, "c1"),
        ]
        # The LLM tries to retrieve → the gate intercepts → it then asks the
        # user the re-ask question (no recommendation this turn).
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro family 10L"})]),
            _text_payload(
                "Just two more: any major conditions in your blood family, "
                "and do you smoke?"),
        ]
        r1 = _run(single_brain.handle_turn(sess, "10 lakh, 20k budget, "
                                                 "zero co-pay"))
        # Bug #108: no recommendation cards on the re-ask turn.
        self.assertEqual(r1.citations, [])
        # One-shot guard now set.
        self.assertTrue(sess.pricing_bundle_reasked)

        # Turn 2 — user answers the missing slots, then the recommend
        # proceeds (gate no longer blocks; one-shot already consumed).
        self._gemini_script = [
            _tool_payload([
                _fc_part("save_profile_field",
                         {"field": "family_medical_history",
                          "value": "none"}),
                _fc_part("save_profile_field",
                         {"field": "smoker", "value": "no"}),
            ]),
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro family 10L no PED"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure"]})]),
            _text_payload("I recommend my:Optima Secure (HDFC ERGO)."),
        ]
        r2 = _run(single_brain.handle_turn(
            sess, "no family history, non-smoker"))
        self.assertEqual(sess.profile.smoker, False)
        self.assertEqual(sess.profile.family_medical_history, [])
        self.assertEqual(
            [c["policy_id"] for c in r2.citations],
            ["hdfc-ergo__optima-secure"],
            "after the missing bundle slots are filled the recommendation "
            "proceeds and cites the policy")

    def test_explicit_skip_bypasses_the_reask_gate(self):
        sess = self._required_filled_session()
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.71, "c1", grade="A", overall=85),
        ]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro family"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure"]})]),
            _text_payload("I recommend my:Optima Secure."),
        ]
        # User explicitly skips the pricing inputs → gate is bypassed,
        # recommendation proceeds on the SAME turn.
        r = _run(single_brain.handle_turn(
            sess, "just show me options, you decide"))
        self.assertTrue(sess.pricing_bundle_skipped)
        self.assertEqual([c["policy_id"] for c in r.citations],
                         ["hdfc-ergo__optima-secure"])

    def test_skip_phrase_detector(self):
        self.assertTrue(_user_skipped_pricing_inputs("just show me options"))
        self.assertTrue(_user_skipped_pricing_inputs("you decide"))
        self.assertTrue(_user_skipped_pricing_inputs("skip the rest"))
        self.assertFalse(_user_skipped_pricing_inputs("10 lakh cover"))
        self.assertFalse(_user_skipped_pricing_inputs(""))


# ════════════════════════════════════════════════════════════════════════════
# BUG #110 — family_medical_history is in the fact-find plan
# ════════════════════════════════════════════════════════════════════════════
class TestBug110FamilyHistoryInPlan(unittest.TestCase):
    def test_system_prompt_asks_family_medical_history(self):
        self.assertIn("Family medical history", SYSTEM_PROMPT)
        self.assertIn("family_medical_history", SYSTEM_PROMPT)
        # The partial-answer re-ask rule names the most-dropped slot.
        self.assertIn("FAMILY MEDICAL HISTORY", SYSTEM_PROMPT)

    def test_family_history_is_in_the_bundle_questions(self):
        self.assertIn("family_medical_history",
                      brain_tools._PRICING_BUNDLE_CORE)
        self.assertIn("family_medical_history",
                      brain_tools._PRICING_BUNDLE_QUESTIONS)


# ════════════════════════════════════════════════════════════════════════════
# BUG #109 — budget persists to budget_band as a canonical band
# ════════════════════════════════════════════════════════════════════════════
class TestBug109BudgetBandPersistence(unittest.TestCase):
    def test_numeric_string_maps_to_band(self):
        # The exact live phrasing.
        self.assertEqual(_coerce_budget_band("max ₹15,000/yr"), "15k_30k")
        self.assertEqual(_coerce_budget_band("15000"), "15k_30k")
        self.assertEqual(_coerce_budget_band("around 30k"), "30k_60k")
        self.assertEqual(_coerce_budget_band("12000"), "under_15k")
        self.assertEqual(_coerce_budget_band("1 lakh"), "60k+")

    def test_numeric_value_maps_to_band(self):
        self.assertEqual(_coerce_budget_band(15000), "15k_30k")
        self.assertEqual(_coerce_budget_band(12000), "under_15k")
        self.assertEqual(_coerce_budget_band(45000), "30k_60k")
        self.assertEqual(_coerce_budget_band(80000), "60k+")

    def test_canonical_band_passes_through(self):
        for band in ("under_15k", "15k_30k", "30k_60k", "60k+"):
            self.assertEqual(_coerce_budget_band(band), band)
        # case / space tolerant
        self.assertEqual(_coerce_budget_band("15K_30K"), "15k_30k")

    def test_unparseable_returns_none_not_raw(self):
        self.assertIsNone(_coerce_budget_band("dunno"))
        self.assertIsNone(_coerce_budget_band(""))

    def test_save_profile_field_persists_band_not_raw(self):
        class _S:
            profile = Profile()
        s = _S()
        out = save_profile_field(s, field="budget_band",
                                 value="max ₹15,000/yr")
        self.assertTrue(out["saved"])
        self.assertEqual(out["value"], "15k_30k")
        self.assertEqual(s.profile.budget_band, "15k_30k",
                         "Bug #109: must be the canonical band the profile "
                         "panel can map, not the raw '15000' string")


if __name__ == "__main__":
    unittest.main(verbosity=2)
