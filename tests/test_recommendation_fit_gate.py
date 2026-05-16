"""KI-280 (2026-05-16) — UNIFIED recommendation-fit gate.

Pins all 7 personas from the live Playwright audit
(/tmp/persona_[1-7]_result.json) so the CITED card list and the advisory
prose are gated by the SAME fitness logic.

The unified gate (backend/retrieval_filters.filter_pipeline, on the live
single_brain → brain_tools.retrieve_policies path) requires the cited set
to pass ALL of:

  1. Product-type vs intent  — no fixed-benefit / hospital-cash / PA / CI
     to a comprehensive-indemnity buyer (KI-279, preserved). Inverse
     preserved: top-up seeker (P4) + explicit-CI seeker (P6) still get
     those products.
  2. Hard eligibility        — when insuring seniors / parents (~70), drop
     plans whose max entry age can't accept them; surface senior-eligible.
  3. Required features        — explicit maternity/newborn need → only cite
     plans whose facts confirm it (unverified ones ranked strictly below).
  4. Objective / fit ranking  — cited list ordered by scorecard fit-score /
     stated objective; #1 = best fit for THIS profile (fixes the
     grade/rank inversion + P5 cost-objective lead).
  5. Dedup                    — same-product / marketing-variant duplicates
     collapsed (shared canonical/UIN identity from policy_identity).

Audit personas (transcripts + observed-bad outcomes drive the asserts):

  P1 Young first-timer comprehensive — Star Hospital Cash wrongly cited;
     C/65 ranked above B/75.  EXPECT: no fixed-benefit; #1 = best grade.
  P2 Senior + PED                    — New National Parivar Mediclaim cited
     twice; A/77 ranked LAST.  EXPECT: deduped; A-grade #1.
  P3 Family + maternity (very imp.)  — non-maternity plans cited equal to
     the maternity-confirmed one; Star Hospital Cash cited.
     EXPECT: maternity-confirmed first, fixed-benefit dropped.
  P4 Top-up seeker (INVERSE)         — Advanced Top Up cited twice.
     EXPECT: top-up KEPT (inverse preserved) + deduped.
  P5 Budget, cost is #1 need         — leads with non-cheapest.
     EXPECT: cost-objective ⇒ cheapest-appropriate ranks first.
  P6 Critical-illness seeker (INV.)  — EXPECT: CI plans KEPT.
  P7 Parents both 70 + PED           — max-entry-65 plans cited; Chola
     Flexi Supreme + Flexi Health (same insurer) both cited.
     EXPECT: entry-age-65 plans dropped; senior-eligible surfaced; deduped.

Run:
    .venv/bin/python -m pytest -q tests/test_recommendation_fit_gate.py
"""

from __future__ import annotations

import unittest

from backend.policy_identity import canonical_key, normalize_uin, product_key
from backend.retrieval_filters import (
    apply_eligibility_filter,
    dedup_by_policy,
    filter_pipeline,
    rank_by_profile_fit,
)
from backend.single_brain import _build_recommendation_citations


def _chunk(
    policy_id,
    policy_name,
    *,
    score=0.5,
    policy_type=None,
    policy_type_canonical=None,
    uin_code=None,
    copay_pct=None,
    sum_insured_options=None,
    max_entry_age=None,
    min_entry_age=None,
    maternity=None,
    newborn=None,
    grade=None,
    overall_score=None,
    doc_type="policy",
):
    """Chunk dict shaped like brain_tools.retrieve_policies output AFTER the
    policy_facts + scorecard enrichment step (KI-280 added uin_code /
    max_entry_age / maternity_coverage / newborn_coverage)."""
    return {
        "policy_id": policy_id,
        "policy_name": policy_name,
        "insurer_slug": policy_id.split("__")[0],
        "doc_type": doc_type,
        "score": score,
        "chunk_text": "",
        "policy_type_indemnity_or_fixed": policy_type_canonical,
        "policy_type": policy_type,
        "uin_code": uin_code,
        "deductible_amount": None,
        "co_payment_pct": copay_pct,
        "sum_insured_options": sum_insured_options,
        "max_entry_age": max_entry_age,
        "min_entry_age": min_entry_age,
        "maternity_coverage": maternity,
        "newborn_coverage": newborn,
        "_grade": grade,
        "_overall_score": overall_score,
    }


# ── Shared catalog slices ───────────────────────────────────────────────────

GOOD_INDEMNITY_A = _chunk(
    "niva-bupa__reassure-3", "ReAssure 3.0",
    score=0.55, policy_type_canonical="indemnity", policy_type="family_floater",
    uin_code="MAXHLIP21177V032122", copay_pct=0,
    sum_insured_options=[1_000_000, 1_500_000, 2_000_000, 5_000_000],
    max_entry_age=70, maternity=True, newborn=True, grade="A", overall_score=88,
)
DECENT_INDEMNITY_C = _chunk(
    "cholamandalam__flexi-health-supreme__wordings", "Chola Flexi Health Supreme",
    score=0.72, policy_type_canonical="family_floater", policy_type=None,
    uin_code="CHOHLIP27040V032627", copay_pct=0,
    sum_insured_options=[1_000_000, 1_500_000, 2_500_000],
    max_entry_age=75, maternity=True, grade="C", overall_score=65,
)
STAR_HOSPITAL_CASH = _chunk(
    "star-health__star-hospital-cash__brochure", "Star Hospital Cash",
    score=0.71, policy_type="hospital_cash", uin_code="SHAHLIP20046V011920",
    max_entry_age=65, grade="D", overall_score=60,
)


# ════════════════════════════════════════════════════════════════════════════
# Shared canonical-identity helper (Rule 5 building block)
# ════════════════════════════════════════════════════════════════════════════
class TestPolicyIdentity(unittest.TestCase):
    def test_uin_primary(self):
        a = {"policy_id": "hdfc-ergo__optima-secure__wordings",
             "uin_code": "HDFHLIP25041V062425"}
        b = {"policy_id": "hdfc-ergo__optima-secure-older-variant",
             "uin_code": "HDFHLIP25041V062425"}
        self.assertEqual(canonical_key(a), canonical_key(b),
                         "Same UIN ⇒ same product (marketing rename).")

    def test_product_key_collapses_doctype_siblings(self):
        self.assertEqual(
            product_key("acko__health-iii__wordings"), "acko__health-iii")
        self.assertEqual(
            product_key("acko__health-iii__brochure"), "acko__health-iii")
        a = {"policy_id": "acko__health-iii__wordings"}
        b = {"policy_id": "acko__health-iii__brochure"}
        self.assertEqual(canonical_key(a), canonical_key(b))

    def test_distinct_products_stay_distinct(self):
        a = {"policy_id": "cholamandalam__flexi-health-supreme__wordings",
             "uin_code": "CHOHLIP27040V032627"}
        b = {"policy_id": "cholamandalam__flexi-health__wordings",
             "uin_code": "CHOHLIP24145V062526"}
        self.assertNotEqual(canonical_key(a), canonical_key(b),
                            "Different UIN ⇒ genuinely different products.")

    def test_normalize_uin_rejects_junk(self):
        self.assertEqual(normalize_uin(""), "")
        self.assertEqual(normalize_uin("N/A"), "")
        self.assertEqual(normalize_uin({"value": "SHAHLIP20046V011920"}),
                         "SHAHLIP20046V011920")


# ════════════════════════════════════════════════════════════════════════════
# P1 — Young first-timer comprehensive (15L, zero co-pay)
# ════════════════════════════════════════════════════════════════════════════
P1 = {
    "age": 29, "location_tier": "metro", "income_band": "25L+",
    "primary_goal": "first_buy", "existing_cover_inr": 0,
    "health_conditions": ["none"], "desired_sum_insured_inr": 1_500_000,
    "copay_pct": 0, "dependents": "self",
}


class TestPersona1(unittest.TestCase):
    def test_no_fixed_benefit_and_best_grade_first(self):
        catalog = [DECENT_INDEMNITY_C, STAR_HOSPITAL_CASH, GOOD_INDEMNITY_A]
        filtered, guard = filter_pipeline(
            catalog, profile=P1,
            query="comprehensive metro 15 lakh zero co-pay first-time buyer",
            intent="recommendation")
        ids = [c["policy_id"] for c in filtered]
        self.assertNotIn("star-health__star-hospital-cash__brochure", ids,
                         "P1: fixed-benefit hospital-cash must not be cited")
        self.assertTrue(ids)
        self.assertEqual(ids[0], "niva-bupa__reassure-3",
                         "P1: #1 must be the best-fit (A/88), not C/65")


# ════════════════════════════════════════════════════════════════════════════
# P2 — Senior + PED. Dedup + grade/rank inversion (A/77 was LAST).
# ════════════════════════════════════════════════════════════════════════════
P2 = {
    "age": 58, "location_tier": "tier-2", "income_band": "10L-25L",
    "primary_goal": "first_buy", "existing_cover_inr": 0,
    "health_conditions": ["diabetes", "hypertension"],
    "desired_sum_insured_inr": 1_000_000, "copay_pct": 10,
    "dependents": "self+spouse",
}
P2_NNP_A = _chunk(
    "national-insurance__new-national-parivar-mediclaim__wordings",
    "New National Parivar Mediclaim", score=0.70,
    policy_type_canonical="family_floater", uin_code="NICHLIP26999V010203",
    copay_pct=10, sum_insured_options=[500_000, 1_000_000, 1_500_000],
    max_entry_age=80, grade="C", overall_score=66)
P2_NNP_DUP = _chunk(
    "national-insurance__new-national-parivar-mediclaim__brochure",
    "New National Parivar Mediclaim", score=0.62,
    policy_type_canonical="family_floater", uin_code="NICHLIP26999V010203",
    copay_pct=10, sum_insured_options=[500_000, 1_000_000, 1_500_000],
    max_entry_age=80, grade="C", overall_score=66)
P2_REASSURE_A = _chunk(
    "niva-bupa__reassure-3", "ReAssure 3.0", score=0.55,
    policy_type_canonical="indemnity", uin_code="MAXHLIP21177V032122",
    copay_pct=10, sum_insured_options=[1_000_000, 1_500_000],
    max_entry_age=70, grade="A", overall_score=77)


class TestPersona2(unittest.TestCase):
    def test_dedup_variant_and_a_grade_leads(self):
        catalog = [P2_NNP_A, P2_NNP_DUP, P2_REASSURE_A]
        filtered, _ = filter_pipeline(
            catalog, profile=P2,
            query="senior 58 family floater diabetes hypertension 10 lakh",
            intent="recommendation")
        ids = [c["policy_id"] for c in filtered]
        # Same-UIN doctype siblings collapse to one card.
        nnp = [i for i in ids if "new-national-parivar" in i]
        self.assertEqual(len(nnp), 1,
                         "P2: New National Parivar must appear ONCE, not twice")
        self.assertEqual(ids[0], "niva-bupa__reassure-3",
                         "P2: A/77 must lead, not be last")


# ════════════════════════════════════════════════════════════════════════════
# P3 — Family + maternity (explicitly "very important")
# ════════════════════════════════════════════════════════════════════════════
P3 = {
    "age": 34, "location_tier": "metro", "income_band": "25L-40L",
    "primary_goal": "first_buy maternity newborn family floater",
    "existing_cover_inr": 0, "health_conditions": ["none"],
    "desired_sum_insured_inr": 2_500_000, "copay_pct": 0,
    "dependents": "self+spouse+kids",
}
P3_MATERNITY_OK = _chunk(
    "cholamandalam__flexi-health-supreme__wordings", "Chola Flexi Health Supreme",
    score=0.60, policy_type_canonical="family_floater",
    uin_code="CHOHLIP27040V032627", copay_pct=0,
    sum_insured_options=[2_500_000], max_entry_age=75,
    maternity=True, newborn=True, grade="C", overall_score=65)
P3_NO_MATERNITY = _chunk(
    "hdfc-ergo__optima-secure", "my:Optima Secure", score=0.71,
    policy_type_canonical="indemnity", uin_code="HDFHLIP25041V062425",
    copay_pct=0, sum_insured_options=[2_500_000], max_entry_age=65,
    maternity=False, newborn=False, grade="B", overall_score=75)
P3_HOSPITAL_CASH = _chunk(
    "star-health__star-hospital-cash__brochure", "Star Hospital Cash",
    score=0.68, policy_type="hospital_cash", uin_code="SHAHLIP20046V011920",
    max_entry_age=65, maternity=True, grade="D", overall_score=60)


class TestPersona3(unittest.TestCase):
    def test_maternity_confirmed_first_fixed_benefit_dropped(self):
        catalog = [P3_MATERNITY_OK, P3_NO_MATERNITY, P3_HOSPITAL_CASH]
        filtered, _ = filter_pipeline(
            catalog, profile=P3,
            query="family floater maternity newborn 25 lakh metro first-time",
            intent="recommendation")
        ids = [c["policy_id"] for c in filtered]
        self.assertNotIn("star-health__star-hospital-cash__brochure", ids,
                         "P3: hospital-cash is not a comprehensive family plan")
        self.assertTrue(ids)
        self.assertEqual(
            ids[0], "cholamandalam__flexi-health-supreme__wordings",
            "P3: maternity-confirmed plan must rank above the "
            "non-maternity B-grade plan when maternity is required")
        # The non-maternity plan, if present at all, ranks strictly below.
        if "hdfc-ergo__optima-secure" in ids:
            self.assertLess(
                ids.index("cholamandalam__flexi-health-supreme__wordings"),
                ids.index("hdfc-ergo__optima-secure"))


# ════════════════════════════════════════════════════════════════════════════
# P4 — Top-up seeker (INVERSE — top-up must be KEPT). + dedup of ×2.
# ════════════════════════════════════════════════════════════════════════════
P4 = {
    "age": 44, "location_tier": "metro", "income_band": "10L-25L",
    "primary_goal": "cheapest top-up over existing employer cover",
    "existing_cover_inr": 500_000, "health_conditions": ["none"],
    "desired_sum_insured_inr": 2_000_000, "copay_pct": 10,
    "dependents": "self",
}
P4_TOPUP_A = _chunk(
    "royal-sundaram__advanced-top-up__wordings", "Advanced Top Up",
    score=0.76, policy_type_canonical="super_top_up",
    uin_code="RSAHLIP21055V032021", copay_pct=0,
    sum_insured_options=[1_000_000, 2_000_000], max_entry_age=65,
    grade="A", overall_score=76)
P4_TOPUP_DUP = _chunk(
    "royal-sundaram__advanced-top-up__brochure", "Advanced Top Up",
    score=0.69, policy_type_canonical="super_top_up",
    uin_code="RSAHLIP21055V032021", copay_pct=0,
    sum_insured_options=[1_000_000, 2_000_000], max_entry_age=65,
    grade="A", overall_score=76)
P4_SBI = _chunk(
    "sbi-general__arogya-supreme", "Arogya Supreme", score=0.60,
    policy_type_canonical="family_floater", uin_code="SBIHLIP21099V012021",
    copay_pct=10, sum_insured_options=[2_000_000], max_entry_age=65,
    grade="B", overall_score=75)


class TestPersona4Inverse(unittest.TestCase):
    def test_topup_kept_and_deduped(self):
        catalog = [P4_TOPUP_A, P4_TOPUP_DUP, P4_SBI]
        filtered, _ = filter_pipeline(
            catalog, profile=P4,
            query="cheapest top-up over existing 5 lakh employer cover 20 lakh",
            intent="recommendation")
        ids = [c["policy_id"] for c in filtered]
        topups = [i for i in ids if "advanced-top-up" in i]
        self.assertEqual(len(topups), 1,
                         "P4: Advanced Top Up must appear ONCE (was ×2)")
        self.assertTrue(any("advanced-top-up" in i for i in ids),
                        "P4 INVERSE: top-up seeker MUST still get top-up plans")


# ════════════════════════════════════════════════════════════════════════════
# P5 — Budget-constrained; cost is the #1 stated objective.
# ════════════════════════════════════════════════════════════════════════════
P5 = {
    "age": 31, "location_tier": "tier-3", "income_band": "<10L",
    "primary_goal": "cost_optimize cheapest lowest premium",
    "existing_cover_inr": 0, "health_conditions": ["none"],
    "desired_sum_insured_inr": 500_000, "copay_pct": 30,
    "dependents": "self",
}
# The live P5 audit defect was COSINE-DRIVEN inversion: the bot led with
# the highest-cosine plan even though, at the same scorecard fit tier, it
# was not the cost-appropriate pick for a "lowest premium is my top
# priority" user. We pin the true root cause: at an equal scorecard tier,
# a higher-cosine plan must NOT out-rank an equal-fit plan when cost is the
# stated #1 objective (cosine is damped; the scorecard fit-score decides).
P5_CHEAPEST = _chunk(
    "royal-sundaram__arogya-sanjeevani", "Arogya Sanjeevani", score=0.55,
    policy_type_canonical="indemnity", uin_code="RSAHLIP21010V012021",
    copay_pct=5, sum_insured_options=[500_000], max_entry_age=65,
    grade="C", overall_score=66)
P5_PRICIER = _chunk(
    "hdfc-ergo__optima-secure", "my:Optima Secure", score=0.75,
    policy_type_canonical="indemnity", uin_code="HDFHLIP25041V062425",
    copay_pct=0, sum_insured_options=[500_000, 1_000_000], max_entry_age=65,
    grade="C", overall_score=64)


class TestPersona5(unittest.TestCase):
    def test_cost_objective_not_cosine_dominated(self):
        # Both plans are the SAME scorecard grade tier (C). The pricier
        # plan's ONLY advantage is raw cosine (0.75 vs 0.55) — exactly the
        # live-audit inversion. With cost the #1 objective, the gate damps
        # cosine so the marginally-better-fit cost-appropriate plan (the
        # canonical Arogya Sanjeevani standard product, 66 vs 64) leads
        # instead of the higher-cosine one.
        ranked = rank_by_profile_fit([P5_PRICIER, P5_CHEAPEST], P5)
        ids = [c["policy_id"] for c in ranked]
        self.assertEqual(len(ids), 2)
        self.assertEqual(
            ids[0], "royal-sundaram__arogya-sanjeevani",
            "P5: cost objective — fit-score decides, NOT raw cosine")

    def test_without_cost_objective_cosine_still_breaks_ties(self):
        # Sanity / non-regression: for a NON-cost profile the cosine weight
        # is unchanged, so the higher-cosine plan can still win a near-tie.
        non_cost = dict(P5, primary_goal="first_buy comprehensive")
        ranked = rank_by_profile_fit([P5_CHEAPEST, P5_PRICIER], non_cost)
        self.assertEqual(
            [c["policy_id"] for c in ranked][0], "hdfc-ergo__optima-secure",
            "non-cost profile: cosine still contributes normally")


# ════════════════════════════════════════════════════════════════════════════
# P6 — Critical-illness seeker (INVERSE — CI must be KEPT).
# ════════════════════════════════════════════════════════════════════════════
P6 = {
    "age": 42, "location_tier": "metro", "income_band": "25L+",
    "primary_goal": "critical illness lump-sum fixed-benefit plan only",
    "existing_cover_inr": 1_500_000, "health_conditions": ["none"],
    "desired_sum_insured_inr": 2_500_000, "copay_pct": 0,
    "dependents": "self",
}
P6_CI_1 = _chunk(
    "national-insurance__national-critical-illness", "National Critical Illness",
    score=0.54, policy_type="critical_illness",
    policy_type_canonical="fixed_benefit", uin_code="NICCIIP20001V010203",
    sum_insured_options=[2_500_000], max_entry_age=65, grade="D",
    overall_score=54)
P6_CI_2 = _chunk(
    "go-digit__digit-health-care-plus", "Digit Health Care Plus", score=0.75,
    policy_type="critical_illness", policy_type_canonical="fixed_benefit",
    uin_code="GODHLIP21099V012021", sum_insured_options=[2_500_000],
    max_entry_age=65, grade="B", overall_score=75)


class TestPersona6Inverse(unittest.TestCase):
    def test_ci_plans_kept_for_explicit_ci_seeker(self):
        catalog = [P6_CI_1, P6_CI_2]
        filtered, _ = filter_pipeline(
            catalog, profile=P6,
            query="critical illness lump-sum 25 lakh fixed benefit",
            intent="recommendation")
        ids = {c["policy_id"] for c in filtered}
        self.assertIn("national-insurance__national-critical-illness", ids,
                      "P6 INVERSE: explicit CI seeker MUST still get CI plans")
        self.assertIn("go-digit__digit-health-care-plus", ids)


# ════════════════════════════════════════════════════════════════════════════
# P7 — Parents both 70 + PED. Entry-age gate + dedup + senior surfacing.
# ════════════════════════════════════════════════════════════════════════════
P7 = {
    "age": 36, "location_tier": "metro", "income_band": "10L-25L",
    "primary_goal": "parents health cover senior citizens",
    "existing_cover_inr": 0,
    "health_conditions": ["father diabetes", "mother hypertension arthritis"],
    "desired_sum_insured_inr": 1_000_000, "copay_pct": 10,
    "dependents": "self+parents", "parents_to_insure": True,
    "parents_age_max": 70, "parents_has_ped": True,
}
P7_CHOLA_SUPREME = _chunk(
    "cholamandalam__flexi-health-supreme__wordings", "Chola Flexi Health Supreme",
    score=0.72, policy_type_canonical="family_floater",
    uin_code="CHOHLIP27040V032627", copay_pct=10,
    sum_insured_options=[1_000_000], max_entry_age=75, grade="C",
    overall_score=65)
P7_CHOLA_FLEXI = _chunk(   # same insurer, DIFFERENT product, max_entry 65
    "cholamandalam__flexi-health__wordings", "Flexi Health", score=0.67,
    policy_type_canonical="individual", uin_code="CHOHLIP24145V062526",
    copay_pct=10, sum_insured_options=[1_000_000], max_entry_age=65,
    grade="C", overall_score=67)
P7_OPTIMA_65 = _chunk(
    "hdfc-ergo__optima-secure", "my:Optima Secure", score=0.70,
    policy_type_canonical="indemnity", uin_code="HDFHLIP25041V062425",
    copay_pct=10, sum_insured_options=[1_000_000], max_entry_age=65,
    grade="B", overall_score=75)
P7_SENIOR_OK = _chunk(
    "star-health__senior-citizens-red-carpet__brochure",
    "Star Senior Citizens Red Carpet", score=0.50,
    policy_type_canonical="indemnity", uin_code="SHAHLIP26041V082526",
    copay_pct=10, sum_insured_options=[1_000_000], min_entry_age=60,
    max_entry_age=75, grade="B", overall_score=74)


class TestPersona7(unittest.TestCase):
    def test_entry_age_gate_dedup_and_senior_surfaced(self):
        catalog = [P7_CHOLA_SUPREME, P7_CHOLA_FLEXI, P7_OPTIMA_65,
                   P7_SENIOR_OK]
        filtered, _ = filter_pipeline(
            catalog, profile=P7,
            query="parents cover both age 70 diabetes hypertension senior "
                  "citizen 10 lakh",
            intent="recommendation")
        ids = [c["policy_id"] for c in filtered]
        # max_entry_age 65 < 70 ⇒ those plans cannot accept 70yo parents.
        self.assertNotIn("cholamandalam__flexi-health__wordings", ids,
                         "P7: max_entry_age 65 cannot accept a 70yo parent")
        self.assertNotIn("hdfc-ergo__optima-secure", ids,
                         "P7: max_entry_age 65 cannot accept a 70yo parent")
        # Entry-age-75 plans survive; senior-eligible surfaces.
        self.assertIn("star-health__senior-citizens-red-carpet__brochure", ids,
                      "P7: a senior-eligible plan must be surfaced")
        self.assertIn("cholamandalam__flexi-health-supreme__wordings", ids,
                      "P7: max_entry_age 75 accepts a 70yo parent")

    def test_same_insurer_distinct_products_not_collapsed(self):
        # Chola Supreme vs Flexi Health are DIFFERENT UINs ⇒ they are NOT a
        # dedup pair; the gate must not over-collapse them. (Flexi Health is
        # dropped here by the age gate, not by dedup.)
        self.assertNotEqual(
            canonical_key(P7_CHOLA_SUPREME), canonical_key(P7_CHOLA_FLEXI))


# ════════════════════════════════════════════════════════════════════════════
# Direct unit coverage for the new gate primitives
# ════════════════════════════════════════════════════════════════════════════
class TestEntryAgeEligibility(unittest.TestCase):
    def test_parents_age_drives_entry_age_gate(self):
        kept = apply_eligibility_filter(
            [P7_OPTIMA_65, P7_CHOLA_SUPREME], P7)
        ids = {c["policy_id"] for c in kept}
        self.assertNotIn("hdfc-ergo__optima-secure", ids)
        self.assertIn("cholamandalam__flexi-health-supreme__wordings", ids)

    def test_payer_only_profile_does_not_trip_entry_age(self):
        # P1 is self-only, age 29 — the parents-age gate must NOT fire and
        # drop a normal adult plan whose max_entry_age is 65.
        kept = apply_eligibility_filter([P3_NO_MATERNITY], P1)
        self.assertEqual(len(kept), 1,
                         "self-only 29yo must not be entry-age-gated by a "
                         "parents rule")


class TestMaternityRequirement(unittest.TestCase):
    def test_unverified_maternity_ranked_below_confirmed(self):
        ranked = rank_by_profile_fit([P3_NO_MATERNITY, P3_MATERNITY_OK], P3)
        ids = [c["policy_id"] for c in ranked]
        self.assertLess(
            ids.index("cholamandalam__flexi-health-supreme__wordings"),
            ids.index("hdfc-ergo__optima-secure"),
            "maternity-confirmed must outrank maternity=False when the "
            "profile explicitly needs maternity")

    def test_no_maternity_need_does_not_penalize(self):
        # P1 has no maternity need — the maternity rule must be inert.
        ranked = rank_by_profile_fit([P3_NO_MATERNITY, GOOD_INDEMNITY_A], P1)
        self.assertEqual(len(ranked), 2)


class TestCanonicalDedupInPipeline(unittest.TestCase):
    def test_dedup_by_policy_uses_canonical_key(self):
        deduped = dedup_by_policy([P2_NNP_A, P2_NNP_DUP])
        self.assertEqual(len(deduped), 1,
                         "same-UIN doctype siblings collapse to one")
        # Highest-score chunk wins.
        self.assertEqual(deduped[0]["policy_id"],
                         "national-insurance__new-national-parivar-mediclaim"
                         "__wordings")


# ════════════════════════════════════════════════════════════════════════════
# LIVE PATH — single_brain._build_recommendation_citations must emit the
# GATED, fit-ranked, canonically-deduped cited set (not the LLM's order).
# ════════════════════════════════════════════════════════════════════════════
def _live_chunk(pid, name, slug, score, cid, uin=None):
    return {
        "chunk_id": cid, "policy_id": pid, "policy_name": name,
        "insurer_slug": slug, "doc_type": "policy",
        "source_url": f"https://example.com/{pid}.pdf",
        "score": score, "uin_code": uin,
    }


class TestLivePathCitationGate(unittest.TestCase):
    def test_cards_ordered_by_gate_rank_not_llm_order(self):
        # filter_pipeline returns chunks in profile-fit order; the union the
        # citation builder sees preserves that. Best-fit appears FIRST.
        gated_stream = [
            _live_chunk("niva-bupa__reassure-3", "ReAssure 3.0",
                        "niva-bupa", 0.55, "c1", "MAXHLIP21177V032122"),
            _live_chunk("cholamandalam__flexi-health-supreme__wordings",
                        "Chola Flexi Health Supreme", "cholamandalam",
                        0.72, "c2", "CHOHLIP27040V032627"),
        ]
        # LLM marked them in the WRONG order (Chola first).
        cites, is_rec = _build_recommendation_citations(
            reply_text="see options",
            retrieved_chunks_all=gated_stream,
            marked_policy_ids=[
                "cholamandalam__flexi-health-supreme__wordings",
                "niva-bupa__reassure-3",
            ],
        )
        self.assertTrue(is_rec)
        self.assertEqual(
            [c["policy_id"] for c in cites],
            ["niva-bupa__reassure-3",
             "cholamandalam__flexi-health-supreme__wordings"],
            "Cited cards must follow the GATE's fit order, not the LLM's "
            "mark_recommendation order")

    def test_canonical_dedup_at_citation_layer(self):
        # Same UIN under two doctype-sibling ids across the turn's union.
        stream = [
            _live_chunk("national-insurance__new-national-parivar-mediclaim"
                        "__wordings", "New National Parivar Mediclaim",
                        "national-insurance", 0.70, "c1",
                        "NICHLIP26999V010203"),
            _live_chunk("national-insurance__new-national-parivar-mediclaim"
                        "__brochure", "New National Parivar Mediclaim",
                        "national-insurance", 0.62, "c2",
                        "NICHLIP26999V010203"),
        ]
        cites, is_rec = _build_recommendation_citations(
            reply_text="I recommend New National Parivar Mediclaim",
            retrieved_chunks_all=stream,
            marked_policy_ids=[],   # prose path
        )
        self.assertTrue(is_rec)
        self.assertEqual(len(cites), 1,
                         "same-UIN duplicate must be cited ONCE (audit P2)")
        self.assertEqual(cites[0]["chunk_id"], "c1",
                         "highest-score chunk hydrates the single card")


if __name__ == "__main__":
    unittest.main()
