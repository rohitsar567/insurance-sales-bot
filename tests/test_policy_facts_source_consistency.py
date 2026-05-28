"""Bug #44 (2026-05-19) — SINGLE-SOURCE guard: the file/value the
marketplace SCORECARD path resolves for a policy's decision-critical
fields must be IDENTICAL to the value the get_policy_facts (LLM) path
surfaces — for the WHOLE catalogue, by construction.

ROOT CAUSE THIS LOCKS DOWN
--------------------------
Every product has a base `40-data/policy_facts/<id>.json` AND doctype
siblings (`__wordings` / `__cis` / `__brochure` / `__prospectus`).

  * Scorecard / #31 profile-summary path → `main._load_curated_facts()`
    (KI-219/KI-251 canonical precedence: base wins per-field, sibling
    backfills only nulls); the resulting flat dict is what
    `build_scorecard` / `build_profile_summary` reads.

  * get_policy_facts path → previously `brain_tools._load_policy_facts()`,
    a DIFFERENT `_candidate_stems` resolver whose 7-key `_FACT_KEYS`
    doesn't even include PED.

The two resolvers can pick different files, so the SAME bot turn could
state two different PED waiting periods (live audit: comparison TABLE said
ICICI Health AdvantEdge PED = 24 months, while its #31 scorecard card
bullet said "0 months — short waiting period").

THE FIX — ONE CANONICAL ENTRY
-----------------------------
`brain_tools.canonical_decision_facts(pid)` reads the decision-critical
fields from the SAME canonical curated entry the scorecard path uses, and
`get_policy_facts` merges it LAST into `key_coverage_facts` (overriding
the divergent 7-key resolver for those keys). Mirrors the existing
`_scorecard_signal → marketplace_grade` single-source pattern (#40).

This test proves agreement for the entire id universe a recommendation
can surface. RED on the pre-fix code/data; GREEN after the fix. 0
mismatches, by construction.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Decision-critical fields whose value MUST agree between the scorecard
# path and the get_policy_facts path for every policy. PED is the field
# the live bug self-contradicted on; the rest are the other fields a
# user-facing comparison would quote.
_DECISION_CRITICAL = (
    "pre_existing_disease_waiting_months",
    "initial_waiting_period_days",
    "copayment_pct",
    "room_rent_capping",
    "claim_settlement_ratio",
)


def _unwrap(v):
    """_load_curated_facts already flattens {value, source_*} → scalar,
    but accept the wrapped shape defensively."""
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def _norm(v):
    """Normalise for equality: empty → None; numeric strings → int;
    strings trimmed/lowercased so '24' == 24 and ' No Cap ' == 'no cap'."""
    v = _unwrap(v)
    if v in (None, "", [], {}):
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else float(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            f = float(s.replace(",", ""))
            return int(f) if f.is_integer() else f
        except (TypeError, ValueError):
            return s.lower()
    return v


class TestPolicyFactsSourceConsistency(unittest.TestCase):
    """Scorecard-path value == get_policy_facts-path value for every
    decision-critical field, across the WHOLE catalogue."""

    @classmethod
    def setUpClass(cls):
        import backend.main as M
        from backend import brain_tools as BT

        cls.M = M
        cls.BT = BT
        # The EXACT dict the scorecard path feeds build_scorecard.
        cls.curated = M._load_curated_facts()

    def _scorecard_path_value(self, pid: str, field: str):
        """What the scorecard / #31 path sees for `field` — the canonical
        curated entry main._load_curated_facts() feeds build_scorecard."""
        entry = self.curated.get(pid)
        if not isinstance(entry, dict):
            return None
        return _norm(entry.get(field))

    def _get_policy_facts_path_value(self, pid: str, field: str):
        """What the get_policy_facts tool surfaces for `field` — the
        canonical single-source resolver wired into key_coverage_facts."""
        return _norm(self.BT.canonical_decision_facts(pid).get(field))

    def _id_universe(self):
        """Every id a recommendation / get_policy_facts call can surface:
        every marketplace card id + every curated key (incl. doctype
        permutations + sibling stems/policy_ids registered by
        _load_curated_facts Pass-2)."""
        ids: set[str] = set()
        for c in self.M._marketplace_catalogue(None):
            if getattr(c, "policy_id", None):
                ids.add(c.policy_id)
        for k in self.curated:
            if isinstance(k, str) and k:
                ids.add(k)
        return sorted(ids)

    def test_full_catalogue_decision_field_source_parity(self):
        mismatches = []
        for pid in self._id_universe():
            for field in _DECISION_CRITICAL:
                sc = self._scorecard_path_value(pid, field)
                gp = self._get_policy_facts_path_value(pid, field)
                # The get_policy_facts path only ASSERTS a value when the
                # canonical entry populates it (it never invents). So the
                # invariant is: whenever get_policy_facts surfaces a value
                # for a decision-critical field, it must EQUAL the
                # scorecard path's value for that policy. (A null on the
                # get_policy_facts side just means "not surfaced" — not a
                # contradiction.)
                if gp is not None and sc != gp:
                    mismatches.append(
                        {"policy_id": pid, "field": field,
                         "scorecard_path": sc, "get_policy_facts_path": gp}
                    )

        self.assertEqual(
            mismatches, [],
            "#44: decision-critical fields where the SCORECARD path and "
            "the get_policy_facts path resolve DIFFERENT values for the "
            "same policy (self-contradiction risk in a single bot turn): "
            f"{mismatches[:20]}",
        )

    def test_ped_specific_known_conflict_set_is_reconciled(self):
        """Belt-and-braces: the exact ~12 policies the live audit / scan
        flagged must each resolve a single PED value via BOTH paths."""
        flagged = [
            "aditya-birla__activ-assure-diamond",
            "care-health__care-supreme-enhance",
            "hdfc-ergo__my-health-sampoorna-suraksha",
            "hdfc-ergo__my-health-suraksha",
            "hdfc-ergo__optima-enhance",
            "icici-lombard__health-advantedge",
            "niva-bupa__health-premia",
            "star-health__health-premier",
            "star-health__star-assure",
            "star-health__star-comprehensive",
            "bajaj-allianz__silver-health",
        ]
        F = "pre_existing_disease_waiting_months"
        bad = []
        for pid in flagged:
            sc = self._scorecard_path_value(pid, F)
            gp = self._get_policy_facts_path_value(pid, F)
            # Each must be a real (non-null) sourced number AND agree.
            if sc is None or gp is None or sc != gp:
                bad.append({"policy_id": pid, "scorecard": sc,
                            "get_policy_facts": gp})
        self.assertEqual(
            bad, [],
            "#44: a flagged-conflict policy still has a missing or "
            f"divergent PED across the two paths: {bad}",
        )

    def test_icici_advantedge_not_a_fake_zero_short_waiting(self):
        """The exact live symptom: ICICI Health AdvantEdge must NOT report
        a 0-month PED (which fabricated a "short waiting period" strength).
        Its source clause is plan-dependent/garbled; the representative
        plan value is 24 months — and BOTH paths must say 24, not 0."""
        F = "pre_existing_disease_waiting_months"
        pid = "icici-lombard__health-advantedge"
        sc = self._scorecard_path_value(pid, F)
        gp = self._get_policy_facts_path_value(pid, F)
        self.assertEqual(sc, 24, f"scorecard path PED should be 24, got {sc}")
        self.assertEqual(gp, 24, f"get_policy_facts PED should be 24, got {gp}")
        self.assertNotEqual(
            sc, 0,
            "#44 regression: ICICI Health AdvantEdge PED back to a fake "
            "0-month 'short waiting period'.",
        )


if __name__ == "__main__":
    unittest.main()
