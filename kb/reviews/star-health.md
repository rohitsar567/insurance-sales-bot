# Star Health & Allied Insurance — Reputation Sheet

_Auto-generated from `40-data/reviews/star-health.json`. Reviews are the v1 substitute for live regulator + sentiment monitoring. Re-build with `python -m rag.build_kb`._

**Aggregate score:** **60.4** (C). _India's largest standalone health insurer by premium, but under IRDAI scrutiny for the sector's highest complaint volume (13,308 in FY24) and lowest CSR (82.3%) among standalone insurers_

**URL verification:** 11/13 URLs reachable via HEAD-check; 2 return 403 to scripts (bot-protected real URLs — open fine in a browser).

## IRDAI claim metrics

| Metric | Value |
| --- | --- |
| Claim settlement ratio (CSR) | **82.31%** (2023-24) |
| Complaints / 10K policies | **52** (2023-24) |
| Source | [IRDAI Annual Report](https://irdai.gov.in/document-detail?documentId=6436847) |

## Aggregator portal ratings

| Portal | Avg star | Review count | URL |
| --- | --- | --- | --- |
| policybazaar | 4.3 | None | [https://www.policybazaar.com/star-health-insurance-reviews-8](https://www.policybazaar.com/star-health-insurance-reviews-881/) |
| insuredekho | 4.4 | 403 | [https://www.insurancedekho.com/health-insurance/star/user-re](https://www.insurancedekho.com/health-insurance/star/user-reviews) |
| mouthshut | 2.0 | None | [https://www.mouthshut.com/product-reviews/star-health-insura](https://www.mouthshut.com/product-reviews/star-health-insurance-reviews-925865246) |

**Trustpilot:** 1.5 (None reviews) — [https://www.trustpilot.com/review/www.starhealth.in](https://www.trustpilot.com/review/www.starhealth.in)

## Reddit / r/IndianFinance sentiment

- Overall: **mixed-negative**
- Mentions estimate: 60
- Themes: Claim rejections on PED grounds frequently mentioned, Senior citizen Red Carpet plan recommended despite service complaints, Wide network and senior citizen coverage praised, IRDAI scrutiny of claim practices widely discussed, Highest complaint volume in standalone health insurer category
- Sample posts:
  - [https://www.quora.com/Is-Star-Health-Insurance-good-Has-anybody-experienced-prob](https://www.quora.com/Is-Star-Health-Insurance-good-Has-anybody-experienced-problems-in-claim-settlement)
  - [https://technofino.in/community/threads/star-health-insurance-renew-or-port.3518](https://technofino.in/community/threads/star-health-insurance-renew-or-port.35180/)

## YouTube coverage

- Overall sentiment: **mixed**
- **Ditto Insurance** — [https://www.youtube.com/watch?v=0tiEsznD61I](https://www.youtube.com/watch?v=0tiEsznD61I) — _comparison Super Star vs HDFC Ergo Optima Secure; Ditto prefers HDFC ERGO for reliability_
- **Ditto Insurance (review)** — [https://joinditto.in/articles/health-insurance/star-health-insurance-review/](https://joinditto.in/articles/health-insurance/star-health-insurance-review/) — _suitable for senior citizens; concerns on claim experience_

## Recent news

- **Star Health under IRDAI lens over claim settlement; insurer says routine process** (Business Today, 2025-03-25, tone: negative) — [https://www.businesstoday.in/personal-finance/insurance/story/star-health-under-](https://www.businesstoday.in/personal-finance/insurance/story/star-health-under-irdai-lens-over-claim-settlement-insurer-says-routine-process-469373-2025-03-25)
- **Star Health Under Fire: IRDAI Scrutinizes High Claim Rejections & Delays** (The420.in, 2025-04, tone: negative) — [https://the420.in/star-health-under-fire-irdai-scrutinizes-high-claim-rejections](https://the420.in/star-health-under-fire-irdai-scrutinizes-high-claim-rejections-delays/)
- **Star Health, CARE, Niva Bupa record most policyholder complaints in FY24** (Business Standard, 2025-09-01, tone: negative) — [https://www.business-standard.com/finance/personal-finance/star-health-care-niva](https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html)
- **Star Health Under Irdai Scrutiny For Health Insurance Claim Settlement Practices** (Outlook Money, 2025-04, tone: negative) — [https://www.outlookmoney.com/insurance/health-insurance/star-health-under-irdai-](https://www.outlookmoney.com/insurance/health-insurance/star-health-under-irdai-scrutiny-for-health-insurance-claim-settlement-practices)

---

_Aggregate score formula: 0.40 × CSR + 0.20 × inverse-complaints + 0.15 × avg-aggregator-star + 0.10 × reddit + 0.10 × youtube + 0.05 × news. See `40-data/reviews/INDEX.md` for the leaderboard._

**Flows into the bot via:** `score_claim_experience()` in `backend/scorecard.py` — IRDAI CSR + complaints become Claim Experience sub-score signals for every policy this insurer offers.