"""BUG #29 — voluntary-deductible eligibility.

PROBLEM (pre-fix): the deductible selector (₹0/25K/50K/1L) and its premium
discount were applied to EVERY policy, even though only 2 of the 148
catalogued policies genuinely offer a user-selectable voluntary deductible.
A top-up's "deductible" is a structural threshold, not a knob the buyer can
trade for a lower premium — discounting on it fabricates savings.

Authoritative rule (premium_calculator.policy_deductible_support):

    supports_voluntary_deductible
        = (curated deductible_amount > 0) AND (NOT a top-up / super-top-up)

Across the full 148-policy catalogue this resolves to EXACTLY:

    {bajaj-allianz__health-guard, star-health__star-assure}

These tests:
  1. Sweep the entire catalogue and pin the invariants (top-ups never
     supported, deductible_amount<=0 never supported) plus the exact
     supported set as a regression pin.
  2. Exercise the end-to-end /api/premium/estimate path proving an
     unsupported policy gets NO discount + an honest 0 echo, while a
     supported policy gets the real ×0.85 discount + a 50000 echo.
  3. Prove bulk_estimate() forces the discount to 1.0 and echoes
     deductible_inr=0 for an unsupported policy.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.brain_tools import _load_policy_facts
from backend.main import _marketplace_catalogue
from backend.premium_calculator import (
    BULK_DEDUCTIBLE_DISCOUNT,
    _policy_product_type,
    bulk_estimate,
    policy_deductible_support,
)

client = TestClient(main.app, raise_server_exceptions=False)

# The full catalogued set — single source of truth for the marketplace cards.
_ALL_CARDS = _marketplace_catalogue(None)
_ALL_PIDS = [c.policy_id for c in _ALL_CARDS if c.policy_id]

# Regression pin — the EXACT set the rule must select across all 148.
EXPECTED_SUPPORTED = {
    "bajaj-allianz__health-guard",
    "star-health__star-assure",
}


def _curated_deductible(pid: str) -> float:
    f = _load_policy_facts(pid) or {}
    ded = f.get("deductible_amount")
    try:
        return float(ded) if ded not in (None, "", []) else 0.0
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Catalogue-wide invariants
# ---------------------------------------------------------------------------

def test_catalogue_has_expected_size():
    """Guards the regression pin: if the catalogue size drifts, the
    EXPECTED_SUPPORTED set must be re-derived deliberately."""
    assert len(_ALL_PIDS) == 148


@pytest.mark.parametrize("pid", _ALL_PIDS)
def test_topups_never_support_a_voluntary_deductible(pid):
    """A top-up / super-top-up's deductible is a structural threshold, not a
    user-selectable knob — it must NEVER be (True, ...)."""
    if _policy_product_type(pid) == "topup":
        assert policy_deductible_support(pid) == (False, [0]), pid


@pytest.mark.parametrize("pid", _ALL_PIDS)
def test_nonpositive_curated_deductible_never_supported(pid):
    """No curated deductible_amount (or <=0) ⇒ no voluntary deductible."""
    if _curated_deductible(pid) <= 0:
        assert policy_deductible_support(pid) == (False, [0]), pid


@pytest.mark.parametrize("pid", _ALL_PIDS)
def test_support_shape_is_always_valid(pid):
    """Whatever the answer, the shape contract holds: (bool, list[int]) with
    0 always present and the list sorted/unique."""
    supports, allowed = policy_deductible_support(pid)
    assert isinstance(supports, bool)
    assert isinstance(allowed, list) and all(isinstance(x, int) for x in allowed)
    assert 0 in allowed
    assert allowed == sorted(set(allowed))
    if not supports:
        assert allowed == [0], pid


def test_exact_supported_set_regression_pin():
    """The EXACT set of policies with supports==True across the full
    catalogue is the two flagship comprehensive plans — nothing else."""
    supported = {
        pid for pid in _ALL_PIDS if policy_deductible_support(pid)[0]
    }
    assert supported == EXPECTED_SUPPORTED


def test_supported_policies_expose_curated_amount():
    """Each supported policy's allowed set is exactly {0, curated_amount}."""
    for pid in EXPECTED_SUPPORTED:
        supports, allowed = policy_deductible_support(pid)
        assert supports is True
        amt = int(_curated_deductible(pid))
        assert amt > 0
        assert allowed == sorted({0, amt}), (pid, allowed)


def test_unknown_or_blank_policy_degrades_safely():
    assert policy_deductible_support(None) == (False, [0])
    assert policy_deductible_support("") == (False, [0])
    assert policy_deductible_support("does-not-exist__nope") == (False, [0])


# ---------------------------------------------------------------------------
# End-to-end /api/premium/estimate — the user-visible behaviour
# ---------------------------------------------------------------------------

_BASE_REQ = {
    "age": 35,
    "sum_insured_inr": 1_000_000,
    "city_tier": "metro",
    "smoker": False,
    "family_size": 1,
    "pre_existing_conditions": "none",
    "copayment_pct": 0,
}


def _estimate(policy_id, deductible_inr=None):
    body = dict(_BASE_REQ, policy_id=policy_id)
    if deductible_inr is not None:
        body["deductible_inr"] = deductible_inr
    r = client.post("/api/premium/estimate", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_unsupported_policy_gets_no_discount_and_honest_echo():
    """hdfc-ergo__optima-restore is a comprehensive plan with NO curated
    voluntary deductible. Passing deductible_inr=100000 must NOT discount
    the premium — point == the no-deductible result — and the echoed
    deductible_inr must be 0 (honest), with supports flag False."""
    pid = "hdfc-ergo__optima-restore"
    assert policy_deductible_support(pid) == (False, [0])

    base = _estimate(pid, deductible_inr=0)
    with_ded = _estimate(pid, deductible_inr=100_000)

    assert with_ded["point_estimate_inr"] == base["point_estimate_inr"]
    assert with_ded["low_inr"] == base["low_inr"]
    assert with_ded["high_inr"] == base["high_inr"]
    assert with_ded["deductible_inr"] == 0
    assert with_ded["supports_voluntary_deductible"] is False
    assert with_ded["allowed_deductibles"] == [0]


def test_supported_policy_gets_real_discount_and_echo():
    """bajaj-allianz__health-guard genuinely supports a voluntary
    deductible. A ₹50,000 deductible must apply the real ×0.85 discount
    and echo deductible_inr=50000 with supports flag True."""
    pid = "bajaj-allianz__health-guard"
    supports, allowed = policy_deductible_support(pid)
    assert supports is True
    assert 50_000 in allowed

    base = _estimate(pid, deductible_inr=0)
    discounted = _estimate(pid, deductible_inr=50_000)

    expected = int(round(base["point_estimate_inr"] * BULK_DEDUCTIBLE_DISCOUNT[50_000]))
    assert discounted["point_estimate_inr"] == expected
    assert discounted["point_estimate_inr"] < base["point_estimate_inr"]
    assert discounted["deductible_inr"] == 50_000
    assert discounted["supports_voluntary_deductible"] is True
    assert 50_000 in discounted["allowed_deductibles"]


def test_estimate_response_always_exposes_support_fields():
    """Even with no deductible in the request the response must carry the
    BUG #29 fields so the widget can decide whether to render the selector."""
    resp = _estimate("hdfc-ergo__optima-restore")
    assert resp["supports_voluntary_deductible"] is False
    assert resp["allowed_deductibles"] == [0]


# ---------------------------------------------------------------------------
# bulk_estimate() — slider-driven multi-policy path
# ---------------------------------------------------------------------------

def test_bulk_estimate_forces_no_discount_for_unsupported():
    """bulk_estimate() must neutralise the deductible discount (×1.0) and
    echo deductible_inr=0 for a policy that does not support it."""
    pid = "hdfc-ergo__optima-restore"
    assert policy_deductible_support(pid) == (False, [0])

    out = bulk_estimate(
        profile={"age": 35, "location_tier": "metro"},
        policy_ids=[pid],
        overrides={pid: {"deductible_inr": 100_000}},
    )
    row = out[pid]
    assert row.deductible_inr == 0
    assert row.breakdown.get("deductible_discount_x", 1.0) == 1.0


def test_bulk_estimate_applies_discount_for_supported():
    """A supported policy with a valid deductible still gets the real
    discount + an honest non-zero echo in bulk_estimate()."""
    pid = "bajaj-allianz__health-guard"
    supports, allowed = policy_deductible_support(pid)
    assert supports is True and 50_000 in allowed

    out = bulk_estimate(
        profile={"age": 35, "location_tier": "metro"},
        policy_ids=[pid],
        overrides={pid: {"deductible_inr": 50_000}},
    )
    row = out[pid]
    assert row.deductible_inr == 50_000
    assert row.breakdown.get("deductible_discount_x") == pytest.approx(
        BULK_DEDUCTIBLE_DISCOUNT[50_000]
    )
