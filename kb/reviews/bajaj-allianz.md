# Bajaj Allianz General Insurance — Reputation Sheet

_Auto-generated from `data/reviews/bajaj-allianz.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **84.5** (B). _Industry-leading complaints record (3 per 10K) and high CSR (92.2%); third-largest non-life insurer in India_

**URL verification:** 11/11 URLs reachable via HEAD-check; 0 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **92.24%** (2023-24) |
| Complaints / 10K policies | **3** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.5 | None | [https://www.policybazaar.com/insurance-companies/bajaj-allia](https://www.policybazaar.com/insurance-companies/bajaj-allianz-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.4 | None | [https://www.insurancedekho.com/health-insurance/bajaj-allian](https://www.insurancedekho.com/health-insurance/bajaj-allianz/claim-settlement) |
| joinditto | 4.5 | None | [https://joinditto.in/articles/health-insurance/bajaj-general](https://joinditto.in/articles/health-insurance/bajaj-general-health-insurance-claim-settlement-ratio/) |

## Reddit / r/IndianFinance sentiment

- Overall: **positive**
- Mentions estimate: 25
- Themes: Best-in-class low complaint count (3 per 10K, industry-leading), Health Guard Gold plan well-reviewed, Strong solvency and credit rating (AAA stable), Settles claims typically within 7 working days, Less marketed than Star/HDFC but quietly strong on metrics
- Sample posts:
  - [https://1finance.co.in/product-scoring/health-insurance/bajaj-allianz-health-gua](https://1finance.co.in/product-scoring/health-insurance/bajaj-allianz-health-guard-gold-plan)
  - [https://www.policyx.com/health-insurance/bajaj-general-health-insurance/claim-se](https://www.policyx.com/health-insurance/bajaj-general-health-insurance/claim-settlement-ratio/)

## YouTube coverage

- Overall sentiment: **favourable**
- **Ditto Insurance** — [https://joinditto.in/articles/health-insurance/bajaj-general-health-insurance-cl](https://joinditto.in/articles/health-insurance/bajaj-general-health-insurance-claim-settlement-ratio/) — _ranked in top-5 by Ditto for retail health_

## Recent news

- **Bajaj Allianz General Insurance delivers strong financial results with profits rising 27% to Rs 921 crore** (Bajaj Allianz, 2024, tone: positive) — [https://www.bajajallianz.com/download-documents/press-release/Press-Release-Fina](https://www.bajajallianz.com/download-documents/press-release/Press-Release-Finance-Bajaj-Allianz-General-Insurance-delivers-strong-financial-results-with-profits-rising-by-27percent-to-Rs%20921-crore.pdf)
- **ICICI Lombard, Bajaj Allianz and New India are the most profitable non-life players in FY 2024** (Cafemutual, 2024, tone: positive) — [https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-](https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-india-are-the-most-profitable-non-life-players-in-fy-2024)
- **Bajaj Allianz GWP grew to Rs 20,630 crore in FY24, 33.2% growth** (Bajaj Allianz IR, 2024, tone: positive) — [https://www.bajajgeneralinsurance.com/about-us/financial-highlights.html](https://www.bajajgeneralinsurance.com/about-us/financial-highlights.html)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.