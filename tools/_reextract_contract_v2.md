# Re-Sourcing Contract v2 (2026-05-16) — verbatim provenance pass

Binding contract for the legacy-provenance re-extraction fleet. The earlier
pass filled NULL cells. THIS pass fixes cells that HAVE a value but whose
`source_quote` is a non-verbatim self-reference ("extracted from PDF data",
"NIM DeepSeek", "regex extracted from PDF text", "rag/extracted structured
JSON", etc.). The two-part verify flagged 2,904 such cells. Goal: every value
on the site traces to a real verbatim source — zero exceptions.

## Which cells to FIX (in your assigned insurers' policy_facts files)
A cell qualifies if ALL of:
- it has a non-null `value` (not null/""/[]; ignore 999/9999 — dead),
- it is NOT `max_renewal_age` (field removed — skip entirely),
- it has NO `source_url` (url-sourced cells e.g. day_care/network are fine — skip),
- its `source_quote` is empty OR matches a provenance/pipeline note:
  `extracted from PDF`, `from extracted PDF data`, `NIM DeepSeek`,
  `regex extracted from PDF`, `rag/extracted`, `structured JSON/field`,
  `Gx batch extract`, `prior pipeline`, `see source PDF for verbatim`.
Do NOT touch cells whose `source_quote` is already a real verbatim clause, nor
`not stated …` sourced-nulls (legitimately empty).

## For each qualifying cell — open the `source_pdf_path` PDF and:
1. **Verbatim clause supports the existing value** → set `source_quote` to
   that exact clause (≤300 chars, copied verbatim), keep `value`, set
   `_confidence` high (explicit) or medium (table/derived), keep
   `source_pdf_path`.
2. **PDF states a DIFFERENT value** → correct `value` to what the PDF says,
   with the verbatim clause as `source_quote`. Never keep a value the source
   contradicts. Never keep the old provenance note.
3. **Field genuinely absent from the PDF** → `value: null`,
   `source_quote: "not stated in <file>.pdf"`, `_confidence: "low"`.
4. **Source PDF is image-only / not text-extractable** (fitz/pdftotext yields
   < ~400 chars of text — e.g. a scanned brochure) AND no text-bearing
   sibling document exists for that policy → DROP the cell:
   `value: null`,
   `source_quote: "source document is an image-only scan; not text-extractable (no OCR available)"`,
   `_confidence: "low"`. (Per owner instruction: drop, do not fabricate, do
   not OCR.) If a text-bearing sibling doc for the SAME policy exists in
   `rag/corpus/<insurer>/`, you may source from it and update
   `source_pdf_path` accordingly.

## Hard rules
- NEVER keep "extracted from PDF data"/NIM/regex/etc. as a source_quote.
- NEVER fabricate a quote. NEVER invent a number. NEVER use 999/9999.
- Edit ONLY your assigned insurers' `40-data/policy_facts/*.json`. No code.
- Valid JSON via `json.dump(d, f, ensure_ascii=False, indent=2)`, preserve key
  order.
- Every quote you write MUST be greppable in the PDF text (whitespace-
  normalised) — an independent adversarial re-audit will re-open the PDFs.

## Output (return exactly)
```json
{"insurers":["..."],"files_processed":N,"cells_reverbatim":N,
 "values_corrected":N,"cells_nulled_absent":N,"cells_dropped_imageonly":N,
 "image_only_pdfs":["path"],"anomalies":["..."]}
```
