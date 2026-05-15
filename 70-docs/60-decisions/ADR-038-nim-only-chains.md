# ADR-038 — NIM-only chains (KI-160)

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Supersedes:** [ADR-031](ADR-031-sticky-primary-election.md) (cross-provider election scope), [ADR-032](ADR-032-llm-chain-architecture.md) (candidate-pool scope)
**Related KIs:** KI-155 (Groq `<FF>` contract violation, root cause), KI-160 (this lock-down), KI-080 / KI-081 / KI-084 / KI-085 / KI-087 (probe + election mechanics, retained within NIM scope)

## Context

KI-155 demonstrated that **Groq Llama-3.3-70B silently ignores the `<FF>...</FF>` structured-output trailer contract** that every fact-find turn depends on. Replies from Groq passed `NimChainLLM.chat()` as plain prose with no parseable trailer, the lenient KI-090 parser couldn't recover a contract key, and the orchestrator fell to `_canonical_fallback` (KI-072 / KI-074) — meaning the user saw a scripted slot prompt even though the LLM had "successfully" responded. From the elector's view the call was a success (HTTP 200, latency in band, no exception), so the probe loop kept Groq elected as a healthy candidate. The credit-gating + sin-bin machinery (KI-084 / KI-085) could not distinguish "model returned valid JSON in the contract format" from "model returned grammatically fine prose that violates the contract."

The cross-provider fallback was added in KI-080 ([ADR-031](ADR-031-sticky-primary-election.md)) to survive a full NIM regional outage. KI-155 inverted the trade-off: cross-provider fallback for **structured-output contracts** is a silent-failure trap, because providers don't agree on instruction-following fidelity at the trailer level, and silent failures are strictly worse for the user than a loud "service degraded" message.

User-facing symptom: fact-find turns that should have advanced one slot kept re-asking the previous slot, with no error in any log line — the bot looked broken in a way no probe or telemetry could catch.

## Decision

**All three LLM chains lock to NIM candidates only.** Concretely:

- **`BRAIN_CHAIN`** — primary `nvidia/llama-3.3-nemotron-super-49b-v1.5`, backup `qwen/qwen3-next-80b-a3b-instruct`, 3rd `mistralai/mistral-large-3-675b-instruct-2512`.
- **`FAST_BRAIN_CHAIN`** — primary `qwen/qwen3-next-80b-a3b-instruct`, backup `nvidia/llama-3.3-nemotron-super-49b-v1.5`.
- **`JUDGE_CHAIN`** — primary `meta/llama-4-maverick-17b-128e-instruct`, backup `mistralai/mistral-large-3-675b-instruct-2512`.

**If every NIM candidate in a chain fails in a single turn, the orchestrator returns a graceful error message to the user instead of cascading to Groq or OpenRouter.** Fail-loud > fail-silent-with-garbage for any chain that consumes a structured-output contract.

`GROQ_API_KEY` + `OPENROUTER_API_KEY` remain in HF Space repository secrets for future re-enable, but the chain config no longer references them — they are dormant, not active election candidates. KI-085's proactive credit gating still applies **within the NIM pool**: per-model 60-second rate-meter, gate at 35-of-40 req/min with headroom 5.

KI-080 sticky-primary election machinery, KI-084 per-phase httpx timeouts, KI-086 admin telemetry, and KI-091 / KI-094 extractor-skip + None-guard remain unchanged — they all operate within the NIM-only pool. ADR-031 and ADR-032 are superseded only on the candidate-pool scope; their probe / timeout / telemetry mechanics are retained.

## Consequences

- **(a) Higher reliability for structured-output contracts.** Every chain consumer (`<FF>` trailer parsers, faithfulness judge, profile extractor) sees output from a provider family that has been validated end-to-end against the contract. No silent contract violations.
- **(b) No Groq daily token consumption from the chain.** The 100K-tokens/day Groq free-tier quota is no longer spent on production chat turns. Groq credentials stay in Space secrets for one-flip re-enable if a future need arises.
- **(c) Tiny risk of total NIM outage causing service-degraded state.** Mitigated by 3+ candidates per chain spanning three distinct model families (Nemotron / Qwen / Mistral for brain, Llama-4 / Mistral for judge). A single-model NIM throttle event still has at least one other candidate to elect within the same provider; only a full NIM regional outage trips the graceful-error path, and that path is **observable** (admin telemetry + explicit error message to user) rather than silent.
- **(d) Probe loop spend drops.** Fewer candidates × no cross-provider probes = lower steady-state probe token consumption on `PROBE_INTERVAL_SEC = 300s`.

## Alternatives considered

- **Keep Groq as last-resort cross-provider candidate.** Rejected — the failure mode KI-155 exposed is structural to Groq's instruction-following on this prompt shape, not a transient or fixable bug. Any election that puts Groq in the election pool at all re-introduces the silent-failure risk the moment NIM degrades enough for the score to flip. The point of NIM-only is to eliminate the silent-failure class, not to ration it.
- **Switch the entire stack to OpenRouter as a universal provider abstraction.** Rejected for the same reason — OpenRouter is a routing layer, not a structured-output guarantee. The same Groq-via-OpenRouter call would have the same trailer violation. OpenRouter would also reintroduce per-call USD spend on a stack whose explicit design constraint is $0 inference.

## Reversal trigger

If NIM imposes a paid-tier requirement, regional outage rate exceeds 1% sustained over 7 days, or the candidate pool shrinks below 2 healthy candidates per chain, re-open this ADR. The reversal would still need a per-candidate contract validation (run the KI-155 `<FF>` regression suite against any prospective non-NIM candidate) before adding it back to the election pool.
