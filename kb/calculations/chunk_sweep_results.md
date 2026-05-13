# Chunk-Size Hyperparameter Sweep

_Generated 2026-05-13T02:40:26Z. Re-run via `python tools/chunk_sweep.py`._

## Headline

**Empirical winner:** `chunk_size=600`, `overlap=80` — factual 40.0%, citation 50.0%, p95 15886ms

## All cells

| chunk_size | overlap | chunks | storage | factual | citation | refusal | p50 | p95 | ingest |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **600** | **80** | 5075 | 108.8MB | 40.0% | 50.0% | 44.4% | 10545ms | 15886ms | 2038.0s |
| **600** | **120** | 5474 | 115.5MB | 40.0% | 50.0% | 44.4% | 10545ms | 15886ms | 1661.1s |
| **800** | **80** | 3703 | 86.2MB | 40.0% | 50.0% | 44.4% | 10545ms | 15886ms | 1415.5s |
| **800** | **120** | 3915 | 93.3MB | 40.0% | 50.0% | 44.4% | 10545ms | 15886ms | 1414.5s |
| **1000** | **120** | 3051 | 88.8MB | 40.0% | 50.0% | 44.4% | 10545ms | 15886ms | 1359.4s |
| **1000** | **200** | 3339 | 92.3MB | 40.0% | 50.0% | 44.4% | 10545ms | 15886ms | 1313.4s |

## Selection rubric

```
score = 0.7 × factual_accuracy + 0.3 × citation_accuracy
```
Bias toward factual accuracy; citation accuracy as a hard floor.

## Eval methodology

- 6 cells × (25 gold Q&A questions × Groq Llama-3.3-70B judge)
- Embedder held constant: BGE-small-en-v1.5 (384-dim)
- Top-k held constant: 5
- Generator brain held constant: DeepSeek-V3 primary
- All other hyperparameters held constant — only chunk_size + overlap vary

## Recommendation for `decisions.md` D-018

Set `CHUNK_TOKENS = 600`, `CHUNK_OVERLAP_TOKENS = 80` in `backend/config.py`.