# `backend/providers/` — STT / TTS / LLM / embedding clients

Every external model is fronted by a small typed client here. The orchestrator and helper modules **only** ever import provider symbols from this folder — single import surface so a provider swap is local.

## Files

| File | Provider | Role | Notes |
| --- | --- | --- | --- |
| `base.py` | — | Abstract `LLM`, `STT`, `TTS`, `Embeddings` Protocols. Every concrete client conforms. | — |
| `nvidia_nim_llm.py` | NVIDIA NIM | Core chain runner — `NimChainLLM(chain=[...])` walks a fallback ladder under a wall-clock budget. Exposes `get_brain_llm()`, `get_fast_brain_llm()`, `get_judge_llm()`. Also home of `_balanced_brain_chain()` (50/50 NIM ↔ Groq rotator). | [ADR-019](../../docs/60-decisions/ADR-019-nim-single-provider-consolidation.md), [ADR-026](../../docs/60-decisions/ADR-026-provider-load-balancing.md) |
| `groq_llm.py` | Groq | Single-call Llama-3.3-70B client. Used as the 50% load-balance primary for the brain chain, never standalone. | [ADR-026](../../docs/60-decisions/ADR-026-provider-load-balancing.md) |
| `openrouter_llm.py` | OpenRouter | Multi-model fallback rung (DeepSeek-V3 etc.) for chains; rarely the primary in production. | — |
| `sarvam_llm.py` | Sarvam-M | Indic-aware LLM; on the judge / translator fallback chains and used by `backend/translator.py`. | [ADR-006](../../docs/60-decisions/ADR-006-sarvam-first-stack.md) |
| `sarvam_stt.py` | Sarvam Saarika v2.5 | Speech-to-text (10 Indic languages + English). | ADR-006 |
| `sarvam_tts.py` | Sarvam Bulbul v2 | Text-to-speech; returns base64 WAV the frontend mounts in the in-DOM `<audio>` element. | ADR-006 |
| `voyage_embeddings.py` | Voyage AI | Original ingest-time embedder. **Not on the hot path** — query-time uses Chroma vectors directly. Configured in `.env` for occasional re-ingest. | [ADR-011](../../docs/60-decisions/ADR-011-bge-local-embeddings.md) |
| `local_embeddings.py` | BGE-small-en-v1.5 (local) | The actual production embedder. 384-dim, free, no rate cap. | ADR-011 |
| `_smoke_test.py` | — | Stand-alone connectivity probe — hits every provider, prints latency + first 80 chars. Run before a long audit. | — |

## Invariants

- **Never instantiate `NvidiaNimLLM(model=...)` directly.** Always go through `NimChainLLM(chain=...)` so the call survives single-pool rate limits. KI-033 migrated the last two stragglers (`profile_extractor`, `fact_find_normalizer`).
- **Brain chains preserve family diversity.** Qwen brain ↔ Mistral judge — failovers can't accidentally collapse to a single family and produce circular grading.
- **Per-call random for load-balance, not a shared counter.** `random.random()` is evaluated at chain-construction time; a shared `itertools.cycle` breaks under async concurrency (see ADR-026 "Why per-call random").

## Chain budgets

| Chain | Per-link timeout (s) | Total budget (s) | Where set |
| --- | --- | --- | --- |
| Brain | 20 | 35 | `nvidia_nim_llm.py::get_brain_llm` |
| Fast brain | 12 | 22 | `nvidia_nim_llm.py::get_fast_brain_llm` |
| Judge | 30 | 75 | `nvidia_nim_llm.py::get_judge_llm` |

Per-link timeout is dynamically clipped to remaining budget.

## Related

- [ADR-006](../../docs/60-decisions/ADR-006-sarvam-first-stack.md), [ADR-011](../../docs/60-decisions/ADR-011-bge-local-embeddings.md), [ADR-019](../../docs/60-decisions/ADR-019-nim-single-provider-consolidation.md), [ADR-026](../../docs/60-decisions/ADR-026-provider-load-balancing.md)
- `tests/test_routing_regression.py::TestProviderLoadBalancing` — pins the 50/50 split
- `data/llm_health.json` — last health-probe snapshot surfaced in the admin tab
