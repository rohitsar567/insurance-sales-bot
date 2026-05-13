# Reviews — Insurer Reputation Index

_Auto-generated. Source: `data/reviews/*.json`. Per-insurer sheets in `kb/reviews/<slug>.md`._

## Leaderboard

| Rank | Insurer | Score | Grade | CSR | Complaints/10K | URL verification |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [HDFC ERGO General Insurance](./hdfc-ergo.md) | **85.7** | A | 99.1% | 15 | 16/17 reachable |
| 2 | [Bajaj Allianz General Insurance](./bajaj-allianz.md) | **84.5** | B | 92.24% | 3 | 11/11 reachable |
| 3 | [Aditya Birla Health Insurance](./aditya-birla.md) | **81.2** | B | 92.97% | 13 | 9/9 reachable |
| 4 | [New India Assurance](./new-india.md) | **78.6** | B | 95.04% | 20 | 7/8 reachable |
| 5 | [ManipalCigna Health Insurance](./manipalcigna.md) | **76.3** | B | 99.0% | 24 | 8/8 reachable |
| 6 | [Niva Bupa Health Insurance](./niva-bupa.md) | **75.8** | B | 91.62% | 43 | 14/17 reachable |
| 7 | [Tata AIG General Insurance](./tata-aig.md) | **75.6** | B | 88.72% | 11 | 9/9 reachable |
| 8 | [ICICI Lombard General Insurance](./icici-lombard.md) | **73.5** | B | 85.0% | 10 | 7/10 reachable |
| 9 | [Care Health Insurance](./care-health.md) | **70.4** | B | 93.13% | 42 | 11/13 reachable |
| 10 | [Star Health & Allied Insurance](./star-health.md) | **60.4** | C | 82.31% | 52 | 11/13 reachable |

## Bot integration

- API: `GET /api/insurers/<slug>/reviews`
- The IRDAI CSR + complaints per 10K from this data feeds the **Claim Experience** sub-score of the scorecard (see `kb/policies/<id>.md` for the per-policy effect).
- v2 expansions: live Reddit/YouTube sentiment refresh, IRDAI weekly refresh, news monitoring with alerts on insurer-specific incidents.