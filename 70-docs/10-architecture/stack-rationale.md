# Tech Stack Rationale

| Field | Value |
| --- | --- |
| Project | Insurance Sales Portfolio Expert |
| Date | 2026-05-17 |
| Status | **Superseded as a present-state map — see below** |

---

## This document has been superseded

The earlier version listed pre-rewrite picks that are no longer accurate:
Next.js 14, Vercel + Render hosting, a Streamlit→Next migration note, a
3-tier Gemini-2.0/2.5 + NIM-judge chain, a DuckDB 62-field structured hot
path. The system was rebuilt around a single brain since then.

The **current, accurate stack and the reasoning behind it** now lives in one
place so it cannot drift again:

> **→ [`README.md`](../../README.md) §7 "Tech stack & key decisions"** (the
> what + the one-line why), with §4 for how the pieces fit together.

## Current stack (authoritative summary)

- **Frontend:** Next.js 16 (App Router), React 19, Tailwind v4, static export.
- **Backend:** FastAPI + Pydantic; `uvicorn`, port 7860 on the HF Space.
- **Brain:** Google **Gemini `gemini-2.5-flash-lite`** + function calling
  (one call/turn) → **NVIDIA NIM** open-model fallback chain (health-elected,
  fail-loud). No separate judge model (retired in the single-brain
  consolidation).
- **Retrieval:** Chroma + BGE-small-en-v1.5 (local CPU, 384-d).
- **Voice:** Sarvam Saarika (STT) + Bulbul (TTS) + Sarvam-M (Indic).
- **Hosting:** Hugging Face Space (Docker) + companion HF dataset for
  corpus/vectors; **not** Vercel/Render (that was the old plan).

The *why* (alternatives considered, trade-offs) is in the README §7 and in the
ADRs under `60-decisions/` — ADRs are point-in-time decision records: read
each one's **Status** line (Accepted / Superseded / Reversed) rather than
assuming it describes the system today. The README is the present-state
authority.
