"""Regression guard — recommendation-path scorecard == marketplace scorecard.

ROOT CAUSE THIS LOCKS DOWN
--------------------------
1. KI-FULLFACTS (2026-05-17): `brain_tools._scorecard_signal` fed
   `build_scorecard` the 7-key eligibility subset → every recommended
   policy came back grade "—"/0 → the >=70 fitness gate dropped 100% →
   empty `citations` → CitedPolicyCards never rendered.

2. #40 (2026-05-18): even after (1), `_scorecard_signal` kept its OWN
   doctype-rank + `_merge_curated` re-implementation that *mirrored*
   `/api/policies/all`. Two parallel implementations drift: marketing-
   rename / KI-145 variant / multi-doctype ids graded a different file
   than the marketplace card (e.g. `my:Optima Secure (older variant)`
   recommended at one grade, marketplace card at another).

THE FIX — SINGLE SOURCE OF TRUTH
--------------------------------
`backend.main._marketplace_catalogue()` is the ONE place a card set is
computed. `backend.main.marketplace_grade(policy_id)` resolves a policy's
canonical card (UIN-primary) from it. `_scorecard_signal` simply delegates
to `marketplace_grade`. The two surfaces can no longer diverge because
there is exactly one computation.

This test makes that *provable*: for the ENTIRE id universe the
recommender can cite — every marketplace card id, every curated id, every
extracted stem incl. `__wordings/__brochure/__cis/__prospectus`
permutations, and every alias name — the recommendation-path GRADE must
equal the marketplace GRADE. 0 mismatches, by construction.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class TestScorecardParity(unittest.TestCase):
    """Rec-path _scorecard_signal grade == marketplace grade, everywhere."""

    @classmethod
    def setUpClass(cls):
        import backend.main as M
        from backend.brain_tools import _scorecard_signal

        cls.M = M
        cls._scorecard_signal = staticmethod(_scorecard_signal)
        cls._cards = M._marketplace_catalogue(None)
        cls._cur = M._load_curated_facts()

    def test_full_id_universe_parity(self):
        """The #40 oracle: rec grade == marketplace grade for EVERY id a
        recommendation can surface (card ids + curated ids + extracted
        stems + doctype permutations + aliases). Must be 0 mismatches."""
        M = self.M
        ids = {c.policy_id for c in self._cards}
        ids |= set(self._cur.keys())
        ids |= {p.stem for p in M.settings.EXTRACTED_DIR.glob("*.json")}
        for c in self._cards:
            ids |= set(c.aliases or [])

        mismatches = []
        checked = 0
        for pid in sorted(ids):
            exp = M.marketplace_grade(pid).get("_grade")
            if exp is None:
                continue  # not a marketplace card (e.g. regulatory) — skip
            checked += 1
            rec = (self._scorecard_signal(pid) or {}).get("_grade")
            if rec != exp:
                mismatches.append(f"{pid}: rec={rec!r} marketplace={exp!r}")

        self.assertGreater(checked, 140, "id universe collapsed — guard broke")
        self.assertEqual(
            mismatches, [],
            f"#40 PARITY BROKEN ({len(mismatches)}/{checked}): the "
            "recommendation grade diverged from the marketplace grade. "
            "Both MUST flow through backend.main.marketplace_grade (the "
            "single source of truth); do not re-implement scoring in "
            "brain_tools._scorecard_signal.\n  " + "\n  ".join(mismatches[:40]),
        )

    def test_proven_alias_and_doctype_edges(self):
        """The exact cases #40 used to break: a marketing-rename variant id
        and doctype-permuted ids must each equal their canonical card."""
        M = self.M
        for pid in (
            "hdfc-ergo__my-optima-secure-older-variant",
            "hdfc-ergo__optima-restore__brochure",
            "hdfc-ergo__optima-restore__cis",
            "royal-sundaram__lifeline__cis",
            "new-india__mediclaim-policy__brochure",
        ):
            mkt = M.marketplace_grade(pid).get("_grade")
            if mkt is None:
                continue  # absent in this env — skip, don't false-fail
            rec = (self._scorecard_signal(pid) or {}).get("_grade")
            self.assertEqual(
                rec, mkt,
                f"{pid}: rec grade {rec!r} != marketplace {mkt!r} "
                "(alias/doctype canonicalisation regressed)",
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


class TestProfileSummaryEndpointParity(unittest.TestCase):
    """Task #31 — profile_summary returned by
    /api/policies/{id}/scorecard?session_id MUST be byte-identical to the
    same canonical card's profile_summary in /api/policies/all?session_id
    (both flow through the SAME build_scorecard pass with the SAME resolved
    profile). Proven across the FULL catalogue for a ≥0.6 profile."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        import backend.main as M
        from backend.session_state import get_session

        cls.M = M
        cls.client = TestClient(M.app, raise_server_exceptions=False)
        # Build a ≥0.6-complete session profile so the catalogue is
        # profile-scored (otherwise both sides are profile-neutral and the
        # parity is trivial).
        cls.sid = "pytest-task31-parity"
        sess = get_session(cls.sid)
        p = sess.profile
        p.name = "Parity Tester"
        p.age = 52
        p.dependents = "self+spouse"
        p.income_band = "25L+"
        p.primary_goal = "upgrade"
        p.location_tier = "metro"
        p.health_conditions = ["diabetes"]
        p.existing_cover_inr = 500000
        p.copay_pct = 0
        p.family_medical_history = ["heart"]
        # Mark them answered so profile_completeness counts them (parity
        # with the live save path).
        p.asked = [
            "name", "age", "dependents", "income_band", "primary_goal",
            "location_tier", "health_conditions",
        ]

    def test_endpoint_profile_summary_matches_catalogue(self):
        M = self.M
        r = self.client.get(f"/api/policies/all?session_id={self.sid}")
        self.assertEqual(r.status_code, 200, r.text)
        cards = r.json()["policies"]
        self.assertGreater(len(cards), 100, "catalogue collapsed")

        # Confirm the catalogue is actually profile-scored (else the parity
        # is vacuous): at least one card must carry profile-aware strengths.
        self.assertTrue(
            any((c.get("profile_summary") or {}).get("strengths") for c in cards),
            "no card has a profile_summary — catalogue not profile-scored",
        )

        mismatches = []
        for c in cards:
            pid = c["policy_id"]
            cat_ps = c.get("profile_summary")
            er = self.client.get(
                f"/api/policies/{pid}/scorecard?session_id={self.sid}"
            )
            self.assertEqual(er.status_code, 200, (pid, er.text[:200]))
            ep_ps = er.json().get("profile_summary")
            if cat_ps != ep_ps:
                mismatches.append(
                    f"{pid}: catalogue={cat_ps!r} endpoint={ep_ps!r}"
                )
        self.assertEqual(
            mismatches, [],
            f"TASK #31 PARITY BROKEN ({len(mismatches)}/{len(cards)}): the "
            "single-scorecard endpoint diverged from the marketplace card "
            "profile_summary for the same canonical id. Both MUST resolve "
            "the session profile identically (brain_tools.union_snapshot) "
            "and run the SAME build_scorecard.\n  " + "\n  ".join(mismatches[:30]),
        )


if __name__ == "__main__":
    unittest.main()
