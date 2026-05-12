# Tech Stack Rationale

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Date | 2026-05-13 |
| Status | Living document — updated as picks change |
| Companion docs | `02-architecture.md` (architecture detail) · `decisions.md` (per-decision log) |

---

## Purpose

This is the **single, consolidated artifact** explaining every technology, provider, and framework choice in the project. It exists so that a reviewer (or future maintainer, or you in three months) can audit any pick by asking:

> "Why this and not the alternative? What evidence supports the choice? What changes if the constraint changes?"

Every entry has alternatives considered and a reasoning trace. Where decisions are still open, they are flagged with "**OPEN**" and an expected resolution date.

---

## 1. Component map — what the system is made of

The product is split into five layers. Each layer has independent technology choices.

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. FRONTEND        web UI · chat · audio · filter · compare    │
└─────────────────────────────────────────────────────────────────┘
                          ↓ HTTPS
┌─────────────────────────────────────────────────────────────────┐
│ 2. BACKEND API     route handlers · auth · request validation  │
└─────────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. ORCHESTRATION   persona prompt · intent routing · providers │
└─────────────────────────────────────────────────────────────────┘
              ↓                                ↓
┌──────────────────────────┐   ┌──────────────────────────────────┐
│ 4a. STRUCTURED STORE     │   │ 4b. VECTOR STORE                 │
│     filter / compare     │   │     RAG retrieval                │
└──────────────────────────┘   └──────────────────────────────────┘
                          ↑
┌─────────────────────────────────────────────────────────────────┐
│ 5. INGEST PIPELINE      crawl · chunk · embed · extract        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. The picks — one table to find anything

| # | Layer | Component | Pick | Status | Why (one line) | Alternatives considered |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Frontend | Framework | **Next.js 14 (App Router)** | Locked | Production-pattern, full UI flexibility, fast w/ shadcn/ui | Streamlit, Gradio, Chainlit, Reflex |
| 2 | Frontend | Styling | **Tailwind CSS + shadcn/ui** | Locked | Copy-paste components produce beautiful UIs in hours | MUI, Chakra, plain CSS |
| 3 | Frontend | Audio capture | **MediaRecorder API** (browser-native) | Locked | Zero deps, cross-browser, blob→POST flow is simple | streamlit-mic-recorder, WebRTC, manual ScriptProcessor |
| 4 | Frontend | Hosting | **Vercel** (free tier) | Locked | Native Next.js host, GitHub auto-deploy, edge-cached | Netlify, Cloudflare Pages |
| 5 | Backend | Framework | **FastAPI + Pydantic** | Locked | Pydantic matches our 48-field schema; OpenAPI auto-docs; async I/O for parallel provider calls | Flask, Django, Node/Express |
| 6 | Backend | Hosting | **Render** (free 750h/mo) | Locked | GitHub auto-deploy, Python-native, persistent disk for DuckDB + Chroma | Fly.io, Railway, Modal |
| 7 | Backend | API style | **REST** for v1, possible WebSocket if voice latency demands | Locked | Simpler; switch to WS only if measured latency exceeds target | Pure WS, gRPC |
| 8 | Backend | FE↔BE type safety | **`openapi-typescript` codegen** from FastAPI's auto-generated OpenAPI | Locked | Single source of truth; backend route changes propagate automatically | tRPC (Node-only), GraphQL, manual TS types |
| 9 | Orchestration | STT | **Sarvam Saarika v2.5** | Locked | Sarvam-first; their newer Indic ASR model | Whisper-large-v3, Deepgram Nova-2, Sarvam Saaras v3 |
| 10 | Orchestration | TTS | **Sarvam Bulbul** | Locked | Sarvam-first; Indic prosody first-class | ElevenLabs, OpenAI TTS, Coqui local |
| 11 | Orchestration | LLM (primary brain) | **Sarvam-M primary + hybrid fallback router** (Llama-3.3-70B Groq / DeepSeek-V3 OpenRouter) | Locked | Sarvam-M primary (Indic, BFSI, narrative); benchmarked fallback brains for complex multi-policy reasoning where frontier open-source wins on gold Q&A | Sarvam-M only, GPT-4o (no API), Claude (no API), single-brain alternatives |
| 12 | Orchestration | LLM (grader / self-critique) | **Groq Llama-3.3-70B-versatile** | Locked | Different model family from Sarvam — no circular eval; free tier; OpenAI-compatible API; ~500 tok/sec | GPT-4o-mini (rejected — no OpenAI), Claude Haiku (rejected — no Anthropic API), Sarvam-M held-out |
| 13 | Orchestration | Embeddings | **Voyage AI `voyage-3`** (Anthropic's recommended embedding partner) | Locked | Top benchmarks; same ecosystem as Claude; ~$0.12/1M tok; user has key | OpenAI text-embedding-3-small (rejected — no GPT), Sarvam embeddings (pending API), Cohere |
| 14 | Storage | Structured DB | **DuckDB** (single file) | Locked | One file, columnar, no server; deploys via `git clone` | SQLite, Postgres |
| 15 | Storage | Vector DB | **Chroma** (local persisted) | Locked | Embedded, no infra, supports metadata filtering | FAISS, Pinecone, Qdrant, Weaviate |
| 16 | Ingest | PDF parsing | **pdfplumber** | Locked | Clean text + page numbers — critical for citation grammar | PyMuPDF, Unstructured.io |
| 17 | Ingest | Chunking | **Custom 800-token chunks, 120-token overlap, page-aware** | Locked | Standard for technical docs; overlap protects clause boundaries | LangChain text splitter, fixed-size |
| 18 | Ingest | Structured extraction | **Sarvam-M (Llama-3.3-70B / DeepSeek-V3 fallback) with Pydantic structured output + self-critique pass** | Locked | Reliable JSON-mode extraction; self-critique gives per-field confidence; fallback brain handles tables / complex clauses Sarvam-M misses | Pure regex, LangChain extraction, Instructor lib |
| 19 | Pricing | Approach | **Hand-curated illustrative bands** from public PolicyBazaar quotes | Locked | Honest, defensible; "advisor-not-broker" positioning | Live scraping, actuarial model |
| 20 | Cross-cutting | Auth | **None for v1** (single-tenant demo) | Locked | Out of scope per `01-requirements.md` §7 | Auth0, Clerk, NextAuth |
| 21 | Cross-cutting | Observability | **JSONL turn log + cost tracker** | Locked | Lightweight; one log file, queryable post-hoc | Langfuse, Helicone, custom dashboard |
| 22 | Cross-cutting | Secrets management | **`.env` (chmod 600, gitignored) + Render env vars + Vercel env vars** | Locked | Three sources, never committed | HashiCorp Vault, Doppler |

---

## 3. Selection rubric — how we chose

Every pick above followed this five-test rubric. **Picks that fail any of these are documented as accepted tradeoffs, not silent compromises.**

### 3.1 Sarvam-first hypothesis

For every component Sarvam plausibly ships (STT, TTS, LLM, embeddings), the starting hypothesis is *"use Sarvam unless we have empirical reason not to."* This is graded behavior for a Sarvam assignment — silent defaults to OpenAI/Anthropic/ElevenLabs are the most common screen-out signal.

We will **benchmark** before locking final Sarvam vs non-Sarvam picks:

- **STT:** 20-utterance test set across English, Hindi, Hinglish — measure word-error rate
- **TTS:** Subjective quality + latency on 5 sample advisor responses
- **LLM:** 50 gold Q&A pairs run with Sarvam-M and GPT-4o — measure factual accuracy + citation accuracy

Where Sarvam wins → ship Sarvam. Where Sarvam loses → document the gap with numbers and ship the alternative. Either outcome is a strong artifact.

### 3.2 Real benchmark, not vibes

For any pick that could affect downstream accuracy (LLM, embeddings, extraction prompt design), we make the call on **empirical evidence**, not first principles. The gold Q&A harness exists for exactly this — it transforms "I think X is better" into "X scored 92% vs Y's 87% on our test set."

### 3.3 Production-pattern, not science-project

Every component is picked **as if the next step is white-labelling to a BFSI customer.** No "great for prototyping" picks that block productionization. Examples:

- ✅ FastAPI — already production-grade
- ✅ DuckDB — embedded but production-deployed at Motherduck and many fintechs
- ❌ ~~Streamlit~~ — great prototyping, no white-label path → switched to Next.js

### 3.4 Single-file / single-deploy where possible

Each extra service is a deploy risk and a moving part. We chose:

- DuckDB (one file) over Postgres (separate process + cluster)
- Chroma local (embedded) over Pinecone (separate service)
- Render (single backend deploy) over Render-FE + Render-BE + Redis + Postgres

The total infrastructure footprint is **2 deploys** (Vercel + Render) + **2 cloud APIs** (Sarvam + OpenAI). That's it.

### 3.5 Documented alternatives

Every pick has at least 2 alternatives recorded. A reviewer can audit the reasoning. "Why DuckDB?" → table row 14 → SQLite (no columnar, no analytics) and Postgres (overkill, deploy overhead). Three options considered; reasoning explicit.

---

## 4. Cost envelope (24-hour build + demo run)

| Item | Estimated cost |
| --- | --- |
| Sarvam API (STT + TTS + LLM) — build + 100 demo calls | likely free under signup credits |
| Voyage AI embeddings (~5M tokens to embed 75 PDFs) | ~$0.45 (free under $50 signup credit) |
| Claude Haiku 4.5 for grader / self-critique (~200 calls) | ~$0.20 |
| Render (free tier, 750 h/mo) | $0 |
| Vercel (free tier, 100 GB bandwidth) | $0 |
| GitHub (public repo) | $0 |
| **Total realistic spend** | **< $1** |

Production projection (1,000 daily active users, 5 turns each):
- ~150K STT calls/mo + ~150K TTS calls/mo + ~150K LLM calls/mo
- Estimated ~$200-400/mo for Sarvam APIs (highly volume-discount-sensitive)
- Render Standard ($7/mo) + Vercel Pro ($20/mo) for resource limits
- **Production envelope: ~$250-500/mo for the first 1K DAU.** Sub-cent per turn.

---

## 5. Open picks (will be resolved in next 24 hours)

| # | Question | Resolution |
| --- | --- | --- |
| O1 | Does Sarvam expose an embeddings API? If yes, benchmark vs OpenAI text-embedding-3-small | Check `dashboard.sarvam.ai` API listing; <30 min |
| O2 | Saarika v2.5 vs Saaras v3 for STT — depends on whether we need translate/codemix modes | Benchmark both on 20-utterance set; <1h |
| O3 | Recommendation engine shape: rule-based pre-filter + LLM justification vs pure-LLM reasoning | Decide after gold Q&A harness runs; leaning rule-based for testability |
| O4 | Caching layer — should LLM responses be cached for repeated questions? | Yes for demo; simple file cache, no Redis |
| O5 | Should we add basic auth on the deployed URL to avoid abuse? | Probably yes — single shared password for interviewer access |

---

## 6. Decisions log cross-reference

Every pick here corresponds to one or more entries in `decisions.md`:

| Pick | Decision IDs |
| --- | --- |
| Vertical-slice scope | D-001 |
| Health insurance category | D-002 |
| Curated corpus, not user-uploaded | D-003 |
| Hybrid structured + unstructured | D-004 |
| **Frontend stack** (Next.js + FastAPI) | D-005 (revised 2026-05-13) |
| Sarvam-first benchmarking | D-006 |
| Pricing as illustrative band | D-007 |
| Consultative persona | D-008 |
| 10 insurers × all health policies | D-009 |
| Secret handling | D-010 |
| Embeddings provider (pending) | D-011 |
| Render deployment over alternatives | D-012 |
| Next.js + Tailwind + shadcn UI stack | D-013 |
| GPT-4o-mini as grader to avoid circular eval | D-014 |

---

## 7. Revision history

| Version | Date | Change |
| --- | --- | --- |
| 0.1 | 2026-05-13 | Initial draft. Switched D-005 frontend from Streamlit to Next.js + FastAPI; added D-011 through D-014 for new picks. |
