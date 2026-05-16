"""Regression test: the scorecard endpoint must NEVER hard-fail for a
catalogued policy (2026-05-16).

Bug pinned: /api/policies/all catalogues a card for every extracted JSON AND
every curated-facts product (40-data/policy_facts/<insurer>__<product>.json).
Curated-only products (e.g. Tata AIG MediCare Lite → policy_id
`tata-aig__medicare-lite`) have NO `rag/extracted/<policy_id>.json` — only
doctype-suffixed extractions like `...__cis.json` — yet the scorecard
endpoint only looked in `rag/extracted/<policy_id>.json` and 404'd otherwise.
That made the scorecard hard-fail for ~77 of 170 catalogued policies and
surfaced as the frontend's generic "Couldn't load the scorecard … Retry".

These tests pin the contract:

1. EVERY policy_id catalogued by /api/policies/all returns HTTP 200 from
   /api/policies/{id}/scorecard — zero exceptions, zero 500s, zero 404s.
2. Each 200 is either a real grade (A-F + non-empty sub_scores) OR the
   DEFINED honest insufficient-data state (grade "—", overall_score 0,
   empty sub_scores, insufficient_data True) — never a fabricated grade.
3. Tata AIG MediCare Lite specifically returns a real, scoreable grade from
   the curated layer (it has 69.6% data completeness — it is NOT a
   sparse-data policy and must not be flagged insufficient).
4. A genuinely-bare policy (near-zero structured data) takes the defined
   insufficient-data path instead of fabricating an "F".
5. A truly non-catalogued (typo) policy_id still gets an honest 404 — that
   path is correct because it is NOT a catalogued product.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from scorecard import build_scorecard

client = TestClient(main.app, raise_server_exceptions=False)


def _catalogued_policy_ids():
    r = client.get("/api/policies/all")
    assert r.status_code == 200, r.text
    pols = r.json()["policies"]
    assert len(pols) > 100, f"expected a full catalogue, got {len(pols)}"
    return [p["policy_id"] for p in pols]


def test_every_catalogued_policy_scorecard_never_hard_fails():
    """No catalogued policy may 500 / 404 / raise. Each must be either a
    valid graded scorecard or the defined insufficient-data state."""
    hard_fails = []
    for pid in _catalogued_policy_ids():
        resp = client.get(f"/api/policies/{pid}/scorecard")
        if resp.status_code != 200:
            hard_fails.append((pid, resp.status_code, resp.text[:160]))
            continue
        body = resp.json()
        if body.get("insufficient_data"):
            # Defined honest sparse-data shape — must be self-consistent and
            # NOT a fabricated grade.
            assert body["grade"] == "—", (pid, body["grade"])
            assert body["overall_score"] == 0, (pid, body["overall_score"])
            assert body["sub_scores"] == [], (pid, body["sub_scores"])
            assert body["one_liner"], pid
        else:
            # Real grade — frozen A-F set, non-empty sub-scores, score in band.
            assert body["grade"] in {"A", "B", "C", "D", "F"}, (pid, body["grade"])
            assert len(body["sub_scores"]) >= 3, (pid, len(body["sub_scores"]))
            assert 0 <= body["overall_score"] <= 100, (pid, body["overall_score"])
    assert not hard_fails, (
        f"{len(hard_fails)} catalogued policies hard-failed the scorecard "
        f"endpoint (must be 0): {hard_fails[:10]}"
    )


def test_tata_aig_medicare_lite_returns_real_grade_from_curated_layer():
    """The concrete reported repro. tata-aig__medicare-lite has no
    rag/extracted/<id>.json (only ...__cis.json) but the curated layer has
    ~69.6% completeness, so it must return a REAL grade — not a 404, not the
    insufficient-data state."""
    r = client.get("/api/policies/tata-aig__medicare-lite/scorecard")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["insufficient_data"] is False, b
    assert b["grade"] in {"A", "B", "C", "D", "F"}, b["grade"]
    assert b["data_completeness_pct"] >= 50.0, b["data_completeness_pct"]
    assert len(b["sub_scores"]) == 6, b["sub_scores"]
    assert "MediCare Lite" in b["policy_name"], b["policy_name"]


def test_sparse_policy_takes_defined_insufficient_path_not_fabricated_grade():
    """A near-empty policy must NOT fabricate the old neutral-base "F"/52 —
    it must take the defined honest insufficient-data path."""
    sc = build_scorecard({"policy_id": "x__bare", "policy_name": "Bare", "insurer_slug": "x"})
    assert sc.insufficient_data is True
    assert sc.grade == "—"
    assert sc.overall_score == 0
    assert sc.sub_scores == []
    assert "Not enough" in sc.one_liner


def test_well_populated_policy_is_not_flagged_insufficient():
    """A fully-populated policy must score normally (regression guard so the
    insufficient-data threshold can never creep up and silence real grades)."""
    strong = {
        "policy_name": "Strong", "insurer_slug": "x",
        "ayush_coverage": {"covered": True}, "day_care_treatments_count": 600,
        "maternity_coverage": {"covered": True}, "copayment_pct": 0,
        "room_rent_capping": "no cap", "pre_existing_disease_waiting_months": 18,
        "initial_waiting_period_days": 30, "cashless_treatment_supported": {"covered": True},
        "network_hospital_count": 14000, "max_entry_age": 70, "no_claim_bonus_pct": 100,
        "restoration_benefit": "full restoration", "preventive_health_checkup": {"covered": True},
        "newborn_coverage": {"covered": True}, "organ_donor_expenses": {"covered": True},
    }
    sc = build_scorecard(strong)
    assert sc.insufficient_data is False
    assert sc.grade in {"A", "B", "C", "D", "F"}
    assert len(sc.sub_scores) == 6


def test_non_catalogued_typo_id_still_returns_honest_404():
    """A genuinely non-existent product id is NOT a catalogued policy, so an
    honest 404 (not a fabricated scorecard) is the correct response."""
    r = client.get("/api/policies/not-a-real__policy-xyz/scorecard")
    assert r.status_code == 404, r.text
