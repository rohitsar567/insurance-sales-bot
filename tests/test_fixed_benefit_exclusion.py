"""KI-279 (2026-05-16) — fixed-benefit exclusion for comprehensive-indemnity intent.

LIVE-CONFIRMED BUG (Playwright audit on the deployed Space):

  USER PROFILE: 29, metro, income 25L+, FIRST health policy
  (existing_cover_inr=0), NO existing cover, NO pre-existing conditions,
  wants 15L sum insured, wants ZERO co-pay.

  WHAT WAS WRONGLY RECOMMENDED AS #1:
    "Star Hospital Cash" by Star Health — a FIXED-BENEFIT daily-cash plan
    (pays a fixed amount per day, NOT indemnity reimbursement of actual
    medical expenses). Recommending a daily-cash / fixed-benefit product
    as a top pick to someone who clearly wants COMPREHENSIVE INDEMNITY
    cover is wrong.

ROOT CAUSE:
  (1) brain_tools._FACT_KEYS loaded only `policy_type_indemnity_or_fixed`.
      star-health__star-hospital-cash__brochure.json carries the type
      under `policy_type` ("hospital_cash") and has NO
      `policy_type_indemnity_or_fixed` key — so the enriched chunk reaching
      retrieval_filters had ZERO type signal.
  (2) retrieval_filters had no rule that excludes / down-ranks fixed-benefit
      products (hospital daily cash, personal accident, critical illness,
      cancer / defined-benefit) when the profile clearly signals the user
      wants comprehensive indemnity cover.

FIX (backend/retrieval_filters.py + backend/brain_tools.py):
  - Detect "wants comprehensive indemnity" intent conservatively (first
    health policy / general-cover goal, a desired SI present, no existing
    base cover, and NOT an explicit supplement / PA / CI / top-up goal).
  - When that intent holds, HARD-DROP policy chunks whose type is
    fixed-benefit (reuse scorecard-equivalent classification + name regex).
  - Belt-and-braces: down-rank any surviving fixed-benefit below all
    indemnity options.
  - Surface `policy_type` through brain_tools enrichment so the signal
    reaches the filter on the live path.

Run:
    .venv/bin/python -m pytest -q tests/test_fixed_benefit_exclusion.py
"""

from __future__ import annotations

import unittest

from backend.brain_tools import _load_policy_facts
from backend.retrieval_filters import (
    apply_eligibility_filter,
    rank_by_profile_fit,
    filter_pipeline,
    _wants_comprehensive_indemnity,
    _is_fixed_benefit_chunk,
)


# The exact failing-session profile.
COMPREHENSIVE_PROFILE = {
    "age": 29,
    "location_tier": "metro",
    "income_band": "25L+",
    "primary_goal": "first_buy",
    "existing_cover_inr": 0,            # FIRST policy — no base cover
    "health_conditions": ["none"],
    "desired_sum_insured_inr": 1_500_000,  # 15 lakh
    "copay_pct": 0,                    # explicit ZERO co-pay preference
    "dependents": "self",
}


def _chunk(
    policy_id,
    policy_name,
    *,
    score=0.5,
    policy_type=None,
    policy_type_canonical=None,
    copay_pct=None,
    sum_insured_options=None,
    grade=None,
    overall_score=None,
    doc_type="policy",
):
    """Chunk dict shaped like brain_tools.retrieve_policies output AFTER the
    policy_facts enrichment step.

    `policy_type` mirrors the raw catalog key (e.g. "hospital_cash");
    `policy_type_canonical` mirrors `policy_type_indemnity_or_fixed`
    ("indemnity" / "fixed_benefit") when curated.
    """
    return {
        "policy_id": policy_id,
        "policy_name": policy_name,
        "insurer_slug": policy_id.split("__")[0],
        "doc_type": doc_type,
        "score": score,
        "chunk_text": "",
        "policy_type_indemnity_or_fixed": policy_type_canonical,
        "policy_type": policy_type,
        "deductible_amount": None,
        "co_payment_pct": copay_pct,
        "sum_insured_options": sum_insured_options,
        "_grade": grade,
        "_overall_score": overall_score,
    }


# The product the bot wrongly recommended #1.
STAR_HOSPITAL_CASH = _chunk(
    "star-health__star-hospital-cash__brochure",
    "Star Hospital Cash",
    score=0.72,
    policy_type="hospital_cash",          # raw catalog key (no canonical key)
    policy_type_canonical=None,           # mirrors the real facts file
    copay_pct=None,
    sum_insured_options=None,
)
PERSONAL_ACCIDENT = _chunk(
    "hdfc-ergo__personal-accident",
    "HDFC ERGO Personal Accident Plan",
    score=0.61,
    policy_type=None,                     # no structured signal — name only
    sum_insured_options=[2500000],
)
CRITICAL_ILLNESS = _chunk(
    "max-life__critical-illness",
    "Max Critical Illness Secure",
    score=0.60,
    policy_type="fixed_benefit",
    policy_type_canonical="fixed_benefit",
    sum_insured_options=[1500000],
)
GOOD_INDEMNITY = _chunk(
    "niva-bupa__reassure-2",
    "ReAssure 2.0",
    score=0.55,
    policy_type="family_floater",
    policy_type_canonical="indemnity",
    copay_pct=0,
    sum_insured_options=[1000000, 1500000, 2000000, 5000000],
    grade="A",
    overall_score=88,
)


class TestComprehensiveIntentDetection(unittest.TestCase):
    def test_failing_profile_signals_comprehensive_indemnity(self):
        self.assertTrue(
            _wants_comprehensive_indemnity(COMPREHENSIVE_PROFILE),
            "First policy + no base cover + desired SI + general goal must "
            "register as a comprehensive-indemnity intent.",
        )

    def test_explicit_supplement_goal_does_not_fire(self):
        supplement = dict(
            COMPREHENSIVE_PROFILE, primary_goal="critical illness cover only"
        )
        self.assertFalse(
            _wants_comprehensive_indemnity(supplement),
            "An explicit critical-illness-only goal must NOT trigger the "
            "comprehensive-indemnity intent (don't drop CI plans for them).",
        )

    def test_topup_goal_does_not_fire(self):
        topup = dict(
            COMPREHENSIVE_PROFILE,
            primary_goal="top-up over existing employer cover",
            existing_cover_inr=500000,
        )
        self.assertFalse(
            _wants_comprehensive_indemnity(topup),
            "A top-up supplement goal must NOT trigger comprehensive intent.",
        )

    def test_no_desired_si_does_not_fire(self):
        vague = dict(COMPREHENSIVE_PROFILE)
        vague.pop("desired_sum_insured_inr")
        vague["primary_goal"] = ""
        self.assertFalse(
            _wants_comprehensive_indemnity(vague),
            "Be conservative: with no desired SI and no goal signal the "
            "intent should not fire.",
        )


class TestFixedBenefitClassification(unittest.TestCase):
    def test_star_hospital_cash_classified_fixed_benefit(self):
        self.assertTrue(
            _is_fixed_benefit_chunk(STAR_HOSPITAL_CASH),
            "Star Hospital Cash (policy_type='hospital_cash' + name) is a "
            "fixed-benefit product.",
        )

    def test_personal_accident_by_name(self):
        self.assertTrue(_is_fixed_benefit_chunk(PERSONAL_ACCIDENT))

    def test_critical_illness_by_canonical_key(self):
        self.assertTrue(_is_fixed_benefit_chunk(CRITICAL_ILLNESS))

    def test_indemnity_not_classified_fixed(self):
        self.assertFalse(_is_fixed_benefit_chunk(GOOD_INDEMNITY))

    def test_real_facts_file_surfaces_policy_type(self):
        facts = _load_policy_facts("star-health__star-hospital-cash__brochure")
        self.assertEqual(
            facts.get("policy_type"), "hospital_cash",
            "brain_tools._FACT_KEYS must include `policy_type` so the "
            "fixed-benefit signal reaches retrieval_filters on the live path.",
        )


class TestFixedBenefitExclusion(unittest.TestCase):
    def test_star_hospital_cash_dropped_for_comprehensive_profile(self):
        kept = apply_eligibility_filter(
            [STAR_HOSPITAL_CASH, GOOD_INDEMNITY], COMPREHENSIVE_PROFILE
        )
        ids = {c["policy_id"] for c in kept}
        self.assertNotIn(
            "star-health__star-hospital-cash__brochure", ids,
            "Star Hospital Cash (fixed-benefit) must be hard-dropped for a "
            "user who clearly wants comprehensive indemnity cover.",
        )
        self.assertIn("niva-bupa__reassure-2", ids)

    def test_all_fixed_benefit_shapes_dropped(self):
        kept = apply_eligibility_filter(
            [STAR_HOSPITAL_CASH, PERSONAL_ACCIDENT, CRITICAL_ILLNESS,
             GOOD_INDEMNITY],
            COMPREHENSIVE_PROFILE,
        )
        ids = {c["policy_id"] for c in kept}
        self.assertEqual(
            ids, {"niva-bupa__reassure-2"},
            "Every fixed-benefit shape (hospital cash / PA / CI) must be "
            "dropped; only the indemnity plan survives.",
        )

    def test_fixed_benefit_kept_for_explicit_ci_seeker(self):
        ci_seeker = dict(
            COMPREHENSIVE_PROFILE,
            primary_goal="critical illness cover only",
        )
        kept = apply_eligibility_filter([CRITICAL_ILLNESS], ci_seeker)
        self.assertEqual(
            len(kept), 1,
            "A user who explicitly wants critical-illness cover must still "
            "see CI plans — do NOT exclude fixed-benefit for them.",
        )

    def test_fixed_benefit_kept_for_supplement_seeker_with_base(self):
        supp = dict(
            COMPREHENSIVE_PROFILE,
            primary_goal="add a hospital cash add-on to my existing plan",
            existing_cover_inr=500000,
        )
        kept = apply_eligibility_filter([STAR_HOSPITAL_CASH], supp)
        self.assertEqual(
            len(kept), 1,
            "Someone with existing base cover wanting a hospital-cash add-on "
            "must still see hospital-cash plans.",
        )

    def test_non_policy_chunks_never_dropped(self):
        reg = _chunk("irdai__circular", "IRDAI Hospital Cash Circular",
                     doc_type="regulatory", policy_type="hospital_cash")
        kept = apply_eligibility_filter([reg], COMPREHENSIVE_PROFILE)
        self.assertEqual(len(kept), 1,
                         "Regulatory/review/profile chunks are never policies.")


class TestFixedBenefitRanking(unittest.TestCase):
    def test_indemnity_outranks_surviving_fixed_benefit(self):
        # Profile where the comprehensive intent does NOT hard-drop (no
        # desired SI, no comprehensive goal token) so fixed-benefit
        # survives the eligibility filter — it must still rank BELOW the
        # indemnity plan via the belt-and-braces demotion.
        vague = {
            "age": 29, "location_tier": "metro", "income_band": "25L+",
            "primary_goal": "", "dependents": "self",
        }
        ranked = rank_by_profile_fit(
            [STAR_HOSPITAL_CASH, GOOD_INDEMNITY], vague
        )
        order = [c["policy_id"] for c in ranked]
        self.assertLess(
            order.index("niva-bupa__reassure-2"),
            order.index("star-health__star-hospital-cash__brochure"),
            "A comprehensive indemnity plan must rank above a fixed-benefit "
            "daily-cash plan.",
        )


class TestFilterPipelineLivePath(unittest.TestCase):
    """End-to-end through the SAME filter_pipeline single_brain ->
    brain_tools.retrieve_policies -> retrieval_filters.filter_pipeline uses."""

    def test_failing_session_star_hospital_cash_not_top(self):
        catalog = [
            STAR_HOSPITAL_CASH, PERSONAL_ACCIDENT, CRITICAL_ILLNESS,
            GOOD_INDEMNITY,
        ]
        filtered, guard = filter_pipeline(
            catalog,
            profile=COMPREHENSIVE_PROFILE,
            query="first health policy metro 15 lakh sum insured zero co-pay",
            intent="recommendation",
        )
        ids = [c["policy_id"] for c in filtered]
        self.assertNotIn(
            "star-health__star-hospital-cash__brochure", ids,
            "Star Hospital Cash must NOT reach the brain for this profile.",
        )
        self.assertTrue(ids, "the comprehensive indemnity plan must survive")
        self.assertEqual(
            ids[0], "niva-bupa__reassure-2",
            "Top recommendation must be the comprehensive indemnity plan.",
        )

    def test_real_facts_file_through_pipeline(self):
        pid = "star-health__star-hospital-cash__brochure"
        chunk = {
            "policy_id": pid,
            "policy_name": "Star Hospital Cash",
            "insurer_slug": "star-health",
            "doc_type": "policy",
            "score": 0.72,
            "chunk_text": "fixed benefit policy pays a fixed amount per day",
        }
        chunk.update(_load_policy_facts(pid))
        filtered, _ = filter_pipeline(
            [chunk, GOOD_INDEMNITY],
            profile=COMPREHENSIVE_PROFILE,
            query="first health policy 15 lakh zero copay",
            intent="recommendation",
        )
        ids = [c["policy_id"] for c in filtered]
        self.assertNotIn(
            pid, ids,
            "Using the REAL policy_facts file, Star Hospital Cash must be "
            "dropped on the live path.",
        )


if __name__ == "__main__":
    unittest.main()
