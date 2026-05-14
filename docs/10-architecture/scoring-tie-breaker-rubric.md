# Tie-Breaker Rubric — When Policies Score Equal

The scorecard's 0–100 grade collapses 6 sub-scores. Two policies can land at the same overall score with very different shapes. This document defines the **objective tie-breakers** the system applies when overall scores are within ±2 points, ordered by buyer impact.

The intent: never tell a buyer "these are equal" — always have a defensible objective reason one wins.

---

## Tier 1 — Insurer Quality (applies across portfolio)

These metrics belong to the insurer, not the policy. If two policies come from different insurers, these dominate.

| # | Metric | Source | Tie-breaker rule |
|---|---|---|---|
| 1 | **Claim Settlement Ratio** (most recent FY) | IRDAI Annual Report | Higher wins. Cutoff: <90% disqualifies regardless of other strengths. |
| 2 | **Complaints per 10,000 policies** | IRDAI Grievance Statistics | Lower wins. The insurer's own published metric reveals operational quality. |
| 3 | **Repudiation rate** | IRDAI public data | Lower wins. A 5% point swing here matters more than a 5% swing in CSR. |
| 4 | **Average claim turnaround time** | Insurer's IRDAI filings | Faster wins. Cashless TAT ≤2h is the gold standard. |

---

## Tier 2 — Network Quality

| # | Metric | Tie-breaker rule |
|---|---|---|
| 5 | **Cashless network density in user's city** | More hospitals within 10km radius wins. Not raw count — geographic density. |
| 6 | **Quality of top-3 hospitals in network** | If both networks include AIIMS / Apollo / Manipal / Fortis flagship branches, equivalent. If one excludes top-tier, the other wins. |
| 7 | **Network depth in tier-2/3 cities** | For non-metro users, presence in town's biggest 2 hospitals is decisive. |

---

## Tier 3 — Cost-vs-Cover Trade Quality

| # | Metric | Tie-breaker rule |
|---|---|---|
| 8 | **Price per ₹L of sum insured** at user's age | Lower wins. Calculated from real premium quotes for matching profile. |
| 9 | **NCB accrual pace + cap** | Higher pace AND higher cap wins. E.g., 50%/year capped at 100% beats 25%/year capped at 50% for buyers who stay claim-free. |
| 10 | **Restoration benefit liquidity** | "Unlimited automatic restoration" beats "one-time per year" beats "available on full exhaustion only". |
| 11 | **PED waiting reduction options** | Some policies let you pay extra to drop PED waiting from 36→24 months. That option is itself valuable for diabetic / hypertensive buyers. |

---

## Tier 4 — Customer Experience Signals

These come from outside the policy wording — Reddit + MouthShut + InsuranceDekho ratings.

| # | Metric | Tie-breaker rule |
|---|---|---|
| 12 | **Reddit sentiment skew** (last 12 months) | "Mostly positive" beats "Mixed" beats "Mostly negative" claim-time stories. |
| 13 | **MouthShut / PolicyBazaar star rating** | Higher wins. Volume matters — a 4.6 over 500 reviews beats a 4.8 over 12 reviews. |
| 14 | **Specific named-creator coverage on YouTube** | If trusted creators (Ditto Insurance, Beshak, Subhanker Saha) have reviewed and rated positively, that's a tie-breaker. |
| 15 | **Press: regulatory actions in last 24 months** | Any IRDAI show-cause notice in last 24 months breaks the tie negative. |

---

## Tier 5 — Specialised Match

Applies only when the user has a specific need flagged in their profile.

| Profile flag | Tie-breaker |
|---|---|
| Maternity planned next 24 months | Policy with ≤24-month maternity wait wins. |
| Surgery planned within 12 months | Policy with no specific-disease wait for that condition wins. |
| AYUSH preference | Policy with explicit AYUSH coverage limits stated (vs "up to SI") wins. |
| Senior parents | Policy that allows porting in mid-term + has senior-specific rider wins. |
| Diabetic buyer | Policy with day-1 diabetes coverage option (e.g., HDFC Energy) wins for that buyer even if generic score lower. |

---

## Application order

When the system needs to break a tie between two policies:

1. Walk through Tier 1 first — insurer quality is the most predictive single metric of claim experience.
2. If still tied (same insurer, two products), drop to Tier 2 (network).
3. Continue down the tiers.
4. **If still tied after all 5 tiers, the tie is genuine** — surface both and let the user pick on subjective preference (brand, ease of website, app reviews, etc.).

The "all-else-equal" framing is honest: in reality, two health policies rarely tie. Surfacing the tiered breakdown gives the buyer a defensible reason for the recommendation order.

---

## What we explicitly DON'T use as tie-breakers

- **Brand recognition** alone. "I've heard of HDFC ERGO" is not a tie-breaker.
- **Commission rate** to the broker / aggregator. The whole platform is built on not letting this influence ranking.
- **Recency of policy launch.** New ≠ better.
- **Glossy marketing** ("award-winning", "most trusted", etc.). Awards are often paid placements.

---

## Implementation contract

The tie-breaker logic lives at `backend/scorecard.py::tie_break(policy_a, policy_b, profile)` (TBD — to be implemented). It returns a structured comparison: `{winner, reason_tier, reason_text, source}`. The frontend renders this when two policies have grades within 2 points and the user has both selected for comparison.
