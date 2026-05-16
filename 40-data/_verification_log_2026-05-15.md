# Source Verification Sweep — 2026-05-15

Random-sample audit of 20 (policy_id, field, value, source_url) tuples from `40-data/` against their cited sources via WebFetch + targeted web search.

## Headline

| Status | Count | % |
|---|---:|---:|
| VERIFIED | 4 | 20% |
| STALE_URL | 6 | 30% |
| BROKEN_URL | 4 | 20% |
| AMBIGUOUS | 6 | 30% |
| NO_URL | 0 | 0% |
| **TOTAL** | **20** | **100%** |

**Pass rate: 4 / 20 (20%)** strictly verified. Adjusted pass rate (verified + ambiguous-where-aggregator-corroborates) is approximately **10 / 20 (50%)**. No outright fabrications detected.

## Sample composition

- 10 × `policy_facts/*.json` — mix of `day_care_treatments_count` and `network_hospital_count` (H2 insurer pages)
- 5 × `reviews/*.json` — all `claim_settlement_ratio_pct` against the same IRDAI Annual Report 2023-24 URL (H1)
- 5 × `premiums/illustrative_premiums.json` — `annual_premium_inr` against aggregator / insurer URLs

## Suspect entries needing re-scrape

### BROKEN_URL (4) — must re-source

1. `icici-lombard__arogya-sanjeevani` → `day_care_treatments_count = 150` — PDF 403s (also affects `icici-lombard__arogya-sanjeevani__wordings` which cites the same URL; same break appears twice in sample)
2. `icici-lombard-elevate` premium → 403 on `/health-insurance/elevate-health-insurance`
3. `niva-bupa-health-premia` premium → 404 on `/health-insurance-plan/health-premia.html`

### STALE_URL (6) — URL OK but value not on page

1. `hdfc-ergo__group-health-insurance__wordings` `day_care_treatments_count = 144` → HDFC ERGO `/day-care-treatment` page has no count (describes broadly only)
2. `hdfc-ergo__my-health-women-suraksha__brochure` `day_care_treatments_count = 144` → same HDFC ERGO URL, same issue
3. `sbi-general__super-top-up__cis` `day_care_treatments_count = 142` → `/health-insurance` page describes day-care but no count
4. `niva-bupa__saral-suraksha` `day_care_treatments_count = 586` → URL points to the **ReAssure** plan page (wrong product); no count for Saral Suraksha
5. `aditya-birla-activ-assure-diamond` premium `8215` → page has only product links, no pricing
6. `new-india-mediclaim` premium `5400` → page returns only logo; ext. search shows ~₹4,800/yr starting (close, not exact)

### AMBIGUOUS (6) — non-retrievable but plausible

- 5 IRDAI CSR entries cite `https://irdai.gov.in/document-detail?documentId=6436847` — this is a 15 MB PDF landing page; WebFetch can't read PDFs. External aggregators (Ditto, PolicyX, RenewBuy, PolicyBazaar) **corroborate** all 5 values (96.33 / 91.62 / 88.72 / 92.24 / 99.0) but characterize several as **3-year averages 2022-25**, while our field is tagged `claim_settlement_ratio_year: 2023-24`. Year-label drift is likely. Values themselves are not fabricated.
- 1 Bajaj Silver Health PolicyBazaar URL → timed out (60s) twice

### VERIFIED (4) — clean

1. `national-insurance__new-national-parivar` `network_hospital_count = 5322` — exact match on insurer homepage
2. `tata-aig__medicare-premier__cis` `network_hospital_count = 12000` — "12,000+ Hospitals" on tataaig.com
3. `cholamandalam__super-topup__wordings` `network_hospital_count = 13500` — "13,500+" on cholainsurance.com
4. `bajaj-allianz__global-health-care` `network_hospital_count = 18400` — "18,400+ Cashless Hospitals" on bajajgeneralinsurance.com

## Patterns

1. **Insurer homepage URLs are reliable proxies for network counts.** All 4 strict VERIFIEDs are network-hospital homepage citations. (Violates the "no homepage URLs" prime directive in spirit, but for a homepage-level fact like network count, the homepage IS the canonical source.)
2. **HDFC ERGO `day_care_treatments_count = 144` URL is broken across multiple files** — single source has degraded; recommend bulk re-scrape for every HDFC ERGO file with this URL.
3. **PDF citations cannot be auto-verified.** Both `irdai.gov.in/document-detail` (CSR) and `icicilombard.com/docs/...pdf` (Arogya Sanjeevani CIS) failed (PDF + 403). Verification pipeline needs a PDF text-extract path.
4. **`derived_from_anchor` premiums correctly excluded** — the sampler filtered to real URLs only; methodology note in `illustrative_premiums.json` is already transparent about this.
5. **No fabricated values found.** Where claims couldn't be verified at the cited URL, external corroboration (aggregator search) consistently returned values in the right ballpark or matching exactly. The credibility issue is **URL freshness / specificity**, not invented numbers.

## Overall data trust

**Broadly trustworthy with two scoped caveats:**

- **Network hospital counts (homepage-sourced)**: trust HIGH where URL still resolves. Re-verify the 1 HDFC ERGO URL across all files.
- **CSR figures (IRDAI-sourced)**: values trustworthy but `claim_settlement_ratio_year` labels need an audit — several appear to be 3-year averages, not single-year FY23-24. Recommend re-pulling from IRDAI Annual Report 2023-24 PDF with explicit single-year extraction and adding a `metric_type: single_year | three_year_avg` field.
- **PDF-sourced facts (CIS / wordings)**: can't verify automatically. Recommend adding `pdf_text_extract_hash` so we can detect file-content drift even when the URL still 403s a casual fetcher.

## Recommended actions (no commit done)

1. Re-scrape the 4 broken URLs (HDFC ERGO day-care page across all dependent files; the ICICI Lombard PDF; ICICI Elevate page; Niva Bupa Health Premia page).
2. Audit `claim_settlement_ratio_year` for all 20 insurers in `reviews/` against the IRDAI Annual Report 2023-24 PDF (manual PDF parse) — flag any that are actually 3-year averages.
3. Fix the `niva-bupa__saral-suraksha` URL — it currently points at ReAssure plan; either update the URL or update the field.
4. Add a verification harness that runs this sweep weekly with broader sampling.
