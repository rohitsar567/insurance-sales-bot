"""Bug #69 + Bug #71 (2026-05-16) — conversation-logic regressions found
live by the user.

Bug #69 — NAME gated last + misleading "no policies match".
  Live transcript: user gave age/city/budget/sum-insured etc.; the advisor
  replied "I couldn't find any policies matching all your criteria. It
  seems I'm missing a few more details to proceed. Could you please tell
  me your name?" then recommended the moment the name was given. Two
  defects:
    1. NAME (a required slot that GATES recommendations) was asked LAST.
       It must be asked EARLY (ideally first).
    2. A missing required slot was surfaced as a retrieval/no-match
       failure — false (name has nothing to do with policy matching) and
       self-defeating.

Bug #71 — weak-grade policies presented as recommendations.
  Live transcript: the advisor surfaced as "recommendations" HDFC ERGO
  my:Optima Secure (B, 75/100) AND Star Family Health Optima (C, 64/100).
  A C-graded 64/100 plan for the user's OWN profile is not a
  recommendation. The recommended set must rank strictly best-first and
  gate to a sensible minimum fit; present fewer (or none) honestly rather
  than pad with a weak plan.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest -q \\
        tests/test_bug69_71_conversation_logic.py
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
from backend.needs_finder import (  # noqa: E402
    Profile,
    _SLOT_ORDER,
    next_question,
)
from backend.single_brain import (  # noqa: E402
    SYSTEM_PROMPT,
    _build_recommendation_citations,
    _recommendation_fit,
    _synthesise_fallback,
)

# Phrases the advisor must NEVER use to surface a merely-missing slot.
_BANNED_NO_MATCH = (
    "couldn't find any policies matching all your criteria",
    "couldnt find any policies matching all your criteria",
    "no policies match",
    "nothing matches your criteria",
    "i'm missing a few more details to proceed",
    "im missing a few more details to proceed",
)


def _assert_not_misleading(case: unittest.TestCase, text: str) -> None:
    low = (text or "").lower()
    for bad in _BANNED_NO_MATCH:
        case.assertNotIn(
            bad, low,
            f"missing-slot turn must not imply a search failed: {text!r}",
        )


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ════════════════════════════════════════════════════════════════════════════
# BUG #69 — deterministic surfaces (slot order + fallback + prompt rules)
# ════════════════════════════════════════════════════════════════════════════
class TestBug69NameFirstDeterministic(unittest.TestCase):
    def test_canonical_slot_order_puts_name_first(self):
        self.assertEqual(
            _SLOT_ORDER[0], "name",
            "name is a recommendation-gating required slot — it must be "
            "first in the canonical fact-find order")
        self.assertEqual(next_question(Profile()), "name")

    def test_system_prompt_mandates_name_first(self):
        self.assertIn("RULE 0", SYSTEM_PROMPT)
        self.assertIn("ASK FOR THE NAME FIRST", SYSTEM_PROMPT)
        # The exact misleading sentence from the live transcript is named
        # and explicitly forbidden in-prompt.
        self.assertIn(
            "I couldn't find any policies matching all your criteria",
            SYSTEM_PROMPT)
        self.assertIn(
            "A MISSING REQUIRED SLOT IS NOT A", SYSTEM_PROMPT)

    def test_fallback_asks_name_when_nothing_known(self):
        msg = _synthesise_fallback(Profile())
        self.assertIn("name", msg.lower())
        _assert_not_misleading(self, msg)

    def test_fallback_only_name_missing_is_honest_and_specific(self):
        # User supplied every other substantive detail; only name is gone.
        p = Profile(
            age=39, dependents="self+spouse", location_tier="metro",
            income_band="10L-25L", primary_goal="first_buy",
            health_conditions=["none"],
        )
        msg = _synthesise_fallback(p)
        self.assertIn("name", msg.lower())
        # Honest + specific: it asks for the NAME, not a fake no-match.
        _assert_not_misleading(self, msg)
        self.assertIn("everything I need except your name", msg)

    def test_fallback_other_single_missing_slot_names_that_slot(self):
        # Only income missing → ask income plainly, never a no-match.
        p = Profile(
            name="Asha", age=39, dependents="self", location_tier="metro",
            primary_goal="first_buy", health_conditions=["none"],
        )
        msg = _synthesise_fallback(p)
        _assert_not_misleading(self, msg)
        self.assertIn("income", msg.lower())


# ════════════════════════════════════════════════════════════════════════════
# Shared end-to-end harness (Gemini + retrieve_policies network seams stubbed)
# ════════════════════════════════════════════════════════════════════════════
def _fc_part(name, args):
    return {"functionCall": {"name": name, "args": args}}


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _tool_payload(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


class _HandleTurnHarness(unittest.TestCase):
    """Drives single_brain.handle_turn for real with the two network seams
    scripted. The fit gate + citation builder + fallback all run for real;
    only Gemini and the vector store are stubbed."""

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
                    c["policy_id"]: c.get("insurer_slug", "") for c in chunks
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

    def _fresh_session(self):
        from backend.session_state import SessionState
        return SessionState(session_id=f"t_{uuid.uuid4().hex[:8]}")

    def _ready_session(self):
        """A session whose 7 required slots are filled AND the post-recap
        pricing bundle is marked skipped — the realistic precondition for a
        RECOMMENDATION turn. Bug #107 only attaches citations once the
        profile gate (brain_tools._profile_complete) is satisfied, and Bug
        #108's one-shot bundle re-ask gate is bypassed when the user skipped
        the pricing inputs. End-to-end recommendation tests must reflect
        that real flow rather than recommend off an empty profile."""
        sess = self._fresh_session()
        sess.profile.name = "Asha"
        sess.profile.age = 35
        sess.profile.dependents = "self+spouse"
        sess.profile.location_tier = "metro"
        sess.profile.income_band = "10L-25L"
        sess.profile.primary_goal = "first_buy"
        sess.profile.health_conditions = ["none"]
        sess.pricing_bundle_skipped = True
        return sess


# ════════════════════════════════════════════════════════════════════════════
# BUG #69 — end-to-end: all details EXCEPT name early; name turn not a no-match
# ════════════════════════════════════════════════════════════════════════════
class TestBug69EndToEnd(_HandleTurnHarness):
    def test_name_requested_early_and_missing_name_not_a_no_match(self):
        sess = self._fresh_session()

        # Turn 1 — the user dumps every substantive detail BUT their name,
        # exactly like the live transcript. The brain captures the facts;
        # the ONLY required slot still missing is `name`. The honest,
        # RULE-0-compliant reply asks for the name and does NOT claim a
        # search failed (no retrieve_policies has even run).
        self._gemini_script = [
            _tool_payload([
                _fc_part("save_profile_field", {"field": "age", "value": "39"}),
                _fc_part("save_profile_field",
                         {"field": "location_tier", "value": "metro"}),
                _fc_part("save_profile_field",
                         {"field": "dependents", "value": "self+spouse"}),
                _fc_part("save_profile_field",
                         {"field": "income_band", "value": "10L-25L"}),
                _fc_part("save_profile_field",
                         {"field": "primary_goal", "value": "first_buy"}),
                _fc_part("save_profile_field",
                         {"field": "health_conditions", "value": "none"}),
            ]),
            _text_payload(
                "I've got everything I need except your name — what should "
                "I call you? Then I'll pull your matches."),
        ]
        r1 = _run(single_brain.handle_turn(
            sess,
            "I'm 39, in Bangalore, with my wife, income about 18 lakh, "
            "first family policy, no health issues, budget ~30k, want "
            "10 lakh cover.",
        ))

        # (a) The missing-name turn must NOT imply a failed search / that
        #     it can't help. retrieve_policies never ran this turn.
        _assert_not_misleading(self, r1.reply_text)
        self.assertNotIn(
            "retrieve_policies",
            [c.get("policy_id", "") for c in r1.citations])
        # (b) It must ask for the name (the only remaining required slot).
        self.assertIn("name", r1.reply_text.lower())
        self.assertEqual(getattr(sess.profile, "name", None), None)
        # Sanity: the substantive facts WERE captured this turn (so the
        # only thing gating a recommendation really is the name).
        self.assertEqual(sess.profile.age, 39)
        self.assertEqual(sess.profile.income_band, "10L-25L")

        # Turn 2 — user gives the name; flow proceeds normally.
        self._gemini_script = [
            _tool_payload([_fc_part("save_profile_field",
                                    {"field": "name", "value": "Rohit"})]),
            _text_payload(
                "Thanks Rohit — pulling plans that fit your profile now."),
        ]
        r2 = _run(single_brain.handle_turn(sess, "Rohit"))
        self.assertEqual(sess.profile.name, "Rohit")
        _assert_not_misleading(self, r2.reply_text)

    def test_fallback_path_when_name_only_missing_is_not_a_no_match(self):
        # Force the deterministic safety net (Gemini emits no text and no
        # tools across all iterations → _synthesise_fallback). The profile
        # arrives with everything but the name. The synthesised reply must
        # be the honest, specific name ask — never a fake no-match.
        sess = self._fresh_session()
        sess.profile = Profile(
            age=39, dependents="self+spouse", location_tier="metro",
            income_band="10L-25L", primary_goal="first_buy",
            health_conditions=["none"],
        )
        self._gemini_script = [_text_payload("") for _ in range(12)]
        r = _run(single_brain.handle_turn(sess, "what next?"))
        _assert_not_misleading(self, r.reply_text)
        self.assertIn("name", r.reply_text.lower())


# ════════════════════════════════════════════════════════════════════════════
# BUG #71 — recommendation fit gate (unit + live citation path)
# ════════════════════════════════════════════════════════════════════════════
def _enriched(pid, name, slug, score, cid, *, grade=None, overall=None):
    """Chunk shaped like brain_tools.retrieve_policies output AFTER the
    scorecard enrichment step (carries _grade / _overall_score)."""
    return {
        "chunk_id": cid, "policy_id": pid, "policy_name": name,
        "insurer_slug": slug, "doc_type": "policy",
        "source_url": f"https://example.com/{pid}.pdf", "score": score,
        "_grade": grade, "_overall_score": overall,
    }


class TestBug71RecommendationFitUnit(unittest.TestCase):
    def test_recommendation_fit_classification(self):
        # Strong: overall >= 70, or A/B with no numeric.
        self.assertTrue(_recommendation_fit({"_overall_score": 75})[0])
        self.assertTrue(_recommendation_fit({"_overall_score": 70})[0])
        self.assertTrue(_recommendation_fit({"_grade": "A"})[0])
        self.assertTrue(_recommendation_fit({"_grade": "B"})[0])
        # Weak with positive evidence: dropped.
        self.assertFalse(_recommendation_fit({"_overall_score": 64})[0])
        self.assertFalse(_recommendation_fit({"_grade": "C"})[0])
        self.assertFalse(_recommendation_fit({"_grade": "D"})[0])
        # A numeric overall is authoritative even if a stale letter says C.
        self.assertTrue(
            _recommendation_fit({"_grade": "C", "_overall_score": 82})[0])
        # NO evidence at all → fail OPEN (pipeline already vetted fit;
        # don't nuke the shortlist when the optional scorecard is down).
        self.assertTrue(_recommendation_fit({})[0])
        self.assertTrue(_recommendation_fit({"_grade": ""})[0])

    def test_live_report_scenario_c_grade_dropped(self):
        # The exact live report: B/75 + C/64 both cited as recs.
        chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.71, "c1", grade="B", overall=75),
            _enriched("star-health__family-health-optima",
                      "Star Family Health Optima", "star-health",
                      0.68, "c2", grade="C", overall=64),
        ]
        reply = ("Top recommendations:\n1. my:Optima Secure (HDFC ERGO)\n"
                 "2. Star Family Health Optima (Star Health)")
        for marks in (["hdfc-ergo__optima-secure",
                       "star-health__family-health-optima"], []):
            cites, is_rec = _build_recommendation_citations(
                reply_text=reply, retrieved_chunks_all=chunks,
                marked_policy_ids=marks)
            ids = [c["policy_id"] for c in cites]
            self.assertTrue(is_rec)
            self.assertNotIn(
                "star-health__family-health-optima", ids,
                "Bug #71: a C/64 plan must not be presented as a "
                "recommendation")
            self.assertEqual(
                ids, ["hdfc-ergo__optima-secure"],
                "only the genuinely-strong B/75 survives — present fewer, "
                "honestly, never pad with the weak one")
            # Grade is preserved on the card (was stripped before).
            self.assertEqual(cites[0]["_grade"], "B")
            self.assertEqual(cites[0]["_overall_score"], 75.0)

    def test_only_weak_matches_yields_empty_rec_set(self):
        chunks = [
            _enriched("a__x", "Plan X", "a", 0.7, "w1", grade="C",
                      overall=64),
            _enriched("b__y", "Plan Y", "b", 0.6, "w2", grade="D",
                      overall=40),
        ]
        cites, is_rec = _build_recommendation_citations(
            reply_text="Top picks: 1. Plan X 2. Plan Y",
            retrieved_chunks_all=chunks,
            marked_policy_ids=["a__x", "b__y"])
        self.assertTrue(is_rec)
        self.assertEqual(
            cites, [],
            "no strong matches ⇒ empty rec set; caller must NOT resurrect "
            "the recall dump with weak plans")

    def test_ranked_strictly_best_first_by_overall(self):
        chunks = [
            _enriched("a__c", "Plan C", "a", 0.95, "o1", grade="B",
                      overall=72),
            _enriched("b__a", "Plan A", "b", 0.40, "o2", grade="A",
                      overall=88),
        ]
        cites, _ = _build_recommendation_citations(
            reply_text="1. Plan C 2. Plan A",
            retrieved_chunks_all=chunks,
            marked_policy_ids=["a__c", "b__a"])
        self.assertEqual(
            [c["policy_id"] for c in cites], ["b__a", "a__c"],
            "strongest fit (A/88) must be #1, NOT the LLM/cosine order")

    def test_scorecard_unavailable_fails_open(self):
        # No _grade / _overall_score at all (scorecard module down). The
        # pipeline already vetted these — keep them, don't wipe the list.
        chunks = [
            _enriched("a__p1", "Alpha One", "a", 0.5, "n1"),
            _enriched("b__p2", "Beta Two", "b", 0.4, "n2"),
        ]
        cites, is_rec = _build_recommendation_citations(
            reply_text="Options: 1. Alpha One 2. Beta Two",
            retrieved_chunks_all=chunks, marked_policy_ids=[])
        self.assertTrue(is_rec)
        self.assertEqual([c["policy_id"] for c in cites],
                         ["a__p1", "b__p2"])


class TestBug71EndToEnd(_HandleTurnHarness):
    def test_handle_turn_does_not_present_c_grade_as_recommendation(self):
        # Retrieval surfaces a strong B/75 and a weak C/64 for this user's
        # own profile. Even if the LLM names both, the cited recommendation
        # set must exclude the C/64.
        sess = self._ready_session()
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "my:Optima Secure",
                      "hdfc-ergo", 0.71, "c1", grade="B", overall=75),
            _enriched("star-health__family-health-optima",
                      "Star Family Health Optima", "star-health",
                      0.68, "c2", grade="C", overall=64),
        ]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro family 10 lakh"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure",
                               "star-health__family-health-optima"]})]),
            _text_payload(
                "Here are my recommendations: my:Optima Secure and "
                "Star Family Health Optima."),
        ]
        r = _run(single_brain.handle_turn(sess, "recommend plans for me"))
        ids = [c["policy_id"] for c in r.citations]
        self.assertIn("hdfc-ergo__optima-secure", ids)
        self.assertNotIn(
            "star-health__family-health-optima", ids,
            "Bug #71 end-to-end: the C/64 plan must not reach the user's "
            "cited recommendation set")
        self.assertEqual(len(ids), 1,
                         "present fewer (the one strong plan), not padded")

    def test_handle_turn_only_weak_matches_no_padded_recommendation(self):
        sess = self._ready_session()
        self._retrieve_chunks = [
            _enriched("a__x", "Plan Xray", "a", 0.7, "w1", grade="C",
                      overall=64),
            _enriched("b__y", "Plan Yankee", "b", 0.6, "w2", grade="D",
                      overall=40),
        ]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "plans"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["a__x", "b__y"]})]),
            _text_payload("Plan Xray and Plan Yankee are options."),
        ]
        r = _run(single_brain.handle_turn(sess, "show me recommendations"))
        self.assertEqual(
            r.citations, [],
            "only weak matches ⇒ no policy presented as a recommendation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
