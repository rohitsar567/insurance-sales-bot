# ICICI Lombard General Insurance — Reputation Sheet

_Auto-generated from `data/reviews/icici-lombard.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **73.5** (B). _Strongest complaint metrics in the cohort (10 per 10K) and solid financials, but health CSR (85%) lags peers like HDFC ERGO and Bajaj_

**URL verification:** 7/10 URLs reachable via HEAD-check; 3 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **85.0%** (2023-24) |
| Complaints / 10K policies | **10** (2023-24 (3-year avg ~9-10)) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.3 | None | [https://www.policybazaar.com/insurance-companies/icici-lomba](https://www.policybazaar.com/insurance-companies/icici-lombard-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.4 | None | [https://www.insurancedekho.com/health-insurance/icici-lombar](https://www.insurancedekho.com/health-insurance/icici-lombard/claim-settlement) |
| joinditto | 4.0 | None | [https://joinditto.in/articles/health-insurance/is-icici-lomb](https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance-good/) |

## Reddit / r/IndianFinance sentiment

- Overall: **mixed-positive**
- Mentions estimate: 30
- Themes: Health Shield product cited as competitive offering, Strong parent brand (ICICI Bank) confidence, Low complaint volume relative to peers (~10 per 10K, well below industry average of 27), Below-average CSR vs HDFC ERGO and Bajaj Allianz, 10,200+ network hospitals praised
- Sample posts:
  - [https://www.oneassure.in/insurance/health-insurance-compare/niva-bupa-re-assure-](https://www.oneassure.in/insurance/health-insurance-compare/niva-bupa-re-assure-vs-icici-health-shield-vs-hdfc-ergo-my-health-suraksha-gold)
  - [https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance](https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance-good/)

## YouTube coverage

- Overall sentiment: **mixed**
- **Ditto Insurance review** — [https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance](https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance-good/) — _decent but not top-3 health insurer pick_

## Recent news

- **ICICI Lombard Q1FY25 net profit grows 48.7% YoY to Rs 580 crore** (ICICI Lombard IR, 2024-07, tone: positive) — [https://www.icicilombard.com/investor-relations](https://www.icicilombard.com/investor-relations)
- **ICICI Lombard, Bajaj Allianz and New India are the most profitable non-life players in FY 2024** (Cafemutual, 2024, tone: positive) — [https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-](https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-india-are-the-most-profitable-non-life-players-in-fy-2024)
- **Integrated Annual Report 2023-24: Uniting for a Singular Purpose** (ICICI Lombard, 2024, tone: neutral) — [https://www.icicilombard.com/70-docs/default-source/financial-information/annualrep](https://www.icicilombard.com/70-docs/default-source/financial-information/annualreport2024.pdf)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.