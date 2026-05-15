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
| `needs_finder.py` | 9-slot fact-find SCHEMA (`GRAPH`). Post-KI-167 the `prompt_en` / `prompt_hi` strings are dead text — the LLM owns voice + cadence end-to-end via its system prompt. The data structure is retained as the schema source for `sales_brain.py`. | [ADR-039](../70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md), [ADR-040](../70-docs/60-decisions/ADR-040-google-gemini-primary.md) |
| `sales_brain.py` | **KI-167 LLM-driven fact-find driver.** One brain call per turn against `FAST_BRAIN_CHAIN` (Gemini 2.0 Flash primary) using native provider JSON mode (`response_mime_type=application/json` on Gemini, `response_format={"type":"json_object"}` on NIM / OpenRouter). Returns `{reply, captures, slot_driving, complete}`. No `<FF>` trailer, no scripted fallback. | [ADR-039](../70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md), [ADR-040](../70-docs/60-decisions/ADR-040-google-gemini-primary.md) |
| `sales_brain_normalizer.py` | **KI-167 deterministic post-processor.** Pure-rule mapping of the LLM's loose `captures` dict to canonical `{field: validated_value}`: alias resolution (`location` → `location_tier`), enum coercion (`Bangalore` → `metro`), INR parsing, type / bounds validation, KI-094 null-drop. No LLM calls. | [ADR-039](../70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md) |
| ~~`fact_find_brain.py`~~ *(deleted in KI-167)* | Was the ADR-030 one-call brain with `<FF>...</FF>` trailer convention + `_canonical_fallback`. Removed entirely. | superseded by [ADR-039](../70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md) |
| ~~`question_paraphraser.py`~~ *(deleted in KI-070)* | Was an LLM rewrite of canonical slot questions (ADR-027). Superseded first by `fact_find_brain.py`, now by `sales_brain.py`. | superseded by [ADR-039](../70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md) |
| `fact_find_normalizer.py` | LLM-driven free-text → slot-value coercion (e.g. "32 lakh" → `3200000`). Goes through `NimChainLLM`, not a single client (KI-033). | — |
| `profile_extractor.py` | LLM extractor that pulls profile updates out of conversational asides ("by the way, my dad has diabetes"). Chain-pattern, never a hardcoded model. | [ADR-022](../70-docs/60-decisions/ADR-022-conversational-profile-updates.md) |
| `profile_store.py` | **NEW (KI-040).** Persistent named-profile JSON store under `40-data/profiles/`. O(1) name-keyed lookup; mirrors into `profile_rag` on every save. | — |
| `profile_rag.py` | Embeds the user's profile as a Chroma chunk so the brain sees it alongside policy chunks for "what's best for me?" turns. Per-chunk `session_id` metadata + `doc_type=profile` exclusion from main retrieval + Python-side triple-check on per-session lookup (KI-102). All `collection.get(...)` calls wrapped in `_safe_collection_get` so never-existed sessions return `None` instead of raising (KI-107). | [ADR-022](../70-docs/60-decisions/ADR-022-conversational-profile-updates.md) |
| `session_state.py` | In-memory session map; tracks fact-find progress + chat history per `session_id`. |
| `faithfulness.py` | 4-gate hallucination guard (retrieval floor → citation integrity → regex numeric grounding → LLM judge). Blocks land in `logs/hallucinations.jsonl`. | — |
| `scorecard.py` | Pure-function 6-sub-score scorer over the 62-field extracted JSON. No LLM. | — |
| `translator.py` | Sarvam-M Indic ↔ English translator wrapper. | [ADR-006](../70-docs/60-decisions/ADR-006-sarvam-first-stack.md) |
| `translation_check.py` | Post-hoc detector for mixed-script replies; flags Hinglish leakage. | — |
| `persona.py` | The consultative-advisor system prompt + view-aware prompt overlays. | [ADR-008](../70-docs/60-decisions/ADR-008-consultative-advisor-persona.md), [ADR-021](../70-docs/60-decisions/ADR-021-view-aware-system-prompt.md) |
| `voice_format.py` | Strips markdown / lists / bullet glyphs so TTS sounds natural. `tts_preprocess` also kills CoT leakage: `<think>...</think>` blocks, `**Reasoning:**` / `**Thought:**` labels, `[INTERNAL]` blocks, sentence-anchored CoT starters; emergency-fallback acknowledger if the whole reply is CoT-shaped (KI-104). | — |
| `premium_calculator.py` | Looks up `40-data/premiums/illustrative_premiums.json` + applies the documented scaling factors. Never claims a real quote. | [ADR-007](../70-docs/60-decisions/ADR-007-illustrative-pricing.md) |
| `security.py` | Request rate-limiting, input sanitisation. | — |
| `admin.py` | Admin-only routes (live LLM-health, usage rollups, hallucination tail). | ADR-023 |
| `llm_health.py` | Lightweight probe that pings each provider and writes `40-data/llm_health.json` for the admin tab. | — |

## Subdirectory

`providers/` — concrete STT / TTS / LLM / embeddings client implementations:

- `sarvam_stt.py` / `sarvam_tts.py` — Sarvam Saarika v2.5 + Bulbul v2.
- `google_gemini_llm.py` — Google AI Studio (`gemini-2.0-flash`, `gemini-2.5-flash`); Tier 0 primary on Brain Fast + Brain Main ([ADR-040](../70-docs/60-decisions/ADR-040-google-gemini-primary.md)).
- `nvidia_nim_llm.py` — NIM client + `NimChainLLM` dispatcher (the elector + cross-provider BACKUP machinery from KI-080). Tier 1 fallback across all chains; judge primary stays on NIM Mistral Large 3 675B.
- `openrouter_llm.py` — OpenRouter client; Tier 2 cross-provider diversity using OR's `models: [...]` server-side fallback (KI-176).
- `local_embeddings.py` — BGE-small-en-v1.5 sentence-transformers.

All LLM access goes through `NimChainLLM(chain=...)` — never instantiate a single-provider client directly. See `backend/providers/README.md`.

## Where to read for what

- **System tour:** root `README.md`
- **Stable contracts a new contributor must know:** root `CLAUDE.md`
- **Decisions with alternatives:** `70-docs/60-decisions/ADR-*.md`
- **Routing invariants:** `tests/test_routing_regression.py`
- **Defect register:** `80-audit/ENTERPRISE_AUDIT.md`
