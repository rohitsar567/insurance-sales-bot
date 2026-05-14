# ADR-007: Illustrative pricing, not real-time quotes

**Status:** Locked
**Date:** 2026-05-13

## Context

Buyers expect to see premium estimates. Indian insurers hide real pricing behind a phone-callback flow; there is no public quote API. Three paths exist:

1. Illustrative band, anchored to public rate cards, with explicit disclaimer.
2. Scrape comparison portals (PolicyBazaar etc.) at query time.
3. Build an actuarial model from first principles.

## Decision

**Path 1: illustrative band with disclaimer.** Path 2 used only for top-5 ground-truth validation of the anchor cells.

## Alternatives considered

| Path | Why rejected as primary |
|---|---|
| (2) Live scrape | Legally gray (ToS), brittle to UI changes, slow per-query, doesn't capture insurer-callback dynamics. |
| (3) Actuarial model | Out of scope; data inputs unavailable; v1 build window doesn't permit it. |

## Implementation

- `data/premiums/illustrative_premiums.json` holds the anchor table.
- Anchor cells sourced from public PolicyBazaar quote pages and insurer rate cards (provenance in `kb/premiums/INDEX.md`).
- Missing `(insurer, policy, age, SI, family-composition)` cells are extrapolated using scaling factors derived from the visible anchor points + standard IRDAI age-bandings.
- **Every premium reply from the bot carries an explicit illustrative disclaimer.**
- Premium calculator UI panel (frontend) shows the same disclaimer prominently.

## Consequences

**Positive:**

- Honest about precision — no implied false certainty.
- Reinforces the "advisor not broker" positioning.
- Removes legal risk of pretending to broker quotes.

**Negative:**

- Users get an estimate band, not a specific number.
- Conversion to actual purchase still requires insurer/aggregator handoff.

## Revisit at scale

v2 path: B2B partnership with an aggregator (PolicyBazaar, Ditto, InsuranceDekho) or direct insurer APIs to get real quotes. See `docs/00-overview/roadmap.md` §v2.2.
