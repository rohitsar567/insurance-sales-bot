"""KI-278 (2026-05-16) — eligibility filtering + profile-fit ranking.

Pins the bug reported for the failing session:

  USER PROFILE: 29, metro, income 25L+, FIRST policy (existing_cover_inr=0),
  no pre-existing conditions, wants 15L sum insured, prefers ZERO co-pay.

  WHAT WAS WRONGLY RECOMMENDED:
    1. Royal Sundaram "Multiplier"      — 20% co-payment (user wants 0%)
    2. Royal Sundaram "Advanced Top Up" — super-top-up; needs a base policy
                                          the first-time buyer does NOT have
    3. generally B/C-grade plans whose metrics contradict stated needs

Two logic defects, both fixed in backend/retrieval_filters.py:
  (a) ELIGIBILITY: top-up / super-top-up plans must be excluded when the
      user has no existing base cover (existing_cover_inr falsy / first buy).
  (b) PROFILE-FIT RANKING: high co-pay plans must not survive an explicit
      zero-copay preference; the SI floor (15L) must gate; better-fit plans
      must rank above worse-fit ones.

Run:
    .venv/bin/python -m unittest tests.test_eligibility_ranking -v
"""

from __future__ import annotations

import unittest

from backend.retrieval_filters import (
    apply_profile_filter,
    apply_eligibility_filter,
    rank_by_profile_fit,
    filter_pipeline,
)


# ---------------------------------------------------------------------------
# The exact failing-session profile.
# ---------------------------------------------------------------------------

FAILING_PROFILE = {
    "age": 29,
    "location_tier": "metro",
    "income_band": "25L+",
    "primary_goal": "first_buy",
    "existing_cover_inr": 0,          # FIRST policy — no base cover
    "health_conditions": ["none"],
    "desired_sum_insured_inr": 1_500_000,  # ₹15 lakh
    "copay_pct": 0,                   # explicit ZERO co-pay preference
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
    """Build a chunk dict shaped like brain_tools.retrieve_policies output
    AFTER the new policy_facts enrichment step."""
    return {
        "policy_id": policy_id,
        "policy_name": policy_name,
        "insurer_slug": policy_id.split("__")[0],
        "doc_type": doc_type,
        "score": score,
        "chunk_text": "",
        # Enriched structured facts (added by brain_tools before filtering):
        "policy_type_indemnity_or_fixed": policy_type,
        "deductible_amount": deductible_amount,
        "co_payment_pct": copay_pct,
        "sum_insured_options": sum_insured_options,
        "_grade": grade,
        "_overall_score": overall_score,
    }


# Realistic catalog slice mirroring the failing session.
MULTIPLIER = _chunk(
    "royal-sundaram__multiplier",
    "Multiplier Health Insurance Plan",
    score=0.71,
    policy_type="family_floater",
    deductible_amount=None,
    copay_pct=20,
    sum_insured_options=[500000, 1000000, 1500000, 2000000, 2500000],
    grade="B",
    overall_score=72,
)
ADVANCED_TOP_UP = _chunk(
    "royal-sundaram__advanced-top-up",
    "Advanced Top Up Health Insurance Plan",
    score=0.69,
    policy_type="super_top_up",
    deductible_amount=500000,
    copay_pct=0,
    sum_insured_options=[1000000, 1500000, 2000000],
    grade="B",
    overall_score=70,
)
GOOD_FIT_A = _chunk(
    "niva-bupa__reassure-2",
    "ReAssure 2.0",
    score=0.62,
    policy_type="indemnity",
    deductible_amount=None,
    copay_pct=0,
    sum_insured_options=[1000000, 1500000, 2000000, 5000000],
    grade="A",
    overall_score=88,
)
GOOD_FIT_B = _chunk(
    "hdfc-ergo__optima-secure",
    "Optima Secure",
    score=0.58,
    policy_type="indemnity",
    deductible_amount=None,
    copay_pct=0,
    sum_insured_options=[500000, 1000000, 1500000, 2000000],
    grade="A",
    overall_score=85,
)
LOW_SI = _chunk(
    "star-health__medi-classic",
    "Medi Classic",
    score=0.66,
    policy_type="indemnity",
    deductible_amount=None,
    copay_pct=0,
    sum_insured_options=[200000, 300000, 500000],   # cannot offer 15L
    grade="C",
    overall_score=60,
)


class TestEligibilityFilter(unittest.TestCase):
    """Defect (a) — top-up / super-top-up exclusion for first-time buyers."""

    def test_super_top_up_dropped_when_no_base_cover(self):
        kept = apply_eligibility_filter(
            [ADVANCED_TOP_UP, GOOD_FIT_A], FAILING_PROFILE
        )
        ids = {c["policy_id"] for c in kept}
        self.assertNotIn(
            "royal-sundaram__advanced-top-up", ids,
            "Super-top-up must be excluded for a first-time buyer with no "
            "existing base cover.",
        )
        self.assertIn("niva-bupa__reassure-2", ids)

    def test_top_up_dropped_by_name_when_facts_missing(self):
        # No structured policy_type, but the NAME says "Top Up" / "Super Top Up".
        top_up_by_name = _chunk(
            "sbi-general__super-top-up",
            "SBI Super Top-up Health Insurance",
            policy_type=None,
            deductible_amount=None,
        )
        kept = apply_eligibility_filter([top_up_by_name], FAILING_PROFILE)
        self.assertEqual(
            kept, [],
            "A plan named 'Super Top-up' must be excluded for a no-base-cover "
            "user even when structured policy_type is missing.",
        )

    def test_top_up_dropped_by_deductible_signal(self):
        # No policy_type, name doesn't say top-up, but it carries a large
        # aggregate deductible — that IS the base-cover requirement.
        deductible_only = _chunk(
            "acko__platinum-super",
            "Platinum Plus Plan",
            policy_type=None,
            deductible_amount=500000,
        )
        kept = apply_eligibility_filter([deductible_only], FAILING_PROFILE)
        self.assertEqual(
            kept, [],
            "A plan with a ₹5L aggregate deductible is a top-up in disguise "
            "and must be excluded for a no-base-cover user.",
        )

    def test_top_up_kept_when_user_has_base_cover(self):
        has_base = dict(FAILING_PROFILE, existing_cover_inr=500000)
        kept = apply_eligibility_filter([ADVANCED_TOP_UP], has_base)
        self.assertEqual(
            len(kept), 1,
            "Top-up IS appropriate when the user already has base cover.",
        )

    def test_si_floor_drops_plans_that_cannot_offer_requested_si(self):
        kept = apply_eligibility_filter([LOW_SI, GOOD_FIT_A], FAILING_PROFILE)
        ids = {c["policy_id"] for c in kept}
        self.assertNotIn(
            "star-health__medi-classic", ids,
            "A plan whose max SI is ₹5L cannot satisfy a ₹15L requirement.",
        )
        self.assertIn("niva-bupa__reassure-2", ids)

    def test_zero_copay_preference_drops_high_copay_plan(self):
        kept = apply_eligibility_filter([MULTIPLIER, GOOD_FIT_A], FAILING_PROFILE)
        ids = {c["policy_id"] for c in kept}
        self.assertNotIn(
            "royal-sundaram__multiplier", ids,
            "A 20% co-pay plan must be excluded when the user explicitly "
            "wants ZERO co-pay and can afford full cover.",
        )

    def test_non_policy_chunks_never_dropped(self):
        reg = _chunk("irdai__circular", "IRDAI Master Circular",
                     doc_type="regulatory", policy_type="super_top_up",
                     deductible_amount=500000)
        kept = apply_eligibility_filter([reg], FAILING_PROFILE)
        self.assertEqual(len(kept), 1,
                         "Regulatory/review/profile chunks are never policies.")


class TestProfileFitRanking(unittest.TestCase):
    """Defect (b) — better-fit plans must outrank worse-fit ones."""

    def test_a_grade_zero_copay_ranks_above_b_grade(self):
        ranked = rank_by_profile_fit(
            [MULTIPLIER, GOOD_FIT_B, GOOD_FIT_A], FAILING_PROFILE
        )
        order = [c["policy_id"] for c in ranked]
        self.assertLess(
            order.index("niva-bupa__reassure-2"),
            order.index("royal-sundaram__multiplier"),
            "A-grade zero-copay plan must rank above a B-grade 20%-copay plan "
            "even though the B-grade plan had higher raw cosine.",
        )

    def test_ranking_stable_for_equally_good_plans(self):
        ranked = rank_by_profile_fit([GOOD_FIT_A, GOOD_FIT_B], FAILING_PROFILE)
        self.assertEqual(len(ranked), 2)


class TestFilterPipelineIntegration(unittest.TestCase):
    """End-to-end: the exact failing catalog through filter_pipeline must
    NOT surface Multiplier or Advanced Top Up, and the top result must be a
    well-fitting A-grade plan."""

    def test_failing_session_catalog(self):
        catalog = [
            MULTIPLIER, ADVANCED_TOP_UP, LOW_SI, GOOD_FIT_A, GOOD_FIT_B,
        ]
        filtered, guard = filter_pipeline(
            catalog,
            profile=FAILING_PROFILE,
            query="first health policy metro 15 lakh sum insured zero co-pay",
            intent="recommendation",
        )
        ids = [c["policy_id"] for c in filtered]

        self.assertNotIn("royal-sundaram__advanced-top-up", ids,
                         "super-top-up must not reach the brain")
        self.assertNotIn("royal-sundaram__multiplier", ids,
                         "20%-copay plan must not reach a zero-copay user")
        self.assertNotIn("star-health__medi-classic", ids,
                         "₹5L-max plan must not reach a ₹15L requirement")
        self.assertTrue(ids, "good-fit plans must still survive")
        # Best-fit (A-grade, zero-copay, offers 15L) should be ranked first.
        self.assertEqual(
            ids[0], "niva-bupa__reassure-2",
            "Top recommendation must be the best profile-fit plan.",
        )

    def test_demographic_filter_still_runs(self):
        # apply_profile_filter (age/senior/maternity) must still be composed.
        senior = _chunk("star-health__red-carpet",
                        "Senior Citizens Red Carpet",
                        policy_type="indemnity",
                        sum_insured_options=[1500000])
        senior["min_entry_age"] = 60
        filtered, _ = filter_pipeline(
            [senior, GOOD_FIT_A], profile=FAILING_PROFILE,
            query="x", intent="recommendation",
        )
        ids = [c["policy_id"] for c in filtered]
        self.assertNotIn("star-health__red-carpet", ids,
                         "29yo must not see a senior-only plan")


if __name__ == "__main__":
    unittest.main()
