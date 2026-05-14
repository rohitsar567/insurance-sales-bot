# ROADMAP — From v1 Vertical Slice to v2 Platform

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| v1 status | Shipping in <24h for Sarvam AI assignment |
| v2 status | This document |

## 0. Purpose

v1 is a **vertical slice**: 10 insurers × Health × ~80 policies × voice-first advisor. The architecture is built so v2 is a **data/config change, not a rebuild**. This document maps the path.

## 1. What v1 ships

**Working product:**
- Voice-first chat advisor over a curated corpus of Indian health insurance policies (~76 PDFs from 10 insurers, ingested into Chroma + DuckDB)
- Multi-language: English + Hindi/Hinglish via Sarvam Saarika STT + Sarvam Bulbul TTS
- Brain router (D-019): NIM DeepSeek-V4-Pro (heavy intents) + V4-Flash (voice + fact-find), Llama-4 Maverick judge. Sarvam-M scoped to Indic translation + voice only.
- 4-gate hallucination defense + auditable refusal log
- 48-field structured extraction per policy
- Clean Next.js + Tailwind frontend
- FastAPI backend deployed on Render; frontend on Vercel
- 8 design / decision documents totaling ~30 pages

**Eval signal:**
- Gold Q&A harness (~300 pairs targeted) + automated grader (NIM Llama-4 Maverick — different family from DeepSeek brain)
- `eval/results.md` versioned table per run
- Live audit log `logs/hallucinations.jsonl` for every blocked claim

**Documented limits:**
- Star Health corpus blocked by CDN — 0/11 policies (workaround in v2 with Playwright)
- IRDAI regulatory corpus blocked by Akamai — deferred to v2 (D-017)
- Pricing is illustrative only (D-007)
- Single-user demo (no auth, no multi-tenant)

## 2. v2 — the path to "platform"

### v2.1 — Corpus expansion (target Q1 2027)

**Goal:** Move from 10 insurers Health → all major Indian insurers × all categories.

| Component | v1 → v2 change |
| --- | --- |
| Insurer adapters | 10 hand-curated adapter files → automated `rag/adapters/<slug>.py` per insurer (template + override) |
| Categories | Health only → Health + Life + Motor + Travel + Critical-illness specific (schema already supports it; data-only change per Doc 02 §7 commitment #2) |
| Policy count | 76 PDFs → ~500 PDFs |
| Refresh cadence | One-time → cron-pulled weekly with diff detection (F-11) |
| Star / Akamai workaround | Manual / blocked → Playwright-driven download per insurer (already MCP-installed) |
| IRDAI corpus | Deferred | Playwright + headless browser → tag chunks as `doc_type=regulatory` → orchestrator surfaces both product + regulatory citations |

**Engineering effort:** ~2 weeks. Schema/code already supports it. The work is per-insurer adapter + scheduling.

### v2.2 — Pricing realism (target Q2 2027)

**Current state (v1):** Illustrative bands only (D-007) — buyer-facing disclaimer.
**v2 path:**

| Step | What | Why |
| --- | --- | --- |
| 1 | Partnership with one or two insurers for real-quote API | Authoritative pricing, B2B integration |
| 2 | Until then: scheduled scrape of comparison portals (PolicyBazaar / InsuranceDekho) at session start | Real bands, refreshed daily |
| 3 | Quote disclaimer: "actual quote varies; final by underwriting" | Compliance, sets expectations |

### v2.3 — Production deployment (target Q1 2027)

| Layer | v1 | v2 |
| --- | --- | --- |
| Compute | Render free tier (cold-start spinup) | Render Standard + keep-warm OR migrate to AWS Fargate for B2B SLA |
| State | Single-tenant DuckDB + Chroma local | Postgres + Pinecone OR managed Chroma for multi-tenant + auth-scoped data |
| Auth | None (single-user demo) | OAuth + per-insurer-tenant isolation |
| Observability | JSONL turn log | OpenTelemetry → Grafana/Datadog dashboards |
| Eval cron | None | Nightly synthetic + 1-5% live-traffic spot grading via Playwright |
| Rate limiting | None | Per-tenant + per-user quotas |

### v2.4 — Voice interface upgrade (target Q3 2027)

**Current state (v1):** Push-to-talk via MediaRecorder API (record-then-send).
**v2 path:**

| Stage | Approach | Latency target |
| --- | --- | --- |
| 1 | VAD auto-cutoff via AudioWorklet | 2-3s perceived latency |
| 2 | Streaming STT via Sarvam Saarika WebSocket | <1.5s perceived latency |
| 3 | Full-duplex realtime (user interruptable) | <500ms TTFB |

### v2.5 — Recommendation engine (target Q2 2027)

**Current state (v1):** Rule-based pre-filter + LLM-reasoned justification with citations.
**v2 path:**

| Step | What |
| --- | --- |
| 1 | Add a learned ranker trained on (profile, policy, conversion) data once we have telemetry |
| 2 | Multi-turn refinement: bot proposes 3, user reacts, bot re-proposes — Bayesian update on profile |
| 3 | Premium-sensitive routing: if buyer is price-anchored, route to lower-premium-band recommendations even if features are weaker |

### v2.6 — Compliance posture (target H1 2027)

| Need | v2 work |
| --- | --- |
| Audit log retention | 7 years per IRDAI policyholder-records retention rules (D-017 reading) |
| PII handling | All buyer profile data encrypted at rest + per-tenant key |
| Mis-selling flags | Flag any session where the LLM-judge flags an unsupported claim |
| Grievance redressal | Built-in escalation path: chat → human → ombudsman; persisted handoff context |
| Regulatory updates | Cron-pulled IRDAI circulars → re-ingest → re-run eval; alert if regulation conflicts with corpus |

## 3. Cost projection v1 → v2

| Phase | Cost | Why |
| --- | --- | --- |
| v1 (this build) | < $1 | Free tiers across the stack |
| v2.1 corpus expansion (one-time) | ~$50 | Voyage embeddings for ~500 PDFs + LLM extraction |
| v2 monthly run-rate, 1k DAU | ~$300-500 | Sarvam STT/TTS/LLM volume + Render Standard + Postgres |
| v2 enterprise (5 insurers × 100k users) | TBD | Pricing depends on Sarvam volume contract |

## 4. What does NOT change between v1 and v2

The point of disciplined v1 architecture is that these things are **stable** across the transition:

1. **48-field structured schema** (`rag/schema.py`) — data-only change to add v2 categories
2. **Provider abstraction** (`backend/providers/base.py`) — swap STT/TTS/LLM via config
3. **Faithfulness verifier** (`backend/faithfulness.py`) — same 4 gates, possibly more
4. **Persona prompt + citation grammar** — same, refined
5. **Eval methodology** (`docs/03-eval-plan.md`) — same harness, more gold data

The "c-readiness commitments" in Doc 02 §7 are the contract. Every v2 feature is a commitment honored.

## 5. The honest tradeoffs in v1

| Choice | Why we made it | What we sacrificed |
| --- | --- | --- |
| Streamlit → Next.js mid-build | Production polish for a BFSI reviewer | 2 extra hours of scaffolding |
| Voyage embeddings → BGE local | Voyage 3 RPM rate limit blocked ingestion | Slightly lower retrieval quality (~3pp) for full corpus access |
| IRDAI corpus deferred | Akamai bot protection; 4-gate faithfulness already refuses regulatory questions cleanly | Bot can't ground answers in IRDAI text — refuses instead of citing |
| Push-to-talk over streaming | Risk of broken realtime > demo latency | 2-3s perceived latency vs <1s |
| No auth | Out of scope per Doc 01 | Single-user demo only |
| Hand-curated 5-node fact-find | Auditable + testable | Less natural than LLM-driven |
| Pipeline A templated gold Q&A | Scales for free; covers single-field lookups | Doesn't test multi-clause reasoning — Pipeline B + C handle that |

Every tradeoff is in `decisions.md` with a "revisit at scale" note.
