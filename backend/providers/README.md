# `backend/providers/` — STT / TTS / LLM / embedding clients

Every external model is fronted by a small typed client here. The orchestrator and helper modules **only** ever import provider symbols from this folder — single import surface so a provider swap is local.

## Files

| File | Provider | Role | Notes |
| --- | --- | --- | --- |
| `base.py` | — | Abstract `LLM`, `STT`, `TTS`, `Embeddings` Protocols. Every concrete client conforms. | — |
| `nvidia_nim_llm.py` | NVIDIA NIM | Core chain runner — `NimChainLLM(chain=[...])` uses probe-driven primary election (KI-080): calls the elected PRIMARY once per turn, falls to elected BACKUP on real-time failure. Exposes `get_brain_llm()` (the separate `get_fast_brain_llm()` / `get_judge_llm()` accessors were collapsed into it in the 2026-05-15 three-chain consolidation). Legacy `_balanced_brain_chain()` (50/50 NIM ↔ Groq rotator) retained as a bypassed feature-flag branch for one-release rollback. | [ADR-019](../../70-docs/60-decisions/ADR-019-nim-single-provider-consolidation.md), [ADR-031](../../70-docs/60-decisions/ADR-031-sticky-primary-election.md) (supersedes [ADR-026](../../70-docs/60-decisions/ADR-026-provider-load-balancing.md)) |
| `groq_llm.py` | Groq | Single-call Llama-3.3-70B client. Used as cross-provider backup election candidate (KI-080) for both brain + fast-brain chains. | [ADR-031](../../70-docs/60-decisions/ADR-031-sticky-primary-election.md) |
| `openrouter_llm.py` | OpenRouter | Multi-model fallback rung (DeepSeek-V3 etc.) for chains; rarely the primary in production. | — |
| `sarvam_llm.py` | Sarvam-M | Indic-aware LLM. Used directly by `backend/single_brain.py` for Indic outputs — Sarvam-M routing is conditional on `_detect_language(user_text)` returning `'indic'`. | [ADR-006](../../70-docs/60-decisions/ADR-006-sarvam-first-stack.md) |
| `sarvam_stt.py` | Sarvam Saarika v2.5 | Speech-to-text (10 Indic languages + English). | ADR-006 |
| `sarvam_tts.py` | Sarvam Bulbul v2 | Text-to-speech; returns base64 WAV the frontend mounts in the in-DOM `<audio>` element. | ADR-006 |
| `voyage_embeddings.py` | Voyage AI | Original ingest-time embedder. **Not on the hot path** — query-time uses Chroma vectors directly. Configured in `.env` for occasional re-ingest. | [ADR-011](../../70-docs/60-decisions/ADR-011-bge-local-embeddings.md) |
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

Per-link timeout is dynamically clipped to remaining budget. KI-084 explicit per-phase `httpx.Timeout(connect=2, read=<per-link>, write=2, pool=2)` is nested inside.

## Credit-aware election (KI-085)

Beyond liveness, election in `backend/llm_health.py` is gated on `credits_remaining > credits_low_water` per candidate so quota-exhausted providers are excluded BEFORE producing a user-facing 429. KI-087 further prefers NIM as primary; Groq/OpenRouter serve as emergency fallback.

### Per-provider signal sources

| Provider | Producer | Signal | Unit | Low-water |
|---|---|---|---|---|
| **Groq** | `update_credits_from_groq` from `groq_llm.py::chat` | `x-ratelimit-remaining-tokens-day` header + `x-ratelimit-reset-tokens-day` for reset | `tokens_day` | **5,000** (one fact-find round-trip ~2.4K + margin) |
| **OpenRouter** | `poll_openrouter_credits` (every 10 min) + `update_credits_from_openrouter_headers` per-call fallback | `GET /api/v1/credits` → `{total_credits, total_usage}` | `usd_balance` | **$0.05** USD |
| **NIM** | `record_nim_call` from `NimChainLLM._try` | Local 60s deque of monotonic timestamps per model | `requests_min` (remaining in current 60s window) | **5.0** slots (gate at 35/40 req/min) |

### Election predicate

In `_is_election_eligible(h, now_mono)`:

```python
if h.credits_reset_at is not None and now_mono >= h.credits_reset_at:
    return True                                # quota already reset
if h.credits_remaining is None:                # cold-start permissive
    return True
return h.credits_remaining > h.credits_low_water
```

### Cold-start

`credits_remaining = None` is permissive — fresh process restarts don't grind to a halt before the first call has stamped credit state. OpenRouter poll fires on startup so the USD balance is non-None within seconds.

See [ADR-032](../../70-docs/60-decisions/ADR-032-llm-chain-architecture.md) §6 for the full table and §12 for the operational runbook.

## Related

- [ADR-006](../../70-docs/60-decisions/ADR-006-sarvam-first-stack.md), [ADR-011](../../70-docs/60-decisions/ADR-011-bge-local-embeddings.md), [ADR-019](../../70-docs/60-decisions/ADR-019-nim-single-provider-consolidation.md), [ADR-026](../../70-docs/60-decisions/ADR-026-provider-load-balancing.md)
- `tests/test_routing_regression.py::TestProviderLoadBalancing` — pins the legacy 50/50 split (kept as a bypassed-by-default invariant; KI-080's probe-driven election supersedes it for live traffic)
- `40-data/llm_health.json` — last health-probe snapshot surfaced in the admin tab
