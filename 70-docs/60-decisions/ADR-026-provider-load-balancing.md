# ADR-026 — Brain provider load-balancing (NIM ↔ Groq, 50/50)

**Status:** Superseded by [ADR-040](ADR-040-google-gemini-primary.md) (2026-05-15) — the 50/50 random-coin load balance was first deprecated by [ADR-031](ADR-031-sticky-primary-election.md)'s probe-driven sticky-primary election (KI-080) and is now fully retired. The current chain shape (Google AI Studio Tier 0 primary → NIM Tier 1 fallback → OpenRouter `:free` Tier 2 diversity) is decided by per-call election against probe scores rather than a static probability split.
**Date:** 2026-05-14
**Owner:** Rohit Saraf
**Originally supersedes:** Partial override of [ADR-019](ADR-019-nim-single-provider-consolidation.md)'s single-provider stance

## Context

ADR-019 consolidated the brain on NVIDIA NIM as a single provider to simplify operations + keep cost at $0. But NIM's free-tier rate cap (**40 req/min, shared across every model on a single API key**) became the single biggest throughput bottleneck once the system was exercised at scale: the 100-persona audit (3,000 turns × ~2 brain calls each) and the parallel 96-Q gold eval (6 workers × ~2 NIM calls each) regularly saturated the quota.

Two ways to mitigate:

1. **Upgrade to paid NIM** — eliminates the rate cap but introduces cost.
2. **Add a second free-tier provider** with its own independent rate quota, and split brain load between them.

Option 2 was chosen because the codebase already had Groq as a cross-provider fallback at the bottom of the brain chains; the LPU primary (`llama-3.3-70b-versatile`) has sub-1s TTFT, often *faster* than NIM Qwen 80B, so adding it as a load-balanced primary is a strict latency win as well.

## Decision

The brain chain primary rotates **50/50 per call** between NIM Qwen 80B and Groq Llama-3.3-70B, via `random.random()` evaluated at chain-construction time. The remaining 7 candidates of the chain stay in their existing fallback order so a Groq-primary call that fails (rare) still gets the full NIM fallback ladder.

Implementation: `_balanced_brain_chain(base, groq_first_probability=0.5)` in [`backend/providers/nvidia_nim_llm.py`](../../backend/providers/nvidia_nim_llm.py). Both `get_brain_llm()` and `get_fast_brain_llm()` go through this rotator.

## Why per-call random (not a shared counter)

A shared `itertools.cycle([0,1])` counter looked simpler but **breaks under async concurrency**. With multiple workers all calling `get_brain_llm()` interleaved with `get_fast_brain_llm()`, a strict alternation produces pathological patterns — e.g. every brain call lands on NIM and every fast-brain on Groq (which the smoke test caught the first time we tried this). Per-call `random.random()` is independent for every invocation, statistically fair in aggregate, and has no shared mutable state to race on.

A unit test pins this behavior — `tests/test_routing_regression.py::TestProviderLoadBalancing` asserts that with `groq_first_probability=0.5` over 1000 calls (seeded), the Groq-primary count lands between 400 and 600.

## Consequences

| Win | Cost |
|---|---|
| ~2× sustained brain throughput across two independent rate caps | One more provider key to monitor (Groq) — but key was already in `.env` from the original cross-provider fallback work |
| Groq LPU's ~1s TTFT often *lower* than NIM Qwen 80B → latency win on top of throughput | Brain replies have two distinct response styles (Qwen vs Llama) — minor consistency concern; mitigated by the faithfulness gate, which judges by content not voice |
| Reliability unchanged — every primary still has the full fallback chain underneath it | If both providers' free tiers simultaneously degrade, system queues; same as before |
| Free-tier remains free | — |

## Alternatives considered

1. **Paid NIM** — rejected for cost; user explicitly opted out of paid services.
2. **Together AI / Fireworks AI** as a third provider — viable but paid for non-trivial volume; deferred to v2.
3. **Two NIM accounts with separate keys + round-robin** — works but bumps against NVIDIA TOS; risky for prod.
4. **Higher Groq probability (>50%)** — Groq's LPU is faster, so 100% Groq looked tempting. Rejected because Groq's free tier itself has a smaller quota; 50/50 is the largest split that keeps both quotas usefully alive.

## Related

- [ADR-019](ADR-019-nim-single-provider-consolidation.md) — original single-provider stance (now partially superseded)
- [`backend/providers/nvidia_nim_llm.py::_balanced_brain_chain`](../../backend/providers/nvidia_nim_llm.py)
- [`tests/test_routing_regression.py::TestProviderLoadBalancing`](../../tests/test_routing_regression.py)
