"""Regression test for KI-278 (2026-05-16) — the structured "CITED POLICIES"
cards MUST be exactly the policies the assistant names in its prose answer
(same set, same order). Single source of truth.

THE BUG (from a real production screenshot):
  Prose said: "Here are a few options: 1. Multiplier (Royal Sundaram)
  2. Advanced Top Up (Royal Sundaram) 3. my:health Suraksha (HDFC Ergo)
  4. Family Health Optima (Star Health)"  — FOUR policies.
  Cards rendered: Royal Sundaram Multiplier, Royal Sundaram Advanced Top Up,
  Star Health "Star Hospital Cash"  — THREE, and the wrong three.

ROOT CAUSE:
  `single_brain.py` built `TurnResult.citations` from `retrieved_chunks_all`
  — every chunk every `retrieve_policies` call returned, deduped by
  chunk_id, in vector-score order — NOT the policies the LLM chose to pitch.
  The frontend then `.slice(0, 3)`'d that recall dump. Prose = LLM's
  curated shortlist; cards = retrieval recall set. Two different sources.

THE CONTRACT THIS PINS:
  `_build_recommendation_citations` returns a citation list that is exactly
  the recommended policies — via explicit mark_recommendation ordering when
  present, else by the policy names actually written in the reply prose —
  with NO score-order fallback that resurrects un-named policies, and NO
  count cap.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m unittest \\
        tests.test_cited_policies_match_prose -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.single_brain import (  # noqa: E402
    _build_recommendation_citations,
    _norm_policy_name,
)


def _chunk(pid: str, name: str, slug: str, score: float, cid: str) -> dict:
    return {
        "chunk_id": cid,
        "policy_id": pid,
        "policy_name": name,
        "insurer_slug": slug,
        "doc_type": "policy_wording",
        "source_url": f"https://example.com/{pid}.pdf",
        "score": score,
    }


# The exact retrieval recall set behind the screenshot: 4 distinct policies
# spread over multiple chunks, where the LLM-recommended HDFC/Star policies
# score LOWER than an un-recommended "Star Hospital Cash" chunk — which is
# precisely why the old score-ordered slice surfaced the wrong 3.
SCREENSHOT_CHUNKS = [
    _chunk("rs-multiplier", "Multiplier", "royal-sundaram", 0.91, "c1"),
    _chunk("rs-multiplier", "Multiplier", "royal-sundaram", 0.88, "c2"),
    _chunk("rs-advtopup", "Advanced Top Up", "royal-sundaram", 0.86, "c3"),
    # Un-recommended but high-scoring — the false positive in the screenshot.
    _chunk("star-hospcash", "Star Hospital Cash", "star-health", 0.84, "c4"),
    # Recommended but LOWER score → dropped by the old .slice(0,3).
    _chunk("hdfc-myhealth", "my:health Suraksha", "hdfc-ergo", 0.71, "c5"),
    _chunk("star-fho", "Family Health Optima", "star-health", 0.68, "c6"),
]

SCREENSHOT_REPLY = (
    "Based on your profile, here are a few options:\n"
    "1. Multiplier (Royal Sundaram)\n"
    "2. Advanced Top Up (Royal Sundaram)\n"
    "3. my:health Suraksha (HDFC Ergo)\n"
    "4. Family Health Optima (Star Health)\n"
    "Want me to compare any of these?"
)


class TestProseNameNormalisation(unittest.TestCase):
    def test_punctuation_and_case_collapse(self) -> None:
        self.assertEqual(
            _norm_policy_name("my:health Suraksha"), "my health suraksha"
        )
        self.assertEqual(
            _norm_policy_name("My-Health  Suraksha"), "my health suraksha"
        )
        self.assertEqual(_norm_policy_name(""), "")


class TestMarkRecommendationPath(unittest.TestCase):
    """When the LLM calls mark_recommendation, those ordered ids ARE the
    citation set — exactly, in order, no cap."""

    def test_exact_set_and_order_from_marked_ids(self) -> None:
        marked = ["rs-multiplier", "rs-advtopup", "hdfc-myhealth", "star-fho"]
        cites, is_rec = _build_recommendation_citations(
            reply_text=SCREENSHOT_REPLY,
            retrieved_chunks_all=SCREENSHOT_CHUNKS,
            marked_policy_ids=marked,
        )
        self.assertTrue(is_rec)
        self.assertEqual([c["policy_id"] for c in cites], marked)
        # The un-recommended high-scorer must NOT appear.
        self.assertNotIn(
            "star-hospcash", [c["policy_id"] for c in cites]
        )
        # Hydrated from the BEST chunk (real corpus url, not invented).
        first = cites[0]
        self.assertEqual(first["policy_name"], "Multiplier")
        self.assertEqual(first["chunk_id"], "c1")  # higher score than c2

    def test_marked_ids_deduped_preserving_order(self) -> None:
        cites, _ = _build_recommendation_citations(
            reply_text="x",
            retrieved_chunks_all=SCREENSHOT_CHUNKS,
            marked_policy_ids=["rs-multiplier", "rs-multiplier", "star-fho"],
        )
        self.assertEqual(
            [c["policy_id"] for c in cites], ["rs-multiplier", "star-fho"]
        )


class TestProseMatchingPath(unittest.TestCase):
    """When the LLM forgets mark_recommendation (~70% of rec turns per
    KI-254), citations are derived from the policy names actually written
    into the prose, in order of appearance."""

    def test_screenshot_scenario_is_fixed(self) -> None:
        cites, is_rec = _build_recommendation_citations(
            reply_text=SCREENSHOT_REPLY,
            retrieved_chunks_all=SCREENSHOT_CHUNKS,
            marked_policy_ids=[],  # LLM forgot to mark
        )
        self.assertTrue(is_rec)
        ids = [c["policy_id"] for c in cites]
        # EXACTLY the 4 named in prose, in prose order.
        self.assertEqual(
            ids,
            ["rs-multiplier", "rs-advtopup", "hdfc-myhealth", "star-fho"],
        )
        # 4 named in prose ⇒ 4 cards (the original bug was 4 → 3).
        self.assertEqual(len(cites), 4)
        # The false positive from the screenshot is gone.
        self.assertNotIn("star-hospcash", ids)

    def test_no_count_cap(self) -> None:
        """A 5-policy shortlist must yield 5 cards, not 3."""
        chunks = [
            _chunk(f"p{i}", f"Policy Alpha {i}", "ins", 0.9 - i * 0.05, f"k{i}")
            for i in range(5)
        ]
        reply = (
            "Options: 1. Policy Alpha 0  2. Policy Alpha 1  "
            "3. Policy Alpha 2  4. Policy Alpha 3  5. Policy Alpha 4"
        )
        cites, is_rec = _build_recommendation_citations(
            reply_text=reply,
            retrieved_chunks_all=chunks,
            marked_policy_ids=[],
        )
        self.assertTrue(is_rec)
        self.assertEqual(len(cites), 5)
        self.assertEqual(
            [c["policy_id"] for c in cites],
            ["p0", "p1", "p2", "p3", "p4"],
        )


class TestNonRecommendationFallback(unittest.TestCase):
    """Pure QA / chit-chat turns (no policy named) keep the legacy recall
    chips so factual answers still surface their supporting source — the
    caller uses recall_citations only when is_recommendation is False."""

    def test_no_policy_named_returns_not_a_recommendation(self) -> None:
        cites, is_rec = _build_recommendation_citations(
            reply_text="A waiting period is the time before a claim is "
            "payable. It typically ranges from 30 days to 4 years.",
            retrieved_chunks_all=SCREENSHOT_CHUNKS,
            marked_policy_ids=[],
        )
        self.assertFalse(is_rec)
        self.assertEqual(cites, [])

    def test_empty_reply_is_not_a_recommendation(self) -> None:
        cites, is_rec = _build_recommendation_citations(
            reply_text="",
            retrieved_chunks_all=SCREENSHOT_CHUNKS,
            marked_policy_ids=[],
        )
        self.assertFalse(is_rec)
        self.assertEqual(cites, [])

    def test_no_chunks_no_marks_is_not_a_recommendation(self) -> None:
        cites, is_rec = _build_recommendation_citations(
            reply_text="Hello! How can I help you with health insurance?",
            retrieved_chunks_all=[],
            marked_policy_ids=[],
        )
        self.assertFalse(is_rec)
        self.assertEqual(cites, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
