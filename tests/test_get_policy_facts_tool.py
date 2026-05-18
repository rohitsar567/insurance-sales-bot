"""Regression test for the Bug 4/5 fix (2026-05-18).

Symptom: after cards were shown, the brain answered "I don't have enough
information" for claim-settlement ratio / denials / complaints and would
not compare two policies — because it had NO tool to reach claim/review
data (retrieve_policies returns policy WORDING only) and was never shown
its own shortlist.

Fix: a `get_policy_facts` tool that returns the authoritative claim /
reputation / scorecard / coverage data (the same `40-data/reviews/<slug>`
+ scorecard the detail-modal uses), plus ACTIVE-SHORTLIST injection so the
model can resolve "#1/#2/the HDFC one" to policy_ids.

These tests are deterministic (no live LLM): they pin the tool wiring and
that get_policy_facts actually returns the real claim-settlement numbers.
"""
import json
import glob
import os
from types import SimpleNamespace

import pytest

from backend import brain_tools, single_brain


def _a_slug_with_reviews():
    """Pick a real insurer slug that has a reviews JSON with a numeric
    claim_settlement_ratio_pct (so the assertion is data-driven, not
    hardcoded)."""
    for fp in sorted(glob.glob("40-data/reviews/*.json")):
        try:
            d = json.loads(open(fp).read())
        except Exception:
            continue
        csr = (d.get("claim_metrics") or {}).get("claim_settlement_ratio_pct")
        if isinstance(csr, (int, float)):
            return d["insurer_slug"], float(csr), d
    pytest.skip("no reviews JSON with a numeric claim_settlement_ratio_pct")


def test_tool_is_registered_and_wired():
    names = [t["name"] for t in single_brain.TOOL_SCHEMAS]
    assert "get_policy_facts" in names, names
    # wired in the dispatcher (string presence is a cheap structural pin)
    import inspect
    src = inspect.getsource(single_brain._execute_tool)
    assert 'name == "get_policy_facts"' in src
    assert "brain_tools.get_policy_facts(" in src


def test_get_policy_facts_returns_real_claim_metrics():
    slug, csr, raw = _a_slug_with_reviews()
    pid = f"test::{slug}::policy"
    session = SimpleNamespace(
        last_recommendation_ids=[pid],
        last_recommendation_snapshot={pid: "Test Policy"},
        slug_to_insurer={pid: slug},
        last_retrieved_chunks=[
            {"policy_id": pid, "policy_name": "Test Policy",
             "insurer_slug": slug}
        ],
    )
    res = brain_tools.get_policy_facts(session, policy_ids=[pid])
    assert res.get("ok") is True, res
    assert res["count"] == 1
    p = res["policies"][0]
    # The exact IRDAI claim-settlement ratio the modal shows — the brain
    # can now cite this instead of refusing.
    assert p["claim_settlement_ratio_pct"] == csr, (p, csr)
    assert p["insurer_slug"] == slug
    assert p["reviews_available"] is True
    # complaints/source surfaced for the "how often do they deny" answer.
    assert "complaints_per_10k_policies" in p
    assert "claim_data_source_url" in p


def test_empty_policy_ids_falls_back_to_active_shortlist():
    slug, csr, _ = _a_slug_with_reviews()
    pid = f"test::{slug}::policy"
    session = SimpleNamespace(
        last_recommendation_ids=[pid],
        last_recommendation_snapshot={pid: "Test Policy"},
        slug_to_insurer={pid: slug},
        last_retrieved_chunks=[
            {"policy_id": pid, "policy_name": "Test Policy",
             "insurer_slug": slug}
        ],
    )
    # No explicit ids → must use session.last_recommendation_ids so
    # "compare the ones you showed" works.
    res = brain_tools.get_policy_facts(session, policy_ids=None)
    assert res.get("ok") is True
    assert [p["policy_id"] for p in res["policies"]] == [pid]


def test_no_shortlist_returns_clean_error_not_crash():
    session = SimpleNamespace(
        last_recommendation_ids=[],
        last_recommendation_snapshot={},
        slug_to_insurer={},
        last_retrieved_chunks=[],
    )
    res = brain_tools.get_policy_facts(session, policy_ids=None)
    assert res.get("ok") is False
    assert "no_policy_ids" in res.get("error", "")
