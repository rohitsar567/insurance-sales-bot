# ADR-031 — Sticky primary election for LLM chains

**Status:** Superseded by [ADR-038](ADR-038-nim-only-chains.md) — 2026-05-15 (KI-160). Cross-provider election candidates (Groq, OpenRouter) removed from all three chains after KI-155 demonstrated Groq Llama-3.3 silently ignores the `<FF>` trailer contract. Sticky-primary election mechanics still apply, but only within the NIM candidate pool. Body retained below for history.

**Status (original):** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Supersedes:** None (extends [ADR-019](ADR-019-nim-single-provider-consolidation.md) + [ADR-026](ADR-026-provider-load-balancing.md))
**Deprecates:** [ADR-026](ADR-026-provider-load-balancing.md)'s `_balanced_brain_chain` 50/50 NIM ↔ Groq rotation (kept as a feature-flagged bypass branch for one-release rollback; default-off).
**Related:** [ADR-026](ADR-026-provider-load-balancing.md), [ADR-030](ADR-030-llm-driven-fact-find.md)

## Context

Live probe at commits `078ff45` / `87ee522` (2026-05-15) showed **70% of fact-find turns timing out at the chain-budget ceiling**. Telemetry stamped every failure as `fallback:timeout`. Root cause: `NimChainLLM.chat()` iterated every chain candidate sequentially per call — under sustained NIM concurrency throttling, the 5 NIM-hosted models in `FAST_BRAIN_CHAIN` all queued together, burning the 22s `total_budget_s` before the chain reached the cross-provider Groq fallback. Each fact-find turn was costing 5-6 NIM calls (every NIM candidate timing out one after the other) before anything actually responded.

User observation that triggered this ADR: *"If background probes elect one LLM, why does each chat turn try every candidate and eat the rate/concurrency limit?"*

The probe loop in `backend/llm_health.py` was already running every 60s and writing per-candidate latency + success scores — but only the admin tab read it. The hot path ignored it. `filter_chain()` was the only consumer, and it only filtered "known-dead" candidates; it didn't elect a preferred one.

KI-025's 50/50 NIM ↔ Groq rotation ([ADR-026](ADR-026-provider-load-balancing.md)) was a static coin flip — fair in aggregate but blind to live degradation. When NIM was throttled, half of all brain calls still went to NIM first and ate the throttle queue before falling to Groq.

## Decision

**`NimChainLLM.chat()` uses probe-driven primary election.** Per call, the chain consults the latest probe state and:

1. Calls the **elected PRIMARY** (highest-scored candidate at probe time).
2. On real-time failure (timeout, HTTP error, structured failure), falls to the **elected BACKUP** — guaranteed to be a *different provider* from PRIMARY (NIM ↔ Groq ↔ OpenRouter).
3. Triggers an asynchronous probe refresh so the next call uses fresh signal.
4. If BACKUP also fails in the same turn, raises — orchestrator catches and applies KI-079 escalation to `BRAIN_CHAIN`, then `_canonical_fallback`.

The election runs in `backend/llm_health.py`:

- Score = inverse-latency × success-rate over the last few probes.
- Sort descending. Top score = PRIMARY.
- BACKUP = highest-scoring candidate from a *different provider* than PRIMARY. If only one provider has live candidates, BACKUP = second-highest candidate from PRIMARY's provider (graceful degradation).
- Election runs at the end of every probe cycle (60s).

### Cold-start fallback

Before the first probe completes (process restart, HF Space rebuild), no election exists. The chain falls back to `chain[0]` as PRIMARY and `chain[1]` as BACKUP — i.e. the static chain order acts as the cold-start prior. The probe loop runs immediately on startup, so cold-start lasts < 60s in normal operation.

### Per-chain election

Each of the three chains gets its own election: `BRAIN_CHAIN` / `FAST_BRAIN_CHAIN` / `JUDGE_CHAIN`. A NIM Qwen 80B that's slow for the brain may still be the fast-brain primary if its latency on shorter prompts beats Nemotron at that moment. Probe runs once per chain so signal is per-role-specific.

### Provider-aware backup election

The provider-diversity rule on BACKUP is **mandatory, not advisory**. The whole point is that if PRIMARY is failing because NIM is throttled, BACKUP must NOT also be on NIM — otherwise the second call queues in the same throttle window. Implementation: every candidate carries a provider tag (`nim` / `groq` / `openrouter`); election iterates the sorted list and picks the first candidate with a different provider tag than PRIMARY.

### Telemetry

Every `chat()` call stamps the result with the actually-used model + whether BACKUP fired. Admin tab's existing "LLM Chain" tab can read the election state and surface each chain's current PRIMARY + BACKUP + last probe timestamp.

## Alternatives considered

| Option | Why rejected |
|---|---|
| **Keep ADR-026's 50/50 rotation, just add health filtering** (skip known-dead before flipping the coin) | Doesn't fix the case where NIM is *slow*, not dead. A 5s NIM call followed by a 1.5s Groq fallback is still 6.5s; probe-election would have just called Groq for 1.5s. Health filtering alone is a strict subset of what election does. |
| **Per-call retry budget instead of per-call election** (let `chat()` keep iterating but cap at 2 calls) | Equivalent to BACKUP semantics but blind to which 2 candidates to pick. Election + 2-call cap is the same compute cost with strictly better candidate choice. |
| **Stream PRIMARY + BACKUP in parallel, take the first to respond** (hedged request) | Doubles outbound rate-limit consumption on every call. The point of KI-080 is to *reduce* per-turn calls; hedging goes the wrong direction. Reconsider if NIM moves to paid tier where rate cap stops biting. |
| **Hardcode PRIMARY per chain (e.g. Groq for brain, Nemotron for fast-brain) and skip the probe** | Loses adaptation. When Groq's free tier degrades (which it does, especially during US business hours), the probe lets us notice within 60s and elect NIM. Hardcoding pins us to whichever provider was best at deploy time. |
| **Move to paid NIM and side-step the rate cap entirely** | Real recurring cost. Same answer as ADR-019 / ADR-026 — user opted out. Election is the free-tier-compatible alternative. |
| **Probe-driven election — chosen.** Adaptive, single-call common case, two-call worst case in-turn, KI-079 as final escalation, provider-diverse backup by construction. | — |

## Consequences

| Win | Cost |
|---|---|
| **Per-turn LLM call count drops from 5-6 to 1 (most cases) or 2 (real-time failure).** Direct fix for the 70% timeout rate observed in live probe at `87ee522`. | One state read per `chat()` call. In-memory, negligible vs network call. |
| **Adaptive to live degradation.** When NIM throttles, the probe notices within 60s and elects Groq; when Groq's LPU saturates, the probe elects back to NIM. KI-025's static 50/50 had no such feedback. | Probe load adds ~3 LLM calls per minute per chain on top of user traffic. Bounded; within free-tier budgets on both providers. |
| **Provider-diverse backup is structural, not stochastic.** A NIM-PRIMARY call that fails always falls to a non-NIM BACKUP. Pre-KI-080, the chain's static order meant a Qwen-failed call might fall to another NIM model first. | If a provider has only one live candidate, BACKUP can't be provider-diverse and degrades to "second-highest same-provider". Documented graceful degradation. |
| **KI-025's `_balanced_brain_chain` is bypassed** but retained as a feature-flag branch for one-release rollback. | ~30 LOC dead-pathway in `nvidia_nim_llm.py`. Slated for deletion in v1.1. |
| **KI-079 escalation still applies.** If both PRIMARY and BACKUP fail in one turn (rare, would require simultaneous NIM + Groq degradation), orchestrator retries once on `BRAIN_CHAIN` (heavy brain, 35s budget) before `_canonical_fallback`. Election narrows the common case; KI-079 + canonical guard the tail. | None — same code path as before KI-080. |
| All three chains' family-diversity invariants hold (Qwen brain candidates ↔ Mistral judge candidates; probe never elects a Mistral as brain or Qwen as judge — election is *within* each chain's candidate list, not across chains). | — |

## Files touched (commit `6159c54`)

- **Modified:**
  - `backend/providers/nvidia_nim_llm.py` — `NimChainLLM.chat()` refactored: replaces sequential candidate-loop with elected-primary + elected-backup path. `_call_one()` extracted as the single-model HTTP call. `_balanced_brain_chain` retained but bypassed by default.
  - `backend/llm_health.py` — extended for primary/backup election. Probe cadence 5min → 60s. New public API: `get_primary` / `get_backup` / `report_failure` / `report_success` / `provider_of`. Election state held in process memory with thread lock + degraded-until timestamps.
- **Unchanged:**
  - `backend/needs_finder.py::GRAPH` and `_canonical_fallback` — KI-070 / KI-072 / KI-074 paths survive.
  - Chain definitions in `nvidia_nim_llm.py` (`BRAIN_CHAIN`, `FAST_BRAIN_CHAIN`, `JUDGE_CHAIN`) — same candidate lists, same family-diversity.
  - `backend/llm_health.py::filter_chain` — still skips known-dead candidates as a belt-and-braces guard before election runs.

## Revisit at scale (v2)

- **Per-intent election.** Currently election is per-chain; with traffic shape data, we could elect different primaries for `qa` vs `comparison` vs `recommendation` (longer outputs benefit from Qwen, shorter from Nemotron — current single-primary-per-chain is a compromise).
- **Probe cadence tuning.** 60s probe interval is fine at current traffic. At >100 concurrent users, 30s probe + 5min EWMA on score would adapt faster to mid-session degradation.
- **Hedged calls** for `comparison` / `recommendation` intent where latency dominates UX (call PRIMARY + BACKUP in parallel, take first valid response). Rejected for v1 because of rate-limit consumption; reconsider on paid NIM.
- **Cross-chain provider-budget arbitration.** Today each chain elects independently. If all three elect NIM, total NIM load goes up. A v2 election could enforce a per-minute provider-call budget across chains and force one chain to elect Groq if NIM budget is near cap.
- **Multi-worker election.** Current `_STATE` is in-process memory; multi-worker deployments would each elect independently. Acceptable today (HF Space single-worker); port to a shared store if uvicorn workers > 1.
- **Delete `_balanced_brain_chain` in v1.1** after one stable release confirms zero regressions from the bypass.
