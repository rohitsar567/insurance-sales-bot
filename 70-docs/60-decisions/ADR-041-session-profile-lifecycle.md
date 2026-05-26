# ADR-041 — Session & profile lifecycle (KI-196)

**Status:** **SUPERSEDED by [ADR-043](ADR-043-remove-cross-session-recall.md) (2026-05-27).** The two-tier persona_id + name-slug pointer store described below was removed entirely. Sessions are now in-memory only. Kept here for historical record.
**Originally accepted:** 2026-05-15
**Owner:** Rohit Saraf
**Related KIs:** KI-020 (legacy clear-chat), KI-040 (silent name-based recall), KI-062 (persona_id keying), KI-077 (named profile recovery), KI-118 (in-memory-only sessions + cross-session name match), KI-167 (LLM-driven sales brain)
**Related ADRs:** [ADR-022](ADR-022-conversational-profile-updates.md), [ADR-039](ADR-039-llm-driven-sales-brain.md), [ADR-043](ADR-043-remove-cross-session-recall.md) (supersedes this ADR)

## Context

The bot grew three identity layers organically:

1. **`session_id`** — UUID issued per browser session; in-memory only since KI-118 (`backend/session_state.py::_sessions`).
2. **`persona_id`** — 12-char SHA1 of name + identity fields; keys the durable JSON at `40-data/profiles/<persona_id>.json` (KI-062).
3. **`name_slug`** — canonical lowercase first-name; the Chroma profile RAG chunk key and the cross-session re-entry key (KI-040 / KI-118).

Three problems compounded:

- **"Clear chat" was visual-only.** The legacy `POST /api/session/reset` had two modes (`drop_profile=false` / `true`) and the button only ever called the soft mode. The visible chat cleared but the server kept the in-memory session, so the bot acted as if mid-conversation on the user's next message.
- **Page reloads silently resumed.** `sessionStorage` survives refresh — correct — but the user got zero feedback that they were "still in" a prior session.
- **Profile recall was silent and aggressive.** KI-118's `rehydrate_by_name` auto-merged any on-disk profile whose name matched the captured name. A user named "Rohit" who happened to share a slug with a prior visitor would silently inherit that stranger's age / dependents / city. Even for the legitimate "same user, new device" case the silent merge confused users into thinking the bot was "remembering them" with no opt-in.
- **Profile completeness badge counted defaults.** The profile-builder modal pre-filled `dependents="self"` (UX nicety); the completeness scorer treated any non-empty slot as "captured", so a brand-new visitor saw "25% DONE" before answering anything.

## Decision

Implement a clean, explicit lifecycle with these semantics:

| Action | `session_id` | Profile JSON on disk | In-session profile state | Chat history |
|---|---|---|---|---|
| Open new tab / browser | NEW UUID | UNCHANGED (persists) | EMPTY | EMPTY |
| Refresh in same tab | SAME (from `sessionStorage`) | UNCHANGED | RESTORED from `_sessions` if still in memory | RESTORED if still in `localStorage` |
| Click "Clear chat" | NEW UUID (rotated server-side) | UNCHANGED | EMPTY | EMPTY |
| User says a name during fact-find that MATCHES an on-disk profile | SAME | UNCHANGED | One-turn pause — bot ASKS before recall | UNCHANGED |
| User explicitly opts into "welcome back" recall | SAME | LOADED into in-session state | UNCHANGED | UNCHANGED |

### Key change — confirmation-gated profile recall

When a fresh session captures a `name` that matches an existing on-disk profile, the orchestrator NO LONGER auto-merges. Instead:

1. The matched profile snapshot + the captured turn data are staged into `session.pending_profile_recall` (a new `Optional[Dict[str, Any]]` field on `SessionState`).
2. The orchestrator overrides `reply_text` for that turn with a deterministic, recognisable welcome-back ask:
   *"Welcome back, Rohit — I have a profile under your name from before: age 29, metro, first buy. Continue from there or start fresh?"*
3. On the NEXT user turn, the confirmation gate at the top of `handle_turn` inspects `session.pending_profile_recall` and the user's reply:
   - Affirm phrases (`yes`, `continue`, `use that`, `from there`, `haan`, …) → run `rehydrate_by_name` → merge stored fields.
   - Negate phrases (`no`, `start fresh`, `new`, `nahi`, …) → drop the pending entry; treat as a new user.
   - Anything else → leave the pending entry staged; the sales_brain's system prompt carries a high-priority directive to ask again.

### Backend surface

- `backend/session_state.py::clear_session(session_id)` — explicit symbol for the new endpoint. Evicts the in-memory entry; never touches `40-data/profiles/`.
- `backend/main.py::POST /api/session/clear` — `{session_id} → {cleared: bool, new_session_id: str}`. Always returns a new UUID so the caller has a guaranteed-fresh id.
- `backend/sales_brain.py::_build_system_prompt(profile, pending_profile_recall=…)` — when `pending_profile_recall` is supplied, the system prompt prepends a "WELCOME-BACK GATE" directive instructing the brain to ask, NOT capture, NOT proceed to the next fact-find slot.
- `backend/main.py::profile_completeness_view` + `profile_update` — `c = _completeness(...)` now masks fields NOT in `Profile.asked` to `None`. Default `dependents="self"` in the form no longer registers as "done"; only fields the user explicitly answered count.

### Frontend surface

- `frontend/src/lib/api.ts::postSessionClear` — typed wrapper for the new endpoint.
- `frontend/src/app/page.tsx::handleClearChat` — always rotates the session_id via `postSessionClear`, adopts the returned `new_session_id`, persists it to `sessionStorage`, and wipes messages + `profileCompleteness` + chat-history `localStorage`.
- `sessionStorage` (not `localStorage`) for the session_id is deliberate: matches the spec — new tab = new UUID, same-tab refresh = same UUID.

## Consequences

**Positive.**
- One-line semantic for every action; no more "did clear-chat work?" ambiguity.
- Recall is opt-in. Privacy posture matches what users assume from a chat UI.
- The 25% phantom-progress badge on first visit is gone — the % matches the fields the user actually answered.
- No data loss path: on-disk profile JSONs are never deleted as a side effect of any frontend action.

**Trade-offs.**
- One extra turn when a returning user gives their name (the welcome-back ask). Worth it — the prior silent merge was a privacy footgun.
- The `Profile.asked` list is now load-bearing for the completeness scorer. It was already maintained by `record_answer` + the sales_brain orchestrator path; the `POST /api/profile` endpoint now also appends to it on every accepted field.

**Carried forward.**
- KI-040 + KI-077 + KI-118 cross-session name-based recovery is preserved — just gated.
- Voice / RAG / recommendation paths are untouched; the gate sits in the fact-find branch only.
- The legacy `POST /api/session/reset` is left in place for backwards compatibility (any external smoke test or admin tool that hits it still works). New frontend code MUST use `/api/session/clear`.

## Rollback

Revert the three backend hunks (orchestrator gate + sales_brain prompt hook + main.py endpoint) and the page.tsx + api.ts hunks. The `Profile.asked`-gated completeness scorer can be reverted independently if the change creates friction for the profile-builder UX.
