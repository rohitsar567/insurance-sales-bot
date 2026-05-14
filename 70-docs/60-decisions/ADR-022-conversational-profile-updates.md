# ADR-022: Conversational profile updates via LLM extractor

**Status:** Locked
**Date:** 2026-05-14

## Context

Fact-find onboarding captures profile fields one structured question at a time ("First, your age?"). Once a user transitions to free-form chat (`session.free_form_session = True`), the bot stops capturing profile updates — even if the user shares clearly relevant facts:

- "I just turned 40."
- "We had a baby last month, do I need to add a dependent?"
- "I was diagnosed with diabetes last year."

These updates should:

1. Update `session.profile` immediately.
2. Trigger re-upsert of the Chroma profile chunk so retrieval reflects new state.
3. Surface to the frontend so the completeness % bar ticks up.

## Decision

**Lightweight LLM extractor on every free-form user message.** New module `backend/profile_extractor.py`.

```python
async def extract_profile_updates(
    user_text: str,
    current_profile: Profile,
) -> dict[str, Any]:
    """Return validated dict of {field_name: new_value}."""
```

Implementation:

- **Model:** NIM Llama-3.3-70B (cheap tier — extraction doesn't need the frontier brain).
- **Temperature 0.0**, max 300 tokens, conservative validation.
- **Strict enum + bounds checks** drop any field that doesn't match the existing schema (age 1-120; income_band ∈ {under_5L, 5L-10L, 10L-25L, 25L+}; etc.).
- **Health conditions are MERGED** — existing conditions preserved, only new ones appended.
- **Failure-isolated:** extractor exceptions never block the chat reply.

The extractor runs in `handle_turn()` AFTER the fact-find branch exits and BEFORE retrieval, so the immediate turn benefits from any newly extracted facts.

## Alternatives considered

| Approach | Why rejected |
|---|---|
| Function-calling pattern on the brain LLM | Requires the brain to interrupt its answer to call a tool; messy reply text. |
| Regex / keyword heuristics ("I'm X years old") | Brittle to phrasing; misses entity-aware updates ("we had a baby"). |
| Update profile only via the explicit Profile panel | Forces the user to context-switch to a form mid-conversation. |

## Wire-up

```
frontend/src/app/page.tsx
  └─ chat response now includes profile_updates field

backend/main.py
  └─ ChatResponse.profile_updates (dict)

backend/orchestrator.py
  ├─ extract_profile_updates() called pre-retrieval in free-form mode
  ├─ session.update_profile_field() applied per extracted field
  ├─ upsert_profile_chunk() re-runs so retrieval sees fresh profile
  └─ TurnResult.profile_updates returned in ChatResponse

backend/profile_extractor.py
  ├─ EXTRACTOR_SYSTEM prompt (enum-strict)
  └─ _validate() type + enum + bounds enforcement
```

## Consequences

**Positive:**

- Profile updates flow naturally from conversation.
- Completeness % auto-ticks up; UI feels responsive.
- Personalized scorecards refresh because the profile chunk in Chroma is fresh.

**Negative:**

- Adds one extractor LLM call per free-form turn (~500 ms latency).
- LLM might over-extract on borderline phrases.

**Mitigations:**

- Conservative validation drops anything outside enum / type / bounds.
- Empty extraction result on uncertain phrasing → no-op (chat reply unaffected).
- Extractor model is the cheap tier (Llama-3.3-70B), not the frontier brain.

## Revisit at scale

- Add a confidence score to the extractor output; only auto-apply at high confidence, flag medium-confidence updates for user confirmation.
- Run the extractor in parallel with retrieval+brain to hide the latency.

## Session isolation for profile RAG (KI-102, `4bb8da0`, 2026-05-15)

The extractor above writes profile updates into `session.profile`. The profile is then embedded as a Chroma chunk via `backend/profile_rag.py::upsert_profile_chunk(profile, session_id)` so the brain sees the user's profile alongside policy chunks during retrieval. Pre-KI-102 the chunk was stored without a `session_id` tag, which meant Chroma's nearest-neighbour retrieval could return User A's profile chunk when User B's session ran retrieval — a cross-session PII leak (age / dependents / health conditions).

**Three-layer fix in `backend/profile_rag.py`:**

1. **Metadata stamp.** `upsert_profile_chunk(profile, session_id)` writes `metadata={"doc_type": "profile", "session_id": session_id, ...}` on every chunk. Sessions written before KI-102 lack `session_id` and are treated as legacy (see layer 3).
2. **Exclude from main retrieval.** The general `retrieve()` pass adds `where={"doc_type": {"$ne": "profile"}}` so profile docs never enter the general policy / regulatory retrieval pool. Profile chunks are read ONLY by the dedicated per-session lookup.
3. **Per-session lookup triple-checks `session_id`.** `_get_profile_chunk_for_session(session_id)` pulls with `where={"$and": [{"doc_type": "profile"}, {"session_id": session_id}]}` AND re-verifies `result.metadata["session_id"] == session_id` in Python after Chroma returns. Defence-in-depth against a Chroma where-clause bug. Legacy chunks (no `session_id` in metadata) fail the Python check and are silently refused (fail-closed, not fail-open).

**Companion: KI-107 `_safe_collection_get`.** The per-session lookup `collection.get(where=...)` raised on never-existed sessions on the HF Space build (Chroma version-dependent behaviour). KI-107 wraps every `.get()` call in `_safe_collection_get` which catches `Exception`, logs a WARNING, and returns `None`. A `None` return is treated identically to "session_id mismatch" — both fail-closed, brain runs without profile context that turn.

**Privacy invariant.** Session B can NEVER read Session A's profile chunk, regardless of embedding similarity, where-clause behaviour, or never-existed-session edge cases.
