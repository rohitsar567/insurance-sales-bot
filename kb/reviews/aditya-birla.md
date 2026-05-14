# Aditya Birla Health Insurance — Reputation Sheet

_Auto-generated from `40-data/reviews/aditya-birla.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **81.2** (B). _Strong CSR (93%) with low complaints (13 per 10K), differentiated by HealthReturns wellness program_

**URL verification:** 9/9 URLs reachable via HEAD-check; 0 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **92.97%** (2023-24) |
| Complaints / 10K policies | **13** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.4 | None | [https://www.policybazaar.com/insurance-companies/aditya-birl](https://www.policybazaar.com/insurance-companies/aditya-birla-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.3 | None | [https://www.insurancedekho.com/health-insurance/aditya-birla](https://www.insurancedekho.com/health-insurance/aditya-birla/claim-settlement) |
| joinditto | 4.5 | None | [https://joinditto.in/articles/health-insurance/aditya-birla-](https://joinditto.in/articles/health-insurance/aditya-birla-health-insurance-claim-settlement-ratio/) |

## Reddit / r/IndianFinance sentiment

- Overall: **mixed-positive**
- Mentions estimate: 28
- Themes: Activ One plan well-received for wellness benefits, HealthReturns wellness program (premium discount for healthy behavior) is differentiator, 100% of claims paid within 3 months in FY25 — operational efficiency strong, Aditya Birla Capital brand trust, 10,000+ cashless network (tied 2nd with Niva/Tata)
- Sample posts:
  - [https://www.policybazaar.com/health-insurance/companies/aditya-birla-vs-tata-aig](https://www.policybazaar.com/health-insurance/companies/aditya-birla-vs-tata-aig/)
  - [https://www.policybazaar.com/health-insurance/comparison/aditya-birla-activ-one-](https://www.policybazaar.com/health-insurance/comparison/aditya-birla-activ-one-vs-tata-aig-medicare/)

## YouTube coverage

- Overall sentiment: **favourable**
- **Ditto Insurance** — [https://joinditto.in/articles/health-insurance/aditya-birla-health-insurance-cla](https://joinditto.in/articles/health-insurance/aditya-birla-health-insurance-claim-settlement-ratio/) — _ranked in Ditto's top-5 health insurers_

## Recent news

- **Aditya Birla Health Insurance Claims Settlement Ratio Analysis for 2024-25** (Angel One, 2025, tone: positive) — [https://www.angelone.in/news/market-updates/aditya-birla-health-insurance-claims](https://www.angelone.in/news/market-updates/aditya-birla-health-insurance-claims-settlement-ratio-analysis-for-2024-25)
- **Aditya Birla Health Insurance paid Rs 952 crore in commissions in April-December 2025 (growing scale)** (Cafemutual, 2026, tone: neutral) — [https://cafemutual.com/news/insurance/37646-how-much-did-the-non-life-insurers-p](https://cafemutual.com/news/insurance/37646-how-much-did-the-non-life-insurers-pay-in-commission-in-april-december-2025)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `40-data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.