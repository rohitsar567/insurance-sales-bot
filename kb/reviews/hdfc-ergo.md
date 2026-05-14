# HDFC ERGO General Insurance — Reputation Sheet

_Auto-generated from `40-data/reviews/hdfc-ergo.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **85.7** (A). _Industry-leading claim settlement ratio (99.1%) with low complaints (15 per 10K) and strong YouTube/advisor recommendation_

**URL verification:** 16/17 URLs reachable via HEAD-check; 1 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **99.1%** (2023-24) |
| Complaints / 10K policies | **15** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.4 | None | [https://www.policybazaar.com/hdfc-ergo-general-reviews-903/](https://www.policybazaar.com/hdfc-ergo-general-reviews-903/) |
| insuredekho | 4.5 | None | [https://www.insurancedekho.com/health-insurance/hdfc-ergo](https://www.insurancedekho.com/health-insurance/hdfc-ergo) |
| joinditto | 4.5 | None | [https://joinditto.in/health-insurance/hdfc-ergo/reviews/](https://joinditto.in/health-insurance/hdfc-ergo/reviews/) |
| mouthshut | 2.5 | None | [https://www.mouthshut.com/product-reviews/hdfc-ergo-health-i](https://www.mouthshut.com/product-reviews/hdfc-ergo-health-insurance-reviews-925865222) |

**Trustpilot:** 1.7 (None reviews) — [https://www.trustpilot.com/review/www.hdfcergo.com](https://www.trustpilot.com/review/www.hdfcergo.com)

## Reddit / r/IndianFinance sentiment

- Overall: **mixed-positive**
- Mentions estimate: 55
- Themes: Optima Secure plan is the most-recommended retail health plan on Indian Reddit, Cashless approval reportedly fast at network hospitals, Some 'silly mistake' / non-disclosure rejection complaints in emergencies, Premium pricing on the higher end but considered worth it, Ditto Insurance / advisors actively recommend HDFC ERGO for reliability
- Sample posts:
  - [https://www.quora.com/Have-you-ever-made-any-claim-against-HDFC-Ergo-health-insu](https://www.quora.com/Have-you-ever-made-any-claim-against-HDFC-Ergo-health-insurance-and-was-it-a-good-experience)
  - [https://www.oneassure.in/insurance/health-insurance-compare/niva-bupa-re-assure-](https://www.oneassure.in/insurance/health-insurance-compare/niva-bupa-re-assure-vs-icici-health-shield-vs-hdfc-ergo-my-health-suraksha-gold)

## YouTube coverage

- Overall sentiment: **favourable**
- **Ditto Insurance** — [https://www.youtube.com/watch?v=2_EhrtJhn44](https://www.youtube.com/watch?v=2_EhrtJhn44) — _HDFC ERGO Optima Secure highly recommended in 3-way comparison_
- **Ditto Insurance** — [https://www.youtube.com/watch?v=i3xMZGMstzE](https://www.youtube.com/watch?v=i3xMZGMstzE) — _Optima Secure preferred over Niva Bupa Aspire_
- **Sagar Sinha** — [https://www.youtube.com/watch?v=1BUFoq5jbmQ](https://www.youtube.com/watch?v=1BUFoq5jbmQ) — _balanced HDFC ERGO vs Niva Bupa review_
- **Sanjay Kathuria** — [https://www.youtube.com/watch?v=EDZ74XMFirI](https://www.youtube.com/watch?v=EDZ74XMFirI) — _comparative 2026 review_

## Recent news

- **HDFC Ergo Aims for 18% Premium Growth in Retail Health Insurance for 2024-25** (Goodreturns, 2024, tone: positive) — [https://www.goodreturns.in/news/hdfc-ergo-aims-to-maintain-premium-growth-2024-2](https://www.goodreturns.in/news/hdfc-ergo-aims-to-maintain-premium-growth-2024-25-011-1392211.html)
- **HDFC ERGO General Insurance Claims Settlement Ratio Analysis for 2024-25** (Angel One, 2025, tone: positive) — [https://www.angelone.in/news/market-updates/hdfc-ergo-general-insurance-claims-s](https://www.angelone.in/news/market-updates/hdfc-ergo-general-insurance-claims-settlement-ratio-analysis-for-2024-25)
- **Pre-approved cashless facility launched for chemotherapy and dialysis** (HDFC ERGO, 2024, tone: positive) — [https://www.hdfcergo.com/](https://www.hdfcergo.com/)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `40-data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.