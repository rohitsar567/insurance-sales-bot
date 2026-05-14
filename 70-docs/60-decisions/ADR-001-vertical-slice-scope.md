# ADR-001: Vertical slice scope, not full platform

**Status:** Locked
**Date:** 2026-05-13

## Context

The Sarvam takehome assignment had a ~24-hour build window with explainability as an explicit grading criterion. Three scope shapes were on the table:

1. Single-document RAG-voice bot over one policy.
2. Vertical slice — full architecture for one category (Health), built so category expansion is config + data, not code.
3. Full platform — 300+ policies across all insurance categories.

## Decision

Build the vertical slice. One category (Health) — but every architectural surface a reviewer cares about is real: hybrid retrieval, schema, voice cascade, citations, evaluation, audit trail, refusal behaviour, scorecard methodology, deployment.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Single-document RAG | Under-signals product vision; reviewer can't see how the system would scale. |
| Full platform | Over-scopes within 24h; ships rough; quality bar suffers across the board. |

## Consequences

**Positive:** Demonstrates senior-engineer scoping discipline. Every part of the bot a BFSI buyer would audit (provenance, refusal, eval rigor) is real, not stubbed.

**Negative:** Life, Motor, and other categories are not covered. The seven "c-readiness commitments" in `70-docs/10-architecture/system-overview.md` §7 become real v2 work.

## Revisit at scale (v2)

Category expansion plan in `70-docs/00-overview/roadmap.md`. Each new category requires: (1) corpus acquisition, (2) per-category schema extension, (3) eval gold set, (4) scorecard sub-score weights. No core code changes.
