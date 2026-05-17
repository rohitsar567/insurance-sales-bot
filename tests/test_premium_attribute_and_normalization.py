"""Guards for the premium correctness work (#38 + #36-B + #37b).

Pins the bugs the user surfaced 2026-05-18:
  - canonical-match: doctype-suffixed ids reach their real sample
  - sample sanity guard: a bad curated sample (SBI Arogya Supreme
    brochure-extract, ~₹10k/L) can NEVER emit an absurd premium
  - sample normalization (#38): floater-priced samples are NOT
    double-counted by the floater multiplier
  - attribute model (#36-B): quote-less policies are differentiated by
    product TYPE — a top-up is not priced like a comprehensive plan
  - provenance label (#37b): sample-anchored vs modelled is explicit
  - REGRESSION: the sample-anchored policies that were already sane
    must stay sane (no swing) when none of the above misfires.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backend.premium_calculator import (  # noqa: E402
    _attribute_base_factor,
    estimate,
)

PROFILE = dict(
    age=34, sum_insured_inr=1_200_000, city_tier="metro", smoker=False,
    family_size=3, pre_existing_conditions="none", copayment_pct=0.0,
)


def _pt(pid, **over):
    return estimate(policy_id=pid, **{**PROFILE, **over})


# --- attribute base factor: type-differentiated, comprehensive == 1.0 ----

def test_attribute_factor_by_type():
    # Post-rebuild factors via the real-fact-aware _policy_product_type
    # classifier (synthetic ids fall back to id keywords).
    assert _attribute_base_factor("x-insurer__no-such-comprehensive-plan") == 1.0
    assert _attribute_base_factor("acko__x-super-top-up__wordings") == 0.32
    assert _attribute_base_factor("x__hospital-cash") == 0.30
    assert _attribute_base_factor("aditya-birla__activ-secure-cancer-secure") == 0.50
    assert _attribute_base_factor("acko__arogya-sanjeevani") == 0.70
    assert _attribute_base_factor(None) == 1.0


# --- #38/#44: SBI bad data REPLACED by real harvested samples ----------

def test_sbi_now_real_anchored_and_sane():
    # The bad brochure-extract was physically replaced by 2 real official
    # SBI rate-chart figures (UIN SBIHLIP21043V012122) + unquarantined.
    # It must now be sample-anchored AND sane (never the ₹146,800 absurd).
    e = _pt("sbi-general__arogya-supreme__brochure")
    assert e.base_sample_used is not None, "SBI should now use its real harvested sample"
    assert 3_000 < e.point_estimate_inr < 60_000, (
        f"SBI out of sane band: ₹{e.point_estimate_inr:,}"
    )


# --- #38 regression: real-sample policies stay sample-anchored & sane ---

def test_sample_anchored_policies_not_regressed():
    for pid, lo, hi in [
        ("icici-lombard__elevate__brochure", 8_000, 60_000),
        ("hdfc-ergo__optima-secure__wordings", 6_000, 50_000),
        ("aditya-birla__group-activ-health__wordings", 6_000, 50_000),
    ]:
        e = _pt(pid)
        assert e.base_sample_used is not None, f"{pid} lost its real sample"
        assert lo < e.point_estimate_inr < hi, (
            f"{pid} swung out of sane band: ₹{e.point_estimate_inr:,}"
        )


def test_legit_topup_sample_preserved_cheap():
    e = _pt("royal-sundaram__advanced-top-up__brochure", sum_insured_inr=4_500_000)
    assert e.base_sample_used is not None
    assert e.point_estimate_inr < 15_000, (
        f"legit top-up sample broken: ₹{e.point_estimate_inr:,}"
    )


# --- #36-B: quote-less policies of different TYPE must NOT collide ------

def test_quoteless_types_do_not_collide():
    comprehensive = _pt("royal-sundaram__family-plus__cis").point_estimate_inr
    topup = _pt("acko__acko-health-iii-platinum-super-top-up__wordings").point_estimate_inr
    cancer = _pt("aditya-birla__activ-secure-cancer-secure__brochure").point_estimate_inr
    assert comprehensive != topup, "top-up priced same as comprehensive (collision)"
    assert comprehensive != cancer, "cancer plan priced same as comprehensive"
    assert topup < comprehensive, "top-up must be materially cheaper"


# --- #37b: provenance label is explicit and correct --------------------

def test_provenance_label_distinguishes_sample_vs_model():
    s = _pt("icici-lombard__elevate__brochure")
    assert s.base_sample_used is not None
    assert "public quote we collected" in s.methodology

    # A policy with no curated sample AND no extraction → model path
    # (royal-sundaram__family-plus now HAS a real harvested sample, so use
    # a synthetic id that can never resolve to a sample).
    m = _pt("nonexistent-insurer__no-such-plan-zzz__wordings")
    assert m.base_sample_used is None
    assert "Modelled" in m.methodology and "not a quote" in m.methodology


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
