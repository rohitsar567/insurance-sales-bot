# Architecture

**Canonical source: [`README.md`](README.md) §4 "How it works, end to end".**

The full, maintained architecture — request flow diagram, the single-brain
design, the fallback chain, voice, retrieval, profile/personalisation — lives
inline in the README so there is exactly **one** place to keep accurate (the
historical split between this file and `70-docs/` is what allowed both to
drift out of date). This file is a one-screen orientation; the README is the
authority.

## One-screen summary

- **Frontend** — Next.js 16 / React 19 / Tailwind v4, static export, served by
  the backend. `frontend/src/app/page.tsx`. Voice = Web Speech (interim) +
  `MediaRecorder` (authoritative) → Sarvam STT; Sarvam TTS replies.
- **Backend** — FastAPI (`backend/main.py`); `uvicorn` on port 7860 in the
  Space. Endpoints: `/api/chat`, `/api/transcribe`, `/api/upload-policy`,
  `/api/coverage`, `/api/profile*`, `/api/scorecard`, `/api/session*`,
  `/api/admin/*`.
- **Brain** — one LLM call per turn: Google Gemini
  (`gemini-2.5-flash`) + function-calling tools
  (`save_profile_field`, `retrieve_policies`, `get_policy_facts`,
  `mark_recommendation`) in
  `backend/single_brain.py` / `backend/brain_tools.py`. A single call owns
  the whole turn: fact-find, retrieval, QA, and recommendation. On a
  transient Gemini error / cold-start 503 → small `backend/nim_fallback.py`
  (NVIDIA NIM) so the turn still completes. Fail-loud, never silently wrong.
  The legacy multi-pass design (orchestrator / sales-brain / QA-brain /
  separate faithfulness judge / profile_extractor / tiered brain) was
  removed — it does not exist in the codebase.
- **Retrieval** — Chroma vector store, BGE-small-en-v1.5 local 384-d
  embeddings (`rag/retrieve.py`). Shared "policies" collection (~150 plans,
  ~7.3k chunks, 20 insurers) + a per-session "quarantine" collection for
  user-uploaded PDFs (24h TTL, session-isolated).
- **Upload safety** — `backend/security.py`, 8 gates, before any embedding.
- **Data** — three repos: code (HF Space `origin` + GitHub `github`
  mirror), the `rohitsar567/insurance-bot-data` HF dataset (corpus +
  vectors, pulled at Docker build), and `40-data/` curated facts versioned
  with the code.
- **Deploy** — HF Space Docker; `entrypoint.sh` runs `uvicorn`; the build
  `snapshot_download`s the data dataset.

For anything beyond this, read `README.md` — do not treat older
`70-docs/`/ADR prose as the present-state map (it predates the single-brain
rewrite and is being reconciled).
