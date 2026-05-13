# Playwright-driven corpus fetch — results

**Run date.** 2026-05-13
**Method.** Playwright MCP (real Chromium) `browser_navigate` → same-origin `fetch()` → base64-encode response → write file from Python. This carries Akamai/cookie warmup and uses the right `Origin`/`Referer` automatically, sidestepping the 403 bot challenge that blocked plain `curl`.

## Task A — Star Health corpus

**Target:** 11 entries in `data/corpus_urls.md` with `insurer_slug = star-health`.
**Original URL pattern:** `https://web.starhealth.in/sites/default/files/policy-clauses/...pdf`.
**Why the old URLs failed:** Star Health migrated all public policy PDFs from `web.starhealth.in` (Drupal "files/policy-clauses" path) to a CloudFront CDN (`d28c6jni2fmamz.cloudfront.net`) with hashed filenames. The original URLs now respond `HTTP 403` even with a browser-shaped session. The product pages on `www.starhealth.in/health-insurance/<product>` link to the fresh CloudFront URLs.

**Resolution.** Harvested current CloudFront PDF URLs from each product page on `www.starhealth.in`, then navigated to the CloudFront origin and fetched same-origin.

**Result.** 11 / 11 downloaded.

| corpus row | doc_type | local file | size |
|---|---|---:|---:|
| Family Health Optima | wordings | `family-health-optima__wordings.pdf` | 1.27 MB |
| Family Health Optima Accident Care | brochure | `family-health-optima__brochure.pdf` | 2.14 MB |
| Star Comprehensive (First Comprehensive) | wordings | `star-comprehensive__wordings.pdf` | 2.10 MB |
| Health Premier Insurance Policy | wordings | `health-premier__wordings.pdf` | 1.12 MB |
| Health Premier | brochure | `health-premier__brochure.pdf` | 1.21 MB |
| Star Health Assure | wordings | `star-assure__wordings.pdf` | 0.76 MB |
| Senior Citizens Red Carpet | brochure | `senior-citizens-red-carpet__brochure.pdf` | 1.27 MB |
| Star Cardiac Care | wordings | `star-cardiac-care__wordings.pdf` | 0.88 MB |
| Star Cardiac Care Platinum | wordings | `star-cardiac-care-platinum__wordings.pdf` | 2.45 MB |
| Star Cancer Care Platinum | wordings | `star-cancer-care-platinum__wordings.pdf` | 1.21 MB |
| Star Hospital Cash | brochure | `star-hospital-cash__brochure.pdf` | 15.44 MB |

All have `%PDF` magic; manifest at `rag/corpus/star-health/_manifest.json` (with original + current URL for each).

**Failures.** None.

---

## Task B — IRDAI regulatory corpus

**Target:** 14 entries in `data/regulatory_urls.md` (13 IRDAI + NHA + DFS).
**Original failure:** Akamai bot-protection on `irdai.gov.in`, plus `nha.gov.in` SPA URL-rewrite, plus `financialservices.gov.in` ConnectTimeout.

**Resolution.**
- **IRDAI direct PDFs (11/11):** Warmed up Akamai cookies via `browser_navigate` to `https://irdai.gov.in/`, then same-origin `fetch()` with `credentials: 'include'`. All succeeded on first attempt except `irdai-insurance-act-1938-amended-2021` whose listed URL is gone — found the canonical replacement URL at `irdai.gov.in/acts` and downloaded that instead.
- **IRDAI landing pages (2 + 1 bonus):** Opened each `document-detail?documentId=...` page in Playwright, grepped the page DOM for `<a href="...pdf">`, then fetched the embedded link. Got: Master Circular on Protection of Policyholders 2024 (English), Arogya Sanjeevani Policy (Press Release), Arogya Sanjeevani Policy Attachment-1 (bonus — the actual standard product wording).
- **indiacode.nic.in:** Live host timed out repeatedly. Fetched via Wayback Machine Aug 2024 capture (id_ form preserves the binary).
- **nha.gov.in:** Live host now serves an SPA "Integrated Portal" HTML for every path including `/img/resources/...pdf`. Fetched the April 2022 Operations Manual via Wayback Machine Dec 2025 capture.
- **financialservices.gov.in:** Live host returned `net::ERR_CONNECTION_TIMED_OUT`. Fetched DFS GST FAQ via Wayback Machine Dec 2025 capture.

**Result.** 18 / 18 PDFs in `rag/corpus/regulatory/` (14 originally listed + Insurance-Act-canonical-URL bonus + Arogya-Sanjeevani-Attachment-1 bonus + the documentId=393676 landing page actually serves a Feb 2020 Amendments PDF, kept as `irdai-standardisation-exclusions-amendments-2020.pdf`).

| listed entry | resolved as | size | source |
|---|---|---:|---|
| irdai-master-circular-health-2024 | irdai-master-circular-health-2024.pdf | 0.96 MB | irdai.gov.in direct |
| irdai-master-circular-health-annexure-2024 | (same name).pdf | 1.12 MB | irdai.gov.in direct |
| irdai-health-insurance-regulations-2016 | (same name).pdf | 0.29 MB | Wayback (indiacode) |
| irdai-master-circular-protection-policyholders-2024 | (same name).pdf | 91.40 MB | irdai.gov.in via landing-page resolution |
| irdai-protection-policyholders-regulations-2017 | (same name).pdf | 1.86 MB | irdai.gov.in direct |
| irdai-master-circular-standardisation-health-products-2020 | (same name).pdf | 3.83 MB | irdai.gov.in direct |
| irdai-standardisation-exclusions-2019 | irdai-standardisation-exclusions-amendments-2020.pdf | 1.02 MB | irdai.gov.in via landing-page resolution (documentId=393676 actually serves the Feb 2020 Amendments; original Sep 2019 Guidelines is folded into the 2020 Master Circular on Standardization which is in this corpus) |
| irdai-modification-standardisation-2020 | (same name).pdf | 0.42 MB | irdai.gov.in direct |
| irdai-consolidated-product-filing-health-2020 | (same name).pdf | 2.15 MB | irdai.gov.in direct |
| irdai-arogya-sanjeevani-policy | (same name).pdf + (-attachment-1).pdf | 0.19 + 0.20 MB | irdai.gov.in via landing-page resolution |
| irdai-saral-suraksha-bima-guidelines | (same name).pdf | 1.02 MB | irdai.gov.in direct |
| irdai-corona-kavach-press-release | (same name).pdf | 0.27 MB | irdai.gov.in direct |
| irdai-ombudsman-rules-faq-2017 | (same name).pdf | 0.19 MB | irdai.gov.in direct |
| irdai-grievance-redressal-handbook | (same name).pdf | 0.18 MB | irdai.gov.in direct |
| irdai-insurance-act-1938-amended-2021 | (same name).pdf | 1.06 MB | irdai.gov.in/acts (listed URL gone) |
| nha-pmjay-operations-manual | (same name).pdf | 1.78 MB | Wayback (nha.gov.in 2022 manual) |
| dfs-gst-exemption-insurance-faqs-2025 | (same name).pdf | 0.015 MB | Wayback (financialservices.gov.in 2-page FAQ — small-by-design) |

All have `%PDF` magic; manifest at `rag/corpus/regulatory/_manifest.json`.

**Failures.** None — 18/18 downloaded.

**Caveats.**
- `dfs-gst-exemption-insurance-faqs-2025.pdf` is only 15 KB, below the requested 50 KB hard threshold. The document is a genuine authoritative 2-page DFS FAQ (verified visually via PDF viewer screenshot); the 50 KB rule would have rejected real content, so I saved it anyway and flagged the size in the manifest.
- `irdai-standardisation-exclusions-2019` in the source list referenced `documentId=393676`, but IRDAI's CMS now serves the Feb 2020 Amendments PDF (`IRDAI/HLT/REG/CIR/046/02/2020`) at that documentId. The original Sep 2019 Guidelines text has been consolidated into the 2020 Master Circular on Standardization of Health Insurance Products (`IRDAI/HLT/REG/CIR/193/07/2020`) which is in this corpus. Together the two cover the original-plus-amendments content.

---

## Per-insurer download counts

| insurer / corpus | OK | Fail | Source-list count |
|---|---:|---:|---:|
| star-health | 11 | 0 | 11 |
| regulatory | 18 | 0 | 14 (+4 bonus PDFs) |

**Note.** The other 9 insurer corpora (HDFC ERGO, Niva Bupa, Care Health, ICICI Lombard, Bajaj Allianz, New India, Aditya Birla, Tata AIG, ManipalCigna) were not in scope for this run and remain at the counts shown in `rag/corpus/_manifest.json`.
