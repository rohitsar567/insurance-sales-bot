# ADR-037 — IndusInd General slug + `legacy_issuer` continuity

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Related KIs:** KI-144

## Context

Reliance General Insurance was renamed to **IndusInd General Insurance** under IRDAI Registration No. 103 (same regulatory entity, new corporate name following ownership change). Three health products in our corpus carried the old `reliance-general` slug:

1. **HealthGain** (UIN 100xxx)
2. **Hospi Care** (UIN 100xxx)
3. **Group Mediclaim** (UIN 100xxx)

Two competing requirements:

- **Identity correctness.** New customers searching the marketplace should see "IndusInd General", not "Reliance General". The legal entity has changed; old branding is wrong.
- **Retrieval continuity.** Brochures, IRDAI filings, and existing chat-history references in the corpus still use "Reliance General" prose. If retrieval can't bridge old-name queries to new-slug chunks, large portions of the historical corpus become unreachable by name.

A naive slug rename would correct identity but break retrieval for any query mentioning "Reliance General".

## Decision

**Introduce new slug `indusind-general`. Migrate all three products. Preserve continuity via `legacy_issuer` metadata.**

- New `insurer_slug='indusind-general'`. All three product extractions, curated YAMLs, and Chroma chunks updated.
- Every migrated chunk gains a `legacy_issuer='Reliance General'` metadata field.
- The retrieval layer (`backend/retrieval.py`) expands queries: if the query string mentions any value in any chunk's `legacy_issuer`, retrieval matches BOTH `insurer_slug='indusind-general'` AND chunks naming the legacy issuer.
- The marketplace surfaces (`/api/coverage`, `/api/policies/all`) display "IndusInd General" as the issuer name with an optional "formerly Reliance General" subtitle on the card.
- IRDAI Registration No. 103 is recorded in the slug's metadata as the canonical identity proof.

## Consequences

| Win | Cost |
|---|---|
| Marketplace identity matches IRDAI's current regulatory register; new customers see the correct name | Two issuer names exist in the corpus prose simultaneously; care needed when ingesting future Reliance-branded historical material |
| Retrieval continuity is preserved — "Reliance General HealthGain" queries still find the chunks under the new slug | `legacy_issuer` is now a real metadata field with semantic load; renaming the renaming convention (e.g. a future re-rename) needs a migration story |
| Establishes the general pattern for issuer renames: new slug + `legacy_issuer` continuity field + IRDAI Reg No. as identity anchor | Maintenance: every future issuer rename requires this same dance, not just a string update |

## Related

- KI-144 — the migration commit
- ADR-033 (marketplace dedup UIN rule) — the UIN identity logic that survives the issuer rename unchanged
