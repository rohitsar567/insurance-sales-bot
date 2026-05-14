# 02 — Architecture

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Document version | 0.1 (draft) |
| Date | 2026-05-13 |
| Depends on | `01-requirements.md` |
| Status | In review |

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
│                      STREAMLIT UI (app/main.py)                      │
│   ─ chat pane · audio recorder · filter sidebar · comparison view    │
│   ─ session state · @st.cache_resource for heavy clients             │
└─────────────────────────────────────────────────────────────────────┘
        │                              │                          │
        │ audio bytes                  │ user_query (text)        │ filter / select
        ▼                              ▼                          ▼
┌──────────────────┐         ┌─────────────────────┐   ┌──────────────────────┐
│ VOICE (app/voice)│         │ ORCHESTRATOR        │   │ STRUCTURED STORE     │
│ ─ Sarvam Saaras  │ ──────► │ (app/orchestrator)  │ ◄ │ DuckDB               │
│   (STT)          │  text   │ ─ persona prompt    │   │ ─ 1 row per policy   │
│ ─ Sarvam Bulbul  │ ◄────── │ ─ fact-find graph   │   │ ─ 40-50 fields       │
│   (TTS)          │  audio  │ ─ tool routing      │   │ ─ filter / compare   │
└──────────────────┘         │ ─ citation grammar  │   └──────────────────────┘
                             │                     │              ▲
                             │                     │              │ extracted
                             └──────────┬──────────┘              │ at ingest time
                                        │                          │
                                        ▼                          │
                             ┌─────────────────────┐               │
                             │  Sarvam-M (LLM)     │               │
                             └─────────────────────┘               │
                                        ▲                          │
                                        │ retrieved chunks         │
                                        │                          │
                             ┌──────────┴──────────┐               │
                             │ VECTOR STORE        │   ┌───────────┴──────────┐
                             │ Chroma              │   │ INGEST PIPELINE      │
                             │ ─ chunk + embedding │ ◄ │ (rag/ingest.py)      │
                             │ ─ metadata: policy, │   │ ─ download PDFs      │
                             │   page, clause      │   │ ─ chunk + embed      │
                             └─────────────────────┘   │ ─ LLM extract → DB   │
                                                       │ ─ per-insurer adapter│
                                                       └──────────────────────┘
                                                                    ▲
                                                                    │ raw PDFs
                                                       ┌────────────┴─────────┐
                                                       │ INSURER WEBSITES /   │
                                                       │ IRDAI PRODUCT DB     │
                                                       └──────────────────────┘
```

---

## 2. Stack picks (v1)

| Layer | Pick | Why | Alternative considered | Decision in `decisions.md` |
| --- | --- | --- | --- | --- |
| STT | **Sarvam Saaras** | Sarvam-first per assignment context; strong Indic/Hinglish handling | Whisper-large-v3, Deepgram Nova | D-006 |
| TTS | **Sarvam Bulbul** | First-party Indic prosody; Sarvam-first | ElevenLabs, OpenAI TTS | D-006 |
| LLM | **Sarvam-M** (or current Sarvam flagship) | Sarvam-first; Indic-tuned reasoning | GPT-4o, Claude Sonnet | D-006 |
| Embeddings | **Sarvam embedding API if available, else `text-embedding-3-small`** | Will benchmark; whichever scores higher on retrieval @5 against our gold set | OpenAI embeddings, BGE-large local | D-006 |
| Vector DB | **Chroma** (local, persisted to disk) | Zero-config, embedded, works on Streamlit Cloud | FAISS (no metadata filtering), Qdrant (overkill) | D-004 |
| Structured DB | **DuckDB** (single file, embedded) | One-file, columnar, no server, deployable to Streamlit Cloud | SQLite, Postgres | D-004 |
| UI | **Streamlit** | Fastest v1; business logic decoupled so v2 swaps UI only | FastAPI + React (v2) | D-005 |
| Audio capture | **`streamlit-mic-recorder`** or `streamlit-webrtc` (whichever proves stable in <1h spike) | Required for browser-side mic | Native HTML5 + WebSocket (too much glue) | — |
| Deployment | **Streamlit Community Cloud** | Free, GitHub-connected, supports Streamlit out of box, secrets UI for API keys | Render, Fly.io | — |

**Provider abstraction:** every provider is behind a thin interface (`app/providers/stt.py`, `app/providers/tts.py`, `app/providers/llm.py`) so a benchmark swap is a config flag. **This is non-negotiable** — it's c-readiness commitment #5.

---

## 3. Canonical data flow (a single user turn)

```
user speaks
    │
    ▼ (audio bytes)
voice.transcribe()  ── Sarvam Saaras ──► text
    │
    ▼
orchestrator.handle_turn(text, session)
    │
    ├── if fact-find phase: needs_finder.next_question(profile) → returns next Q
    │
    ├── if filter/compare intent detected: structured_store.query(filters) → rows
    │
    └── if Q&A intent: 
            rag.retrieve(query, top_k=5)
                ─ returns chunks with (policy_id, page, clause, text, score)
            llm.generate(persona_prompt + chat_history + retrieved_chunks + user_query)
                ─ enforces citation grammar [Source: ...]
                ─ enforces refusal when retrieval is weak
                ─ returns text response
    │
    ▼
voice.synthesize(text)  ── Sarvam Bulbul ──► audio bytes
    │
    ▼
ui.play_audio(audio_bytes)
ui.append_to_chat(text + citations)
log_turn({stt_confidence, retrieved_ids, latency_ms, cost_usd, refused})
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
    extracted = llm.extract_structured(
        full_pdf_text,
        schema=HealthPolicy,
        few_shot_examples=[hdfc_ergo_example, niva_bupa_example]
    )
    
    # self-critique pass: LLM checks every field's evidence in the source
    confidence_per_field = llm.self_critique(extracted, full_pdf_text)
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
├── app/
│   ├── __init__.py
│   ├── main.py                 # Streamlit entrypoint
│   ├── orchestrator.py         # turn handler, persona prompt, intent routing
│   ├── needs_finder.py         # adaptive fact-find question graph
│   ├── ui_chat.py
│   ├── ui_filter.py            # schema-driven filter UI
│   ├── ui_compare.py           # side-by-side comparison
│   └── providers/
│       ├── stt.py              # Sarvam Saaras client behind interface
│       ├── tts.py              # Sarvam Bulbul client behind interface
│       ├── llm.py              # Sarvam-M client behind interface
│       └── embeddings.py
├── rag/
│   ├── corpus/                 # raw PDFs (committed for reproducibility)
│   ├── extracted/              # per-policy JSON (committed)
│   ├── vectors/                # Chroma persistence (gitignored — rebuilt)
│   ├── policies.duckdb         # structured store (committed)
│   ├── schema.py               # Pydantic HealthPolicy model
│   ├── ingest.py               # download → chunk → embed → extract → store
│   ├── retrieve.py             # query → top-k chunks
│   ├── extract.py              # LLM-driven structured extraction
│   └── adapters/
│       ├── base.py
│       ├── star_health.py
│       ├── hdfc_ergo.py
│       └── ... (10 adapters)
├── eval/
│   ├── gold_qa.json            # 50–100 gold Q&A pairs (10/policy for top policies)
│   ├── grader.py               # automated grader (LLM judge + regex)
│   ├── run.py                  # batch runner
│   └── results.md              # last run's accuracy table
├── data/
│   ├── insurers.json           # 10 insurers metadata
│   └── corpus_urls.md          # output of corpus-discovery sub-agent
├── docs/
│   ├── 01-requirements.md
│   ├── 02-architecture.md      # THIS FILE
│   ├── 03-eval-plan.md
│   ├── 04-failure-modes.md
│   ├── 05-needs-analysis-flow.md
│   ├── decisions.md
│   └── ROADMAP.md
├── tests/
│   └── test_smoke.py
├── streamlit_app.py            # Streamlit Cloud entrypoint (thin wrapper)
├── requirements.txt
├── .env / .env.example
├── .gitignore
└── README.md
```

---

## 10. Deployment

- **Repo:** `github.com/rohitsar567/insurance-sales-bot` (public)
- **Hosting:** Streamlit Community Cloud — `share.streamlit.io` connects to the repo, deploys `streamlit_app.py` on push
- **Secrets:** Sarvam API key set via Streamlit Cloud's secrets manager (mirrors `.env`)
- **Resources:** ~1 GB ephemeral, persistent disk for `rag/` (corpus + DuckDB committed to repo so deploy is reproducible from `git clone`)
- **Vector store rebuild:** on first deploy, app detects empty `rag/vectors/` and rebuilds from `rag/corpus/` (one-time ~5 min cold start; subsequent starts cached)

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
