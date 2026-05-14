# Eval — Gold Q&A + Run History

_Auto-generated. Source: `eval/gold_qa.json` + `eval/results.json` + `eval/run.py`._

## Gold Q&A composition — 96 pairs total

| Type | Count |
| --- | --- |
| `waiting_period` | 27 |
| `coverage_scope` | 21 |
| `exclusions_oos` | 20 |
| `sub_limit` | 12 |
| `regulatory_oos` | 10 |
| `bonus` | 6 |

**Refusal-test questions:** 30 (these test the bot correctly refuses out-of-corpus questions)

## Most recent eval run

- Ran: 2026-05-12T22:30:15Z
- Questions: 25
- Factual accuracy: **40.0%**
- Citation accuracy: **50.0%**
- Refusal precision: **44.4%**
- Blocked by faithfulness: 12

## Methodology

- Gold Q&A built by 3 pipelines: auto-from-extraction (templated), LLM-drafted (human-verified), hand-crafted adversarial. See `70-docs/03-eval-plan.md`.
- Grader: NIM Llama-4 Maverick (Meta MoE) — different family from the DeepSeek-V4 brain → non-circular (D-019, 2026-05-14). The earlier Groq Llama grader was retired in the same consolidation.
- Re-run: `python -m eval.run [--limit N] [--policy <id>]`.
- CI gate: `.github/workflows/eval.yml` runs eval on every PR; blocks merge if factual_accuracy < 0.65 or citation_accuracy < 0.55.