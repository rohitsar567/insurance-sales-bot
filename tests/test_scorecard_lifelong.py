"""Regression test: max_renewal_age is REMOVED from the scoring model.

History: the PDF-extraction LLM emitted max_renewal_age=999 as a "lifelong"
sentinel because score_renewal_protection rewarded `maxr >= 99`. That
fabricated +25 on 137 policies' grades and leaked a fake "Strong fit:
lifelong renewability" bullet into the UI.

Decision (product owner): lifelong renewability is mandated by IRDAI for every
health-indemnity policy since 2020 — it is universal, so it does NOT
differentiate policies and must NOT be scored. max_renewal_age was therefore
removed entirely. These tests pin that removal so it can't silently creep
back: renewal scoring now depends ONLY on max_entry_age, and max_renewal_age
(in any form — number, 999, or a lifelong flag) must have ZERO effect.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import scorecard
from scorecard import score_renewal_protection, compute_data_completeness, SCORED_FIELDS, ALIASES


def test_max_renewal_age_not_a_scored_field():
    assert "max_renewal_age" not in SCORED_FIELDS
    assert "max_renewal_age" not in ALIASES
    assert not hasattr(scorecard, "_is_lifelong_renewal")  # helper deleted


def test_max_renewal_age_has_zero_effect_on_renewal_score():
    # 999 sentinel, a real cap, a lifelong flag, and absence must all score
    # identically — only max_entry_age may move the number.
    base = score_renewal_protection({}).score
    for variant in (
        {"max_renewal_age": {"value": 999}},
        {"max_renewal_age": {"value": 65}},
        {"max_renewal_age": {"value": None, "lifelong_renewal": True}},
        {"max_renewal_age": 80},
    ):
        assert score_renewal_protection(variant).score == base


def test_entry_age_is_the_sole_driver():
    low = score_renewal_protection({"max_entry_age": {"value": 45}}).score
    mid = score_renewal_protection({"max_entry_age": {"value": 60}}).score
    high = score_renewal_protection({"max_entry_age": {"value": 70}}).score
    assert low < mid < high


def test_lifelong_shown_as_informational_signal_not_scored():
    sc = score_renewal_protection({})
    assert sc.name == "Renewal Protection"
    assert any("lifelong" in s.lower() and "not scored" in s.lower() for s in sc.signals)


def test_completeness_ignores_max_renewal_age():
    # Putting a value in max_renewal_age must NOT raise data completeness,
    # because the scorecard no longer reads it.
    empty = compute_data_completeness({})
    with_renewal = compute_data_completeness({"max_renewal_age": {"value": 999}})
    assert empty == with_renewal


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
