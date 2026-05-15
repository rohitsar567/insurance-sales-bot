# 02 — Architecture

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Document version | 0.2 |
| Date | 2026-05-15 |
| Depends on | `problem-statement.md` |
| Status | Live |

---

## 0. Purpose

This document specifies the technical architecture: the stack, the data flow, the canonical schema, the per-insurer adapter pattern, and the seven commitments that make v1 (vertical slice) cheaply expandable to v2 (full platform). Every decision here is mirrored in `decisions.md` (with alternatives + reasoning).

---

## 1. System diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          USER (browser)                              │
│                  (mic input · speaker output · chat UI)              │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  audio bytes / text / clicks
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│             NEXT.JS 14 UI (frontend/src/app/page.tsx)                │
│  ─ chat surface · Live + push-to-talk toggle · profile builder       │
│  ─ Web Speech (interim) + MediaRecorder (authoritative) hybrid       │
│  ─ in-DOM <audio> for TTS playback (barge-in compatible)             │
└─────────────────────────────────────────────────────────────────────┘
        │                              │                          │
        │ audio blob                   │ user_query (text)        │ filter / select
        ▼                              ▼                          ▼
┌──────────────────┐         ┌─────────────────────┐   ┌──────────────────────┐
│ VOICE            │         │ ORCHESTRATOR        │   │ STRUCTURED STORE     │
│ ─ Sarvam Saarika │ ──────► │ (backend/           │ ◄ │ DuckDB               │
│   STT            │  text   │  orchestrator.py)   │   │ ─ 1 row per policy   │
│ ─ Sarvam Bulbul  │ ◄────── │ ─ classify_intent   │   │ ─ 62-field schema    │
│   TTS            │  audio  │ ─ profile RAG       │   │ ─ filter / compare   │
└──────────────────┘         │ ─ sales_brain /     │   └──────────────────────┘
                             │   QA brain dispatch │              ▲
                             │ ─ faithfulness gates│              │ extracted
                             └──────────┬──────────┘              │ at ingest time
                                        │                          │
                                        ▼                          │
                  ┌──────────────────────────────────┐             │
                  │  LLM CHAINS (3-tier, ADR-040)    │             │
                  │  ┌────────────────────────────┐  │             │
                  │  │ Brain Fast (sales_brain):  │  │             │
                  │  │  Gemini 2.0 Flash (Tier 0) │  │             │
                  │  │  → NIM Qwen/Mistral/Llama4 │  │             │
                  │  │  → OR :free fallback       │  │             │
                  │  │  → NIM Nemotron 49B (last) │  │             │
                  │  ├────────────────────────────┤  │             │
                  │  │ Brain Main (synthesis):    │  │             │
                  │  │  Gemini 2.5 Flash (Tier 0) │  │             │
                  │  │  → NIM Mistral/Maverick/   │  │             │
                  │  │    Qwen                    │  │             │
                  │  │  → OR Nemotron-3-Super     │  │             │
                  │  │  → NIM Nemotron 49B (last) │  │             │
                  │  ├────────────────────────────┤  │             │
                  │  │ Judge (cross-family):      │  │             │
                  │  │  NIM Mistral Large 3 675B  │  │             │
                  │  │  → NIM Llama-4 Maverick    │  │             │
                  │  │  → OR Qwen 80B :free       │  │             │
                  │  │  → NIM Nemotron 49B (last) │  │             │
                  │  └────────────────────────────┘  │             │
                  │  Sticky-primary election (KI-080)│             │
                  │  per chain via                   │             │
                  │  backend/llm_health.py probe loop│             │
                  └──────────────────────────────────┘             │
                                        ▲                          │
                                        │ retrieved chunks         │
                                        │                          │
                             ┌──────────┴──────────┐               │
                             │ VECTOR STORE        │   ┌───────────┴──────────┐
                             │ Chroma              │   │ INGEST PIPELINE      │
                             │ ─ chunk + BGE-small │ ◄ │ (rag/ingest.py)      │
                             │ ─ metadata: policy, │   │ ─ download PDFs      │
                             │   page, clause      │   │ ─ chunk + embed      │
                             │ ─ profile chunks    │   │ ─ LLM extract → DB   │
                             │   (session-scoped)  │   │ ─ per-insurer adapter│
                             └─────────────────────┘   └──────────────────────┘
                                                                    ▲
                                                                    │ raw PDFs
                                                       ┌────────────┴─────────┐
                                                       │ INSURER WEBSITES /   │
                                                       │ IRDAI PRODUCT DB     │
                                                       └──────────────────────┘
```

---

## 2. Stack picks (current)

| Layer | Pick | Why | Decision |
| --- | --- | --- | --- |
| STT | **Sarvam Saarika v2.5** | Best-in-class Indian-accent + Hinglish handling. | [ADR-006](../60-decisions/ADR-006-sarvam-first-stack.md) |
| TTS | **Sarvam Bulbul v2** | First-party Indic prosody; ₹ + lakh shorthand normalised pre-TTS by `voice_format.py`. | [ADR-006](../60-decisions/ADR-006-sarvam-first-stack.md) |
| LLM — Brain Fast (sales_brain) | **Gemini 2.0 Flash** primary → NIM Qwen 80B / Mistral Large 3 675B / Llama-4 Maverick → OR `:free` (Nemotron-3-Super, Qwen 80B) → NIM Nemotron 49B last resort. Native JSON mode via `response_mime_type=application/json` / `response_format={"type":"json_object"}`. | Frontier free-tier quality on Gemini (1500 req/day) with NIM diversity below it. Sticky-primary election (KI-080) picks one candidate per call. | [ADR-040](../60-decisions/ADR-040-google-gemini-primary.md), [ADR-039](../60-decisions/ADR-039-llm-driven-sales-brain.md) |
| LLM — Brain Main (QA / comparison / recommendation) | **Gemini 2.5 Flash** primary → NIM Mistral 675B / Maverick / Qwen 80B → OR Nemotron-3-Super → NIM Nemotron 49B last resort. | Higher synthesis quality than 2.0 on long-context recommendation; shares the Google quota with sales_brain. | [ADR-040](../60-decisions/ADR-040-google-gemini-primary.md) |
| LLM — Judge (faithfulness Gate 4) | **NIM Mistral Large 3 675B** primary → NIM Llama-4 Maverick → OR Qwen 80B `:free` → NIM Nemotron 49B last resort. | Different family from the Gemini brain — preserves brain ↔ judge family-diversity invariant ([ADR-014](../60-decisions/ADR-014-groq-llama-grader.md) lineage). | [ADR-040](../60-decisions/ADR-040-google-gemini-primary.md) |
| Indic translation | **Sarvam-M** | Best-in-class Hinglish ↔ English translation. | [ADR-006](../60-decisions/ADR-006-sarvam-first-stack.md) |
| Embeddings | **BGE-small-en-v1.5 (local CPU, 384-d)** | Free, runs offline, no rate limits. | [ADR-011](../60-decisions/ADR-011-bge-local-embeddings.md) |
| Vector DB | **Chroma** (local persisted) | Embedded, supports metadata filtering, HNSW bloat guard ([ADR-029](../60-decisions/ADR-029-hnsw-bloat-tripwire.md)) | [ADR-004](../60-decisions/ADR-004-hybrid-structured-vector.md) |
| Structured DB | **DuckDB** (single file) | One-file, columnar, no server. | [ADR-004](../60-decisions/ADR-004-hybrid-structured-vector.md) |
| UI | **Next.js 14 (App Router) + Tailwind v4 + shadcn/ui** | Production-pattern, full UI flexibility. | [ADR-005](../60-decisions/ADR-005-nextjs-fastapi-frontend.md), [ADR-013](../60-decisions/ADR-013-tailwind-shadcn-ui.md) |
| Audio capture | **Web Speech API (interim text) + `MediaRecorder` (authoritative blob), hybrid (KI-168)** | Live UX cadence on the UI + Sarvam-grade STT for the actual transcript. | [ADR-028](../60-decisions/ADR-028-voice-ux-single-default-mode.md) |
| Deployment | **HF Spaces (Docker) + companion HF Dataset for corpus / vectors** | Free, GitHub-mirrored, snapshot_download for data hydration. | [ADR-012](../60-decisions/ADR-012-render-then-hf-space-deploy.md), [ADR-020](../60-decisions/ADR-020-code-data-split-hf-dataset.md) |

**Provider keys:** `GOOGLE_API_KEY` (Google AI Studio, 1500 req/day free), `NVIDIA_NIM_API_KEY` (free, 40 req/min), `OPENROUTER_API_KEY` ($10 unlocks 1000 req/day on `:free` models), `SARVAM_API_KEY` (Sarvam STT + TTS + translation).

**Provider abstraction:** every LLM provider lives under `backend/providers/` (`google_gemini_llm.py`, `nvidia_nim_llm.py`, `openrouter_llm.py`) behind a common `LLMProvider` interface. `NimChainLLM` is provider-agnostic — chains may freely mix Google / NIM / OpenRouter candidates by URL. **This is non-negotiable** — it's c-readiness commitment #5.

---

## 3. Canonical data flow (a single user turn)

```
user speaks
    │
    │  Web Speech streams interim transcript to chat input (live UX)
    │  MediaRecorder captures audio blob in parallel
    │
    ▼ (browser silence-detect → audio blob)
POST /api/transcribe  ── Sarvam Saarika v2.5 ──► authoritative text
    │  (fallback to Web Speech transcript if Sarvam errors)
    │
    ▼ (auto-submit)
POST /api/chat  → orchestrator.handle_turn(text, session)
    │
    ├── if intent == fact_find:
    │       sales_brain.drive_sales_turn(profile, history, user_text)
    │           ─ ONE LLM call against FAST_BRAIN_CHAIN (Gemini 2.0 Flash primary)
    │           ─ native JSON mode returns {reply, captures, slot_driving, complete}
    │           ─ sales_brain_normalizer maps loose captures → canonical fields
    │           ─ KI-094 None-guard ensures null captures don't wipe filled fields
    │           ─ KI-171 skips faithfulness judge on fact-find turns
    │
    ├── if intent == comparison: structured_store.query(filters) → rows
    │
    └── if intent ∈ {qa, recommendation}:
            rag.retrieve(query, top_k=10)
                ─ returns chunks with (policy_id, page, clause, text, score)
                ─ profile_rag chunks filtered to current session_id (KI-102)
            BRAIN_CHAIN.chat(persona_prompt + history + chunks + user_query)
                ─ Gemini 2.5 Flash primary → NIM Mistral 675B fallback
                ─ enforces citation grammar [Source: ...]
                ─ refusal returned when retrieval is weak
            faithfulness gates (skipped on recommendation per KI-171):
                Gate 1: retrieval floor    Gate 2: citation integrity
                Gate 3: regex numeric      Gate 4: cross-family judge
                                              (NIM Mistral Large 3 675B)
    │
    ▼
voice.synthesize(text)  ── Sarvam Bulbul v2 ──► base64 audio
    │  (voice_format.tts_preprocess strips CoT leaks + expands ₹ shorthand)
    │
    ▼
frontend in-DOM <audio> element plays the response (barge-in compatible)
log_turn({intent, brain_used, retrieved_ids, latency_ms, faithfulness, refused})
```

---

## 4. Policy schema (extensible to Life / Motor / Travel)

Full Pydantic model lives in `rag/schema.py` (designed in parallel by a sub-agent — see that file for the canonical 40–50 fields).

**Field groupings:**

1. **Identity** — `policy_id`, `insurer_name`, `insurer_slug`, `policy_name`, `policy_type` (enum), `uin_code`
2. **Eligibility** — `min_entry_age`, `max_entry_age`, `max_renewal_age`, `family_composition`
3. **Sum insured & premium** — `sum_insured_options[]`, `premium_payment_modes[]`, `premium_band_illustrative`, `grace_period_days`
4. **Waiting periods** *(comparison-critical)* — `initial_waiting_period_days`, `pre_existing_disease_waiting_months`, `specific_disease_waiting_months`, `maternity_waiting_months`, `specific_diseases_listed[]`
5. **Coverage scope** *(comparison-critical)* — `pre_hospitalization_days`, `post_hospitalization_days`, `day_care_treatments_count`, `domiciliary_treatment`, `ayush_coverage`, `maternity_coverage`, `newborn_coverage`, `organ_donor_expenses`, `ambulance_cover`, `critical_illness_cover`, `restoration_benefit`, `no_claim_bonus_pct`, `preventive_health_checkup`
6. **Sub-limits & caps** *(comparison-critical)* — `room_rent_capping`, `icu_capping`, `copayment_pct`, `disease_wise_sub_limits` (json), `deductible_amount`
7. **Geography & network** — `geographic_coverage_india`, `worldwide_emergency_cover`, `network_hospital_count`, `cashless_treatment_supported`
8. **Exclusions** — `permanent_exclusions[]`, `temporary_exclusions[]`, `notable_exclusions_summary`
9. **Claim & service** — `claim_settlement_ratio`, `claim_process_summary`, `tat_cashless_authorization_hours`
10. **Riders** — `available_riders[]`, `top_rider_examples`, `rider_premium_indicative`
11. **Source metadata** — `source_pdf_path`, `source_pdf_url`, `last_updated_date`, `extraction_confidence_pct`

**v2 expansion:** add category-specific optional field groups (`life_*`, `motor_*`, `travel_*`). Existing fields are never removed. Adding a category = schema additive change, no migration.

---

## 5. Extraction pipeline

```
for pdf in corpus:
    text_chunks = chunk(pdf, target_tokens=800, overlap=120)
    # chunks indexed in Chroma with metadata: policy_id, page, clause_path

    # one-shot LLM extraction with the schema as a structured-output target
    extracted = NimChainLLM(BRAIN_CHAIN).extract_structured(
        full_pdf_text,
        schema=HealthPolicy,
        few_shot_examples=[hdfc_ergo_example, niva_bupa_example]
    )

    # self-critique pass: judge LLM checks every field's evidence in the source
    confidence_per_field = NimChainLLM(JUDGE_CHAIN).self_critique(extracted, full_pdf_text)
    extracted.extraction_confidence_pct = aggregate(confidence_per_field)

    # write to DuckDB
    structured_store.upsert(extracted)
```

**Quality bar:** any field below 80% self-rated confidence is **stored with a `_low_confidence` flag** and excluded from comparison surfaces (but kept queryable). This protects the comparison view from looking authoritative when it isn't.

**Eval (see Doc 03):** manually spot-check 5 policies × all fields. If extraction accuracy < 90% on critical fields (waiting periods, sub-limits, room rent), iterate the extraction prompt.

---

## 6. Per-insurer adapter pattern

Each insurer's website has a unique structure. We isolate per-insurer logic in `rag/adapters/<insurer_slug>.py`:

```python
# rag/adapters/base.py
class InsurerAdapter(ABC):
    insurer_slug: str
    insurer_name: str
    
    @abstractmethod
    def discover_policy_urls(self) -> list[PolicyURL]:
        """Crawl/scrape insurer site to find all health-policy PDF URLs."""
    
    @abstractmethod
    def normalize_pdf(self, pdf_path: Path) -> Path:
        """Insurer-specific PDF normalization (e.g. handle watermarks, OCR fallback)."""
```

Adding an 11th insurer = one new adapter file. **c-readiness commitment #1.**

For v1, we accept that discover_policy_urls() is hand-curated (from sub-agent corpus list) — v2 makes it agentic.

---

## 7. The seven c-readiness commitments (from prior brainstorm)

These are non-negotiable architectural invariants:

| # | Commitment | Where enforced |
| --- | --- | --- |
| 1 | Insurer-agnostic crawler — per-insurer adapter modules | `rag/adapters/` |
| 2 | Category-agnostic structured schema — Life/Motor/Travel fields nullable today | `rag/schema.py` |
| 3 | Pluggable extraction pipeline — prompt-and-schema-driven, not hand-coded per insurer | `rag/extract.py` |
| 4 | Data-driven filter UI — sliders/checkboxes rendered from schema | `app/ui_filter.py` |
| 5 | Provider-agnostic STT/TTS/LLM/embeddings — thin interface, swap via config | `app/providers/` |
| 6 | Eval harness scales linearly — same script on 5 policies works on 500 | `eval/run.py` |
| 7 | Stateless services, single source of truth in DB — no Streamlit-session shortcuts | All `app/` modules |

Every PR / commit should be self-audited against these.

---

## 8. Observability (minimum-viable)

Every turn writes one JSON line to `logs/turns.jsonl`:

```json
{
  "ts": "2026-05-13T14:23:11Z",
  "session_id": "...",
  "turn_id": "...",
  "user_text": "what's the waiting period for cataract?",
  "stt_confidence": 0.94,
  "intent": "policy_qa",
  "retrieved_chunks": ["star-comprehensive:p18:c4.2.7", "..."],
  "llm_latency_ms": 1340,
  "tts_latency_ms": 720,
  "total_latency_ms": 3210,
  "refused": false,
  "refusal_reason": null,
  "cost_estimate_inr": 1.4
}
```

**Why this matters:** the Sarvam interviewer can replay any session, see what the bot retrieved, see what it cost, see where it refused. This is the difference between a demo and a service.

---

## 9. Repo layout

```
insurance-sales-bot/
├── backend/
│   ├── main.py                 # FastAPI app; HTTP routes (/api/chat, /api/transcribe, /api/profile, admin)
│   ├── orchestrator.py         # turn handler, persona prompt, intent routing
│   ├── sales_brain.py          # LLM-driven fact-find (one call/turn, native JSON mode)
│   ├── sales_brain_normalizer.py  # deterministic post-processor over LLM captures
│   ├── needs_finder.py         # 9-slot fact-find SCHEMA (data only; prompts retired)
│   ├── profile_extractor.py    # conversational profile-update extractor
│   ├── profile_store.py        # persistent named-profile JSON store (KI-040)
│   ├── profile_rag.py          # session-scoped profile chunk in Chroma (KI-102)
│   ├── faithfulness.py         # 4-gate hallucination guard
│   ├── persona.py              # consultative-advisor system prompt
│   ├── voice_format.py         # TTS pre-processing (CoT strip, ₹ shorthand)
│   ├── translator.py           # Sarvam-M Indic ↔ English translation
│   ├── llm_health.py           # background probe + sticky-primary election
│   ├── admin.py                # /api/admin/* (LLM health, performance, profiles)
│   └── providers/
│       ├── sarvam_stt.py       # Sarvam Saarika v2.5 client
│       ├── sarvam_tts.py       # Sarvam Bulbul v2 client
│       ├── google_gemini_llm.py  # Google AI Studio (Tier 0 primary)
│       ├── nvidia_nim_llm.py   # NIM client + NimChainLLM dispatcher
│       ├── openrouter_llm.py   # OpenRouter client (cross-provider diversity)
│       └── local_embeddings.py # BGE-small-en-v1.5 sentence-transformers
├── frontend/
│   ├── src/app/page.tsx        # chat surface + voice toolbar + tab switcher
│   ├── src/lib/api.ts          # typed API client (openapi-typescript codegen)
│   ├── src/lib/useStreamingVoice.ts  # hybrid Web Speech + MediaRecorder hook (KI-168)
│   └── src/lib/useLiveConversation.ts  # Live-mode VAD hook
├── rag/
│   ├── corpus/                 # raw PDFs (hydrated from HF dataset at Docker build)
│   ├── extracted/              # per-policy JSON (62-field HealthPolicy)
│   ├── vectors/                # Chroma persistence (HF dataset side)
│   ├── policies.duckdb         # DuckDB rollup
│   ├── schema.py               # Pydantic HealthPolicy model
│   ├── ingest.py               # download → chunk → embed → extract → store
│   ├── retrieve.py             # query → top-k chunks
│   └── extract.py              # LLM-driven structured extraction
├── 40-data/
│   ├── policy_facts/           # curated {value, source_quote} JSONs (256 variants)
│   ├── premiums/illustrative_premiums.json
│   ├── reviews/                # per-insurer IRDAI + aggregator + Reddit data
│   ├── profiles/               # persistent named profiles (KI-040)
│   ├── llm_health.json         # last probe snapshot
│   └── llm_usage.jsonl         # per-call telemetry
├── eval/
│   ├── gold_qa.json            # 96+ gold Q&A pairs
│   ├── run.py                  # in-process orchestrator runner + regex + judge
│   └── results.md / results.json
├── 70-docs/
│   ├── 00-overview/            # roadmap + problem-statement
│   ├── 10-architecture/        # this doc + stack-rationale + safety + scoring
│   ├── 20-data-pipeline/       # ingestion-policy + info-source-map
│   ├── 30-engineering/         # discovery-script + needs-analysis-flow
│   ├── 40-evaluation/          # eval-methodology + known-issues + quality sprints
│   └── 60-decisions/           # ADR-001 … ADR-040
├── 80-audit/
│   ├── ENTERPRISE_AUDIT.md     # live defect register
│   └── <run_id>/               # 100-persona audit transcripts
├── tools/                      # operational scripts (corpus / KB / probes / cron)
├── tests/
│   ├── test_routing_regression.py
│   └── test_persona_*.py
├── Dockerfile                  # HF Space image (Python + snapshot_download)
├── requirements.txt
├── .env / .env.example
├── .gitignore
└── README.md
```

---

## 10. Deployment

- **Code repo:** `github.com/rohitsar567/insurance-sales-bot` (public; mirrored to HF Space `huggingface.co/spaces/rohitsar567/InsuranceBot`).
- **Data repo:** `huggingface.co/datasets/rohitsar567/insurance-bot-data` — 539 files / ~498 MB (corpus PDFs + Chroma vectors + extracted JSONs). See [ADR-020](../60-decisions/ADR-020-code-data-split-hf-dataset.md).
- **Hosting:** HF Space Docker image. `Dockerfile` runs `huggingface_hub.snapshot_download` at build time to hydrate `rag/` from the data repo so the Space repo stays code-only (~3 MB).
- **Secrets (HF Space repository secrets):** `GOOGLE_API_KEY`, `NVIDIA_NIM_API_KEY`, `OPENROUTER_API_KEY`, `SARVAM_API_KEY`, admin password / IP allowlist. Mirrored locally in `.env` (chmod 600, gitignored).
- **Per-deploy verification:** `tests/live_verify.py` runs a small smoke against the live Space URL post-deploy.
- **Triple-mirror invariant ([ADR-024](../60-decisions/ADR-024-triple-mirror-code-and-data.md)):** every commit pushed to BOTH GitHub origin AND the HF Space remote; data side mirrors via `tools/upload_*_to_dataset.py`.

---

## 11. Open decisions deferred to Doc 03 / Doc 04 / Doc 05

| # | Decision | Owner |
| --- | --- | --- |
| A | Final embedding provider after benchmark | `decisions.md` D-011 (pending Sarvam embeddings API check) |
| B | Refusal taxonomy — exact categories | `04-failure-modes.md` |
| C | Fact-find question graph — nodes, edges, termination | `05-needs-analysis-flow.md` |
| D | Gold Q&A construction approach | `03-eval-plan.md` |
| E | Recommendation engine — rule-based pre-filter vs. pure-LLM | Likely D-012, pending eval results |

---

## 12. Revision history

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-05-13 | Initial draft |
