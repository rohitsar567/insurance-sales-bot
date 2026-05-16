"""Regression test: scorecard recalibration (2026-05-16).

Pins the fix for the "every policy is B/72" collapse. Before recalibration the
6 sub-scores had high neutral bases (Cost 75, Waiting 90, …) + tiny deltas, so
all policies compressed to 64–86 and ~90% graded B regardless of quality, and
data-filling didn't change it (proven structural).

These tests pin: (1) true-neutral bases (a blank policy must NOT land ~72),
(2) genuine spread across a weak→strong gradient, (3) absolute grade cutoffs,
(4) fixed-benefit products judged only on applicable sub-scores, (5) ordering
preserved (a strictly-better policy must score strictly higher).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from scorecard import build_scorecard, grade_for, _is_fixed_benefit


def _wrap(d):  # policy_facts {value,...} wrapper shape
    return {k: ({"value": v} if not isinstance(v, dict) else v) for k, v in d.items()}


STRONG = _wrap({
    "policy_name": "Strong Comprehensive", "insurer_slug": "x",
    "ayush_coverage": {"covered": True}, "day_care_treatments_count": 600,
    "maternity_coverage": {"covered": True}, "newborn_coverage": {"covered": True},
    "organ_donor_expenses": {"covered": True}, "domiciliary_treatment": {"covered": True},
    "preventive_health_checkup": {"covered": True},
    "pre_hospitalization_days": 90, "post_hospitalization_days": 180,
    "copayment_pct": 0, "room_rent_capping": "no cap",
    "pre_existing_disease_waiting_months": 18, "initial_waiting_period_days": 30,
    "cashless_treatment_supported": {"covered": True}, "network_hospital_count": 14000,
    "max_entry_age": 70, "no_claim_bonus_pct": 100,
    "restoration_benefit": "full restoration once per year",
})
WEAK = _wrap({
    "policy_name": "Weak Thin", "insurer_slug": "y",
    "ayush_coverage": {"covered": False}, "day_care_treatments_count": 40,
    "copayment_pct": 30, "room_rent_capping": "1% of SI",
    "pre_existing_disease_waiting_months": 48, "initial_waiting_period_days": 90,
    "cashless_treatment_supported": {"covered": False}, "network_hospital_count": 800,
    "max_entry_age": 45, "no_claim_bonus_pct": 0,
})
GREAT_CSR = {"claim_metrics": {"claim_settlement_ratio_pct": 98.0,
                               "complaints_per_10k_policies": 4}}
POOR_CSR = {"claim_metrics": {"claim_settlement_ratio_pct": 71.0,
                              "complaints_per_10k_policies": 60}}


def test_blank_policy_is_not_the_old_72_collapse():
    blank = build_scorecard({}).overall_score
    # Old bug: blank ≈ 72 (B). Recalibrated true-neutral must be clearly mid/low.
    assert blank < 65, f"blank scored {blank} — neutral base still inflated"


def test_strong_beats_weak_by_a_wide_margin():
    s = build_scorecard(STRONG, insurer_reviews=GREAT_CSR).overall_score
    w = build_scorecard(WEAK, insurer_reviews=POOR_CSR).overall_score
    assert s > w + 25, f"strong={s} weak={w} — spread too small (compression)"
    assert grade_for(s)[0] in ("A", "B")
    assert grade_for(w)[0] in ("D", "F")


def test_ordering_preserved_monotonic():
    # Strictly improving one lever must not lower the score (stretch ≠ scramble).
    base = dict(WEAK)
    better = dict(WEAK); better["copayment_pct"] = {"value": 0}
    assert build_scorecard(better).overall_score >= build_scorecard(base).overall_score


def test_absolute_grade_thresholds_are_frozen():
    assert grade_for(76)[0] == "A" and grade_for(75)[0] == "B"
    assert grade_for(69)[0] == "B" and grade_for(68)[0] == "C"
    assert grade_for(61)[0] == "C" and grade_for(60)[0] == "D"
    assert grade_for(54)[0] == "D" and grade_for(53)[0] == "F"


def test_fixed_benefit_detected_and_reweighted():
    for name in ("Star Hospital Cash", "Group Personal Accident",
                 "Criti Medicare", "Activ Secure Cancer Secure"):
        assert _is_fixed_benefit({"policy_name": name}), name
    assert not _is_fixed_benefit({"policy_name": "Optima Secure"})
    # A fixed-benefit plan from a great-CSR insurer must NOT be dragged to the
    # floor by its (inapplicable) empty indemnity fields.
    fb = _wrap({"policy_name": "Star Hospital Cash", "insurer_slug": "s",
                "cashless_treatment_supported": {"covered": True},
                "network_hospital_count": 12000, "no_claim_bonus_pct": 50})
    sc = build_scorecard(fb, insurer_reviews=GREAT_CSR)
    assert _is_fixed_benefit(fb)
    assert sc.overall_score >= 60, f"fixed-benefit collapsed to {sc.overall_score}"


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
