#!/usr/bin/env python3
"""Schema-preserving writer for web-sourced / pdf-sourced core facts.

Reads a JSON job file (list of edits) and applies them to
40-data/policy_facts/<stem>.json, preserving the exact wrapped-object shape
the loader expects:
    {"value":…, "unit":…, "source_quote":…, "source_url":…,
     "extraction_method":…, "_confidence":…}

A web-sourced entry uses source_url (NOT source_pdf_path); a wording-PDF
entry uses source_pdf_path. We keep an existing `unit` if the field had one.

Job file schema (list):
[
  {"stem": "niva-bupa__health-premia__wordings",
   "field": "min_entry_age",
   "value": 18,
   "unit": "years",
   "source_url": "https://transactions.nivabupa.com/.../Health-Premia-Prospectus.pdf",
   "source_quote": "entry ages for Adults under the policy is from 18 years ...",
   "confidence": "high"},
  ...
]

Refuses to overwrite a field that is already non-null (so it never clobbers
genuine curated data). Use --force only for explicit corrections.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FACTS_DIR = ROOT / "40-data" / "policy_facts"


def _is_empty(v) -> bool:
    if isinstance(v, dict):
        if "value" in v:
            v = v["value"]
        elif "covered" in v:
            v = v["covered"]
    return v is None or v == "" or v == [] or (isinstance(v, dict) and not v)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: apply_sourced_fact.py JOB.json [--force]", file=sys.stderr)
        return 2
    job = json.loads(Path(sys.argv[1]).read_text())
    force = "--force" in sys.argv
    applied = 0
    skipped = []
    by_file: dict[str, list[dict]] = {}
    for e in job:
        by_file.setdefault(e["stem"], []).append(e)

    for stem, edits in by_file.items():
        fp = FACTS_DIR / f"{stem}.json"
        if not fp.exists():
            skipped.append((stem, "FILE-MISSING"))
            continue
        data = json.loads(fp.read_text())
        dirty = False
        for e in edits:
            fld = e["field"]
            cur = data.get(fld)
            if cur is not None and not _is_empty(cur) and not force:
                skipped.append((f"{stem}.{fld}", "already-populated"))
                continue
            entry = {"value": e["value"]}
            # preserve a pre-existing unit, else use the supplied one
            unit = None
            if isinstance(cur, dict) and cur.get("unit") not in (None, ""):
                unit = cur["unit"]
            if e.get("unit"):
                unit = e["unit"]
            if unit is not None:
                entry["unit"] = unit
            if e.get("source_url"):
                entry["source_url"] = e["source_url"]
            if e.get("source_pdf_path"):
                entry["source_pdf_path"] = e["source_pdf_path"]
            entry["source_quote"] = e["source_quote"][:600]
            entry["extraction_method"] = e.get(
                "extraction_method",
                "tools/apply_sourced_fact.py — re-sourced from insurer "
                "official public material (WebFetch-verified verbatim quote)",
            )
            entry["_confidence"] = e.get("confidence", "high")
            data[fld] = entry
            dirty = True
            applied += 1
        if dirty:
            fp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")

    print(f"applied {applied} field edits")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for s, why in skipped[:40]:
            print(f"  {s}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
