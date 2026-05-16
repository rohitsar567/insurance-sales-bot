# Re-Sourcing Contract v3 (2026-05-16) — exhaustive verbatim sweep

v2 fixed only cells whose source_quote matched a narrow provenance-note list.
The hardened verifier proved ~947 cells still carry a NON-verbatim source_quote
that slipped that list: heuristic/placeholder notes ("classified as X from PDF
heuristics", "Default IRDAI 24-month", "limit: …", "(standard … per Policy
Schedule)", "No mandatory copay extracted") and human paraphrases/summaries.
v3 uses an OPERATIONAL definition — no pattern list — so nothing slips.

## Qualifying rule (OPERATIONAL — applies to every value-bearing cell)
For each cell in your assigned insurers' `40-data/policy_facts/*.json` with:
- a non-null `value` (ignore null/""/[]; ignore 999/9999), AND
- NOT `max_renewal_age` (skip — removed), AND
- NO `source_url` (url-sourced day_care/network are fine — skip), AND
- source_quote is NOT a "not stated …/image-only scan …" sourced-null,

→ open the cell's `source_pdf_path` PDF (PyMuPDF/`fitz`, column-aware) and
**TEST**: does the `source_quote` actually occur in the PDF text (whitespace-
normalised, case-insensitive — a clause is "present" if a majority of its
8-word shingles are in the text)?
- **Present verbatim** → leave it (already good).
- **NOT present** (paraphrase, summary, heuristic note, placeholder, inferred,
  "limit:…", "Default IRDAI…", "classified as…") → it qualifies; FIX it.

## Fixing a qualifying cell (same as v2 rules)
1. Verbatim clause in the PDF supports the value → set `source_quote` to that
   exact clause (≤300 chars), keep value, `_confidence` high/medium.
2. PDF states a different value → correct `value`, with the verbatim clause.
3. Field genuinely absent from the PDF → `value:null`,
   `source_quote:"not stated in <file>.pdf"`, `_confidence:"low"`.
4. Source PDF image-only (<400 extractable chars) & no text sibling → drop:
   `value:null`, `source_quote:"source document is an image-only scan; not
   text-extractable (no OCR available)"`, `_confidence:"low"`. (If a text-
   bearing sibling doc for the same policy exists, source from it + update
   `source_pdf_path`.)

## Hard rules
- NEVER keep a non-verbatim source_quote on a non-null value. NEVER fabricate,
  paraphrase, summarise, or infer a quote — copy exact PDF text only.
- NEVER invent a number. NEVER 999/9999.
- Edit ONLY assigned insurers' policy_facts files. No code. Valid JSON via
  `json.dump(d,f,ensure_ascii=False,indent=2)`, key order preserved.
- An independent adversarial re-audit WILL re-open the PDFs — every quote must
  survive a fresh shingle/semantic check.

## Output
```json
{"insurers":["..."],"files_processed":N,"cells_checked":N,
 "already_verbatim_left":N,"reverbatim_fixed":N,"values_corrected":N,
 "nulled_absent":N,"dropped_imageonly":N,"anomalies":["..."]}
```
