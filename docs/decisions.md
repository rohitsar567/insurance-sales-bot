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

*Entries added as we go. Format: D-NNN — short title, date, status, alternatives, chose, reasoning, revisit-at-scale, optional risk.*
