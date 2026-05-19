"""Guard regression for bug #44 (2026-05-19).

A live audit found one bot response self-contradicting: the comparison
table said ICICI Health AdvantEdge PED waiting = 24 months while its
#31 scorecard card said "0 months — short waiting period". Root cause:
each policy has multiple `40-data/policy_facts/<policy>[__doctype].json`
files; the scorecard path and the get_policy_facts path can resolve
DIFFERENT files, and 10+ policies' files disagreed on
`pre_existing_disease_waiting_months` (a decision-critical field) — so
the SAME response could state two different PED numbers and the bogus
icici "0" inflated its grade with a fake "short waiting" strength.

Invariant pinned here: across ALL doctype files of a single policy, the
NON-None `pre_existing_disease_waiting_months` values must be IDENTICAL
(at most one distinct number) — i.e. no surface can contradict another
by construction, regardless of doctype resolution. RED on the pre-fix
data (>=10 policies with 2 distinct values); GREEN after reconciliation.
"""
import glob
import json
import os
import unittest


def _ped(d):
    if isinstance(d, dict):
        if "pre_existing_disease_waiting_months" in d:
            v = d["pre_existing_disease_waiting_months"]
            return v.get("value") if isinstance(v, dict) else v
        for x in d.values():
            r = _ped(x)
            if r is not None:
                return r
    elif isinstance(d, list):
        for x in d:
            r = _ped(x)
            if r is not None:
                return r
    return None


_DOCS = ("__wordings", "__cis", "__brochure", "__prospectus")


def _policy_key(stem: str) -> str:
    for s in _DOCS:
        if stem.endswith(s):
            return stem[: -len(s)]
    return stem


class TestPolicyFactsPEDConsistency(unittest.TestCase):
    def test_no_policy_has_conflicting_ped_across_its_files(self):
        groups: dict[str, dict[str, object]] = {}
        for fp in sorted(glob.glob("40-data/policy_facts/*.json")):
            stem = os.path.basename(fp)[:-5]
            key = _policy_key(stem)
            try:
                val = _ped(json.load(open(fp)))
            except Exception:
                continue
            groups.setdefault(key, {})[stem] = val

        conflicts = []
        for key, files in groups.items():
            distinct = {v for v in files.values() if v is not None}
            if len(distinct) > 1:
                conflicts.append((key, files))

        self.assertEqual(
            conflicts, [],
            "#44: policies whose doctype files DISAGREE on "
            "pre_existing_disease_waiting_months (self-contradiction "
            f"risk): {[(k, v) for k, v in conflicts][:15]}")


if __name__ == "__main__":
    unittest.main()
