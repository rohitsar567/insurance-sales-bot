# ADR-039 — Replace scripted fact_find_brain with LLM-driven sales_brain (KI-167)

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Supersedes:** [ADR-027](ADR-027-fact-find-llm-paraphraser.md) (LLM paraphraser on top of `GRAPH`) and [ADR-030](ADR-030-llm-driven-fact-find.md) (one-call brain with `<FF>` trailer + canonical fallback). ADR-030's "schema-aware single LLM call per turn" intuition is preserved; the surface that implemented it (`fact_find_brain.py` + `<FF>...</FF>` trailer + `_canonical_fallback` greedy slot-walker + scripted `Question.prompt_en` prefixed with `"Got that — {slot}."`) is fully removed.
**Related KIs:** KI-070 / KI-072 / KI-074 / KI-075 / KI-090 / KI-091 / KI-094 / KI-103 / KI-150 / KI-155 / KI-156 / KI-158 / KI-161 (all retired by KI-167), KI-167 (this rip-out), KI-160 / [ADR-038](ADR-038-nim-only-chains.md) (NIM-only chain pool — the substrate this ADR builds on).

## Context

`backend/fact_find_brain.py` started life under ADR-030 as a single-LLM-call replacement for the ADR-027 paraphraser + hardcoded `GRAPH` stitch. It needed eight subsequent patches to stay alive:

- **KI-090** — lenient `<FF>` parser because real LLMs dropped the literal tags around their JSON tail.
- **KI-091 / KI-094** — guard against the extractor returning `{"name": null}` and overwriting captured fields.
- **KI-103** — `no_trailer` loop-breaker after the same slot failed twice.
- **KI-150** — `max_tokens` 420 → 700 because the prose+trailer prompt was truncating mid-paraphrase.
- **KI-155 / KI-156 / KI-158 / KI-161** — silent contract violations where the elected LLM returned grammatical prose with no parseable `<FF>` block. Every turn it happened, `_canonical_fallback` fired and the user saw the scripted `Question.prompt_en` of the next unfilled slot, prefixed with `"Got that — {slot}."` — i.e. the bot reverted to the very state machine ADR-030 was supposed to replace.

User-visible symptom by 2026-05-15: *"zero natural LLM chat — it always defaults to the script."* The `<FF>` trailer convention + `_canonical_fallback` were no longer a degraded path; they were the **dominant** path on multiple chain candidates, and the scripted prefix kept leaking into every fallback turn. The architecture had become reactive accretion — eight patches stacked on a fragile structured-output contract — not deliberate design. KI-160 / [ADR-038](ADR-038-nim-only-chains.md) closed the cross-provider silent-failure class at the chain level; KI-167 closes the structured-output silent-failure class at the prompt level.

## Decision

**Rip out `backend/fact_find_brain.py` entirely.** Replace with `backend/sales_brain.py` — a single LLM-driven sales agent that owns the entire fact-find conversation surface. Concretely:

- **One NIM call per turn**, via `NimChainLLM(FAST_BRAIN_CHAIN)` against the [ADR-038](ADR-038-nim-only-chains.md) NIM-only pool. The call uses NVIDIA NIM's `response_format={"type": "json_object"}` for structured output — the same JSON-mode contract already validated in `backend/translation_check.py`, `backend/faithfulness.py`, and `backend/security.py`.
- **System prompt** contains the 9-slot schema (slot id, description, accepted value shapes), current profile state (which slots are filled with which values), and an instruction to (a) reply naturally in the advisor's voice, (b) capture any new facts the user just stated, (c) decide which slot to drive next, (d) flag completion when all required slots are filled. The LLM is free to ask in any order, in any voice, multi-fact in one turn or one slot at a time — there is no scripted prompt to match, no opener to rotate, no acknowledger prefix.
- **JSON response shape:** `{"reply": "<prose for user>", "captures": {<slot_id>: <raw_value>, ...}, "slot_driving": "<slot_id or null>", "complete": <bool>}`. No `<FF>` tags, no trailer convention — `response_format` guarantees the whole response body parses as JSON.
- **Deterministic post-processor** (`backend/sales_brain_normalizer.py`, KI-167 WS1) takes the LLM's loose `captures` dict and emits a clean `{canonical_field: validated_value}` map: field-name alias resolution (`location` → `location_tier`), enum normalization (`Bangalore` → `metro`), INR-amount parsing, null/empty drop, type/bounds validation. No LLM calls — pure rules. The orchestrator applies the normalized map via `session.update_profile_field()` exactly as before.
- **Profile persistence is unchanged.** Captured fields still flow through `backend/profile_store.save_profile()` for disk durability and `backend/profile_rag.upsert_profile_chunk()` for retrieval-side visibility. The session-isolation guarantees from KI-102 / KI-107 / KI-112 remain in force — `sales_brain.py` only changes the *upstream* fact-find loop; the downstream persistence layer is untouched.
- **No scripted prompts.** `backend/needs_finder.py::Question.prompt_en` is no longer consulted by the fact-find branch. The `GRAPH` data structure stays in `needs_finder.py` for now (slot-id definitions are still useful as the schema source for the LLM's system prompt), but its `prompt_en` field is dead text.
- **No canonical_fallback.** `_canonical_fallback`, `_normalize_for_slot`, `_pick_opener`, `_NEUTRAL_OPENERS`, `_FAMILY_OPENERS`, `_contains_self_introduction`, `"Got that — {slot}."` prefix logic, `_ff_failed_attempts` / `_ff_skipped_slots` session fields, and every `fact_find_brain::fallback:*` telemetry variant are deleted in WS2 / WS3.
- **No trailer convention.** The lenient parser ladder (`<FF>` → fenced `json` → bare-JSON-tail) is deleted. Structured output is guaranteed by the provider, not pattern-matched from prose.

The outer 25s `asyncio.wait_for` ceiling around the brain call is retained. On total NIM exhaustion, the orchestrator returns the [ADR-038](ADR-038-nim-only-chains.md) graceful error message to the user (fail-loud) rather than cascading into a scripted reply.

## Consequences

### Positive

- **Bot feels human.** No more scripted `"Got that — {slot}."` leakage, no more identical `prompt_en` strings across sessions, no more two-line opener+question cadence. The LLM owns voice + flow end-to-end.
- **One fewer LLM call per turn.** The pre-KI-167 fact-find turn ran the brain + (on KI-091 QA turns) the profile extractor. Captures are now in the brain's structured response, so the extractor is dead code for fact-find turns and stays skipped on QA turns per KI-091.
- **Structured-output guarantees parseability.** NIM's `response_format={"type":"json_object"}` is enforced server-side; the response body either parses as JSON or the call fails loud. Eliminates the entire class of "model returned grammatical prose with no parseable trailer" silent failures (KI-155 / KI-156 / KI-158 / KI-161).
- **Eliminates 8 KIs of patching.** KI-090 (lenient parser), KI-091 / KI-094 (extractor null-overwrite guards on fact-find turns), KI-103 (no_trailer loop-breaker), KI-150 (`max_tokens` 420→700), KI-155 / KI-156 / KI-158 / KI-161 (trailer contract violations) all become moot — the root causes are gone, not patched.
- **Smaller surface area.** `backend/fact_find_brain.py` (441 LOC), the `_canonical_fallback` branch in `backend/orchestrator.py`, the `_pick_opener` family in `backend/orchestrator.py`, and the dead `Question.prompt_en` strings in `backend/needs_finder.py` all go away. Net negative LOC.

### Negative

- **Persona-audit fixtures need updating.** The persona transcripts in `eval/` and the `tests/test_persona_*` fixtures hardcode the expected turn order from the rules engine (age → dependents → income → ...). The LLM may capture in a different order (or multi-fact in one turn) depending on what the user said. WS4 owns rewriting these fixtures to assert on **final captured state** rather than per-turn slot order.
- **Higher LLM dependency.** There is no scripted safety net underneath the sales brain. If every NIM candidate in `FAST_BRAIN_CHAIN` fails simultaneously, the user sees the graceful error message from [ADR-038](ADR-038-nim-only-chains.md) — not a scripted fact-find prompt.
- **Loss of explicit completion path.** Pre-KI-167, `complete=True` was enforced by the orchestrator checking all 9 slots filled. Post-KI-167 the LLM is responsible for setting `complete: true`. The deterministic post-processor double-checks (`complete && any required slot still empty` ⇒ override to `false`), but the surface-of-trust shifts from rules engine to LLM.

### Mitigations

- **NIM JSON mode is production-validated.** `backend/translation_check.py`, `backend/faithfulness.py`, and `backend/security.py` already run `response_format={"type":"json_object"}` against the same NIM candidate pool. Failure mode is well-understood and observable.
- **Outer 25s `asyncio.wait_for` ceiling retained.** On total NIM exhaustion the failure surfaces as a clear error message (per [ADR-038](ADR-038-nim-only-chains.md)), not as a hang or as a regression to scripted text.
- **Deterministic post-processor enforces required-slot completeness.** The LLM cannot prematurely mark `complete: true` while a required slot is empty — `sales_brain_normalizer.py` overrides.
- **Persona audit refactor scoped to WS4.** Per-turn brittleness is a known cost; fixture rewrite targets final-state assertions which are stable regardless of which order the brain captures slots in.

## Alternatives considered

- **Keep `fact_find_brain.py`, harden the `<FF>` parser further.** Rejected. KI-155 / KI-156 / KI-158 / KI-161 demonstrated that the failure class is structural to a prose-plus-trailer prompt shape under load — not a parser bug. Each patch shifted the failure to a new turn / candidate / token cap. The directive *"Never use trailer conventions when the provider offers structured output"* (memory: KI-160 / [ADR-038](ADR-038-nim-only-chains.md)) generalizes to the prompt level: don't pattern-match structure out of prose when the provider can guarantee it server-side.
- **Keep `_canonical_fallback` as a last-resort path.** Rejected. The whole point of the rip-out is that the fallback was the dominant path, and the scripted `Question.prompt_en` prefixed with `"Got that — {slot}."` was exactly the user-visible artefact KI-167 is aimed at. Leaving the fallback in would keep the regression surface alive.
- **Hybrid: scripted slots 1-3, LLM brain for slots 4-9.** Rejected. The robotic feel of "what's your age? what's your city? who are your dependents?" hits in the *first three turns* — splitting the loop preserves the worst part of the old system.

## Reversal trigger

Re-open this ADR if (a) NIM removes `response_format` support, or (b) a 7-day window shows the deterministic post-processor's required-slot-completeness override firing > 5% of completion turns (signalling the LLM systematically lies about completion), or (c) persona audit pass-rate drops below 80% after the WS4 fixture rewrite and the cause is brain-side rather than fixture-side. The reversal would still NOT restore the `<FF>` trailer convention — it would either tighten the JSON-mode prompt or shift to a different provider's structured-output API.
