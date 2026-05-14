# New India Assurance — Reputation Sheet

_Auto-generated from `40-data/reviews/new-india.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **78.6** (B). _Highest claim payout among general insurers (ICR 103%, CSR 95% by count); PSU service experience drags consumer-perception score_

**URL verification:** 7/8 URLs reachable via HEAD-check; 1 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **95.04%** (2023-24 (by claim count)) |
| Complaints / 10K policies | **20** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.2 | None | [https://www.policybazaar.com/insurance-companies/new-india-a](https://www.policybazaar.com/insurance-companies/new-india-assurance-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.0 | None | [https://www.insurancedekho.com/health-insurance/new-india/cl](https://www.insurancedekho.com/health-insurance/new-india/claim-settlement) |
| policyx | 4.1 | None | [https://www.policyx.com/health-insurance/new-india-health-in](https://www.policyx.com/health-insurance/new-india-health-insurance/claim-settlement-ratio/) |

## Reddit / r/IndianFinance sentiment

- Overall: **mixed**
- Mentions estimate: 25
- Themes: PSU insurer with India's largest GWP (Rs 43,618 cr in FY25), Highest claim payout among general insurers (paying more than premiums collected, ICR 103%), Customer service / digital experience consistently rated below private peers, Branch-heavy, paper-heavy claim process flagged, Strong cashless network for legacy users; preferred by older customers
- Sample posts:
  - [https://www.deccanchronicle.com/business/new-india-assurance-tops-health-insuran](https://www.deccanchronicle.com/business/new-india-assurance-tops-health-insurance-claims-settlement-1841899)
  - [https://www.policybazaar.com/insurance-companies/new-india-assurance-health-insu](https://www.policybazaar.com/insurance-companies/new-india-assurance-health-insurance/claim-settlement-ratio/)

## YouTube coverage

- Overall sentiment: **mixed**

## Recent news

- **New India Assurance Tops Health Insurance Claims Settlement** (Deccan Chronicle, 2024, tone: positive) — [https://www.deccanchronicle.com/business/new-india-assurance-tops-health-insuran](https://www.deccanchronicle.com/business/new-india-assurance-tops-health-insurance-claims-settlement-1841899)
- **New India Assurance Q3FY24 results: Net profit falls 4.38% to Rs 715 crore** (Business Standard, 2024-02-09, tone: neutral) — [https://www.business-standard.com/companies/news/new-india-assurance-q3fy24-resu](https://www.business-standard.com/companies/news/new-india-assurance-q3fy24-results-net-profit-falls-4-38-to-rs-715-crore-124020901758_1.html)
- **ICICI Lombard, Bajaj Allianz and New India are the most profitable non-life players in FY 2024** (Cafemutual, 2024, tone: positive) — [https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-](https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-india-are-the-most-profitable-non-life-players-in-fy-2024)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `40-data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.