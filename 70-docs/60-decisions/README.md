# Architecture Decision Records (ADRs)

Each major technical or product decision is captured as a single ADR using a lightly adapted Michael Nygard template: **Status · Context · Decision · Alternatives · Consequences · Revisit-at-scale**.

ADR numbers map 1:1 to the original `D-NNN` entries that lived in the legacy `70-docs/decisions.md` (now split into individual files for easier review, citation, and supersession tracking).

## Index

| # | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-vertical-slice-scope.md) | Vertical slice scope, not full platform | Locked |
| [ADR-002](ADR-002-health-category-vertical.md) | Health as the v1 vertical category | Locked |
| [ADR-003](ADR-003-curated-corpus.md) | Curated corpus over user-uploads | Locked (partially superseded by ADR-044) |
| [ADR-004](ADR-004-hybrid-structured-vector.md) | Hybrid structured + vector retrieval | Locked |
| [ADR-005](ADR-005-nextjs-fastapi-frontend.md) | Next.js 14 + FastAPI (superseded original Streamlit pick) | Locked (revised) |
| [ADR-006](ADR-006-sarvam-first-stack.md) | Sarvam-first STT/TTS/LLM defaults | Partially superseded by ADR-019 |
| [ADR-007](ADR-007-illustrative-pricing.md) | Illustrative pricing, not real-time quotes | Locked |
| [ADR-008](ADR-008-consultative-advisor-persona.md) | Consultative-advisor persona | Locked |
| [ADR-009](ADR-009-19-insurer-comprehensive-schema.md) | 19 insurers × all health policies; 48-field schema | Locked (historical scope; current 21/148) |
| [ADR-010](ADR-010-secret-handling.md) | Secret handling: `.env` chmod 600, gitignored | Locked |
| [ADR-011](ADR-011-bge-local-embeddings.md) | Local BGE-small embeddings (Voyage was original) | Locked |
| [ADR-012](ADR-012-render-then-hf-space-deploy.md) | Render → HF Space migration | Superseded |
| [ADR-013](ADR-013-tailwind-shadcn-ui.md) | Tailwind CSS + shadcn/ui | Locked |
| [ADR-014](ADR-014-groq-llama-grader.md) | Groq Llama-3.3-70B grader | Superseded by ADR-019 |
| [ADR-015](ADR-015-openapi-typescript-codegen.md) | REST + `openapi-typescript` codegen | Locked |
| [ADR-016](ADR-016-hybrid-brain-router.md) | Hybrid brain router (Sarvam + fallback) | Superseded by ADR-019 |
| [ADR-017](ADR-017-irdai-corpus-playwright-rescue.md) | IRDAI regulatory corpus deferred → Playwright rescue | Locked |
| [ADR-018](ADR-018-chunk-size-sweep-deferred.md) | Chunk-size sweep deferred; 800/120 baseline | Deferred to v2 |
| [ADR-019](ADR-019-nim-single-provider-consolidation.md) | NVIDIA NIM as single non-Sarvam provider | Locked |
| [ADR-020](ADR-020-code-data-split-hf-dataset.md) | Code in Space repo, data in companion HF Dataset | Locked |
| [ADR-021](ADR-021-view-aware-system-prompt.md) | View-aware system prompt (D-020-frontend copilot) | Locked |
| [ADR-022](ADR-022-conversational-profile-updates.md) | Conversational profile updates via LLM extractor | Locked |
| [ADR-023](ADR-023-admin-panel-ip-gated.md) | Admin panel IP-gated; surfaced as in-app tab | Locked |
| [ADR-024](ADR-024-triple-mirror-code-and-data.md) | Triple-mirror: HF + GitHub + local for both code and data | Locked |

## How to add a new ADR

1. Pick the next number (ADR-025).
2. Copy the template from any existing ADR (Status / Context / Decision / Alternatives / Consequences / Revisit-at-scale).
3. Status starts at `Proposed`; flip to `Locked` when implemented, `Superseded` when replaced.
4. Add a row to this index.
5. If superseding an older ADR, edit that ADR's status to `Superseded by ADR-NNN`.

## Why split this from `decisions.md`?

The legacy 32 KB `decisions.md` accumulated decisions chronologically. As the project grew, reviewers couldn't find the *current* state for a given concern without reading every entry in order. Per-decision files give each ADR a permanent URL, allow supersession tracking, and let new decisions land without merge conflicts in a giant monolithic file.
