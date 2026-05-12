# Knowledge Base — Master Index

_Generated 2026-05-12T23:12:11Z. Auto-regenerable via `python -m rag.build_kb`._

This is the **single canonical KB** for this project. Every data point in the bot
(citations, scorecards, comparison views) traces back to one of these files.

## Layout

```
kb/
├── INDEX.md                          (this file)
├── policies/<policy_id>.md          (11 files — one per extracted policy)
├── research/
│   ├── corpus_acquisition.md         (how we got 75 PDFs)
│   ├── url_verification.md           (HEAD-check results)
│   └── verified_insurers.md          (10 insurers, home URLs)
└── calculations/
    ├── scorecard_results.md          (all scores)
    ├── eval_results.md               (gold Q&A grader output)
    └── extraction_quality_audit.md   (per-field completeness)
```

## Quick links

- **All policies (graded):** [`calculations/scorecard_results.md`](calculations/scorecard_results.md)
- **All policy KB sheets:** [`policies/`](policies/)
- **Eval run results:** [`calculations/eval_results.md`](calculations/eval_results.md)
- **Extraction quality:** [`calculations/extraction_quality_audit.md`](calculations/extraction_quality_audit.md)
- **URL verification:** [`research/url_verification.md`](research/url_verification.md)
- **Corpus acquisition:** [`research/corpus_acquisition.md`](research/corpus_acquisition.md)

## Derivation conventions

Every field in every KB file is tagged with one of:
- **[E]** Extracted directly from a source PDF
- **[E?]** Extractable in the schema but absent / null in this specific source
- **[C]** Computed from extracted fields (e.g. scorecard score)
- **[I]** Implied / canonicalised by us (e.g. insurer slug)
- **[V]** Externally verified (HEAD-check, URL probe)

## Headline counts

- Policies extracted: **11**
- Insurers covered: **4**
- Grade distribution: {'B': 5, 'C': 6}

## Why we maintain this in markdown

JSON is for machines. Markdown is for reviewers. Each KB file is intentionally
human-readable so an interviewer or auditor can open `kb/policies/<some-id>.md`
and read every data point with its source — without running the bot.

The bot's runtime answers are NEVER allowed to use information that isn't
traceable to one of these files (see `backend/faithfulness.py`).
