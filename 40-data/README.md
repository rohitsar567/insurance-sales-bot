# `data/` — Runtime + marketplace data

Three classes of file live here, intentionally side-by-side:

1. **Runtime state** — written by the live server during normal operation (`profiles/`, `sessions/`, `llm_health.json`, `llm_usage.jsonl`).
2. **Pre-computed marketplace data** — curated artefacts the server reads on every relevant turn (`policy_facts/`, `premiums/`, `reviews/`).
3. **Source/lineage maps** — human-readable manifests of where every claim traces back to (`corpus_urls.md`, `regulatory_urls.md`, `information_source_map.md`).

The structured policy schema and PDFs themselves live under `rag/`. This folder is downstream.

## Top-level files

| File | What it is | Owner |
| --- | --- | --- |
| `corpus_urls.md` | Discovery manifest — every PDF URL ingested into `rag/corpus/`. | discovery agent / `tools/check_link_rot.py` |
| `regulatory_urls.md` | IRDAI / regulatory PDF URLs. See [ADR-017](../docs/60-decisions/ADR-017-irdai-corpus-playwright-rescue.md). | discovery agent |
| `information_source_map.md` | Human-readable claim → URL → verdict map. Master audit doc for the Source Methodology directive. Mirror of `eval/info_source_map.json`. | `tools/info_source_map.py` |
| `llm_health.json` | Last per-provider health-probe snapshot (latency, success, last error). Powers the admin tab. | `backend/llm_health.py` |
| `llm_usage.jsonl` | Append-only per-call log: provider, model, tokens, latency, success. Aggregated in the admin tab. | `backend/main.py` |

## Subdirectories

| Path | Class | Contents |
| --- | --- | --- |
| `profiles/` | runtime | Persistent named-profile JSON store (KI-040). One file per user, normalised-name slug. See `data/profiles/README.md`. |
| `sessions/` | runtime | Per-session conversation state JSONs. Ephemeral — pruned periodically. Currently includes `anonymous.json` (no-name fallback). |
| `policy_facts/` | pre-computed | **256 curated JSONs**, one per policy variant. Each field carries `{value, unit?, source_pdf_path, source_quote}` provenance. The Indian-BFSI-audit-grade machine source; `kb/policies/*.md` are the human-readable mirror. See `_curation_report.md` for the three batches that built it. |
| `policies/` | pre-computed | Subfolder per insurer with PDFs / supplementary text used for one-off lookups outside the main ingest pipeline. |
| `premiums/` | pre-computed | `illustrative_premiums.json` — sample starting premiums pulled from PolicyBazaar / JoinDitto / Beshak + insurer rate cards (2026-05-13). Refreshed by `tools/refresh_premiums.py`. **Illustrative only** per [ADR-007](../docs/60-decisions/ADR-007-illustrative-pricing.md). |
| `reviews/` | pre-computed | One JSON per insurer with IRDAI claim-settlement metrics, complaints/10K, aggregator sentiment, news tone. Index + leaderboard in `reviews/INDEX.md`. Source: IRDAI Annual Report 2023-24. |

## Provenance + KPIs

| Metric | Value (2026-05-14) | Where to verify |
| --- | --- | --- |
| Curated policy variants | 256 | `data/policy_facts/` file count |
| Per-policy avg field completeness | 83.5% (Batch 1) | `data/policy_facts/_curation_report.md` |
| Information-source-map verdicts | ✅ 798 · ⚠️ 321 · ❌ 0 · ⏳ 1385 | `eval/info_source_map.json` |

## Related

- [`kb/AUDIT_TRAIL.md`](../kb/AUDIT_TRAIL.md) — end-to-end lineage; `data/policy_facts/` is stage 8 output
- [`kb/INDEX.md`](../kb/INDEX.md) — policy index with completeness % per file
- [ADR-007](../docs/60-decisions/ADR-007-illustrative-pricing.md) — pricing is illustrative, never a real quote
- [ADR-009](../docs/60-decisions/ADR-009-19-insurer-comprehensive-schema.md) — 19-insurer scope + 48-field schema
