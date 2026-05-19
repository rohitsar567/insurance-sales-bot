"""BUG #30 (2026-05-19) — existing-cover-aware ranking + "never promise
without performing".

Failing scenario the bug pins:
  Profile = {existing_cover_inr=100000 (₹1L employer), primary_goal=first_buy,
             smoker=yes, family_medical_history=["diabetes"],
             desired_sum_insured_inr=2_000_000}

Three sub-defects fixed:

  B1-c  retrieval_filters._fit_score had NO existing-cover / top-up term, so
        the ranker was existing-cover-blind: with ANY positive existing
        cover a relevant top-up must now out-rank a SAME-GRADE non-top-up
        (and land in the cited set), and brain_tools._quality_seed_candidates
        must union-in top-ups when existing_cover_inr is truthy.

  B2    single_brain must NEVER end a turn on a forward-looking promise
        ("let me re-evaluate / check / search") with NO tool call:
        `_is_promissory_no_action` detects it and the handle_turn loop
        re-prompts EXACTLY ONCE to force the actual tool call this turn.

  B3    _CONSTRAINT_FIELD_PHRASES now maps existing_cover_inr so a re-eval
        triggered by the existing cover is explained, not silent.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest -q \\
        tests/test_bug30_existing_cover_and_promise.py
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
from backend.retrieval_filters import (  # noqa: E402
    rank_by_profile_fit,
    filter_pipeline,
)
from backend.single_brain import (  # noqa: E402
    _is_promissory_no_action,
    _constraint_reason_clause,
    _HONEST_EMPTY_REPLY,
)


# ---------------------------------------------------------------------------
# The exact BUG #30 failing-session profile.
# ---------------------------------------------------------------------------

BUG30_PROFILE = {
    "age": 29,
    "location_tier": "metro",
    "income_band": "25L+",
    "primary_goal": "first_buy",
    "existing_cover_inr": 100_000,        # ₹1L employer cover — SMALL but > 0
    "health_conditions": ["none"],
    "desired_sum_insured_inr": 2_000_000,  # ₹20 lakh
    "copay_pct": 0,
    "smoker": True,
    "family_medical_history": ["diabetes"],
    "dependents": "self",
}


def _chunk(
    policy_id: str,
    policy_name: str,
    *,
    score: float = 0.5,
    policy_type: str | None = None,
    deductible_amount: int | None = None,
    copay_pct: int | None = None,
    sum_insured_options: list[int] | None = None,
    grade: str | None = None,
    overall_score: int | None = None,
    doc_type: str = "policy",
) -> dict:
    """Chunk shaped like brain_tools.retrieve_policies output AFTER the
    policy_facts enrichment step (mirrors tests/test_eligibility_ranking)."""
    return {
        "policy_id": policy_id,
        "policy_name": policy_name,
        "insurer_slug": policy_id.split("__")[0],
        "doc_type": doc_type,
        "score": score,
        "chunk_text": "",
        "policy_type_indemnity_or_fixed": policy_type,
        "deductible_amount": deductible_amount,
        "co_payment_pct": copay_pct,
        "sum_insured_options": sum_insured_options,
        "_grade": grade,
        "_overall_score": overall_score,
    }


# A SAME-GRADE pair: identical cosine, grade, overall, SI, copay. The ONLY
# differentiator is that one is a super-top-up. Pre-fix _fit_score scored
# them equally (stable sort kept incoming order → top-up second). Post-fix
# the +22 existing-cover term must lift the top-up above the non-top-up.
SAME_GRADE_TOPUP = _chunk(
    "royal-sundaram__advanced-top-up",
    "Advanced Top Up Health Insurance Plan",
    score=0.60,
    policy_type="super_top_up",
    deductible_amount=300_000,
    copay_pct=0,
    sum_insured_options=[1_000_000, 2_000_000, 5_000_000],
    grade="A",
    overall_score=85,
)
SAME_GRADE_PRIMARY = _chunk(
    "niva-bupa__reassure-2",
    "ReAssure 2.0",
    score=0.60,
    policy_type="indemnity",
    deductible_amount=None,
    copay_pct=0,
    sum_insured_options=[1_000_000, 2_000_000, 5_000_000],
    grade="A",
    overall_score=85,
)


# ---------------------------------------------------------------------------
# B1-c — existing-cover-aware ranking
# ---------------------------------------------------------------------------

class TestExistingCoverRanking(unittest.TestCase):
    def test_topup_outranks_same_grade_non_topup_with_existing_cover(self):
        """With existing_cover_inr=100000 a SAME-GRADE top-up must rank
        ABOVE the same-grade non-top-up (the +22 term breaks the tie that,
        pre-fix, left the top-up buried)."""
        ranked = rank_by_profile_fit(
            # incoming order puts the PRIMARY first so a stable sort would,
            # absent the new term, KEEP the top-up second.
            [SAME_GRADE_PRIMARY, SAME_GRADE_TOPUP], BUG30_PROFILE
        )
        order = [c["policy_id"] for c in ranked]
        self.assertLess(
            order.index("royal-sundaram__advanced-top-up"),
            order.index("niva-bupa__reassure-2"),
            "A relevant super-top-up must out-rank a same-grade non-top-up "
            "when the user already holds ₹1L existing base cover.",
        )

    def test_topup_lands_in_cited_set_through_pipeline(self):
        """End-to-end: through filter_pipeline the top-up survives
        eligibility (user HAS base cover) AND lands in the cited set."""
        filtered, _guard = filter_pipeline(
            [SAME_GRADE_PRIMARY, SAME_GRADE_TOPUP],
            profile=BUG30_PROFILE,
            query=("comprehensive base health plan metro 20 lakh super "
                   "top-up plan layered over existing 1 lakh employer base "
                   "cover diabetes smoker"),
            intent="recommendation",
        )
        ids = [c["policy_id"] for c in filtered]
        self.assertIn(
            "royal-sundaram__advanced-top-up", ids,
            "the relevant super-top-up must reach the brain (cited set) for "
            "a user who already holds base cover.",
        )
        self.assertIn("niva-bupa__reassure-2", ids,
                      "the strong primary plan must also survive.")
        self.assertLess(
            ids.index("royal-sundaram__advanced-top-up"),
            ids.index("niva-bupa__reassure-2"),
            "post-fix the top-up ranks ahead of the same-grade primary.",
        )

    def test_term_inert_without_existing_cover(self):
        """Regression: with NO existing cover the term must be inert — a
        first-time buyer's ranking is unchanged (stable order preserved)."""
        no_cover = dict(BUG30_PROFILE, existing_cover_inr=0)
        ranked = rank_by_profile_fit(
            [SAME_GRADE_PRIMARY, SAME_GRADE_TOPUP], no_cover
        )
        order = [c["policy_id"] for c in ranked]
        # Equal score → stable sort keeps the incoming order (primary first).
        self.assertEqual(
            order[0], "niva-bupa__reassure-2",
            "with no existing cover the +22 term must NOT fire — ordering "
            "is unchanged from the profile-neutral baseline.",
        )


class TestQualitySeedUnionsTopUps(unittest.TestCase):
    """B1-c — _quality_seed_candidates must union-in top-up policies when
    existing_cover_inr is truthy, even if they fall OUTSIDE the
    profile-neutral top-25 window."""

    class _Prof:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    def _patch_curated(self, monkeyish):
        # Build a synthetic catalogue: 26 high-overall NON-top-up policies
        # (fill the top-25 window) + ONE top-up with a LOWER overall so it
        # falls OUTSIDE the window. The fix must still seed it.
        curated = {}
        for i in range(26):
            pid = f"insurerx__primary-{i:02d}"
            curated[pid] = {
                "policy_id": pid,
                "policy_name": f"Primary Plan {i}",
                "insurer_slug": "insurerx",
            }
        curated["royal-sundaram__advanced-top-up"] = {
            "policy_id": "royal-sundaram__advanced-top-up",
            "policy_name": "Advanced Top Up Health Insurance Plan",
            "insurer_slug": "royal-sundaram",
        }

        def _fake_curated_all():
            return curated

        def _fake_scorecard_signal(pid, profile=None):
            if pid == "royal-sundaram__advanced-top-up":
                return {"_overall_score": 50, "_grade": "C"}  # OUTSIDE top-25
            n = int(pid.split("-")[-1])
            return {"_overall_score": 95 - n, "_grade": "A"}  # all > 50

        def _fake_load_facts(pid):
            if pid == "royal-sundaram__advanced-top-up":
                return {
                    "policy_type_indemnity_or_fixed": "super_top_up",
                    "deductible_amount": 300_000,
                }
            return {"policy_type_indemnity_or_fixed": "indemnity"}

        return (_fake_curated_all, _fake_scorecard_signal, _fake_load_facts)

    def test_topup_seeded_when_existing_cover_truthy(self):
        ca, sig, lf = self._patch_curated(None)
        with mock.patch.object(brain_tools, "_curated_facts_all", ca), \
             mock.patch.object(brain_tools, "_scorecard_signal", sig), \
             mock.patch.object(brain_tools, "_load_policy_facts", lf), \
             mock.patch.object(brain_tools, "_has_extraction",
                               lambda pid: True):
            brain_tools._qseed_cache.clear()
            prof = self._Prof(existing_cover_inr=100_000, age=29)
            seeded = brain_tools._quality_seed_candidates(prof, limit=25)
        ids = {c["policy_id"] for c in seeded}
        self.assertIn(
            "royal-sundaram__advanced-top-up", ids,
            "a relevant super-top-up OUTSIDE the profile-neutral top-25 "
            "must still be union-seeded when existing_cover_inr is truthy.",
        )

    def test_topup_not_seeded_without_existing_cover(self):
        ca, sig, lf = self._patch_curated(None)
        with mock.patch.object(brain_tools, "_curated_facts_all", ca), \
             mock.patch.object(brain_tools, "_scorecard_signal", sig), \
             mock.patch.object(brain_tools, "_load_policy_facts", lf), \
             mock.patch.object(brain_tools, "_has_extraction",
                               lambda pid: True):
            brain_tools._qseed_cache.clear()
            prof = self._Prof(existing_cover_inr=0, age=29)
            seeded = brain_tools._quality_seed_candidates(prof, limit=25)
        ids = {c["policy_id"] for c in seeded}
        self.assertNotIn(
            "royal-sundaram__advanced-top-up", ids,
            "with no existing cover the out-of-window top-up must NOT be "
            "force-seeded (term inert for first-time buyers).",
        )


# ---------------------------------------------------------------------------
# B2 — promissory-no-action detector
# ---------------------------------------------------------------------------

class TestIsPromissoryNoAction(unittest.TestCase):
    def test_detects_all_canonical_promise_phrases(self):
        for phrase in (
            "Let me re-evaluate the options for you.",
            "Sure, let me check that.",
            "Let me look into the best plans.",
            "I'll look into it and get back.",
            "I will re-evaluate given your existing cover.",
            "Let me search for better-fit plans.",
            "Let me find a top-up that suits you.",
            "Give me a moment to reconsider.",
            "I'll check the shortlist again.",
            "Let me see if there's a better option.",
        ):
            self.assertTrue(
                _is_promissory_no_action(phrase),
                f"must flag promissory phrase: {phrase!r}",
            )

    def test_case_insensitive(self):
        self.assertTrue(_is_promissory_no_action("LET ME RE-EVALUATE NOW"))

    def test_non_promissory_text_is_not_flagged(self):
        for ok in (
            "Here are two strong plans for your profile: ...",
            "Your ₹1L employer cover supplements this primary plan.",
            "Could you confirm your preferred sum insured?",
            "",
            "   ",
        ):
            self.assertFalse(
                _is_promissory_no_action(ok),
                f"must NOT flag non-promissory text: {ok!r}",
            )


# ---------------------------------------------------------------------------
# B3 — existing-cover constraint phrase
# ---------------------------------------------------------------------------

class TestExistingCoverConstraintPhrase(unittest.TestCase):
    def test_existing_cover_inr_maps_to_its_phrase(self):
        clause = _constraint_reason_clause({"existing_cover_inr": "100000"})
        self.assertIn("existing cover you already hold", clause)


# ---------------------------------------------------------------------------
# B2 — handle_turn loop guard: a promissory no-tool reply forces EXACTLY
#      ONE re-prompt iteration (no infinite loop).
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _fc_part(name, args):
    return {"functionCall": {"name": name, "args": args}}


def _text_payload(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _tool_payload(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


def _enriched(pid, name, slug, score, cid, *, grade="A", overall=85):
    return {
        "chunk_id": cid, "policy_id": pid, "policy_name": name,
        "insurer_slug": slug, "doc_type": "policy",
        "source_url": f"https://example.com/{pid}.pdf", "score": score,
        "_grade": grade, "_overall_score": overall,
    }


class TestPromissoryLoopGuard(unittest.TestCase):
    """A no-tool promissory turn must trigger EXACTLY ONE re-prompt that
    forces the actual tool call this turn (mirrors
    tests/test_last_text_preserved_across_tool_iters harness)."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ,
                                    {"GOOGLE_API_KEY": "test-key"})
        self._env.start()
        self._gemini_script: list = []
        self._calls: list = []
        self._retrieve_chunks: list = []

        async def _fake_gemini(*_a, **_k):
            self._calls.append(_k.get("contents"))
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
        sess.profile.age = 29
        sess.profile.dependents = "self"
        sess.profile.location_tier = "metro"
        sess.profile.income_band = "25L+"
        sess.profile.primary_goal = "first_buy"
        sess.profile.health_conditions = ["none"]
        sess.profile.existing_cover_inr = 100_000
        sess.pricing_bundle_skipped = True
        return sess

    def test_promissory_no_tool_turn_forces_exactly_one_reprompt(self):
        sess = self._ready_session()
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "Optima Secure",
                      "hdfc-ergo", 0.81, "c1"),
            _enriched("royal-sundaram__advanced-top-up",
                      "Advanced Top Up", "royal-sundaram", 0.70, "c2"),
        ]
        self._gemini_script = [
            # iter 1 — PROMISSORY, NO tool call. Must trigger 1 re-prompt.
            _text_payload(
                "Let me re-evaluate given your ₹1L employer cover."),
            # iter 2 — model now actually calls the tools.
            _tool_payload([_fc_part("retrieve_policies", {
                "query": ("comprehensive base plan super top-up plan "
                          "layered over existing 1 lakh employer base "
                          "cover")})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure",
                               "royal-sundaram__advanced-top-up"]})]),
            # iter 3 — final prose with the revised shortlist.
            _text_payload(
                "Here is the revised shortlist: 1. Optima Secure works as "
                "your PRIMARY plan; your ₹1L employer cover supplements it. "
                "2. Advanced Top Up sits ABOVE your ₹1L existing cover."),
        ]

        r = _run(single_brain.handle_turn(
            sess, "But I already have ₹1L cover from my employer — "
                  "reconsider please"))

        # The re-prompt fires (a synthetic user turn injected) and the
        # final reply is the REAL revised shortlist, never the promise.
        self.assertIn("revised shortlist", r.reply_text)
        self.assertNotIn("Let me re-evaluate", r.reply_text,
                          "the turn must NOT end on the promise")
        self.assertNotEqual(r.reply_text, _HONEST_EMPTY_REPLY)
        # _classify_intent → "recommendation" only when BOTH
        # retrieve_policies AND mark_recommendation actually fired; proves
        # the re-prompt forced the real tool calls.
        self.assertEqual(
            r.intent, "recommendation",
            "the re-prompt must have forced the actual retrieve_policies + "
            "mark_recommendation tool calls",
        )
        self.assertIn("retrieve_policies", r.brain_used)

        # Exactly ONE re-prompt: find the injected guard user-turn in the
        # contents passed to the final Gemini call.
        final_contents = self._calls[-1]
        guard_turns = [
            c for c in final_contents
            if c.get("role") == "user"
            and any("called no tool" in p.get("text", "")
                    for p in c.get("parts", []))
        ]
        self.assertEqual(
            len(guard_turns), 1,
            "the B2 loop guard must fire EXACTLY ONCE per turn "
            "(no infinite loop / no double re-prompt).",
        )

    def test_no_reprompt_when_turn_has_a_tool_call(self):
        """A turn that DOES call a tool must not trigger the B2 guard even
        if its (later) prose happens to contain a promise-like phrase."""
        sess = self._ready_session()
        self._retrieve_chunks = [
            _enriched("hdfc-ergo__optima-secure", "Optima Secure",
                      "hdfc-ergo", 0.81, "c1"),
        ]
        self._gemini_script = [
            _tool_payload([_fc_part("retrieve_policies",
                                    {"query": "metro comprehensive"})]),
            _tool_payload([_fc_part("mark_recommendation", {
                "policy_ids": ["hdfc-ergo__optima-secure"]})]),
            _text_payload(
                "Here is Optima Secure — a strong primary plan; your "
                "₹1L employer cover supplements it."),
        ]
        r = _run(single_brain.handle_turn(sess, "show options"))
        self.assertIn("Optima Secure", r.reply_text)
        final_contents = self._calls[-1]
        guard_turns = [
            c for c in final_contents
            if c.get("role") == "user"
            and any("called no tool" in p.get("text", "")
                    for p in c.get("parts", []))
        ]
        self.assertEqual(len(guard_turns), 0,
                         "no B2 guard when the turn already called a tool")


if __name__ == "__main__":
    unittest.main(verbosity=2)
