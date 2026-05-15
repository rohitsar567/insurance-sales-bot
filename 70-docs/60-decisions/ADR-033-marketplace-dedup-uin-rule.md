# ADR-033 — Marketplace dedup: one IRDAI UIN = one card

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Related KIs:** KI-141, KI-142, KI-143, KI-145

## Context

The marketplace aggregation surfaces (`/api/coverage`, `/api/policies/all`) were emitting duplicate cards for the same regulatory product. Two pathological patterns:

1. **Pure renames** — an insurer rebrands a filed product (e.g. issuer change, marketing name refresh) without re-filing with IRDAI. Two extracted JSONs end up with the same UIN but slightly different `policy_name` strings, producing two near-identical cards.
2. **Sub-variants** — an insurer files a single UIN that covers multiple plan tiers (Silver / Gold / Platinum, or per-age-band variants). Each tier has materially different decision-critical terms (room rent cap, sub-limits, co-pay, NCB ladder) but shares the regulatory identity.

Without a dedup rule, the user saw card-fatigue and lost trust in the marketplace as a comparison surface. Worse, "Also marketed as" relationships were invisible — the user had no way to know that "Insurer A's Optima Restore" and "Insurer A's Optima Restore Plus" were the same regulatory product with a tier choice.

## Decision

**The IRDAI UIN is the canonical product identity.** One UIN ⇒ one marketplace card. Variants are resolved by comparing decision-critical terms:

- **Same UIN + same key terms** (room rent cap, co-pay, NCB ladder, sub-limit list, network size band) ⇒ pure rename. Emit ONE card; old names go into an `aliases: ["Also marketed as: …"]` field on the card.
- **Same UIN + ≥2 different decision-critical terms** ⇒ sub-variant. Emit a separate card per variant, but flag the shared UIN in metadata so the comparison view can group them visually ("3 variants of UIN ABC-HLT-...").

The dedup runs at aggregation time in the `/api/coverage` and `/api/policies/all` handlers, not at ingest. Ingest stays write-only — the same product can be ingested from multiple sources (brochure PDF, insurer website, IRDAI filing) and the aggregation layer reconciles.

## Consequences

| Win | Cost |
|---|---|
| Marketplace card count drops ~12% (renames collapsed) with zero info loss — old names surface in alias text | The "key terms" comparison list is hand-curated; adding a new decision-critical term requires touching the dedup rule |
| Sub-variants stay visible and comparable; users see "3 variants of the same regulatory product" instead of either 1 collapsed card (info loss) or 3 unrelated cards (confusion) | Aggregation-time dedup adds ~30ms per `/api/coverage` call; cached after first computation |
| IRDAI's regulatory filing becomes the authoritative product identity — matches how regulators think about products, decouples from insurer marketing churn | Insurers occasionally re-file under a new UIN for a minor change; those land as separate cards by design |

## Related

- KI-145 — field-comparison refinement that pinned the rename-vs-sub-variant boundary
- KI-141 / KI-142 / KI-143 — earlier dedup attempts (name-based, embedding-based) that failed because they couldn't distinguish rename from sub-variant
