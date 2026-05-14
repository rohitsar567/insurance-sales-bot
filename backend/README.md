# `backend/` — FastAPI orchestrator

FastAPI + Pydantic service that fronts every chat turn. The HTTP entry point is `main.py`; everything else is a typed module the orchestrator composes per turn.

## Entry points

| File | Role |
| --- | --- |
| `main.py` | FastAPI app, all HTTP routes (`/api/chat`, `/api/profile`, `/api/policies`, admin endpoints). Wires CORS, request validation, returns the typed response that the frontend's `openapi-typescript` codegen consumes. |
| `orchestrator.py` | The brain of a turn: `classify_intent`, `pick_brain`, fact-find routing, profile-RAG injection, faithfulness gate dispatch. Pinned by `tests/test_routing_regression.py`. |
| `config.py` | Pydantic-settings — single source of truth for env-vars, model IDs, chunk sizes, chain budgets. |

## Per-turn helpers

| File | Role | Related ADR |
| --- | --- | --- |
| `needs_finder.py` | 9-slot fact-find graph (`GRAPH`) + slot detection from free text. | [ADR-027](../docs/60-decisions/ADR-027-fact-find-llm-paraphraser.md) |
| `question_paraphraser.py` | LLM rewrite of the canonical slot question so each session sounds fresh; verifier rejects off-slot drift. Cached per `(session_id, slot_id)`. | ADR-027 |
| `fact_find_normalizer.py` | LLM-driven free-text → slot-value coercion (e.g. "32 lakh" → `3200000`). Goes through `NimChainLLM`, not a single client (KI-033). | — |
| `profile_extractor.py` | LLM extractor that pulls profile updates out of conversational asides ("by the way, my dad has diabetes"). Chain-pattern, never a hardcoded model. | [ADR-022](../docs/60-decisions/ADR-022-conversational-profile-updates.md) |
| `profile_store.py` | **NEW (KI-040).** Persistent named-profile JSON store under `data/profiles/`. O(1) name-keyed lookup; mirrors into `profile_rag` on every save. | — |
| `profile_rag.py` | Embeds the user's profile as a Chroma chunk so the brain sees it alongside policy chunks for "what's best for me?" turns. | — |
| `session_state.py` | In-memory session map; tracks fact-find progress + chat history per `session_id`. |
| `faithfulness.py` | 4-gate hallucination guard (retrieval floor → citation integrity → regex numeric grounding → LLM judge). Blocks land in `logs/hallucinations.jsonl`. | — |
| `scorecard.py` | Pure-function 6-sub-score scorer over the 62-field extracted JSON. No LLM. | — |
| `translator.py` | Sarvam-M Indic ↔ English translator wrapper. | [ADR-006](../docs/60-decisions/ADR-006-sarvam-first-stack.md) |
| `translation_check.py` | Post-hoc detector for mixed-script replies; flags Hinglish leakage. | — |
| `persona.py` | The consultative-advisor system prompt + view-aware prompt overlays. | [ADR-008](../docs/60-decisions/ADR-008-consultative-advisor-persona.md), [ADR-021](../docs/60-decisions/ADR-021-view-aware-system-prompt.md) |
| `voice_format.py` | Strips markdown / lists / bullet glyphs so TTS sounds natural. | — |
| `premium_calculator.py` | Looks up `data/premiums/illustrative_premiums.json` + applies the documented scaling factors. Never claims a real quote. | [ADR-007](../docs/60-decisions/ADR-007-illustrative-pricing.md) |
| `security.py` | Request rate-limiting, input sanitisation, admin-IP allowlist. | [ADR-023](../docs/60-decisions/ADR-023-admin-panel-ip-gated.md) |
| `admin.py` | Admin-only routes (live LLM-health, usage rollups, hallucination tail). | ADR-023 |
| `llm_health.py` | Lightweight probe that pings each provider and writes `data/llm_health.json` for the admin tab. | — |

## Subdirectory

`providers/` — concrete STT / TTS / LLM / embeddings client implementations. All LLM access goes through `NimChainLLM(chain=...)` from `providers/nvidia_nim_llm.py` — never instantiate a single-provider client directly. See `backend/providers/README.md`.

## Where to read for what

- **System tour:** root `README.md`
- **Stable contracts a new contributor must know:** root `CLAUDE.md`
- **Decisions with alternatives:** `docs/60-decisions/ADR-*.md`
- **Routing invariants:** `tests/test_routing_regression.py`
- **Defect register:** `audit_results/ENTERPRISE_AUDIT.md`
