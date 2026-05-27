# ADR-002: Health as the v1 vertical category

**Status:** Locked
**Date:** 2026-05-13

## Context

Given the vertical-slice scope (ADR-001), one insurance category had to be chosen. Candidates: Health, Life, Motor.

## Decision

**Health insurance.**

## Alternatives considered

| Category | Why not for v1 |
|---|---|
| Life insurance | Harder emotional sale; numerical comparison less clean; smaller buyer-controlled-decision share (term life is the only directly comparable product). |
| Motor insurance | Price-commoditised; lower BFSI margin; less compelling demo (less depth to compare). |

## Why Health

1. **Richest structured-attribute surface** — waiting periods, PED handling, sub-limits, network hospitals, claim ratio, geographic spread, restoration benefits, room rent caps, AYUSH coverage, OPD riders. Enough fields to make cross-policy comparison non-trivial.
2. **Broadest user relevance** — every Indian adult has or considers health insurance.
3. **Cleanest public corpus** — top 19 insurers all publish policy wordings, brochures, and CIS documents on their public websites.
4. **Regulator-rich** — IRDAI master circulars define standard exclusions, free-look periods, portability rules. Gives the bot a natural "regulatory overlay" layer (see ADR-017).

## Consequences

**Positive:** Maximum surface area for demonstrating the architecture in v1.

**Negative:** Life and Motor users see "this only covers Health."

## Revisit at scale

v2 adds Life (term + ULIP + endowment) and Motor (private car + two-wheeler). The 48-field Health schema generalises with ~70% reuse — most additions are category-specific waiting/exclusion fields.

---

*(Current state per [ADR-044](ADR-044-uploaded-pdf-parity.md): corpus expanded to 21 catalogued insurer slugs / 148 marketplace cards; the 19-insurer baseline this ADR was scoped against captured the original launch corpus.)*
