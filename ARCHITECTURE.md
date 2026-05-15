# Insurance Sales Portfolio Expert — Architecture & Design

A condensed, current architecture map of the Insurance Sales AI Agent. For
authoritative system-level detail see [`70-docs/10-architecture/system-overview.md`](70-docs/10-architecture/system-overview.md);
for routing + chain mechanics see
[`70-docs/60-decisions/ADR-040-google-gemini-primary.md`](70-docs/60-decisions/ADR-040-google-gemini-primary.md).

---

## System architecture

### 1. Frontend
- **Technology:** Next.js 14 (App Router) + Tailwind v4 + shadcn/ui — deployed
  to the HF Space static surface.
- **Voice capture (post-KI-168 hybrid):** Web Speech API streams interim
  transcripts into the chat input for live UX feel, while `MediaRecorder` runs
  in parallel and captures the authoritative audio blob. On browser
  silence-detect the blob is POSTed to `/api/transcribe` (Sarvam Saarika STT,
  the authoritative transcript); auto-submit then falls back to the Web Speech
  transcript only if Sarvam errors. KI-173 heartbeat + KI-174
  `visibilitychange` / `focus` revival keep the mic alive across tab and app
  switches.

### 2. Backend (FastAPI orchestrator)
- **Technology:** FastAPI + Pydantic. Single per-turn entry `POST /api/chat`
  fans out through `backend/orchestrator.py`.
- **Sales brain (`backend/sales_brain.py`, KI-167):** one LLM call per
  fact-find turn against `FAST_BRAIN_CHAIN`. Uses native provider JSON mode
  (`response_mime_type=application/json` on Gemini,
  `response_format={"type":"json_object"}` on NIM / OpenRouter) — no `<FF>`
  trailer convention, no scripted canonical fallback. See
  [ADR-039](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md).
- **Faithfulness:** 4-gate guard in `backend/faithfulness.py` (retrieval floor
  → citation integrity → regex numeric grounding → cross-family judge).
  Skipped on `fact_find` + `recommendation` intents per KI-171.

### 3. LLM chains (3-tier, post-ADR-040)
| Role | Tier 0 (primary) | Tier 1 (NIM fallback) | Tier 2 (OR diversity) | Last resort |
|---|---|---|---|---|
| Brain Fast (sales_brain) | Gemini 2.0 Flash | Qwen 3-Next 80B → Mistral Large 3 675B → Llama-4 Maverick | OR Nemotron-3-Super 120B → OR Qwen 80B `:free` | NIM Nemotron 49B |
| Brain Main (synthesis) | Gemini 2.5 Flash | Mistral Large 3 675B → Llama-4 Maverick → Qwen 80B | OR Nemotron-3-Super 120B | NIM Nemotron 49B |
| Judge (faithfulness Gate 4) | NIM Mistral Large 3 675B | Llama-4 Maverick | OR Qwen 80B `:free` | NIM Nemotron 49B |

Provider keys: `GOOGLE_API_KEY` (1500 req/day free), `NVIDIA_NIM_API_KEY`
(free, 40 req/min), `OPENROUTER_API_KEY` ($10 unlocks 1000 req/day on `:free`
models). Brain ↔ judge family-diversity invariant is preserved — Gemini-brain
calls are graded by Mistral.

### 4. Retrieval & data
- **Vector store:** Chroma persistent client at `rag/vectors/` (BGE-small-en-v1.5
  local 384-d embeddings — see [ADR-011](70-docs/60-decisions/ADR-011-bge-local-embeddings.md)).
- **Structured store:** DuckDB rollup of 62-field `HealthPolicy` extractions
  (`rag/policies.duckdb`); the source-of-truth JSONs live in
  `rag/extracted/<policy_id>.json` and curated facts in `40-data/policy_facts/`.
- **Profile RAG:** session profile is embedded as a Chroma chunk with
  `session_id` metadata; KI-102 enforces strict session isolation on lookup.

### 5. Voice pipeline
- **STT:** Sarvam Saarika v2.5 (`/api/transcribe`).
- **TTS:** Sarvam Bulbul v2 — bot replies normalised by
  `backend/voice_format.py::tts_preprocess` (markdown stripped, CoT leakage
  killed per KI-104, money shorthand expanded per KI-066).

---

## How a single turn flows

1. **Voice input** — browser captures audio with `MediaRecorder` while Web
   Speech provides interim text for the UI.
2. **Transcription** — silence-detect POSTs the blob to `/api/transcribe`
   (Sarvam Saarika STT); Web Speech transcript is the fallback.
3. **Chat call** — `POST /api/chat` runs `classify_intent`,
   profile-RAG injection, and dispatches to `sales_brain` (fact-find) or to
   retrieval + Brain Main (QA / comparison / recommendation).
4. **Brain call** — sticky-primary election (KI-080) picks one candidate from
   the relevant chain; native JSON mode guarantees parseable output.
5. **Faithfulness gates** — retrieval floor + citation integrity + regex
   numeric grounding + (cross-family) judge LLM. Blocked replies land in
   `logs/hallucinations.jsonl`.
6. **TTS synthesis** — Sarvam Bulbul returns base64 audio in the same JSON
   response; frontend plays it through the in-DOM `<audio>` element so
   barge-in's `querySelectorAll("audio").pause()` actually finds it.
7. **Telemetry** — every turn writes a JSONL record (intent, brain_used,
   latency, faithfulness verdict, cost estimate) for replay + audit.

---

## Where to read next

- [`70-docs/10-architecture/system-overview.md`](70-docs/10-architecture/system-overview.md) — full system map with the canonical data flow.
- [`70-docs/10-architecture/stack-rationale.md`](70-docs/10-architecture/stack-rationale.md) — every technology pick with alternatives + reasoning.
- [`70-docs/60-decisions/ADR-040-google-gemini-primary.md`](70-docs/60-decisions/ADR-040-google-gemini-primary.md) — the current chain shape.
- [`70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md`](70-docs/60-decisions/ADR-039-llm-driven-sales-brain.md) — fact-find LLM-driven sales brain.
- [`backend/README.md`](backend/README.md) — per-module entry points.
- [`80-audit/ENTERPRISE_AUDIT.md`](80-audit/ENTERPRISE_AUDIT.md) — live defect register + production-readiness scorecard.
