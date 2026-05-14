# `tools/` — Operational scripts

Loose collection of CLI scripts: corpus operations, data uploads, probes, KB regeneration, scheduled-job runners. Nothing under `tools/` is imported by the live server — `backend/` and `rag/` are the runtime surface.

Scheduling for the long-running ones is wired via macOS LaunchAgents — see `CRON_README.md` in this folder for cadence + script paths, and [ADR-029](../70-docs/60-decisions/ADR-029-disk-storage-hardening.md) for the disk-safety LaunchAgents.

## Corpus + extraction batch ops

| Script | Purpose |
| --- | --- |
| `extract_all_corpus.py`, `extract_batch_5.py`, `extract_failed.py`, `extract_pdf_range.py`, `reextract_all.py` | Batch re-extractions over `rag/corpus/`. Useful when the schema or extraction prompt changes. |
| `extract_pdf_text.py`, `extract_policy_text.py`, `extract_policy_text_batch2.py` | Raw text dumps for manual inspection / regex curation. |
| `curate_batch2.py`, `curate_remaining.py`, `clear_batch2.py` | Verbatim-quote curation passes that produced `data/policy_facts/`. See [`data/policy_facts/_curation_report.md`](../data/policy_facts/_curation_report.md). |
| `generate_policy_facts.py` | Convert extraction outputs to the `data/policy_facts/<id>.json` shape with `{value, unit, source_pdf_path, source_quote}` provenance. |
| `pydantic_validate_batch_5.py`, `validate_batch_5.py`, `validate_json.py`, `validate_schema.py` | Schema validators for the 62-field `HealthPolicy`. |
| `count_fields.py` | Per-policy completeness scorer that feeds the `kb/INDEX.md` completeness % column. |

## Source-map + verification

| Script | Purpose |
| --- | --- |
| `info_source_map.py` | Builds `eval/info_source_map.json` + `data/information_source_map.md` — claim → URL → verdict (✅ / ⚠️ / ❌ / ⏳). The canonical KPI for source-grounding quality. |
| `verify_urls.py` | HEAD-checks every URL in the corpus / facts; writes `eval/verified_urls.json`. |
| `verify_review_urls.py`, `verify_new_corpus.py` | Sub-verifiers for the reviews dataset and freshly-added corpus URLs. |
| `browser_verify.py` | Playwright-backed verifier for URLs that block HEAD requests. Output: `tools/browser_verified.json`. |
| `check_link_rot.py`, `check_pdf_etags.py` | LaunchAgent-driven freshness checks — corpus URL rot + PDF eTag drift. |
| `refresh_premiums.py` | LaunchAgent-driven refresh of `data/premiums/illustrative_premiums.json`. |

## KB + dataset builders

| Script | Purpose |
| --- | --- |
| `build_kb_mirror.py` | Regenerates the entire `kb/policies/<id>.md` tree from `data/policy_facts/`. Idempotent. |
| `ingest_kb_summaries.py` | Ingests `kb/policies/*.md` summaries into Chroma so policy meta is retrievable. Carries the HNSW bloat tripwire. |
| `ingest_reviews.py` | Ingests `data/reviews/<insurer>.json` into Chroma. Carries the HNSW bloat tripwire. |
| `build_readme_pdf.py` | Renders the master `README.md` to PDF for offline review. |

## HF Hub uploads (data-side mirror)

| Script | Target |
| --- | --- |
| `upload_to_hf.py` | Code-side push to the HF Space repo (`huggingface.co/spaces/rohitsar567/InsuranceBot`). |
| `upload_corpus_to_dataset.py`, `upload_extracted_to_dataset.py`, `upload_vectors_to_dataset.py`, `upload_all_to_dataset.py` | Push specific slices of `rag/` to the companion HF Dataset `rohitsar567/insurance-bot-data`. See [ADR-020](../70-docs/60-decisions/ADR-020-code-data-split-hf-dataset.md) and [ADR-024](../70-docs/60-decisions/ADR-024-triple-mirror-code-and-data.md). |
| `set_hf_secrets.py` | One-shot helper that pushes the runtime secrets into the HF Space (idempotent). |

## Probes + diagnostics

| Script | Provider it pokes |
| --- | --- |
| `sarvam_probe.py`, `sarvam_nothink_probe.py` | Sarvam-M / Saarika / Bulbul connectivity + latency. |
| `groq_probe.py`, `groq_long_probe.py` | Groq Llama free-tier latency + sustained-rate test. |
| `openrouter_probe.py`, `or_models.py` | OpenRouter routing + model-list inspection. |
| `pdf_probe.py` | pdfplumber parse on a single PDF — first stop when extraction silently produces empty text. |
| `heavy_smoke_test.py` | End-to-end smoke against the live HF Space (every provider in one call). |

## Chunk-size & retrieval sweeps

| Script | Purpose |
| --- | --- |
| `chunk_sweep.py`, `chunk_sweep_diagnostic.py` | Grid-search over chunk size / overlap. Output: `eval/chunk_sweep_results.json`. See [ADR-018](../70-docs/60-decisions/ADR-018-chunk-size-sweep-deferred.md). |
| `sweep_retrieval.py` | Retrieval-strategy A/B (filter vs no-filter, top-k variants). |

## Scheduled jobs / shell wrappers

| Path | Purpose |
| --- | --- |
| `install_crons.sh`, `CRON_README.md` | Install the LaunchAgents; the README is the canonical cadence + path reference. |
| `install_git_hooks.sh`, `git-hooks/` | Pre-commit hooks (decimal grep, secret scan, schema validation). |
| `full_pipeline.sh`, `pipeline_finish_all.sh`, `post_extract_deploy.sh`, `reextract_then_deploy.sh`, `quarterly_rebuild.sh` | Multi-step orchestrations (download → extract → ingest → push → smoke). |
| `reconcile_manifest.py` | Drift check between `rag/corpus/_manifest.json` and what's actually on disk. |

## Subdirectory

`audit/` — multi-persona conversational audit framework. See `tools/audit/README.md`.

## Related

- `CRON_README.md` (this folder) — LaunchAgent cadence reference
- [ADR-020](../70-docs/60-decisions/ADR-020-code-data-split-hf-dataset.md), [ADR-024](../70-docs/60-decisions/ADR-024-triple-mirror-code-and-data.md), [ADR-029](../70-docs/60-decisions/ADR-029-disk-storage-hardening.md)
- `80-audit/ENTERPRISE_AUDIT.md` — defect register, including silent-LaunchAgent regressions (D-002)
