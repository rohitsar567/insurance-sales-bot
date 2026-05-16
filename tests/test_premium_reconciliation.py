"""KI-278 (2026-05-16) — header "Premium range" chip vs per-settings panel
reconciliation + smoker / family-history wiring + full-profile exhaustiveness.

Pins the bug from Image#7:

  For ONE profile the header chip showed ₹6,500–₹26,500/yr while opening the
  panel showed ₹16,235–₹21,965 (point ₹19,100). Two contradictory numbers
  for the same person.

Root cause: estimate_premium_band() hard-coded sum_insured_default=₹10L and
IGNORED the profile, while PremiumCalculatorPanel seeded its SI slider from
desired_sum_insured_inr → existing_cover_inr → ₹10L. A user who stated a
₹25L target therefore had the header priced at ₹10L and the panel at ₹25L.

Fix: estimate_premium_band() resolves SI via resolve_profile_sum_insured()
(the single source of truth shared with the panel/widget), prices the whole
basket at that SI, and rounds the band edges DIRECTIONALLY (floor min / ceil
max) so the displayed band is a strict superset of every basket member —
making the per-settings panel point ALWAYS land inside the header band.
"""

from backend.premium_calculator import (
    _DEFAULT_BAND_POLICY_IDS,
    bulk_estimate,
    estimate,
    estimate_premium_band,
    resolve_profile_sum_insured,
)


# ---------------------------------------------------------------------------
# resolve_profile_sum_insured — the shared SI contract
# ---------------------------------------------------------------------------

def test_si_precedence_desired_over_existing_over_default():
    # desired_sum_insured_inr wins
    assert (
        resolve_profile_sum_insured(
            {"desired_sum_insured_inr": 2_500_000, "existing_cover_inr": 800_000}
        )
        == 2_500_000
    )
    # existing_cover_inr next when no desired
    assert resolve_profile_sum_insured({"existing_cover_inr": 800_000}) == 800_000
    # legacy default when neither present
    assert resolve_profile_sum_insured({}) == 1_000_000
    assert resolve_profile_sum_insured(None) == 1_000_000


def test_si_unclamped_and_snapped_to_slider_grid():
    # SI RATIONALISATION (D2, 2026-05-16) — the global ₹5 L / ₹1 Cr clamp was
    # REMOVED. The user's actual stated target is now honoured (snapped to the
    # ₹50k grid only), not squashed into a synthetic envelope.
    # Above the OLD ceiling: prices at the real ₹2 Cr (was clamped to ₹1 Cr).
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 20_000_000}) == 20_000_000
    # Below the OLD floor: prices at the real ₹1 L (was clamped to ₹5 L).
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 100_000}) == 100_000
    # Off-grid still snaps to nearest ₹50k.
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 1_234_000}) == 1_250_000


def test_si_coerces_garbage_gracefully():
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": "2500000"}) == 2_500_000
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": None,
                                        "existing_cover_inr": "abc"}) == 1_000_000
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 0}) == 1_000_000


# ---------------------------------------------------------------------------
# Header ≠ panel reconciliation — the core defect
# ---------------------------------------------------------------------------

def _panel_points_for(profile: dict) -> list[int]:
    """The per-settings panel = one basket policy priced at the SAME
    profile-resolved SI the header band uses. Reproduce every basket member's
    point so we can assert each one lies inside the displayed band."""
    si = resolve_profile_sum_insured(profile)
    rows = bulk_estimate(
        list(_DEFAULT_BAND_POLICY_IDS),
        profile=profile,
        overrides={pid: {"sum_insured_inr": si} for pid in _DEFAULT_BAND_POLICY_IDS},
    )
    return sorted(r.premium_inr_annual for r in rows.values() if r.premium_inr_annual)


import pytest


@pytest.mark.parametrize(
    "profile,label",
    [
        ({"age": 35, "location_tier": "metro", "dependents": "self",
          "desired_sum_insured_inr": 2_500_000}, "defect profile (₹25L stated)"),
        ({"age": 35, "location_tier": "metro", "dependents": "self"},
         "no SI signal (₹10L default)"),
        ({"age": 55, "location_tier": "tier2", "dependents": "spouse and 2 kids",
          "smoker": True, "family_medical_history": ["cancer"],
          "existing_cover_inr": 800_000}, "rich profile"),
        ({"age": 28, "location_tier": "metro", "dependents": "parents",
          "parents_age_max": 72, "parents_has_ped": True}, "parents-on-cover"),
    ],
)
def test_header_band_is_superset_of_every_panel_point(profile, label):
    band = estimate_premium_band(dict(profile))
    points = _panel_points_for(profile)
    assert points, f"no basket points for {label}"
    for p in points:
        assert band["min_inr"] <= p <= band["max_inr"], (
            f"{label}: panel point ₹{p:,} fell OUTSIDE header band "
            f"₹{band['min_inr']:,}–₹{band['max_inr']:,} — header≠panel regression"
        )


def test_band_exposes_resolved_si_for_panel_alignment():
    band = estimate_premium_band({"desired_sum_insured_inr": 2_500_000})
    assert band["sum_insured_used"] == 2_500_000
    # And it is the same value the panel/widget would seed its slider with.
    assert band["sum_insured_used"] == resolve_profile_sum_insured(
        {"desired_sum_insured_inr": 2_500_000}
    )


def test_band_moves_with_stated_si_was_the_bug():
    """Pre-fix this returned an IDENTICAL band regardless of stated SI."""
    base = {"age": 35, "location_tier": "metro", "dependents": "self"}
    b_10l = estimate_premium_band(dict(base))
    b_25l = estimate_premium_band({**base, "desired_sum_insured_inr": 2_500_000})
    assert b_25l["max_inr"] > b_10l["max_inr"]
    assert b_25l["sum_insured_used"] == 2_500_000
    assert b_10l["sum_insured_used"] == 1_000_000


# ---------------------------------------------------------------------------
# Smoker + family_medical_history wiring (defect #2)
# ---------------------------------------------------------------------------

def test_smoker_moves_both_band_and_per_policy():
    base = {"age": 40, "location_tier": "metro", "dependents": "self"}
    b0 = estimate_premium_band(dict(base))
    b1 = estimate_premium_band({**base, "smoker": True})
    assert b1["max_inr"] > b0["max_inr"], "smoker not reflected in header band"

    e0 = estimate(age=40, sum_insured_inr=1_000_000, policy_id="hdfc-ergo__optima-secure")
    e1 = estimate(age=40, sum_insured_inr=1_000_000, policy_id="hdfc-ergo__optima-secure",
                  smoker=True)
    assert e1.point_estimate_inr > e0.point_estimate_inr, "smoker not in per-policy estimate"


def test_family_medical_history_moves_both_band_and_per_policy():
    base = {"age": 40, "location_tier": "metro", "dependents": "self"}
    b0 = estimate_premium_band(dict(base))
    b1 = estimate_premium_band({**base, "family_medical_history": ["cancer", "diabetes"]})
    assert b1["max_inr"] > b0["max_inr"], "family history not in header band"

    e0 = estimate(age=40, sum_insured_inr=1_000_000, policy_id="hdfc-ergo__optima-secure")
    e1 = estimate(age=40, sum_insured_inr=1_000_000, policy_id="hdfc-ergo__optima-secure",
                  family_medical_history=["cancer", "diabetes"])
    assert e1.point_estimate_inr > e0.point_estimate_inr, "family history not in estimate"


# ---------------------------------------------------------------------------
# Exhaustiveness — every pricing-relevant SLOT_UNION field must move the band
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "delta,field",
    [
        ({"age": 60}, "age"),
        ({"location_tier": "tier2"}, "location_tier"),
        ({"dependents": "spouse and 2 kids"}, "dependents/family_size"),
        ({"smoker": True}, "smoker"),
        ({"copay_pct": 30}, "copay_pct"),
        ({"family_medical_history": ["cancer", "heart"]}, "family_medical_history"),
        ({"health_conditions": ["diabetes"]}, "health_conditions"),
        ({"existing_cover_inr": 800_000}, "existing_cover_inr"),
        ({"desired_sum_insured_inr": 2_500_000}, "desired_sum_insured_inr"),
    ],
)
def test_every_pricing_slot_moves_the_header_band(delta, field):
    base = {"age": 35, "location_tier": "metro", "dependents": "self"}
    b0 = estimate_premium_band(dict(base))
    b1 = estimate_premium_band({**base, **delta})
    moved = (
        b1["min_inr"] != b0["min_inr"]
        or b1["max_inr"] != b0["max_inr"]
        or b1["sum_insured_used"] != b0["sum_insured_used"]
    )
    assert moved, f"{field} dropped on the header-band path (no effect)"


def test_parents_on_cover_moves_band():
    base = {"age": 35, "location_tier": "metro", "dependents": "parents"}
    b0 = estimate_premium_band(dict(base))
    b1 = estimate_premium_band({**base, "parents_age_max": 72})
    b2 = estimate_premium_band({**base, "parents_age_max": 72, "parents_has_ped": True})
    assert b1["max_inr"] > b0["max_inr"], "parents_age_max dropped"
    assert b2["max_inr"] > b1["max_inr"], "parents_has_ped dropped"
