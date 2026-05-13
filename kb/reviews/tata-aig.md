# Tata AIG General Insurance — Reputation Sheet

_Auto-generated from `data/reviews/tata-aig.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **75.6** (B). _Solid mid-tier player: 88.7% CSR, low complaints (~11 per 10K), strong brand & global-coverage products_

**URL verification:** 9/9 URLs reachable via HEAD-check; 0 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **88.72%** (2023-24 (3-year avg)) |
| Complaints / 10K policies | **11** (2023-24 (3-year avg ~10.65)) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.3 | None | [https://www.policybazaar.com/insurance-companies/tata-aig-he](https://www.policybazaar.com/insurance-companies/tata-aig-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.4 | None | [https://www.insurancedekho.com/health-insurance/tata-aig/cla](https://www.insurancedekho.com/health-insurance/tata-aig/claim-settlement) |
| joinditto | 4.2 | None | [https://joinditto.in/articles/health-insurance/tata-aig-heal](https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-claim-settlement-ratio/) |

## Reddit / r/IndianFinance sentiment

- Overall: **mixed-positive**
- Mentions estimate: 22
- Themes: MediCare Plus and MediCare Premier well-regarded for global coverage, Tata brand trust, Below-industry-average CSR is the main concern, Service standards rated above average; GWP grew from Rs 1,930 cr (FY22) to Rs 3,592 cr (FY25), 10,000+ cashless network
- Sample posts:
  - [https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/ta](https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/tata-aig-medi-care/)
  - [https://www.policybazaar.com/health-insurance/companies/aditya-birla-vs-tata-aig](https://www.policybazaar.com/health-insurance/companies/aditya-birla-vs-tata-aig/)

## YouTube coverage

- Overall sentiment: **mixed**
- **1Finance** — [https://1finance.co.in/product-scoring/health-insurance/tata-aig-medicare](https://1finance.co.in/product-scoring/health-insurance/tata-aig-medicare) — _MediCare scored on product features; recommended for premium segment_

## Recent news

- **Tata AIG GWP grew from Rs 1,930 cr (FY22) to Rs 3,592 cr (FY25), strong YoY growth** (Ditto Insurance, 2026, tone: positive) — [https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-claim-s](https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-claim-settlement-ratio/)
- **Tata AIG MediCare Premier plan launches** (Beshak, 2026-04, tone: neutral) — [https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/ta](https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/tata-aig-medi-care-premier/)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.