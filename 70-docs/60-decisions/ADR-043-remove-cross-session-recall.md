# ADR-043 — Remove cross-session profile recall entirely

**Date:** 2026-05-27
**Status:** Accepted
**Supersedes:** ADR-041 (`session-profile-lifecycle.md`), ADR-042 (`privacy-hardening-and-sticky-retry.md`). The portions of ADR-042 covering sticky-session retry and the admin LLM Chain refresh wiring are KEPT — those concerns are independent of the recall feature and remain live.

## Context

Two-week arc of recall hardening that kept exposing new failure modes:

| Doc / Fix | When | Hardening pass |
|---|---|---|
| ADR-041 (session-profile-lifecycle) | 2026-05-15 | Original two-tier name-slug + persona_id JSON store with a staged-confirm Welcome Back gate |
| KI-196 / Bug #25 | 2026-05-19 | Probe must fire when name lands on turn ≥2, not just turn 1 |
| Bug #26 (state-recovery) | 2026-05-19 | Container restart mid-conversation → rebuild profile from chat_history |
| ADR-042 v1 | 2026-05-27 | Redact stored attrs from the Welcome-Back prompt + prior-turn match-before-merge guard |
| ADR-042 v2 | 2026-05-27 | Same-turn age regex guard (since `_affirm_or_deny` fires before `save_profile_field`) |
| ADR-042 v3 | 2026-05-27 | Extend extractors to dependents / location_tier / income_band |
| ADR-042 v4 | 2026-05-27 | Two-fact recall gate in `rehydrate_by_name` — bare name no longer stages |

Each pass was a real bug class. After v4 the live audit still showed seeded-test failures because the slug pointer was being overwritten between tests, exposing the deeper structural truth: **a name-only key cannot safely distinguish two visitors who share the name**, and the workarounds keep accumulating without converging.

Cost/benefit for an insurance-shopping product:

- **Value of recall:** "the bot remembers you next visit." Insurance shopping is rare-purchase (typical user buys once every several years); return sessions are uncommon.
- **Complexity tax:** ~1500 LOC across `profile_store.py`, `profile_persistence.py`, `profile_rag.py`, ~half of `session_state.py`, the `recall_block` / `restored_block` / `_affirm_or_deny` / `_RECALL_*` constants and the ~70-line prelude in `single_brain.handle_turn`, the `/api/profile/recall-by-name` endpoint + frontend wrapper, `40-data/profiles/<name>.json` × 100+ files on disk, two ADRs, multiple dedicated test files, and audit-time state drift on the live HF Space.
- **Privacy surface:** the name-slug pointer collides across distinct users; every layer of guard (redaction, match-before-merge, two-fact gate) was added because that collision creates a leak vector. A "minimum data retention" posture sidesteps all of it.

The choice that simplifies all of that in one move: **don't carry anything across sessions.**

## Decision

Remove the cross-session profile recall feature entirely. Sessions are in-memory only.

Concretely:

- **Deleted modules:** `backend/profile_store.py`, `backend/profile_persistence.py`, `backend/profile_rag.py`.
- **Deleted endpoint:** `POST /api/profile/recall-by-name`. Old clients pinging the path get 404 — the correct degraded response.
- **Deleted frontend caller:** `postProfileRecallByName` + `RecallByNameResponse` type in `frontend/src/lib/api.ts`.
- **Deleted symbols (formerly in `backend/session_state.py`):** `rehydrate_by_name`, `apply_pending_recall`, `_AGE_HINT_RE`, `_extract_age_from_text`, `_extract_dependents_from_text`, `_extract_location_tier_from_text`, `_extract_income_band_from_text`, `_parse_user_text_facts`, `_LOCATION_TIER_MAP`, `_RECALL_SUMMARY_FIELDS`, the `pending_profile_recall` / `recall_probe_done` / `recall_match_deferred` fields on `SessionState`.
- **Deleted symbols (formerly in `backend/single_brain.py`):** `_affirm_or_deny`, `_RECALL_AFFIRM_TOKENS`, `_RECALL_DENY_TOKENS`, `_RECALL_AFFIRM_PHRASES`, `_RECALL_DENY_PHRASES`, `_RECALL_TOKEN_RE`, the entire `recall_block` + `restored_block` prompt sections, the `pending_recall` / `recall_applied` parameters to `_system_instruction`, and the ~70-line recall prelude in `handle_turn`.
- **Deleted data:** `40-data/profiles/` directory removed from the repo (`git rm -r`) and from the live HF Space's working filesystem on next rebuild.
- **Deleted tests:** `test_bug2526_recall_and_reconstruct.py`, `test_bug45_chat_profile_persistence.py`, `test_profile_rag_isolation.py`, `test_profile_recall_session_isolation.py`, `test_returning_user_recall_singlebrain.py`. `test_session_no_disk_persistence.py` is KEPT and its docstring updated.
- **Converted to in-memory:** the `POST /api/profile/select` + `POST /api/profile/reject` shortlist endpoints in `backend/admin.py` now mutate only `SessionState.profile.selected_policies` / `rejected_policies` — no disk write. `brain_tools.mark_recommendation`'s shown-policy tracking is similarly in-memory only.
- **Converted to live-session view:** `/api/admin/profiles`, `/api/admin/persona-drift`, `/api/admin/recommendation-history` now read from `session_state._sessions` (the in-memory dict of currently live sessions) instead of walking on-disk JSON files.

## What's kept

- **`SessionState.profile`** — still the per-session dataclass, populated by `save_profile_field` and by `POST /api/profile`. Evicted on `_TTL_SECONDS = 60 * 60` idle.
- **State-recovery from chat_history (Bug #26)** — in-session only. If a container restart blanks `_sessions` but the browser is still on the same tab, the brain rebuilds the profile from the chat history the client re-sends. Never reads disk.
- **The sticky-session retry policy from ADR-042** (`_gemini_call(is_sticky=True)` with jittered exp backoffs) — kept as-is; it has nothing to do with recall.
- **The admin LLM Chain refresh wiring from ADR-042** (KI-296) — kept; same independence.

## Consequences

- **Privacy by default.** Closing the tab is a complete forget. No name-keyed cross-session inheritance is possible because no such mechanism exists.
- **Code volume.** Net deletion ≈ 1,500 LOC plus the 100+ profile JSONs.
- **Operator view changes shape.** Admin profile / drift / recommendation-history endpoints now show live in-memory sessions only; historical events evict with the session. If long-term analytics are wanted, the right channel is an append-only anonymised `usage_log.jsonl`, not a profile JSON store.
- **No more "Welcome back, <name>?" UX.** Trade-off accepted — the slug-collision class of bug goes away with it.
- **State recovery still survives container restarts** for users who stay on the same tab. The common operational concern from Bug #26 is unaffected.
- **Frontend changes are minimal.** `postProfileRecallByName` is deleted; no callers existed. The Clear-chat / profile-builder UX paths continue to work unchanged.

## Verification

- Compile: every backend module compiles after the deletion.
- Imports: every backend module imports cleanly under `.venv/bin/python` — no orphan references to removed modules.
- Grep sweep across `backend/`, `frontend/`, and `tests/` for `profile_store`, `profile_persistence`, `profile_rag`, `rehydrate_by_name`, `apply_pending_recall`, `try_recall_by_name`, `recall_by_name_payload`, `extract_potential_name`, `auto_persist_session`, `pending_profile_recall`, `recall_match_deferred`, `recall_probe_done`, `_RECALL_AFFIRM`, `_RECALL_DENY`, `_affirm_or_deny`, `_extract_age_from_text`, `_extract_dependents_from_text`, `_extract_location_tier_from_text`, `_extract_income_band_from_text`, `_parse_user_text_facts`, `_AGE_HINT_RE`, `_LOCATION_TIER_MAP`, `record_policy_event`, `_LazyProfilesDir`, `_PROFILES_DIR_FOR_DRIFT` — all return zero active code references (only the comment lines in this ADR and the prose pointers in CLAUDE.md / README.md remain).
- Live audit (deferred to deploy commit) — confirms no Welcome Back text fires regardless of name, the bot proceeds with normal fact-find, and the admin tabs render an empty/live-only view.

## Open follow-ups

None. The deferred recall-feature follow-ups from ADR-042 are dissolved by the removal — there is nothing to disambiguate.

## How this is told in an interview

- Build of trust → forced explicit privacy choices. The product holds user health and family data; "minimum data retention" is a stronger story than "we keep your profile but redact the prompt."
- Documented the iteration honestly. Five hardening passes on a feature with a high complexity-to-value ratio was the signal to delete, not the signal to ship pass six.
- Showed the operating discipline of cutting your own code. The change is a 1,500-LOC *deletion*. The simpler architecture is easier to explain, easier to audit, and provably free of the collision-class bug.
