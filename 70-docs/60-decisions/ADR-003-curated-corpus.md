# ADR-003: Curated corpus over user-uploaded PDFs

**Status:** Locked
**Date:** 2026-05-13

## Context

A health-insurance bot needs PDF text to ground its answers. The PDFs can come from either:

1. **Curated** — we acquire a corpus once, version it, embed it offline.
2. **User-uploaded** — every user uploads their own policy doc; the bot extracts on the fly.

## Decision

**Curated corpus** of 190 policy PDFs (19 insurers) + 18 regulatory PDFs. User-uploads are accepted but routed to an isolated `user_uploads_quarantine` Chroma collection so they never pollute the canonical retrieval.

## Alternatives considered

| Approach | Why rejected for v1 |
|---|---|
| Pure user-upload | Highest input variance (bad scans, password-protected PDFs, partial documents). No cross-policy comparison possible — the bot only knows what the user just uploaded. |
| Hybrid with shared collection | User uploads polluting shared retrieval would let one bad upload poison answers for every other user. |

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

Same approach, larger corpus. Add scheduled monthly refresh job that re-fetches every URL via Playwright and re-embeds any changed PDF. User-upload quarantine collection remains isolated.
