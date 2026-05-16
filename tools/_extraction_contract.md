# Policy-Facts Extraction Contract (v1 — 2026-05-16)

Single source of truth for every fill/verify agent. Deviating from this is a defect.

## Goal
Replace every `"value": null` and every `"value": 999`/`9999` (a poisoned
"no-max" sentinel) in `40-data/policy_facts/<insurer>__<product>__<doctype>.json`
with a **real value backed by a verbatim quote from the source PDF** — or an
honest, sourced "not stated".

## Path mapping (deterministic)
`40-data/policy_facts/<insurer>__<product>__<doctype>.json`
  → PDF: `rag/corpus/<insurer>/<product>__<doctype>.pdf`
Also check the file's own `source_pdf_path` and `max_renewal_age.source_pdf_path`.
If no PDF exists locally, see "Missing PDF" below — do NOT guess.

## Per-cell rule (apply to EVERY field dict that has a `value` key)
Only touch a cell if its current `value` is `null`, `999`, or `9999`.
Leave already-good values untouched (do not "improve" them).

For each such cell, read the PDF and:

1. **Value found explicitly** → set:
   - `"value"`: the real value (int/number/bool/string/list per existing `unit`)
   - `"source_quote"`: the **verbatim** sentence/clause from the PDF that states
     it (≤ 300 chars, copy exactly, do not paraphrase)
   - `"source_pdf_path"`: the correct `rag/corpus/...pdf` path
   - `"_confidence"`: `"high"`
2. **Value derivable by direct reading** (e.g. table cell, "Annexure A lists 586
   day-care procedures") → same as above with `"_confidence": "medium"` and a
   `source_quote` that contains the basis.
3. **Genuinely absent from this document** → set:
   - `"value"`: `null`
   - `"source_quote"`: `"not stated in <filename>.pdf"`
   - `"_confidence"`: `"low"`
   NEVER invent a number. NEVER use 999/9999. A sourced null beats a fake number.

## `max_renewal_age` — DO NOT FILL (field removed from scoring)
SKIP this field entirely. Do not read PDFs for it, do not set it, do not add a
`lifelong_renewal` key. Lifelong renewability is mandated by IRDAI for every
health-indemnity product (since 2020) — it is universal, so it does not
differentiate policies and has been removed from the scoring model. Leave any
existing `max_renewal_age` cell exactly as-is (even if it is `999`/`null`);
it is dead data that will be stripped in the final cleanup pass. Spend zero
time on it. Focus on every OTHER null cell.

## Boolean fields (`*_coverage`, `*_supported`, `ambulance_cover`, etc.)
`value` must be `true`/`false` (JSON booleans) with a verbatim quote. "Covered
under Section X" → true; an exclusions-list mention → false. Unclear → null+low.

## Hard constraints
- Edit ONLY policy_facts files for your assigned insurer slugs. Touch no code.
- Output must be valid JSON — load with `json.load`, write with
  `json.dump(d, f, ensure_ascii=False, indent=2)`; preserve key order.
- Every non-null filled cell MUST have a non-empty `source_quote` that actually
  occurs in the PDF text. This is verified independently afterward — fabricated
  quotes will be caught and bounced.
- No homepage/search URLs anywhere. English-only quotes.

## Missing PDF
If the mapped PDF does not exist locally: do not fill from memory. Record the
file in your summary under `missing_pdf` and move on (a separate task is
sourcing those PDFs).

## Agent output (return exactly this JSON)
```json
{"insurers": ["..."], "files_processed": N, "cells_filled": N,
 "cells_left_null_sourced": N, "lifelong_flagged": N,
 "missing_pdf": ["path", ...], "anomalies": ["short notes"]}
```
