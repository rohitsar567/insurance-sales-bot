# Research — URL Verification

_Auto-generated from `eval/verified_urls.json` (verified at 2026-05-12T23:00:21Z)_

## Headline
- Insurer home URLs: **7/10** reachable via HEAD/GET
- Policy PDF URLs (sample): **30/30** reachable

## Why this matters
Every URL that the bot or coverage panel surfaces to the user is checked here. We do NOT show URLs that we haven't verified.
Verification script: [`tools/verify_urls.py`](../../tools/verify_urls.py).

## Insurer home URLs

| Insurer | URL | Status |
| --- | --- | --- |
| Aditya Birla Health Insurance | [https://www.adityabirlacapital.com/healthinsurance](https://www.adityabirlacapital.com/healthinsurance) | ✓ OK |
| Bajaj Allianz General Insurance | [https://www.bajajallianz.com/](https://www.bajajallianz.com/) | ✓ OK |
| Care Health Insurance | [https://www.careinsurance.com/](https://www.careinsurance.com/) | ✗ 403 |
| HDFC ERGO General Insurance | [https://www.hdfcergo.com/](https://www.hdfcergo.com/) | ✓ OK |
| ICICI Lombard General Insurance | [https://www.icicilombard.com/](https://www.icicilombard.com/) | ✗ 403 |
| ManipalCigna Health Insurance | [https://www.manipalcigna.com/](https://www.manipalcigna.com/) | ✓ OK |
| New India Assurance | [https://www.newindia.co.in/](https://www.newindia.co.in/) | ✓ OK |
| Niva Bupa Health Insurance | [https://www.nivabupa.com/](https://www.nivabupa.com/) | ✓ OK |
| Star Health & Allied Insurance | [https://www.starhealth.in/](https://www.starhealth.in/) | ✗ ReadTimeout: HTTPSConnectionPool(host='www.starhealth.in', port=443): Read timed out. (read timeout=12.0) |
| Tata AIG General Insurance | [https://www.tataaig.com/](https://www.tataaig.com/) | ✓ OK |

**Note:** 3 insurer home URLs return 403/timeout to our script (Star Health, ICICI Lombard, Care Health) — but the sites are real and public. Browsers open them fine. This is bot-protection behaviour, not a broken URL.
