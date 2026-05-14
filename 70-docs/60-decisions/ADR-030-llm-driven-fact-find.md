# ADR-030 — LLM-driven fact-find (single brain call per turn)

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Supersedes:** [ADR-027](ADR-027-fact-find-llm-paraphraser.md) (the LLM paraphraser was a band-aid on the hardcoded-graph stitching; this is the full replacement.)
**Related:** [ADR-022](ADR-022-conversational-profile-updates.md) (free-form profile updates — the answer-side LLM that this ADR generalizes into the whole loop)

## Context

The fact-find loop as it stood after ADR-027 was a three-layer template-stitch:

1. **Layer 1 — hardcoded `GRAPH`** in `backend/needs_finder.py`: nine canonical questions, fixed order, fixed prose.
2. **Layer 2 — opener rotation** in `backend/orchestrator.py`: `_pick_opener()` chose one of `_NEUTRAL_OPENERS` ("Got it.", "Thanks.", "Noted.") or `_FAMILY_OPENERS` ("Got it — family of three.", ...) plus an acknowledger template.
3. **Layer 3 — LLM paraphraser** (ADR-027): a single `FAST_BRAIN_CHAIN` call per slot rewrote the canonical question text using recent user history, with a verifier rejecting slot drift / missing `?` / out-of-bounds length.

Real-user testing on 2026-05-15 surfaced two failure modes that no amount of paraphrase tuning fixed:

- **Robotic copy-paste cadence.** Even with paraphrased question text, the opener + acknowledger + paraphrase + canonical-fallback combinatorics produced predictable rhythms across personas. User feedback: *"robotic copy paste responses rather than natural llm generated responses."*
- **One-slot-at-a-time stiffness.** A user saying *"I'm 34, live in Mumbai, just myself"* got only the slot the GRAPH was currently driving captured. Other facts had to be re-extracted in subsequent turns or dropped on the floor.

The root cause is architectural: a hardcoded state machine cannot natively express "the next reply depends on what the user just said, what they've already told us, and what we still need." Paraphrasing the *question text* doesn't change that — it just gives the state machine a costume.

## Decision

**One brain call per fact-find turn produces a natural conversational reply plus a JSON tail block describing what was captured and what's next.**

Implementation: `backend/fact_find_brain.py::drive_fact_find()`. The single call is `NimChainLLM(FAST_BRAIN_CHAIN, total_budget_s=22s)` so it benefits from the [ADR-031](ADR-031-sticky-primary-election.md) probe-driven primary election (which supersedes ADR-026's static 50/50 rotation) and the cumulative budget ceiling from KI-021.

System prompt contains:

- The 9-slot schema (slot id, description, accepted value shapes, examples).
- Current profile state (which slots are filled, with values).
- The last N turns of chat history.
- An instruction to emit conversational prose followed by a strict-JSON `<FF>...</FF>` tail block.

Output contract:

```
<assistant prose>

<FF>
{
  "captured": {"age": 34, "dependents": "self", "location_tier": "metro"},
  "slot_driving": "income_band",
  "complete": false
}
</FF>
```

The orchestrator strips the `<FF>` block before the prose is shown to the user; the JSON block updates `session_state.profile` and selects the next slot in one pass.

### Safeguards

| Guard | Trigger | Action |
|---|---|---|
| JSON-block-must-parse | `<FF>...</FF>` missing or invalid JSON | Drop the capture update; show user-visible prose; on next turn fall back to canonical `next_question(slot_id)` |
| Slot-not-progressing | 3 consecutive turns with same `slot_driving` and zero captures and `complete: false` | Bail to canonical `next_question(slot_id)` for the remainder of the session |
| Hard 12s total budget | `FAST_BRAIN_CHAIN.total_budget_s` exhausted | Fall back to canonical `next_question(slot_id)`, log chain-exhaustion |
| LLM-failure fallback | `NimChainLLM` raises after exhausting all candidates | Fall back to canonical `next_question(slot_id)` |

The canonical `GRAPH` and `next_question(slot_id)` in `backend/needs_finder.py` are retained *purely as the safeguard fallback path* — never the primary path in steady state, but always available so fact-find can never wedge.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Keep ADR-027 paraphraser but expand `_NEUTRAL_OPENERS` / `_FAMILY_OPENERS` to ~30 templates each | Combinatorial expansion fights the symptom, not the cause. The cadence problem isn't word diversity — it's that the state machine drives one slot per turn regardless of what the user actually said. Multi-fact capture stays broken. |
| LLM acknowledger only (paraphrase the opener + acknowledger but keep hardcoded question text) | Half-measure. The stiff bit users perceived was the *question text being identical across sessions* — paraphrasing only the opener would have left the canonical question as the steady-state anchor every user heard. |
| Full LLM rewrite — **chosen.** One call, one schema-aware prose+JSON output, native multi-fact capture, paraphrase + opener + acknowledger + slot-selection all collapsed into one decision surface. | — |
| Let the LLM pick the next slot freely, no GRAPH | Equivalent to this ADR but without the schema-as-system-prompt anchor. We keep the 9-slot schema in the prompt so the brain still has a hard list of what to fill; `complete: true` is only valid when all 9 are filled. Removing the schema entirely would make completion criteria undefined. |

## Consequences

| Win | Cost |
|---|---|
| Fact-find prose adapts naturally per session — no template fingerprints. | +3-5s per fact-find turn vs the canonical-fallback path (one chain call instead of a string lookup), partially offset by Groq LPU rotation. |
| **Native multi-fact capture** — one user utterance can fill 2-4 slots in one turn. Verified live on 2026-05-15: opener *"Hi, I'm Rohit Sar. I'm 32, just myself, living in Mumbai."* → captured `{name, age, dependents, location_tier}` in one turn. | Variance in turn count across personas — some personas finish in 3 turns, others in 7. Bounded by `complete: true` requiring all 9 slots filled. |
| Deletes ~500 LOC of template / state-machine code (paraphraser module, `_pick_opener`, `_NEUTRAL_OPENERS`, `_FAMILY_OPENERS`, `_contains_self_introduction`, acknowledger templates, KI-067 first-policy regex). Net diff at commit `364591b`: +546 / -746. | One new module (`backend/fact_find_brain.py`, 441 LOC including system prompt + 5 worked examples + JSON parser + safeguards). |
| Reliability — parse-fail + slot-not-progressing + budget + chain-exhaustion all cleanly degrade to the canonical path. Worst case is identical to pre-KI-070 behavior. | The canonical `GRAPH` must stay in the codebase even though it's almost never executed. Cost: ~80 lines of dormant strings. |
| All three brain invariants survive: provider diversity (FAST_BRAIN_CHAIN has NIM + Groq), family diversity (judge stays on Mistral chain), single-key per provider. | — |

## Files touched (commit `364591b`)

- **Added:** `backend/fact_find_brain.py` (441 lines — `drive_fact_find()`, `_SYSTEM_PROMPT`, `_parse_ff_block()`, `_strip_ff_block()`, `FactFindOutcome` dataclass).
- **Modified:** `backend/orchestrator.py` (fact-find branch ~387 lines → ~95 lines that call `drive_fact_find()` and apply captured updates), `backend/needs_finder.py` (docstring update on `next_question` — now marked as fallback).
- **Deleted:** `backend/question_paraphraser.py` (193 lines).
- **Unchanged:** `backend/needs_finder.py::GRAPH` and `next_question(slot_id)` (retained as safeguard fallback). KI-040 named-profile persistence. KI-062 persona_id keying. KI-063 shown/selected/rejected interaction log. KI-046 refusal rules. `backend/profile_rag.py::upsert_profile_chunk` (profile-as-RAG layer survives).

## Revisit at scale (v2)

- Stream the prose token-by-token to the frontend (the JSON tail block is parsed server-side after stream completion) — would cut perceived latency back below the canonical-fallback path.
- Promote the same single-call pattern to the QA path so the orchestrator's QA / fact-find branches collapse into one brain call with a richer schema (intent in the JSON block instead of out-of-band classification).
- A/B test brain primary on this role: Qwen 80B for capability vs Nemotron Nano 30B for TTFT. Current default is `FAST_BRAIN_CHAIN` (Nemotron primary) but the 12s budget can absorb a Qwen call if quality wins.
