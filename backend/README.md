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
| `needs_finder.py` | 9-slot fact-find schema (`GRAPH`) + canonical question prompts. Post-KI-070 used as **safeguard fallback only** — primary path is `fact_find_brain.py::drive_fact_find` (single LLM call per turn). | [ADR-030](../70-docs/60-decisions/ADR-030-llm-driven-fact-find.md) |
| `fact_find_brain.py` | KI-070 single-LLM-call fact-find driver. One brain call per turn produces natural prose + `<FF>{...}</FF>` JSON tail (captured slots + next slot + complete flag). KI-090 made the tail parser lenient (strict / fenced / bare-JSON-tail). | [ADR-030](../70-docs/60-decisions/ADR-030-llm-driven-fact-find.md), [ADR-032](../70-docs/60-decisions/ADR-032-llm-chain-architecture.md) |
| ~~`question_paraphraser.py`~~ *(deleted in KI-070)* | Was an LLM rewrite of canonical slot questions (ADR-027). Superseded by `fact_find_brain.py`'s single-call architecture. | superseded by [ADR-030](../70-docs/60-decisions/ADR-030-llm-driven-fact-find.md) |
| `fact_find_normalizer.py` | LLM-driven free-text → slot-value coercion (e.g. "32 lakh" → `3200000`). Goes through `NimChainLLM`, not a single client (KI-033). | — |
| `profile_extractor.py` | LLM extractor that pulls profile updates out of conversational asides ("by the way, my dad has diabetes"). Chain-pattern, never a hardcoded model. | [ADR-022](../70-docs/60-decisions/ADR-022-conversational-profile-updates.md) |
| `profile_store.py` | **NEW (KI-040).** Persistent named-profile JSON store under `40-data/profiles/`. O(1) name-keyed lookup; mirrors into `profile_rag` on every save. | — |
| `profile_rag.py` | Embeds the user's profile as a Chroma chunk so the brain sees it alongside policy chunks for "what's best for me?" turns. | — |
| `session_state.py` | In-memory session map; tracks fact-find progress + chat history per `session_id`. |
| `faithfulness.py` | 4-gate hallucination guard (retrieval floor → citation integrity → regex numeric grounding → LLM judge). Blocks land in `logs/hallucinations.jsonl`. | — |
| `scorecard.py` | Pure-function 6-sub-score scorer over the 62-field extracted JSON. No LLM. | — |
| `translator.py` | Sarvam-M Indic ↔ English translator wrapper. | [ADR-006](../70-docs/60-decisions/ADR-006-sarvam-first-stack.md) |
| `translation_check.py` | Post-hoc detector for mixed-script replies; flags Hinglish leakage. | — |
| `persona.py` | The consultative-advisor system prompt + view-aware prompt overlays. | [ADR-008](../70-docs/60-decisions/ADR-008-consultative-advisor-persona.md), [ADR-021](../70-docs/60-decisions/ADR-021-view-aware-system-prompt.md) |
| `voice_format.py` | Strips markdown / lists / bullet glyphs so TTS sounds natural. | — |
| `premium_calculator.py` | Looks up `40-data/premiums/illustrative_premiums.json` + applies the documented scaling factors. Never claims a real quote. | [ADR-007](../70-docs/60-decisions/ADR-007-illustrative-pricing.md) |
| `security.py` | Request rate-limiting, input sanitisation. | — |
| `admin.py` | Admin-only routes (live LLM-health, usage rollups, hallucination tail). | ADR-023 |
| `llm_health.py` | Lightweight probe that pings each provider and writes `40-data/llm_health.json` for the admin tab. | — |

## Subdirectory

`providers/` — concrete STT / TTS / LLM / embeddings client implementations. All LLM access goes through `NimChainLLM(chain=...)` from `providers/nvidia_nim_llm.py` — never instantiate a single-provider client directly. See `backend/providers/README.md`.

## Where to read for what

- **System tour:** root `README.md`
- **Stable contracts a new contributor must know:** root `CLAUDE.md`
- **Decisions with alternatives:** `70-docs/60-decisions/ADR-*.md`
- **Routing invariants:** `tests/test_routing_regression.py`
- **Defect register:** `80-audit/ENTERPRISE_AUDIT.md`
