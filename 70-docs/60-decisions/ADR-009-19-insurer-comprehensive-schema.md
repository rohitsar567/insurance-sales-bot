# ADR-009: 19 insurers × all health policies; 48-field structured schema

**Status:** Locked (historical scope). Current state per [ADR-044](ADR-044-uploaded-pdf-parity.md): 21 catalogued insurer slugs / 148 marketplace cards / 7,317 Chroma chunks. The 48-field structured schema (`rag/schema.py::HealthPolicy`) is unchanged.
**Date:** 2026-05-13

## Context

Original v1 plan: 5 insurers × ~3 policies each = ~15 PDFs. Mid-build, the user expanded scope explicitly for comprehensiveness.

## Decision

**19 insurers × all available health policies = 190 product PDFs + 18 regulatory PDFs = 208 total.**

Insurers covered: Star, HDFC ERGO, Niva Bupa, Care, ICICI Lombard, Bajaj Allianz, New India Assurance, Aditya Birla, Tata AIG, ManipalCigna, SBI General, Acko, IFFCO Tokio, Cholamandalam MS, Go Digit, Reliance General, Royal Sundaram, Oriental Insurance, National Insurance.

Structured schema: **48 fields per policy** — premium, sum insured, waiting periods (initial, PED, specific disease), family-composition options, sub-limits (room rent, ICU, modern treatments), network hospital count, restoration benefit, no-claim bonus, AYUSH, OPD riders, claim ratio, geographic coverage, and more.

## Why this scope

| Dimension | Impact of going wider |
|---|---|
| Insurers | Cross-policy comparison is meaningless with <5; credible at 19. |
| Per-insurer policies | Buyers want to see ALL options from a brand they trust, not 1 cherry-picked product. |
| 48 fields | Below ~30 fields, filter UI is too narrow; above ~60 fields, schema becomes brittle. 48 sits in the empirical sweet spot for Indian health insurance. |

## Acquisition strategy

- Initial: research agent + `requests` library on insurer websites.
- Failures: Star Health (CDN-blocked), IRDAI (Akamai-blocked) → Playwright rescue (see ADR-017).
- Quality threshold: every PDF must HEAD-verify, have ≥1 page, parse via pdfplumber without errors.

## Consequences

**Positive:**

- Marketplace UI is credibly useful (255 listed policies, 224 with markdown writeups).
- Cross-policy comparison queries hit non-trivial breadth.
- Brand coverage matches Indian buyer behavior (people compare within insurers they recognise).

**Negative:**

- Corpus acquisition is the longest-pole task in the build.
- Per-PDF extraction quality varies — some CIS-only documents lack fields that wordings cover; some completeness ratings drop into the 20-40% range.

**Mitigations:**

- Per-policy `completeness` metric exposed in `kb/INDEX.md`.
- Extraction quality audit in `kb/calculations/extraction_quality_audit.md` documents which fields are sparse and why.

## Revisit at scale

v2: extend to Life and Motor (ADR-002 §revisit). Add ESG annual reports to the regulatory corpus for ratings sub-score.
