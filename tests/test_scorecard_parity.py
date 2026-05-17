"""Regression guard — recommendation-path scorecard == marketplace scorecard.

ROOT CAUSE THIS LOCKS DOWN (KI-FULLFACTS, 2026-05-17)
-----------------------------------------------------
`brain_tools._scorecard_signal` used to feed `build_scorecard` the 7-key
eligibility subset (`_load_policy_facts`), which is below build_scorecard's
data-completeness floor → every recommended policy came back grade "—" /
overall 0 → the Bug #71 >=70 fitness gate dropped 100% of them → the
recommendation `citations` array was always empty → CitedPolicyCards never
rendered. Meanwhile /api/policies/all scored the SAME policies correctly
(HDFC ERGO Optima Restore = A) because it feeds the FULL curated layer
(main._load_curated_facts, KI-219/251 canonical precedence).

The fix makes `_scorecard_signal` reuse that EXACT function. This test makes
the two paths *provably* unable to diverge again: for a representative
spread of policies, the recommendation-path GRADE LETTER must equal the
marketplace GRADE LETTER. (Overall scores may differ by a few points
because the marketplace overlays EXTRACTED_DIR data on top; the LETTER —
what _recommendation_fit gates on — must match.)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class TestScorecardParity(unittest.TestCase):
    """Rec-path _scorecard_signal grade letter == marketplace grade letter."""

    @classmethod
    def setUpClass(cls):
        from backend.main import _load_curated_facts
        from backend.scorecard import build_scorecard
        from backend.brain_tools import _scorecard_signal, _insurer_reviews

        cls._scorecard_signal = staticmethod(_scorecard_signal)
        cur = _load_curated_facts()

        # Marketplace grade for a policy_id = build_scorecard on the SAME
        # full curated dict + reviews the /api/policies/all path uses.
        def _mkt_grade(pid: str):
            data = cur.get(pid)
            if not data:
                return None
            slug = data.get("insurer_slug") or (
                pid.split("__", 1)[0] if "__" in pid else ""
            )
            sc = build_scorecard(
                data, insurer_reviews=_insurer_reviews(slug), profile=None
            )
            return sc.grade

        cls._mkt_grade = staticmethod(_mkt_grade)
        # A representative spread across grade bands + insurers, incl. the
        # user's own policy (HDFC ERGO Optima Restore — must be a strong A).
        cls._sample = [
            "hdfc-ergo__optima-restore",
            "hdfc-ergo__my-health-sampoorna-suraksha",
            "hdfc-ergo__my-optima-secure",
            "oriental-insurance__happy-family-floater",
            "star-health__family-health-optima",
        ]

    def test_rec_path_grade_matches_marketplace_grade(self):
        mismatches = []
        for pid in self._sample:
            mkt = self._mkt_grade(pid)
            if mkt is None:
                continue  # policy not in curated layer in this env — skip
            rec = (self._scorecard_signal(pid, profile=None) or {}).get("_grade")
            if rec != mkt:
                mismatches.append(f"{pid}: rec={rec!r} marketplace={mkt!r}")
        self.assertEqual(
            mismatches, [],
            "recommendation-path scorecard grade diverged from the "
            "marketplace grade — the two paths must feed build_scorecard "
            "the SAME curated facts (see KI-FULLFACTS):\n  "
            + "\n  ".join(mismatches),
        )

    def test_optima_restore_is_a_strong_recommendable_grade(self):
        # The user's own policy — a genuinely strong plan. It must NOT come
        # back as the data-starved "—"/0 sentinel, and must clear the Bug
        # #71 recommendation fitness floor so its card can render.
        from backend.single_brain import _recommendation_fit

        sig = self._scorecard_signal("hdfc-ergo__optima-restore", profile=None)
        self.assertIn(sig.get("_grade"), ("A", "B"),
                      f"Optima Restore must be A/B, got {sig!r}")
        strong, overall, grade = _recommendation_fit({
            "_overall_score": sig.get("_overall_score"),
            "_grade": sig.get("_grade"),
        })
        self.assertTrue(
            strong,
            f"Optima Restore must clear the rec-fit floor "
            f"(grade={grade} overall={overall})",
        )


if __name__ == "__main__":
    unittest.main()
