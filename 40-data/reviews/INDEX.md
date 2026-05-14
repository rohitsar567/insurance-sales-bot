# Insurer Reputation Leaderboard

**Last updated:** 2026-05-13
**Data source:** IRDAI Annual Report 2023-24 (primary), aggregator portals, Reddit / Indian finance communities, YouTube, news media
**Scoring formula:** `0.40 * CSR + 0.20 * (100 - min(complaints_per_10k, 50)*2) + 0.15 * (avg_star*20) + 0.10 * reddit_sentiment + 0.10 * youtube_sentiment + 0.05 * (news_positive / news_total * 100)`
**Letter grade:** A ≥85, B ≥70, C ≥55, D ≥40, F <40

---

## Ranked leaderboard

| Rank | Insurer | Slug | Score | Grade | CSR (FY24) | Complaints / 10K | Headline |
|------|---------|------|-------|-------|------------|------------------|----------|
| 1 | HDFC ERGO General Insurance | hdfc-ergo | **85.7** | A | 99.1% | 15 | Industry-leading CSR, strong YouTube/advisor recommendation |
| 2 | Bajaj Allianz General Insurance | bajaj-allianz | **84.5** | B | 92.2% | 3 | Lowest complaint volume in the cohort; third-largest non-life insurer |
| 3 | Aditya Birla Health Insurance | aditya-birla | **81.2** | B | 93.0% | 13 | Strong CSR, low complaints, HealthReturns wellness differentiator |
| 4 | New India Assurance | new-india | **78.6** | B | 95.0% | 20 | Highest claim payout among general insurers; PSU service experience drags consumer score |
| 5 | ManipalCigna Health Insurance | manipalcigna | **76.3** | B | 99.0% | 24 | High CSR; smaller scale and above-median complaints |
| 6 | Niva Bupa Health Insurance | niva-bupa | **75.8** | B | 91.6% | 43 | Solid CSR, IPO momentum; complaints above industry average |
| 7 | Tata AIG General Insurance | tata-aig | **75.6** | B | 88.7% | 11 | Strong brand & global-coverage products; CSR slightly below leaders |
| 8 | ICICI Lombard General Insurance | icici-lombard | **73.5** | B | 85.0% | 10 | Strongest complaint metrics; health CSR lags HDFC/Bajaj |
| 9 | Care Health Insurance | care-health | **70.4** | B | 93.1% (3yr avg) | 42 | Solid headline CSR but bottom-of-table ICR (57.7%) and complaints volume |
| 10 | Star Health & Allied Insurance | star-health | **60.4** | C | 82.3% | 52 | India's largest standalone health insurer but under IRDAI scrutiny for highest complaints (13,308 FY24) |

---

## Key findings

1. **HDFC ERGO** is the clearest top pick on every objective metric: 99.1% CSR, 15 complaints per 10K, near-universal advisor recommendation (Ditto, YouTube comparison videos).
2. **Bajaj Allianz** is the dark horse — best-in-class complaint metrics (3 per 10K, industry-leading), AAA credit rating, but lower retail-health brand awareness than HDFC / Star.
3. **Star Health** is the cautionary outlier: under live IRDAI scrutiny (December 2024 show-cause notice), highest complaints volume of any private insurer (13,308 in FY24), and the lowest standalone-health CSR (82.3%).
4. **Standalone health insurers (Star, Niva Bupa, Care, Aditya Birla, ManipalCigna)** generally have higher complaint volumes than general insurers (HDFC ERGO, Bajaj, ICICI Lombard, Tata AIG, New India), consistent with the IRDAI sector trend (standalone segment 63.6% incurred claims ratio vs 82.5% sector-wide).
5. **PSU New India Assurance** has the highest payout discipline (ICR 103%, paying out more than premium collected), but PSU-style branch + paperwork friction is the dominant Reddit complaint.

---

## Data quality notes

- IRDAI 2023-24 Annual Report (the single authoritative source) is referenced in every file under `claim_metrics.source_irdai_url`.
- `complaints_per_10k_policies` for several insurers is the 3-year average (FY22-FY25) rather than the strict FY24 single-year figure, because the underlying IRDAI portal disclosures aggregate three-year history for the consumer-friendly metric. This is flagged in each JSON's `complaints_year` field.
- Trustpilot Indian-customer coverage is thin; most Indian customers review via Policybazaar / InsureDekho / Mouthshut. Trustpilot scores are largely populated by negative-experience escalations, so they skew low. Use with care.
- News tone is editorial classification by this aggregator, not the publication's own tagging.
- Reddit `mentions_last_year_estimate` is an approximate eyeball from search results, not a true API count.

## Sources (representative)

- IRDAI Annual Report 2023-24: https://irdai.gov.in/document-detail?documentId=6436847
- Business Standard FY24 complaints round-up: https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html
- Policyx data lab: https://www.policyx.com/data-lab/claim-settlement-ratio-insurance-companies-India.php
- Policybazaar CSR table: https://www.policybazaar.com/health-insurance/claim-settlement-ratio/
- Ditto Insurance methodology + reviews: https://joinditto.in/health-insurance/
- The South First (FY24 payout analysis): https://thesouthfirst.com/health/health-insurance-claims-standalone-insurers-pay-rs-3-07-per-rs-5-claim-general-insurers-offer-rs-4-17/
