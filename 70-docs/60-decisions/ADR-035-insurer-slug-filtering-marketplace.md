# ADR-035 — Insurer-slug filtering: hide `profile` + `regulatory` from marketplace

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Related KIs:** KI-129, KI-130, KI-132

## Context

The single Chroma collection (ADR-025) holds three classes of document chunks indistinguishable by storage but very different in purpose:

1. **Product chunks** — per-policy content extracted from brochures, wordings, curated YAML. `insurer_slug` is a real insurer (`hdfc-ergo`, `niva-bupa`, …). These ARE the marketplace's product inventory.
2. **Profile chunks** — per-user conversational context written by the orchestrator during fact-find, used to bias retrieval toward the user's situation. `insurer_slug='profile'`.
3. **Regulatory chunks** — IRDAI circulars, NHA health-stack docs, regulatory FAQs. `insurer_slug='regulatory'`.

Pre-fix, `/api/coverage` and `/api/policies/all` SELECT-ed every distinct `insurer_slug` from Chroma metadata. The marketplace then rendered `profile` and `regulatory` as if they were insurers — broken cards, no products underneath, user confusion.

## Decision

**Exclude `insurer_slug IN ('profile', 'regulatory')` from every user-facing marketplace aggregation surface.** The chunks remain in the collection unchanged — they still get retrieved during chat for context-boost and regulatory-grounding — but they never surface as marketplace entries.

Implementation:

- `backend/marketplace.py::list_insurer_slugs()` adds `WHERE insurer_slug NOT IN ('profile', 'regulatory')` to the metadata SELECT.
- `backend/marketplace.py::list_all_policies()` applies the same filter on the chunk-walk.
- The retrieval layer (`backend/retrieval.py`) does NOT filter — `profile` chunks must still surface to the brain as soft context, `regulatory` chunks must still surface as IRDAI citations.

The exclusion list lives in `backend/marketplace.py::_NON_PRODUCT_SLUGS = frozenset({'profile', 'regulatory'})`. Adding a future non-product slug (e.g. `internal-notes`) requires only adding it to this set.

## Consequences

| Win | Cost |
|---|---|
| Marketplace surfaces show only real insurers; no broken `profile` / `regulatory` cards | Two surfaces with subtly different read paths to the same collection — risk of one being updated without the other; mitigated by the single `_NON_PRODUCT_SLUGS` constant |
| Profile + regulatory chunks keep their retrieval-side power (user-context boosting, IRDAI grounding) — no functional regression | The single-collection design (ADR-025) is now leaning harder on `insurer_slug` semantics; a typo in any of these reserved slugs would silently corrupt the marketplace |
| The pattern generalizes: any future non-product slug class can be added to `_NON_PRODUCT_SLUGS` without schema migration | Operators must remember the convention — `insurer_slug` is overloaded |

## Related

- KI-129, KI-130, KI-132 — three commits hardening marketplace filtering after the broken-cards bug report
- ADR-025 — the single-collection-with-metadata-partitioning decision this filter operates on
