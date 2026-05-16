#!/usr/bin/env python3
"""Enumerate CARD-LEVEL null core customer-facing fields.

Reproduces the marketplace loader's merge precisely so the before/after
reflects what a buyer actually sees on the card:
  base = rag/extracted/<stem>.json
  override = 40-data/policy_facts/<policy_id|stem>.json   (curated, non-null wins)
  + curated doctype-sibling field-level backfill (KI-251)

Only ONE card per product_key is shown (wordings wins) — same dedup the
marketplace does — so counts are per *visible card*, not per file.

Usage:
  python tools/enumerate_core_field_gaps.py            # human summary
  python tools/enumerate_core_field_gaps.py --json     # machine list
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACTS_DIR = ROOT / "40-data" / "policy_facts"
EXTRACT_DIR = ROOT / "rag" / "extracted"

CORE_FIELDS: dict[str, list[str]] = {
    "sum_insured_options": ["sum_insured_options"],
    "min_entry_age": ["min_entry_age", "min_entry_age_years"],
    "max_entry_age": ["max_entry_age", "max_entry_age_years"],
    "network_hospital_count": ["network_hospital_count", "network_hospital_count_text"],
    "pre_existing_disease_waiting_months": [
        "pre_existing_disease_waiting_months", "ped_waiting_months",
    ],
    "initial_waiting_period_days": ["initial_waiting_period_days", "initial_waiting_days"],
    "copayment_pct": ["copayment_pct", "co_payment_pct"],
    "room_rent_capping": ["room_rent_capping", "room_rent_capped_at_pct_of_si"],
    "no_claim_bonus_pct": ["no_claim_bonus_pct", "ncb_pct", "cumulative_bonus_pct"],
    "maternity_coverage": ["maternity_coverage"],
    "ayush_coverage": ["ayush_coverage"],
    "cashless_treatment_supported": ["cashless_treatment_supported"],
}
_DOCTYPE_SUFFIXES = ("__wordings", "__prospectus", "__cis", "__brochure")
_SIB_FILL_RANK = {"__wordings": 0, "__prospectus": 1, "__cis": 2, "__brochure": 3}


def _uw(v):
    if isinstance(v, dict):
        if "value" in v:
            return v.get("value")
        if "covered" in v:
            return v.get("covered")
    return v


def _empty(v) -> bool:
    v = _uw(v)
    return v is None or v == "" or v == [] or (isinstance(v, dict) and not v)


def _product_key(stem: str) -> str:
    for suf in _DOCTYPE_SUFFIXES:
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def _merge_curated(extracted: dict, curated: dict | None) -> dict:
    if not curated:
        return dict(extracted)
    m = dict(extracted)
    for k, v in curated.items():
        if not _empty(v):
            m[k] = v
    return m


def main() -> int:
    facts_files = {
        f.stem: json.loads(f.read_text())
        for f in FACTS_DIR.glob("*.json")
        if not f.name.startswith("_")
    }
    by_pid = {}
    for stem, d in facts_files.items():
        by_pid[stem] = d
        pid = d.get("policy_id")
        if isinstance(pid, str):
            by_pid[pid] = d

    # sibling field-level backfill within curated layer (KI-251)
    sib_groups: dict[str, list[str]] = defaultdict(list)
    for stem in facts_files:
        sib_groups[_product_key(stem)].append(stem)

    def sib_filled(stem: str) -> dict:
        d = dict(facts_files[stem])
        pk = _product_key(stem)
        order = sorted(
            (s for s in sib_groups[pk] if s != stem),
            key=lambda s: next(
                ((r, s) for suf, r in _SIB_FILL_RANK.items() if s.endswith(suf)),
                (99, s),
            ),
        )
        for sib in order:
            for k, v in facts_files[sib].items():
                if k in ("policy_id", "policy_name", "insurer_slug"):
                    continue
                if _empty(d.get(k)) and not _empty(v):
                    d[k] = v
        return d

    per_field = defaultdict(list)
    per_insurer = defaultdict(lambda: defaultdict(list))
    per_card_nulls: dict[str, list[str]] = {}
    seen_pk = set()
    cards = 0

    for ex_f in sorted(EXTRACT_DIR.glob("*.json")):
        if ex_f.name.startswith("_"):
            continue
        try:
            ex = json.loads(ex_f.read_text())
        except Exception:
            continue
        stem = ex_f.stem
        pid = ex.get("policy_id", stem)
        slug = ex.get("insurer_slug", "")
        if slug == "regulatory":
            continue
        pk = _product_key(pid if isinstance(pid, str) else stem)
        if pk in seen_pk:
            continue
        seen_pk.add(pk)
        cards += 1

        cur = by_pid.get(pid) or by_pid.get(stem)
        cur_stem = None
        if cur is not None:
            for s, d in facts_files.items():
                if d is cur:
                    cur_stem = s
                    break
        if cur_stem:
            cur = sib_filled(cur_stem)
        merged = _merge_curated(ex, cur)

        ins = slug or stem.split("__", 1)[0]
        nulls = []
        for canon, aliases in CORE_FIELDS.items():
            val = None
            for a in aliases:
                if a in merged and not _empty(merged[a]):
                    val = _uw(merged[a])
                    break
            if val is None:
                nulls.append(canon)
                per_field[canon].append(pid)
                per_insurer[ins][canon].append(pid)
        if nulls:
            per_card_nulls[stem] = {"policy_id": pid, "insurer": ins, "nulls": nulls}

    if "--json" in sys.argv:
        print(json.dumps({
            "cards": cards,
            "cards_with_any_core_null": len(per_card_nulls),
            "per_field_null_count": {k: len(per_field[k]) for k in CORE_FIELDS},
            "per_insurer": {
                ins: {f: len(p) for f, p in flds.items()}
                for ins, flds in per_insurer.items()
            },
            "per_card_nulls": per_card_nulls,
        }, indent=2))
        return 0

    print(f"Visible marketplace cards: {cards}")
    print(f"Cards with >=1 core field null: {len(per_card_nulls)}\n")
    print("=== NULL COUNT PER CORE FIELD (card-level) ===")
    for k in CORE_FIELDS:
        print(f"  {k:42s} {len(per_field[k]):4d}")
    print("\n=== PER INSURER ===")
    for ins in sorted(per_insurer):
        flds = per_insurer[ins]
        affected = sorted({p for ps in flds.values() for p in ps})
        tot = sum(len(v) for v in flds.values())
        print(f"\n  {ins}  ({len(affected)} cards, {tot} field-gaps)")
        for f in CORE_FIELDS:
            if f in flds:
                print(f"      {f:40s} {len(flds[f]):3d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
