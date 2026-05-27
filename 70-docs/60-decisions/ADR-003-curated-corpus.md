# ADR-003: Curated corpus over user-uploaded PDFs

**Status:** Locked — partially superseded by [ADR-044](ADR-044-uploaded-pdf-parity.md) (2026-05-27) on the user-upload question.
**Date:** 2026-05-13

## Context

A health-insurance bot needs PDF text to ground its answers. The PDFs can come from either:

1. **Curated** — we acquire a corpus once, version it, embed it offline.
2. **User-uploaded** — every user uploads their own policy doc; the bot extracts on the fly.

## Decision

**Curated corpus** of 148 catalogued policies across 21 insurers (current as of 2026-05-27; older docs referencing 190/19 are pre-dedup file counts) + 18 regulatory PDFs. User-uploads were initially routed to an isolated `user_uploads_quarantine` Chroma collection (v1 design). ADR-044 (2026-05-27) revised this — uploads now dual-write into both the per-session quarantine AND the global `policies` collection, so the upload becomes a first-class marketplace card with the same scorecard / premium / RAG endpoints. The 8-gate defence in `backend/security.py` is the mitigation against the "pollutes canonical retrieval" risk the original framing avoided.

## Alternatives considered

| Approach | Why rejected for v1 |
|---|---|
| Pure user-upload | Highest input variance (bad scans, password-protected PDFs, partial documents). No cross-policy comparison possible — the bot only knows what the user just uploaded. |
| Hybrid with shared collection | User uploads polluting shared retrieval would let one bad upload poison answers for every other user. → now what shipped via ADR-044 — the 8-gate defence + heuristic-floor + Gemini extraction chain replaces the "isolation" mitigation with an "every upload is gated, extracted, and parity-checked against the catalogued 148" mitigation. Cross-session retrieval scoping remains an open follow-up. |

## Consequences

**Positive:**

- Removes the largest source of input variance (bad uploads).
- Enables cross-policy comparison and recommendation queries — the core differentiator.
- Positions the corpus as a product moat vs. generic "RAG over anything" bots.
- Provenance is verifiable: every PDF traces to a public insurer URL with HEAD-verified status.

**Negative:**

- Corpus acquisition is the longest-pole task in the build (acquired via the `tools/` agent crawl + Playwright rescue — see ADR-017).
- Corpus freshness depends on a manual refresh cadence.

## Revisit at scale

Same approach, larger corpus. Add scheduled monthly refresh job that re-fetches every URL via Playwright and re-embeds any changed PDF. The user-upload model has moved on — see [ADR-044](ADR-044-uploaded-pdf-parity.md) (2026-05-27) for the dual-write + 8-gate + heuristic-floor + Gemini extraction chain that supersedes the original quarantine-only design.
