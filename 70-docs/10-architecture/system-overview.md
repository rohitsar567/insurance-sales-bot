# 02 — Architecture

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Document version | 1.0 |
| Date | 2026-05-17 |
| Status | **Superseded as a present-state map — see below** |

---

## This document has been superseded

The earlier version of this file described the **pre-rewrite** architecture
(an `orchestrator.py`, a `sales_brain`/`QA-brain` split, a 4-gate
`faithfulness.py` judge, a 3-tier Gemini/NIM/OpenRouter chain, a DuckDB
62-field hot path, Next.js 14, Vercel/Render hosting). **None of that exists
in the codebase anymore** — it was replaced by the single-brain rewrite.

To prevent two documents drifting apart again, there is now exactly **one**
authoritative architecture description:

> **→ [`README.md`](../../README.md) §4 "How it works, end to end"** is the
> canonical, maintained system map (request flow, the single-brain design,
> the NIM fallback chain, retrieval, voice, profile/personalisation,
> deployment). A one-screen summary also lives in
> [`ARCHITECTURE.md`](../../ARCHITECTURE.md).

## Current architecture in three lines

- One LLM call per turn: **Gemini `gemini-2.5-flash-lite` + function-calling
  tools** (`backend/single_brain.py`, `brain_tools.py`); on Gemini failure →
  `backend/nim_fallback.py` (NVIDIA NIM chain, health-elected, fail-loud).
- Retrieval: **Chroma + BGE-small local 384-d** (`rag/retrieve.py`); shared
  policies collection + per-session 24h quarantine for user-uploaded PDFs
  (8 security gates in `backend/security.py`).
- **Next.js 16 / FastAPI**, deployed as an HF Space (Docker, `uvicorn`,
  port 7860); heavy data pulled at build from the `insurance-bot-data` HF
  dataset; curated facts in `40-data/`.

Everything beyond these three lines: read the README. Do not treat any
remaining pre-rewrite prose elsewhere in `70-docs/` as the present state —
those files are decision history, being reconciled; the README is the
authority for how the system runs today.
