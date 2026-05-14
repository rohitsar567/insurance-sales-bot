# ADR-021: View-aware system prompt (frontend copilot context)

**Status:** Locked
**Date:** 2026-05-14

## Context

When a user has a policy detail modal open and asks *"what's the waiting period on this?"*, the bot has no way to resolve "this" — neither the LLM nor the retrieval system knows what's on screen. Users had to re-state the policy name on every turn, which made the bot feel disconnected from the UI.

## Decision

Frontend passes a **`view_context`** payload on every chat request describing what the user is currently looking at:

```typescript
type ViewContext = {
  active_view: "chat" | "marketplace" | "profile" | "premium" | "policy_detail";
  active_policy_id?: string;
  filters?: Record<string, unknown>;
};
```

Backend accepts this in `ChatRequest` (`backend/main.py`), threads it through `handle_turn(view_context=...)` (`backend/orchestrator.py`), and `build_messages()` (`backend/persona.py`) injects a block into the system prompt:

```
USER IS CURRENTLY LOOKING AT:
- active view: policy_detail
- policy open in detail: care-health__care-supreme__wordings
- marketplace filters: {min_sum_insured: 500000}
When the user's question refers to 'this policy', 'this insurer', 'these
filters', or otherwise relies on what's on screen, ground your answer in
the active view above — do not ask the user to re-state it.
```

## Alternatives considered

| Approach | Why rejected |
|---|---|
| Auto-resolve "this" via NLP heuristics in the backend | Brittle; can't know what view is open without frontend telling it. |
| Append "the user is viewing X" to every user message in the frontend | Pollutes chat history with synthetic content; user sees it in their own messages on reload. |
| Tool-calling pattern (LLM asks for view state) | Adds a round trip; not all NIM models support clean tool-calling. |

## Implementation

End-to-end plumbing (commit `271442b`, shipped 2026-05-14):

```
frontend/src/lib/api.ts
  ├─ ViewContext type
  └─ postChat({ view_context })

frontend/src/app/page.tsx
  └─ active_view computed per turn from {openPolicy, showMarketplace, …}

backend/main.py
  └─ ChatRequest.view_context (Optional[dict])

backend/orchestrator.py
  └─ handle_turn(view_context=...) → build_messages(view_context=...)

backend/persona.py
  └─ build_messages injects USER IS CURRENTLY LOOKING AT block
```

## Consequences

**Positive:**

- Bot answers feel grounded in what the user is doing.
- No additional retrieval cost — view_context is a system-prompt addition only.
- Backwards compatible — frontend that doesn't send `view_context` works fine (the system-prompt block only renders when the field is present).

**Negative:**

- Adds ~80 tokens to the system prompt when active.
- view_context only takes effect AFTER fact-find completes — until `session.free_form_session=True`, the orchestrator routes to fact-find and the brain never sees the view_context.

**Mitigations:**

- The token cost is negligible vs. the typical retrieved-context size.
- The fact-find ordering is the right product behavior (don't answer policy questions for a user we know nothing about); the copilot kicks in exactly when the user transitions to free-form.

## Revisit at scale

Same. Extend `ViewContext` with marketplace cursor / scroll position for "the policy I was looking at three rows up" style queries.
