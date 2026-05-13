# Niva Bupa Health Insurance — Reputation Sheet

_Auto-generated from `data/reviews/niva-bupa.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **75.8** (B). _Strong CSR (91.6%) and growth post-IPO; complaints volume above industry average is the main drag_

**URL verification:** 14/17 URLs reachable via HEAD-check; 3 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **91.62%** (2023-24 (3-year avg through FY25)) |
| Complaints / 10K policies | **43** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.4 | None | [https://www.policybazaar.com/max-bupa-health-general-reviews](https://www.policybazaar.com/max-bupa-health-general-reviews-477/) |
| insuredekho | 4.3 | 192 | [https://www.insurancedekho.com/health-insurance/niva-bupa-he](https://www.insurancedekho.com/health-insurance/niva-bupa-health-insurance/user-reviews) |
| mouthshut | 2.0 | None | [https://www.mouthshut.com/product-reviews/niva-bupa-health-i](https://www.mouthshut.com/product-reviews/niva-bupa-health-insurance-reviews-925608449) |
| justdial | 3.5 | 903 | [https://www.justdial.com/Mumbai/Niva-Bupa-Health-Insurance-C](https://www.justdial.com/Mumbai/Niva-Bupa-Health-Insurance-Company-Ltd-Customer-Care/022PXX22-XX22-121222183516-A3L5_BZDET/reviews) |

**Trustpilot:** 1.8 (None reviews) — [https://www.trustpilot.com/review/nivabupa.com](https://www.trustpilot.com/review/nivabupa.com)

## Reddit / r/IndianFinance sentiment

- Overall: **mixed**
- Mentions estimate: 45
- Themes: ReAssure 2.0 plan widely discussed and recommended, Bupa global parent brand association = trust signal, App quality and customer service complaints common, Higher than industry-average complaint volume (42.85 per 10K), Recently listed on Indian stock market (Nov 2024) — growing scale
- Sample posts:
  - [https://www.quora.com/Has-anyone-taken-Niva-Bupa-health-insurance-in-India-How-r](https://www.quora.com/Has-anyone-taken-Niva-Bupa-health-insurance-in-India-How-reliable-is-it)
  - [https://www.policybazaar.com/health-insurance/companies/hdfc-ergo-vs-niva-bupa/](https://www.policybazaar.com/health-insurance/companies/hdfc-ergo-vs-niva-bupa/)

## YouTube coverage

- Overall sentiment: **favourable**
- **Ditto Insurance** — [https://www.youtube.com/watch?v=2_EhrtJhn44](https://www.youtube.com/watch?v=2_EhrtJhn44) — _ReAssure 2.0 vs HDFC Ergo vs Care comparison; recommended for premium plans_
- **Sagar Sinha** — [https://www.youtube.com/watch?v=1BUFoq5jbmQ](https://www.youtube.com/watch?v=1BUFoq5jbmQ) — _balanced comparison with HDFC ERGO_
- **Sanjay Kathuria** — [https://www.youtube.com/watch?v=EDZ74XMFirI](https://www.youtube.com/watch?v=EDZ74XMFirI) — _comparative 2026 review_

## Recent news

- **Niva Bupa shares make positive debut on bourses; list at 6% premium** (Business Standard, 2024-11-14, tone: positive) — [https://www.business-standard.com/markets/news/niva-bupa-shares-make-positive-de](https://www.business-standard.com/markets/news/niva-bupa-shares-make-positive-debut-on-bourses-list-at-6-premium-124111400326_1.html)
- **Bupa Group Indian health insurance business, Niva Bupa, successfully completes IPO** (Bupa Group, 2024-11, tone: positive) — [https://www.bupa.com/news-and-press/press-releases/2024/bupa-indian-health-insur](https://www.bupa.com/news-and-press/press-releases/2024/bupa-indian-health-insurance-business-niva-bupa-completes-ipo)
- **Star Health, CARE, Niva Bupa record most policyholder complaints in FY24** (Business Standard, 2025-09-01, tone: negative) — [https://www.business-standard.com/finance/personal-finance/star-health-care-niva](https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html)
- **Niva Bupa IPO: Growth, claim settlement and retention metrics compared with industry peers** (Upstox, 2024-11, tone: neutral) — [https://upstox.com/news/market-news/ipo/understanding-niva-bupa-s-market-positio](https://upstox.com/news/market-news/ipo/understanding-niva-bupa-s-market-position-growth-claim-settlement-and-retention-metrics-compared-with-industry-peers/article-127535/)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.