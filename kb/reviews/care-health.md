# Care Health Insurance — Reputation Sheet

_Auto-generated from `40-data/reviews/care-health.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **70.4** (B). _Solid CSR (93%) and YouTube/advisor endorsement, but bottom-of-table ICR (57.7%) and complaints volume drag the score_

**URL verification:** 11/13 URLs reachable via HEAD-check; 2 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **93.13%** (2023-24 (3-year avg)) |
| Complaints / 10K policies | **42** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.4 | None | [https://www.policybazaar.com/insurance-companies/religare-he](https://www.policybazaar.com/insurance-companies/religare-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.3 | None | [https://www.insurancedekho.com/health-insurance/care/user-re](https://www.insurancedekho.com/health-insurance/care/user-reviews) |
| joinditto | 4.0 | None | [https://joinditto.in/articles/health-insurance/care-health-i](https://joinditto.in/articles/health-insurance/care-health-insurance-review/) |

## Reddit / r/IndianFinance sentiment

- Overall: **mixed**
- Mentions estimate: 35
- Themes: Care Supreme plan widely recommended for comprehensive coverage, Receptive to escalations; reverses decisions when documentation supports customer, PED-related claim denials a recurring concern (e.g., myopia treated as PED case), Religare Enterprises corporate governance noise lingers as reputation drag, Above-industry complaint volume (42 per 10K)
- Sample posts:
  - [https://joinditto.in/articles/health-insurance/is-care-health-insurance-good/](https://joinditto.in/articles/health-insurance/is-care-health-insurance-good/)
  - [https://www.policybazaar.com/health-insurance/companies/hdfc-ergo-vs-niva-bupa/](https://www.policybazaar.com/health-insurance/companies/hdfc-ergo-vs-niva-bupa/)

## YouTube coverage

- Overall sentiment: **favourable**
- **Ditto Insurance** — [https://www.youtube.com/watch?v=2_EhrtJhn44](https://www.youtube.com/watch?v=2_EhrtJhn44) — _Care Supreme in 3-way comparison; competitive option_
- **Ditto Insurance** — [https://www.youtube.com/watch?v=1rRvcVkcQVw](https://www.youtube.com/watch?v=1rRvcVkcQVw) — _Care Supreme vs HDFC Ergo Optima Secure 2026 comparison_

## Recent news

- **Care Health Insurance settled only 57.69% of claims, paying Rs 2.88 per Rs 5 claimed** (The South First, 2024-12, tone: negative) — [https://thesouthfirst.com/health/health-insurance-claims-standalone-insurers-pay](https://thesouthfirst.com/health/health-insurance-claims-standalone-insurers-pay-rs-3-07-per-rs-5-claim-general-insurers-offer-rs-4-17/)
- **Star Health, CARE, Niva Bupa record most policyholder complaints in FY24** (Business Standard, 2025-09-01, tone: negative) — [https://www.business-standard.com/finance/personal-finance/star-health-care-niva](https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html)
- **Care Health Insurance Claim Settlement Ratio Analysis** (Ditto Insurance, 2026-04, tone: neutral) — [https://joinditto.in/articles/health-insurance/care-health-insurance-claim-settl](https://joinditto.in/articles/health-insurance/care-health-insurance-claim-settlement-ratio/)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `40-data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.