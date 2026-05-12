# Decisions Log

Every meaningful technical and product decision, with alternatives considered and the reasoning for the chosen path. Append-only. Each entry is auditable.

---

## D-001 — Vertical slice scope, not full platform

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:**
- (a) Single-document RAG-voice bot for one policy
- (b) Vertical slice — full architecture for one category (Health), built for category expansion
- (c) Full platform — 300 policies across all categories
**Chose:** (b)
**Reasoning:** With <24h to ship and an explainability-graded assignment, (a) under-signals product vision, (c) over-scopes and ships rough. (b) demonstrates senior-engineer scoping discipline while showing the full architectural surface a reviewer cares about.
**Revisit at scale:** All seven "c-readiness commitments" (see Doc 02) become real work in v2.

---

## D-002 — Category for vertical slice: Health

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Health, Life, Motor
**Chose:** Health
**Reasoning:** Richest structured-attribute surface (waiting periods, PED, sub-limits, network, claim ratio); broadest user relevance; cleanest public corpus from top 10 insurers (Star, HDFC ERGO, Niva Bupa, Care, ICICI Lombard, Bajaj Allianz, New India, Aditya Birla, Tata AIG, ManipalCigna).
**Revisit at scale:** v2 adds Life (already harder — emotional, harder numeric compare) and Motor (price commodity).

---

## D-003 — Corpus curated, not user-uploaded

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** User-uploaded PDFs vs. pre-acquired corpus
**Chose:** Pre-acquired
**Reasoning:** Removes biggest source of input variance (bad uploads); enables cross-policy comparison/recommendation; positions the corpus as a product moat vs. generic RAG-over-anything.
**Revisit at scale:** Same approach, larger corpus + scheduled refresh.

---

## D-004 — Architecture: hybrid structured + unstructured

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Pure RAG, pure structured DB, hybrid
**Chose:** Hybrid (DuckDB for structured, Chroma for vector)
**Reasoning:** Filter UI / comparison / recommendation pre-ranking require structured data; free-form Q&A with clause citations requires unstructured RAG. Linked by canonical `policy_id`.
**Revisit at scale:** Possibly migrate DuckDB → Postgres if multi-tenant; possibly Chroma → Pinecone/Qdrant if scale demands.

---

## D-005 — Streamlit for v1 UI

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Streamlit · FastAPI + React · Next.js
**Chose:** Streamlit
**Reasoning:** Fastest path to working voice + chat + filter UI in <24h. Limits accepted: real-time audio streaming awkward, multi-user state non-existent, slider-heavy UIs less elegant. Business logic kept in separate `app/` module so v2 swaps only the UI layer.
**Revisit at scale:** FastAPI + React for production v2.

---

## D-006 — Sarvam-first benchmarking for STT/TTS/LLM

**Date:** 2026-05-13
**Status:** Locked (provider picks pending Doc 02)
**Alternatives considered:** Sarvam vs. Whisper/Deepgram (STT), Sarvam vs. ElevenLabs/OpenAI (TTS), Sarvam-M vs. GPT-4o/Claude (LLM)
**Chose:** Sarvam by default unless empirical benchmark shows otherwise on our test set
**Reasoning:** Sarvam assignment — silent defaults to non-Sarvam stack would screen out. Each component is behind a thin interface so swapping is a config flag.
**Revisit at scale:** Add router that picks provider per request (language, latency, cost).

---

## D-007 — Pricing as illustrative band, not real-time quote

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:**
- (i) Illustrative band with disclaimer + sourcing
- (ii) Scrape comparison portals at query time
- (iii) Build actuarial model from first principles
**Chose:** (i) primary, (ii) for top-5 ground-truth validation
**Reasoning:** Insurers hide real pricing behind callback. (iii) is out of scope. (ii) is gray-area legally and brittle. (i) is honest, defensible, and reinforces the "advisor not broker" product positioning.
**Revisit at scale:** Add live aggregator integrations / B2B insurer API.

---

## D-008 — Persona: consultative advisor, not closer

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Hard-sell pitcher vs. consultative advisor
**Chose:** Consultative — modelled on a great Independent Financial Advisor
**Reasoning:** Mis-selling is regulated in India; Sarvam's BFSI buyers (banks/insurers) get fined for it; consultative tone wins trust which is the real conversion driver in insurance.
**Revisit at scale:** Same. Tone may flex by deployment partner.

---

## D-009 — Scope expansion: 10 insurers, comprehensive schema

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** 5 insurers × ~3 policies each (original v1 plan), 10 insurers × all health policies (expanded)
**Chose:** 10 insurers × all health policies (target 40–80 PDFs), 40–50 structured fields per policy
**Reasoning:** User explicitly expanded scope mid-flight for comprehensiveness. Aggressive but achievable with agentic crawl + batched extraction. Coverage of geography, PED, waiting periods, sub-limits, riders, etc. needed for the comparison surface to be credibly useful.
**Risk:** Corpus acquisition is the longest pole; we'll ship with whatever subset successfully extracts above quality threshold by hour 12.

---

## D-010 — Secret handling: Sarvam API key

**Date:** 2026-05-13
**Status:** Locked
**Reasoning:** Key lives only in `.env` (chmod 600, gitignored from line 1). `.env.example` checked in with placeholder. Streamlit Cloud deployment uses its own secrets UI. Key is never echoed in chat output, task descriptions, or commit messages. If leaked, rotate immediately at dashboard.sarvam.ai.

---

---

## D-005 (revised) — Frontend stack: Next.js + FastAPI (was: Streamlit)

**Date:** 2026-05-13 (revised mid-build)
**Status:** Locked
**Alternatives considered:** Streamlit (original v1 pick) · Gradio · Chainlit · Reflex · Next.js + FastAPI
**Chose:** **Next.js 14 (App Router) frontend + FastAPI backend**
**Reasoning for revision:** User unlocked the constraint mid-build ("use whatever is best"). Streamlit is fast-to-demo but signals "prototype" to a BFSI reviewer. Next.js + FastAPI signals "production-pattern, white-labelable to a bank." Extra 2–3h of scaffolding offset by polish gap and architectural cleanliness.
**Revisit at scale:** Same stack. Standard production pattern for AI products in 2026.
**Risk:** FE/BE auth + CORS + dual deploy adds complexity. Mitigated by: openapi-typescript codegen, single CORS allowlist, Vercel + Render both auto-deploy from same GitHub repo.

---

## D-011 — Embeddings provider: Voyage AI (Anthropic's partner)

**Date:** 2026-05-13
**Status:** Pending — awaiting Voyage API key confirmation
**Alternatives considered:** OpenAI text-embedding-3-small · Voyage voyage-3 · Sarvam embeddings (if API exists) · BGE-m3 local · Cohere embed-v3
**Chose:** **Voyage voyage-3**; fallback **BGE-m3 local** if no Voyage key
**Reasoning:** User confirmed they have Anthropic, not OpenAI — rules out OpenAI embeddings. Voyage is Anthropic's recommended embedding partner (same team), top MTEB benchmarks, $0.12/1M tokens (well under $50 signup credit). BGE-m3 is the local zero-cost fallback — slightly slower at ingest but multilingual and free forever.
**Revisit at scale:** Re-benchmark Sarvam embeddings when their API exposes them; potentially route by language (Voyage for English, Sarvam for Indic).

---

## D-012 — Backend deployment: Render

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Render · Fly.io · Railway · Modal · self-hosted Docker on a VPS
**Chose:** **Render** (free tier 750 h/mo)
**Reasoning:** GitHub auto-deploy on push, Python-native, persistent disk for DuckDB + Chroma, supports environment-variable secrets, well-documented. Fly.io was close second (better global routing) but more setup overhead.
**Revisit at scale:** Migrate to dedicated cloud (AWS / GCP) when v2 needs multi-region or auth.

---

## D-013 — Frontend UI library: Tailwind CSS + shadcn/ui

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** Tailwind + shadcn/ui · MUI · Chakra UI · Mantine · plain CSS
**Chose:** **Tailwind + shadcn/ui**
**Reasoning:** shadcn components are copy-paste primitives that produce beautiful, accessible UIs in hours. Tailwind utility classes give fine-grained control. Combined: fastest path to "looks like a real product" in a 1-day build.
**Revisit at scale:** Same stack.

---

## D-014 — Grader LLM: Claude Haiku 4.5

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** GPT-4o-mini (rejected — user has Anthropic, not OpenAI) · Claude Haiku 4.5 · Claude Sonnet 4.6 · Sarvam-M held out
**Chose:** **Claude Haiku 4.5**
**Reasoning:** Avoids circular eval (don't grade Sarvam-M responses with Sarvam-M — biased toward its own style). Haiku is cheap, fast, strong enough for binary semantic-match grading. Sonnet would also work but Haiku has the right cost/latency profile for a grader running 50+ times per eval pass.
**Revisit at scale:** Consider Claude Sonnet for nuanced grading (semantic similarity with rubric); add Anthropic's own eval/grading tools if they ship them.

---

## D-015 — API contract: REST with OpenAPI-driven TS codegen

**Date:** 2026-05-13
**Status:** Locked
**Alternatives considered:** REST + manual TypeScript types · REST + `openapi-typescript` codegen · tRPC (Node-only, doesn't fit Python BE) · GraphQL · gRPC
**Chose:** **REST + `openapi-typescript` codegen from FastAPI's auto-generated OpenAPI**
**Reasoning:** FastAPI ships an OpenAPI schema out of the box. `openapi-typescript` turns it into TypeScript types for the Next.js frontend — single source of truth, types update on backend change. Simpler than GraphQL for our request/response shape.
**Revisit at scale:** Same. If real-time streaming becomes the dominant pattern (e.g. streaming TTS), add a WebSocket route alongside REST.

---

*Entries added as we go. Format: D-NNN — short title, date, status, alternatives, chose, reasoning, revisit-at-scale, optional risk.*
