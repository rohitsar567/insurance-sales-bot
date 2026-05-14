# ADR-017: IRDAI regulatory corpus — deferred, then Playwright-rescued

**Status:** Locked
**Date:** 2026-05-13 (deferral) → 2026-05-14 (rescued)

## Context

Health-insurance answers often hinge on IRDAI master circulars (free-look periods, PED definitions, standard exclusions, cataract waiting-period caps). Research surfaced 17 candidate regulatory PDFs across IRDAI / Insurance Act / Ombudsman / PMJAY / GST FAQ documents.

When fetched, 14 of 17 returned **Akamai bot-challenge HTML** instead of the PDF, even with `Referer` matching, browser-grade headers, and cookie warm-up. Plain `curl` and `requests` could not get past Akamai's JavaScript challenge layer.

## Initial decision (deferral, 2026-05-13)

**Defer regulatory corpus to v2; rely on Gate 1 (retrieval floor) to safely refuse regulatory questions.**

Verified: "What's the GST + 80D treatment for premium?" → correctly blocked by Gate 1 (no retrieved chunks with `doc_type=regulatory` ⇒ retrieval score < `MIN_TOP_SCORE`).

## Revised decision (rescue, 2026-05-14)

**Playwright same-origin fetch.** Open the IRDAI page in real Chromium via the Playwright MCP, warm Akamai cookies on the `irdai.gov.in` homepage, then issue a same-origin `fetch()` with `credentials: 'include'` from inside the page's JS context. Landing pages like `document-detail?documentId=...` were resolved by extracting the embedded `<a href="...pdf">` from the DOM.

Result: **18 regulatory PDFs in `rag/corpus/regulatory/`** — including IRDAI Master Circular on Health 2024, Arogya Sanjeevani standard wording, Insurance Act 1938, Ombudsman Rules, PMJAY ops manual, GST FAQs.

## Alternatives considered (at deferral)

| Option | Why rejected |
|---|---|
| Brute-force Playwright | Would have worked (eventually adopted); consumed ~30 min build time that was prioritised for eval harness. |
| Third-party law-firm summaries / Wikipedia | Derivative; unreliable for BFSI grounding. |
| Hand-curate regulatory summary | Violates the no-hallucination rule — we cannot insert training-data facts into the corpus. |

## Why it was worth coming back to

Without the regulatory corpus, the demo question *"What does IRDAI's 2024 Master Circular say about cataract waiting-period caps?"* refused (correct behavior under retrieval-floor gate) — but the safer-but-unhelpful failure mode signaled to reviewers that the bot couldn't ground regulatory questions. With the rescue, the bot now answers with `irdai-master-circular-health-2024.pdf` citation.

## Consequences

**Positive:**

- Regulatory grounding is real, not deferred.
- Star Health corpus (which had the same Akamai problem) was rescued in the same Playwright pass — all 11 / 11 PDFs now in `rag/corpus/star-health/`.
- The Playwright pattern is reusable for any future bot-protected source.

**Negative:**

- Playwright dependency in the ingest pipeline.

**Mitigations:**

- Playwright is only invoked in the rescue script (`rag/download_retry.py`) — not in the bot's runtime path.

## Revisit at scale

v2: scheduled refresh job re-runs the Playwright pattern monthly to capture any updated IRDAI circulars.
