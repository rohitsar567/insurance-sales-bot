# ManipalCigna Health Insurance — Reputation Sheet

_Auto-generated from `data/reviews/manipalcigna.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **76.3** (B). _High reported CSR (99%) and Manipal-hospital network advantage; smaller scale and above-median complaints temper the score_

**URL verification:** 8/8 URLs reachable via HEAD-check; 0 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **99.0%** (2023-24) |
| Complaints / 10K policies | **24** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.3 | None | [https://www.policybazaar.com/insurance-companies/manipalcign](https://www.policybazaar.com/insurance-companies/manipalcigna-health-insurance/claim-settlement-ratio/) |
| insuredekho | 4.2 | None | [https://www.insurancedekho.com/health-insurance/manipalcigna](https://www.insurancedekho.com/health-insurance/manipalcigna/claim-settlement) |
| joinditto | 4.0 | None | [https://joinditto.in/articles/health-insurance/manipal-cigna](https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-review/) |

## Reddit / r/IndianFinance sentiment

- Overall: **mixed**
- Mentions estimate: 18
- Themes: Prohealth Plus / Prime products discussed favorably for comprehensive coverage, Manipal Hospitals network synergy a structural advantage, Smaller scale = less consumer mindshare than top-5, Complaints (24 per 10K) above median but below Care/Niva Bupa/Star, Cigna global brand association
- Sample posts:
  - [https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-re](https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-review/)
  - [https://www.policyx.com/health-insurance/manipalcigna-health-insurance/claim-set](https://www.policyx.com/health-insurance/manipalcigna-health-insurance/claim-settlement-ratio/)

## YouTube coverage

- Overall sentiment: **mixed**
- **Ditto Insurance review** — [https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-re](https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-review/) — _competent option; not top recommendation but solid for niche profiles_

## Recent news

- **ManipalCigna ICR at 74.81% in FY24-25 — within healthy 70-90% comfort range** (Cafemutual, 2024-12, tone: neutral) — [https://cafemutual.com/news/insurance/33084-which-companies-are-better-at-settli](https://cafemutual.com/news/insurance/33084-which-companies-are-better-at-settling-health-claims)
- **ManipalCigna claim settlement ratio analysis** (RenewBuy, 2024, tone: neutral) — [https://www.renewbuy.com/health-insurance/manipalcigna-health-insurance/claim-se](https://www.renewbuy.com/health-insurance/manipalcigna-health-insurance/claim-settlement-ratio)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.