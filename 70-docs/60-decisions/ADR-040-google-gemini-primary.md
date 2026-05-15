# ADR-040 — Google Gemini Flash as primary LLM tier (KI-179)

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Supersedes:** [ADR-038](ADR-038-nim-only-chains.md) (NIM-only chain lock) — partially. The "fail-loud > fail-silent-with-garbage" principle and the brain ↔ judge family-diversity invariant are preserved; the strict NIM-only candidate-pool scope is relaxed now that the `<FF>` trailer convention that motivated the lock no longer exists post-[ADR-039](ADR-039-llm-driven-sales-brain.md) / KI-167.
**Related KIs:** KI-167 (sales_brain rip-out), KI-171 (judge skip on fact_find + recommendation), KI-175 (NIM chain reorder — nemotron demoted to last), KI-176 (OpenRouter `models: [...]` server-side fallback), KI-178 (live audit of OR free-tier JSON-mode support), KI-179 (this decision — Google AI Studio added as Tier 0).

## Context

After KI-167 ([ADR-039](ADR-039-llm-driven-sales-brain.md)) eliminated the `<FF>...</FF>` trailer convention in favour of native provider JSON mode (`response_format={"type":"json_object"}` on NIM, `response_mime_type=application/json` on Gemini), the structural reason for the NIM-only lock dissolved. KI-176 added OpenRouter back as a cross-provider fallback using OR's `models: [...]` array param for server-side fallback within the OR free pool.

KI-178 then audited which OpenRouter free-tier models actually support `response_format`:

- **No Gemini variants exist on OR's free tier.**
- **Llama 3.3 70B and Hermes 3 405B on OR free do not expose `response_format`** — JSON would have to be prompt-engineered, which carries a non-trivial parse-failure rate (the very class of silent failure KI-160 / ADR-038 was instituted to prevent).
- **The only OR free-tier models with native JSON mode** are `nvidia/nemotron-3-super-120b-a12b:free`, `qwen/qwen3-next-80b-a3b-instruct:free`, and `google/gemma-4-31b:free`.

In parallel, the operator obtained a Google AI Studio API key. Google's documented free tier on Gemini 2.0 / 2.5 Flash is **1,500 requests/day, 15 requests/minute, native JSON mode via `response_mime_type=application/json`**, with conversational quality that is genuinely better than any model in the existing NIM pool for the sales-brain free-form QA + recommendation surfaces. The free quota is adequate for the demo + early-production workload (a typical fact-find conversation is ~10 turns; 1,500 req/day supports ~150 conversations/day before falling to fallbacks).

The chain shape needs to reflect this: a frontier-quality free tier exists, and routing past it would be deliberately ignoring a strictly-better primary.

## Decision

**Add Google AI Studio as Tier 0 (primary) on the Brain Fast and Brain Main chains.** NIM stays as Tier 1 fallback with its 675B Mistral Large 3 as the strongest non-Gemini candidate. OpenRouter ($10 stays parked) sits as Tier 2 diversity pool before the nemotron-49b last resort. The judge stays NIM-primary on Mistral Large 3 675B — different family from Gemini, preserving the brain ↔ judge family-diversity invariant.

Concretely, the three chains become:

### Brain Fast (fact-find conversation, `backend/sales_brain.py`)

1. **PRIMARY — Google AI Studio:** `gemini-2.0-flash` (1500 req/day free, native JSON mode)
2. Fallback 1 — NIM: `qwen3-next-80b-a3b-instruct`
3. Fallback 2 — NIM: `mistralai/mistral-large-3-675b-instruct-2512` (675B dense)
4. Fallback 3 — NIM: `meta/llama-4-maverick-17b-128e-instruct` (128B MoE)
5. Fallback 4 — OpenRouter: `nvidia/nemotron-3-super-120b-a12b:free`
6. Fallback 5 — OpenRouter: `qwen/qwen3-next-80b-a3b-instruct:free`
7. Last resort — NIM: `nvidia/llama-3.3-nemotron-super-49b-v1.5`

### Brain Main (orchestrator free-form QA + recommendation synthesis)

1. **PRIMARY — Google AI Studio:** `gemini-2.5-flash` (same 1500 req/day quota, higher synthesis quality than 2.0 Flash on long-context recommendation generation)
2. Fallback 1 — NIM: `mistralai/mistral-large-3-675b-instruct-2512`
3. Fallback 2 — NIM: `meta/llama-4-maverick-17b-128e-instruct`
4. Fallback 3 — NIM: `qwen/qwen3-next-80b-a3b-instruct`
5. Fallback 4 — OpenRouter: `nvidia/nemotron-3-super-120b-a12b:free`
6. Last resort — NIM: `nvidia/llama-3.3-nemotron-super-49b-v1.5`

### Judge (faithfulness Gate 4 — KI-171 skips this on `fact_find` + `recommendation` queries)

1. **PRIMARY — NIM:** `mistralai/mistral-large-3-675b-instruct-2512` (different family from the Gemini brain; preserves cross-family invariant)
2. Fallback 1 — NIM: `meta/llama-4-maverick-17b-128e-instruct`
3. Fallback 2 — OpenRouter: `qwen/qwen3-next-80b-a3b-instruct:free`
4. Last resort — NIM: `nvidia/llama-3.3-nemotron-super-49b-v1.5`

A new `backend/providers/google_gemini_llm.py` wrapper (KI-179) implements the `LLMProvider` interface against Google AI Studio's REST API, matching the same `chat()` signature as `NvidiaNimLLM` and `OpenRouterLLM`. `NimChainLLM` is provider-agnostic — chains may now mix Google / NIM / OpenRouter candidates by URL.

## Consequences

### Positive

- **Frontier-tier conversational quality on the free path.** Gemini 2.0 Flash + 2.5 Flash are genuinely best-in-class for the sales-brain surface (one-shot JSON-mode replies with tight schemas + natural prose) and the recommendation-synthesis surface (multi-policy comparison + ranked shortlist).
- **Native JSON mode preserved.** `response_mime_type=application/json` is Google's server-enforced equivalent of NIM's `response_format={"type":"json_object"}` — the [ADR-039](ADR-039-llm-driven-sales-brain.md) structured-output guarantee survives the provider swap.
- **NIM's massive Mistral Large 3 (675B dense) stays accessible as Fallback 1** on both Brain chains. The judge chain still leads with Mistral 675B, so the rescue-by-judge path remains identical.
- **OpenRouter $10 diversity pool stays valuable.** The two OR `:free` candidates with verified JSON mode (nemotron-3-super-120b, qwen3-next-80b) cover a third-provider tier under both Google and NIM, so a simultaneous Google + NIM degradation has at least one diverse candidate to elect.
- **Family diversity preserved at the judge boundary.** Brain primaries are Gemini (Google family); judge primary is Mistral Large 3 675B (Mistral family) — the brain ↔ judge family diversity that [ADR-014](ADR-014-groq-llama-grader.md) / [ADR-032](ADR-032-llm-chain-architecture.md) require is intact.

### Negative

- **One additional dependency** — Google AI Studio key (`GOOGLE_API_KEY`). The provider list grows from {NIM, OpenRouter, Sarvam} to {Google, NIM, OpenRouter, Sarvam}.
- **Free-tier quota cap of 1500 req/day on Gemini Flash.** Adequate for demo + early production but not unbounded. A sustained 100+ concurrent-conversation load would exhaust the quota and force fallback to NIM Mistral 675B.

### Mitigations

- **6-level fallback chain on Brain Fast, 5-level on Brain Main.** A Google AI Studio outage / quota exhaustion / 429 simply falls to NIM Mistral 675B — the same provider family that has been production-validated since KI-160.
- **Quota visibility.** Daily request counter is visible at [aistudio.google.com](https://aistudio.google.com); admin telemetry (KI-086 `/api/admin/llm-health`) surfaces per-candidate health + degraded-until timestamps, so quota-exhaustion is observable in the existing admin panel.
- **Key rotation procedure exists.** Google AI Studio keys can be rotated in-console; the `GOOGLE_API_KEY` env in HF Space secrets is one-flip to swap.
- **Probe loop (KI-080 + KI-088 NIM semaphore) extends naturally** to the Google provider — `backend/llm_health.py` scores every candidate uniformly on `(1 / max(50, latency_ms)) * success_rate`; the elector treats provider-of-origin as opaque.

## Alternatives considered

- **Ship Llama 3.3 70B / Hermes 3 405B from OpenRouter free tier as the new primary.** Rejected. KI-178 confirmed neither model exposes `response_format` on OR free — JSON would have to be prompt-engineered out of prose, reintroducing the class of silent failure that KI-160 / [ADR-038](ADR-038-nim-only-chains.md) was instituted to prevent. The point of native JSON mode is server-side enforcement; a model without it is structurally worse for our use case regardless of its parameter count.

- **Stay NIM-only (KI-175 state — nemotron demoted to last, Mistral 675B as new NIM primary).** Rejected. Live testing showed Gemini Flash conversational quality is genuinely better on the sales-brain surface (more natural opener variety, less repetition, fewer "Got that —" style filler patterns), and the 1500 req/day Google free tier is plentiful for the workload. Routing past a strictly-better free primary would be deliberately ignoring it.

- **Buy paid Gemini via OpenRouter's $10 balance.** Rejected. OR's paid Gemini is ~1000–2000 conversations before the $10 refill is needed; direct Google AI Studio is genuinely free at the same quality level. The OR $10 is better spent on the diversity-pool free models (nemotron-3-super-120b, qwen3-next-80b) that round out the fallback chain.

- **Make Gemini the judge too (same family across all roles).** Rejected. Brain ↔ judge family diversity ([ADR-014](ADR-014-groq-llama-grader.md) / [ADR-032](ADR-032-llm-chain-architecture.md)) is the structural defence against circular grading — the judge would mark its own homework. Keeping the judge on Mistral Large 3 675B (Mistral family) preserves the non-circular grading invariant when the brain is Gemini.

## Reversal trigger

Re-open this ADR if (a) Google AI Studio imposes a paid-tier requirement or removes `response_mime_type` support, (b) the 1500 req/day quota is consistently exhausted before noon UTC over a 7-day window (signalling we need either a paid tier or a different primary), (c) live audit shows Gemini Flash silently violating the JSON-mode contract on any sales-brain or recommendation turn (the failure class KI-160 originally locked NIM against), or (d) the brain ↔ judge family-diversity invariant breaks (e.g. Google ships a judge-grade reasoning model and we're tempted to consolidate). Reversal would shift the Brain primaries back to NIM Mistral 675B with OpenRouter as Tier 1 — i.e. the KI-176 state — not the strict NIM-only lock of KI-160 (the `<FF>` trailer convention that motivated that lock is permanently retired).
