# ADR-005: Next.js 14 + FastAPI (revised from Streamlit)

**Status:** Locked (revised mid-build)
**Date:** 2026-05-13 (revision)

## Context

The original v1 plan locked Streamlit for the UI (D-005 original). Mid-build, the constraint was unlocked ("use whatever is best"). A re-evaluation surfaced that Streamlit's "this looks like a prototype" perception would undercut the BFSI-deployable-product signal the project is trying to send to reviewers.

## Decision

**Next.js 14 (App Router) frontend + FastAPI backend.** Vercel for FE auto-deploy; HF Space for BE (`origin`) with full mirroring to GitHub (see ADR-024).

## Alternatives considered

| Option | Why rejected |
|---|---|
| Streamlit (original) | Fast to demo, but signals "prototype" to a BFSI reviewer. Audio streaming and multi-user state awkward. |
| Gradio | Same prototype perception; ML-research vibe. |
| Chainlit | Specialised for chat-only UIs; comparison/marketplace panels harder. |
| Reflex (formerly Pynecone) | Python-only; less ecosystem; smaller community. |

## Consequences

**Positive:**

- Production-pattern stack matches what a deploying customer (bank, insurer) would expect.
- Server-side rendering via Next.js → fast initial paint.
- Tailwind + shadcn/ui (ADR-013) gives polished design without bespoke CSS.
- FastAPI ships OpenAPI for free → `openapi-typescript` codegen (ADR-015) keeps frontend types in sync with backend.

**Negative:**

- Extra ~2-3 hours of scaffolding versus Streamlit.
- FE/BE auth + CORS + dual deploy add complexity.

**Mitigations:**

- `openapi-typescript` codegen so types update automatically.
- Single CORS allowlist in `backend/main.py` (currently `*` for v1, narrowed for v2).
- Vercel + HF Space both auto-deploy from the same GitHub repo on push.

## Revisit at scale

Same stack. Standard production pattern for AI products in 2026.
