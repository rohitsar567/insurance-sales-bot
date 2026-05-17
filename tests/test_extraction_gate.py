"""Guard for the recommendation-card EXTRACTION GATE (#29).

THE BUG (from a real production screenshot):
  A recommended card rendered with the raw policy_id slug as its title
  ("manipalcigna__sarv…"), grade "N/A", body "No extraction available for
  this policy.", and "Why this fits you: Data not indexed".

ROOT CAUSE:
  `_scorecard_signal` / `_quality_seed_candidates` grade off the ~790-entry
  CURATED layer, but the card UI renders from the EXTRACTED layer
  (settings.EXTRACTED_DIR/*.json — the same set the marketplace shows).
  Quality-seed injected curated-graded-but-not-extracted policies into the
  candidate pool, so the LLM could recommend a policy whose card cannot
  render.

THE CONTRACT THIS PINS:
  A policy with no extracted corpus file is NEVER quality-seeded and is
  ALWAYS dropped from the cited set, even if the LLM explicitly marks it —
  so a broken "N/A / No extraction available" card can never reach the UI.

This file deliberately uses the REAL `_has_extraction` predicate (the
package-wide conftest autouse fixture stubs it True for the logic tests;
here we restore the real one so the gate itself is exercised).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend import brain_tools  # noqa: E402
from backend.brain_tools import (  # noqa: E402
    _has_extraction as _REAL_HAS_EXTRACTION,
    _quality_seed_candidates,
)
from backend.config import settings  # noqa: E402
from backend.single_brain import _build_recommendation_citations  # noqa: E402


def _a_real_extracted_stem() -> str:
    """Any policy_id that genuinely has an extracted corpus file on disk."""
    files = sorted(settings.EXTRACTED_DIR.glob("*.json"))
    assert files, "no extracted corpus files — cannot test the gate"
    return files[0].stem


@pytest.fixture
def real_extraction(monkeypatch):
    """Override the conftest autouse stub: use the REAL predicate so the
    gate's actual on-disk behaviour is what gets exercised here."""
    monkeypatch.setattr(brain_tools, "_has_extraction", _REAL_HAS_EXTRACTION)
    brain_tools._extraction_cache.clear()
    brain_tools._qseed_cache.clear()
    return _REAL_HAS_EXTRACTION


def test_predicate_true_for_extracted_false_for_missing(real_extraction):
    real = _a_real_extracted_stem()
    assert brain_tools._has_extraction(real) is True
    assert (
        brain_tools._has_extraction("definitely__not-a-real-policy-xyz")
        is False
    )
    assert brain_tools._has_extraction("") is False


def test_non_extracted_policy_never_cited_even_when_marked(real_extraction):
    """The exact production failure: a marked policy with no extracted
    corpus must be DROPPED, not rendered as an N/A card."""
    real = _a_real_extracted_stem()
    chunks = [
        {
            "chunk_id": "real1",
            "policy_id": real,
            "policy_name": "Real Extracted Plan",
            "insurer_slug": real.split("__", 1)[0] if "__" in real else "x",
            "doc_type": "policy",
            "source_url": f"https://example.com/{real}.pdf",
            "score": 0.9,
        },
        {
            "chunk_id": "ghost1",
            "policy_id": "manipalcigna__sarvah-param-NOT-EXTRACTED",
            "policy_name": "Ghost Plan",
            "insurer_slug": "manipalcigna",
            "doc_type": "policy",
            "source_url": "",
            "score": 0.95,  # higher score — must STILL be dropped
        },
    ]
    cites, is_rec = _build_recommendation_citations(
        reply_text="See Real Extracted Plan and Ghost Plan.",
        retrieved_chunks_all=chunks,
        marked_policy_ids=[
            "manipalcigna__sarvah-param-NOT-EXTRACTED",
            real,
        ],
    )
    assert is_rec is True
    ids = [c["policy_id"] for c in cites]
    assert "manipalcigna__sarvah-param-NOT-EXTRACTED" not in ids
    assert ids == [real]


def test_quality_seed_only_emits_renderable_policies(real_extraction):
    """Every quality-seeded candidate must have an extracted file — so it
    can never inject a policy whose card renders as N/A."""
    seeded = _quality_seed_candidates(profile=None, limit=25)
    assert seeded, "quality-seed returned nothing — basket starved"
    offenders = [
        c["policy_id"]
        for c in seeded
        if not _REAL_HAS_EXTRACTION(c.get("policy_id") or "")
    ]
    assert not offenders, (
        f"quality-seed emitted non-renderable policies: {offenders}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
