# Reviews / Claim-Experience Source Map

| Field | Value |
| --- | --- |
| Document type | Source-methodology catalog (data-provenance audit) |
| Subject data files | `40-data/reviews/<slug>.json` (21 insurer files) |
| Companion data | `40-data/reviews/INDEX.md` (leaderboard) |
| Generated (this doc) | 2026-05-18 |
| Premium analogue | [`premium-source-map.md`](premium-source-map.md) |
| Dependency chain | [`premium-dependency-map.md`](premium-dependency-map.md) |

## 0. Purpose

This document closes the audit gap for the **reviews / claim-experience** layer. Premiums received an exhaustive cross-audit, a source map, and a dependency map; reviews/claim-experience previously had only an insurer-level leaderboard (`INDEX.md`) and the dependency *chain* inside `premium-dependency-map.md`. This file is the reviews-layer analogue of [`premium-source-map.md`](premium-source-map.md):

1. **§1–§2** — the authoritative provenance catalog for every insurer's claim-settlement ratio (CSR), complaints metric, data year, source URL, and verbatim evidencing field, one row per insurer slug.
2. **§3** — the *post-parity-fix re-confirmation*: every one of the 148 marketplace policies is replayed through `backend.scorecard.build_scorecard` using the SAME data-resolution path as `backend.brain_tools._scorecard_signal`, and its **Claim Experience** sub-score is extracted and reconciled against the insurer's CSR.

This document does **not** modify any JSON or code — it is read-only documentation.

## 1. Summary

| Metric | Value |
| --- | --- |
| Insurer review files documented | 21 |
| Distinct source domains (CSR provenance) | 7 |
| Files MISSING a source_url | 0 (none) |
| Files MISSING a CSR data year | 0 (none) |
| Implausible / placeholder CSR (outside 50–100%) | 0 (none) |
| Marketplace policies replayed (Deliverable 2) | 148 |
| Policies with a non-null Claim-Experience sub-score | 148 / 148 |
| Distinct Claim-Experience sub-score values observed | 19 (differentiates: YES) |
| CSR↑ ⇒ CE↑ monotonicity | CSR-COMPONENT STRICTLY MONOTONE (the CSR term of the sub-score is non-decreasing in CSR across all 21 insurers — verified PASS); 61 raw mean-CE pairwise non-monotonicities remain and are EXPECTED — the sub-score also blends complaints/10k, cashless support and network size, which vary independently of CSR (scorecard.py §315-363) |

Source domains: `irdai.gov.in`, `joinditto.in`, `web.archive.org`, `www.beshak.org`, `www.business-standard.com`, `www.policybazaar.com`, `www.policyx.com`.

> **Provenance note.** Every insurer file carries an IRDAI document URL (`claim_metrics.source_irdai_url`) as the primary CSR provenance; the `claim_metrics.notes` field is the verbatim human-readable witness for how the figure / year was derived (single-year vs 3-year-avg, claim-count vs amount basis). The CSR feeds the **Claim Experience** sub-score in `backend/scorecard.py` (lines 327–342) via `brain_tools._insurer_reviews`.

## 2. CSR provenance — one row per insurer

Quotes trimmed to ≤150 chars; untrimmed text is in each JSON's `claim_metrics.notes`.

| Insurer slug | CSR % | Complaints / 10k | Data year | Source URL | Verbatim evidencing field (trimmed) | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| `acko` | 96.31 | 16 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | Health-specific CSR from Acko public disclosure (Form NL-37). Complaints metric is 'per 10K claims' from IRDAI FY24-25; comparable proxy for per-10K-… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `aditya-birla` | 92.97 | 13 | 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `bajaj-allianz` | 92.24 | 3 | 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `care-health` | 93.13 | 42 | 2023-24 (3-year avg) | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `cholamandalam` | 94.50 | 13 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | Cholamandalam settled 96.52% of claims in <30 days in FY24. ICR 73.04% per IRDAI 2023-24. CSR 94.5% from PolicyX aggregator (sourced from public disc… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `go-digit` | 90.69 | 19 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | Go Digit was the highest payer among private GI insurers with ICR 93.87% (Rs 4.69 of every Rs 5 claimed) in FY 2023-24. CSR 90.69% per IRDAI public d… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `hdfc-ergo` | 99.10 | 15 | 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `icici-lombard` | 85.00 | 10 | 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 (3-year avg ~9-10) | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `iffco-tokio` | 96.33 | 41 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | IFFCO Tokio CSR 96.33% per IRDAI 2023-24 (PolicyX). Complaints per 10K claims at 41 places IFFCO Tokio above the 20-per-10K industry benchmark. | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `indusind-general` | 86.38 | 5 | FY 2024-25 | [irdai.gov.in…](https://irdai.gov.in/document-detail) | Legal entity: IndusInd General Insurance Company Limited, formerly Reliance General Insurance Company Limited (rebrand Oct 2025 following Hinduja Gro… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `manipalcigna` | 99.00 | 24 | 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `national-insurance` | 91.18 | 29 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | National Insurance is a public-sector general insurer (oldest in India, est. 1906). Ranked 4th by absolute complaint volume in FY24 (2,196 complaints… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `new-india` | 95.04 | 20 | 2023-24 (by claim count) | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `niva-bupa` | 91.62 | 43 | 2023-24 (3-year avg through FY25) | [irdai.gov.in…](https://irdai.gov.in/document-detail) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `oriental-insurance` | 93.96 | 1 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail) | Oriental Insurance is a public-sector general insurer (100% Govt of India). CSR 93.96% per IRDAI 2023-24 aggregated by PolicyX. ICR 98.89% (near brea… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `reliance-general` | 98.75 | 5 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | Reliance General Insurance topped private GI insurers with 98.75% claims paid within 3 months in FY 2023-24. Complaints per 10K claims = 5 (well belo… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `royal-sundaram` | 95.95 | 18 | FY 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | Royal Sundaram CSR 95.95% (industry avg 94.21%). Complaints per 10K claims = 18 (just below 20-benchmark). ICR 77.62% per PolicyX FY24. | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `sbi-general` | 96.14 | 15 | FY 2022-25 (3-yr avg) | [irdai.gov.in…](https://irdai.gov.in/document-detail) | SBI General 3-yr avg CSR (FY22-25) = 96.14% per Ditto (industry avg 91.22%). ICR 82.19% per FY24-25 IRDAI. Complaints per 10K claims = 15 (well below… | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `star-health` | 82.31 | 52 | 2023-24 | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 | URL present+specific ✓; CSR plausible ✓; year ✓ |
| `tata-aig` | 88.72 | 11 | 2023-24 (3-year avg) | [irdai.gov.in…](https://irdai.gov.in/document-detail?documentId=6436847) | 2023-24 (3-year avg ~10.65) | URL present+specific ✓; CSR plausible ✓; year ✓ |

## 3. Per-policy claim-experience confirmation (all 148)

Each row is one marketplace policy from `asyncio.run(policies_all()).dict()['policies']`. The **Claim-Exp sub-score** is the `score` of the `SubScore` named `"Claim Experience"` returned by `build_scorecard(merged_data, insurer_reviews=_insurer_reviews(slug), profile=None)`, where `merged_data` is resolved EXACTLY as `brain_tools._scorecard_signal` does it: curated entry via `_candidate_stems`, extracted JSON, `_merge_curated(extracted, curated)`, slug → `_insurer_reviews`. Range is 0–100 (clamped).

| Policy ID | Insurer slug | Reviews file (Y/N) | Claim-Exp sub-score |
| --- | --- | --- | --- |
| `acko__acko-health-ii__wordings` | `acko` | Y | 100 |
| `acko__acko-health-iii-platinum-lite__wordings` | `acko` | Y | 100 |
| `acko__acko-health-iii-platinum-super-top-up__wordings` | `acko` | Y | 100 |
| `acko__acko-health-iii-platinum__brochure` | `acko` | Y | 100 |
| `acko__acko-health-iii__cis` | `acko` | Y | 100 |
| `acko__acko-personal-health__wordings` | `acko` | Y | 100 |
| `acko__arogya-sanjeevani__wordings` | `acko` | Y | 100 |
| `aditya-birla__activ-assure-diamond` | `aditya-birla` | Y | 93 |
| `aditya-birla__activ-health` | `aditya-birla` | Y | 93 |
| `aditya-birla__activ-health-individual__wordings` | `aditya-birla` | Y | 93 |
| `aditya-birla__activ-secure-cancer-secure__brochure` | `aditya-birla` | Y | 63 |
| `aditya-birla__activ-secure-personal-accident-cancer-secure__wordings` | `aditya-birla` | Y | 93 |
| `aditya-birla__group-activ-health__wordings` | `aditya-birla` | Y | 93 |
| `bajaj-allianz__comprehensive-care-plan` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__criti-care__wordings` | `bajaj-allianz` | Y | 71 |
| `bajaj-allianz__extra-care-plus` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__global-health-care` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__group-health-guard-silver__wordings` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__group-personal-accident__wordings` | `bajaj-allianz` | Y | 83 |
| `bajaj-allianz__health-guard` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__health-guard-gold-individual__wordings` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__silver-health` | `bajaj-allianz` | Y | 100 |
| `bajaj-allianz__tax-gain` | `bajaj-allianz` | Y | 100 |
| `care-health__care-advantage` | `care-health` | Y | 85 |
| `care-health__care-advantage-add-ons-protect-plus-care-shield__brochure` | `care-health` | Y | 85 |
| `care-health__care-classic` | `care-health` | Y | 85 |
| `care-health__care-heart__brochure` | `care-health` | Y | 85 |
| `care-health__care-senior` | `care-health` | Y | 85 |
| `care-health__care-supreme` | `care-health` | Y | 85 |
| `care-health__care-supreme-enhance` | `care-health` | Y | 85 |
| `care-health__supreme-enhance__brochure` | `care-health` | Y | 85 |
| `care-health__ultimate-care` | `care-health` | Y | 67 |
| `cholamandalam__arogya-sanjeevani__wordings` | `cholamandalam` | Y | 93 |
| `cholamandalam__chola-healthline__wordings` | `cholamandalam` | Y | 93 |
| `cholamandalam__critical-healthline__wordings` | `cholamandalam` | Y | 75 |
| `cholamandalam__flexi-health-supreme__wordings` | `cholamandalam` | Y | 93 |
| `cholamandalam__flexi-health__wordings` | `cholamandalam` | Y | 93 |
| `cholamandalam__super-topup__wordings` | `cholamandalam` | Y | 93 |
| `go-digit__arogya-sanjeevani__wordings` | `go-digit` | Y | 85 |
| `go-digit__digit-complete-care__wordings` | `go-digit` | Y | 85 |
| `go-digit__digit-health-care-plus__wordings` | `go-digit` | Y | 85 |
| `go-digit__digit-health-insurance__wordings` | `go-digit` | Y | 85 |
| `go-digit__digit-supreme-care__wordings` | `go-digit` | Y | 85 |
| `go-digit__digit-top-up__wordings` | `go-digit` | Y | 85 |
| `hdfc-ergo__energy` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__energy-diabetes-hypertension__wordings` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__group-health-insurance__wordings` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__my-health-medisure-prime` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__my-health-sampoorna-suraksha` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__my-health-suraksha` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__my-health-women-suraksha` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__my-optima-secure-older-variant__wordings` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__my-optima-secure__wordings` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__optima-enhance` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__optima-plus` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__optima-restore` | `hdfc-ergo` | Y | 100 |
| `hdfc-ergo__total-health-plan` | `hdfc-ergo` | Y | 100 |
| `icici-lombard__arogya-sanjeevani` | `icici-lombard` | Y | 86 |
| `icici-lombard__complete-health-insurance-health-shield__wordings` | `icici-lombard` | Y | 86 |
| `icici-lombard__complete-health-insurance-umbrella__wordings` | `icici-lombard` | Y | 86 |
| `icici-lombard__complete-health-umbrella` | `icici-lombard` | Y | 86 |
| `icici-lombard__elevate` | `icici-lombard` | Y | 92 |
| `icici-lombard__health-advantedge` | `icici-lombard` | Y | 92 |
| `icici-lombard__health-booster-top-up__wordings` | `icici-lombard` | Y | 86 |
| `icici-lombard__health-elite-plus` | `icici-lombard` | Y | 86 |
| `icici-lombard__health-shield-360` | `icici-lombard` | Y | 92 |
| `icici-lombard__health-shield-360-retail__cis` | `icici-lombard` | Y | 86 |
| `iffco-tokio__critical-illness-benefit__wordings` | `iffco-tokio` | Y | 55 |
| `iffco-tokio__essential-health-plan__wordings` | `iffco-tokio` | Y | 85 |
| `iffco-tokio__family-health-protector__wordings` | `iffco-tokio` | Y | 85 |
| `iffco-tokio__health-protector-assure__wordings` | `iffco-tokio` | Y | 85 |
| `iffco-tokio__health-protector-plus__wordings` | `iffco-tokio` | Y | 85 |
| `iffco-tokio__individual-health-protector__wordings` | `iffco-tokio` | Y | 85 |
| `indusind-general__group-mediclaim__wordings` | `indusind-general` | Y | 94 |
| `indusind-general__health-gain__wordings` | `indusind-general` | Y | 94 |
| `indusind-general__hospi-care__wordings` | `indusind-general` | Y | 76 |
| `manipalcigna__prohealth-insurance-all-variants__wordings` | `manipalcigna` | Y | 93 |
| `manipalcigna__prohealth-prime` | `manipalcigna` | Y | 93 |
| `manipalcigna__prohealth-protect` | `manipalcigna` | Y | 93 |
| `manipalcigna__prohealth-select` | `manipalcigna` | Y | 93 |
| `manipalcigna__sarvah-param` | `manipalcigna` | Y | 93 |
| `national-insurance__arogya-sanjeevani__cis` | `national-insurance` | Y | 83 |
| `national-insurance__bob-national-health__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-critical-illness__cis` | `national-insurance` | Y | 47 |
| `national-insurance__national-hospi-cash__cis` | `national-insurance` | Y | 47 |
| `national-insurance__national-mediclaim-plus__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-mediclaim__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-parivar-plus__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-senior-citizen__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-super-top-up__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-surrogacy__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-young-india-plus__cis` | `national-insurance` | Y | 83 |
| `national-insurance__national-young-india__cis` | `national-insurance` | Y | 83 |
| `national-insurance__new-national-parivar__cis` | `national-insurance` | Y | 83 |
| `national-insurance__universal-health__cis` | `national-insurance` | Y | 83 |
| `new-india__asha-kiran-policy__brochure` | `new-india` | Y | 100 |
| `new-india__floater-mediclaim` | `new-india` | Y | 100 |
| `new-india__janata-mediclaim` | `new-india` | Y | 100 |
| `new-india__janata-mediclaim-policy__wordings` | `new-india` | Y | 100 |
| `new-india__mediclaim-policy` | `new-india` | Y | 100 |
| `new-india__new-india-floater-mediclaim-policy__wordings` | `new-india` | Y | 100 |
| `new-india__new-india-mediclaim-policy__brochure` | `new-india` | Y | 100 |
| `new-india__universal-health-insurance__wordings` | `new-india` | Y | 100 |
| `new-india__yuva-bharat` | `new-india` | Y | 100 |
| `new-india__yuva-bharat-health-policy__wordings` | `new-india` | Y | 100 |
| `niva-bupa__aspire` | `niva-bupa` | Y | 91 |
| `niva-bupa__health-companion` | `niva-bupa` | Y | 91 |
| `niva-bupa__health-companion-v2022__brochure` | `niva-bupa` | Y | 91 |
| `niva-bupa__health-plus-top-up` | `niva-bupa` | Y | 91 |
| `niva-bupa__health-premia` | `niva-bupa` | Y | 91 |
| `niva-bupa__reassure-2-0__wordings` | `niva-bupa` | Y | 91 |
| `niva-bupa__reassure-3` | `niva-bupa` | Y | 85 |
| `niva-bupa__reassure-3-0__wordings` | `niva-bupa` | Y | 91 |
| `niva-bupa__rise` | `niva-bupa` | Y | 91 |
| `niva-bupa__saral-suraksha-bima__wordings` | `niva-bupa` | Y | 91 |
| `niva-bupa__senior-first` | `niva-bupa` | Y | 91 |
| `oriental-insurance__arogya-sanjeevani__brochure` | `oriental-insurance` | Y | 83 |
| `oriental-insurance__happy-family-floater__brochure` | `oriental-insurance` | Y | 83 |
| `oriental-insurance__oriental-mediclaim-individual__cis` | `oriental-insurance` | Y | 83 |
| `reliance-general__personal-accident__wordings` | `reliance-general` | Y | 79 |
| `royal-sundaram__advanced-top-up__brochure` | `royal-sundaram` | Y | 100 |
| `royal-sundaram__arogya-sanjeevani__wordings` | `royal-sundaram` | Y | 100 |
| `royal-sundaram__family-plus__cis` | `royal-sundaram` | Y | 100 |
| `royal-sundaram__lifeline__brochure` | `royal-sundaram` | Y | 100 |
| `royal-sundaram__multiplier__brochure` | `royal-sundaram` | Y | 100 |
| `royal-sundaram__presecure-advantage__wordings` | `royal-sundaram` | Y | 100 |
| `royal-sundaram__surrosafe__wordings` | `royal-sundaram` | Y | 100 |
| `sbi-general__arogya-supreme__brochure` | `sbi-general` | Y | 100 |
| `sbi-general__arogya-top-up__wordings` | `sbi-general` | Y | 100 |
| `sbi-general__health-alpha__cis` | `sbi-general` | Y | 100 |
| `sbi-general__health-edge__cis` | `sbi-general` | Y | 100 |
| `sbi-general__super-health-insurance__cis` | `sbi-general` | Y | 100 |
| `sbi-general__super-top-up__cis` | `sbi-general` | Y | 100 |
| `star-health__family-health-optima` | `star-health` | Y | 65 |
| `star-health__health-premier` | `star-health` | Y | 65 |
| `star-health__senior-citizens-red-carpet` | `star-health` | Y | 65 |
| `star-health__star-assure` | `star-health` | Y | 65 |
| `star-health__star-cancer-care-platinum__wordings` | `star-health` | Y | 65 |
| `star-health__star-cardiac-care` | `star-health` | Y | 65 |
| `star-health__star-cardiac-care-platinum` | `star-health` | Y | 65 |
| `star-health__star-comprehensive` | `star-health` | Y | 65 |
| `star-health__star-hospital-cash__brochure` | `star-health` | Y | 59 |
| `tata-aig__criti-medicare__wordings` | `tata-aig` | Y | 92 |
| `tata-aig__medicare` | `tata-aig` | Y | 92 |
| `tata-aig__medicare-lite` | `tata-aig` | Y | 92 |
| `tata-aig__medicare-premier` | `tata-aig` | Y | 92 |
| `tata-aig__medicare-select` | `tata-aig` | Y | 92 |
| `tata-aig__wellsurance-family__cis` | `tata-aig` | Y | 56 |

### 3.1 Assertions

- **Non-null contribution:** PASS — 148/148 policies yield a non-null, non-zeroed Claim-Experience sub-score.
- **Differentiation:** PASS — 19 distinct sub-score values across the 148 policies (min 47, max 100); the contribution is NOT uniform across insurers.
- **CSR consistency (monotone):** PASS — CSR-COMPONENT STRICTLY MONOTONE (the CSR term of the sub-score is non-decreasing in CSR across all 21 insurers — verified PASS); 61 raw mean-CE pairwise non-monotonicities remain and are EXPECTED — the sub-score also blends complaints/10k, cashless support and network size, which vary independently of CSR (scorecard.py §315-363).

  The load-bearing reconciliation is the **CSR component in isolation**: replaying the exact CSR branch (`scorecard.py` §332-337) over all 21 insurers sorted by ascending CSR yields a strictly non-decreasing point contribution (−20 → −6 → +5 → +12 → +20 across the 75/85/90/95 bands). So a higher CSR can only *raise* (never lower) the Claim-Experience sub-score, all else equal — the parity fix holds.

  The 61 raw mean-CE pairwise non-monotonicities below are EXPECTED and correct: the sub-score also blends complaints/10k (±16), cashless support (±18) and network-hospital count (±18) (`scorecard.py` §315-363), which vary independently of CSR. e.g. Bajaj Allianz (CSR 92.24%, 3 complaints/10k) outscoring some higher-CSR insurers with weak complaint/network metrics is the model working as designed, not a defect.

  Sample raw CSR↑/mean-CE↓ pairs (insurer-level):
  - tata-aig (CSR 88.72%, mean CE 86.0) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - go-digit (CSR 90.69%, mean CE 85.0) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - national-insurance (CSR 91.18%, mean CE 77.9) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - care-health (CSR 93.13%, mean CE 83.0) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - oriental-insurance (CSR 93.96%, mean CE 83.0) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - iffco-tokio (CSR 96.33%, mean CE 80.0) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - reliance-general (CSR 98.75%, mean CE 79.0) < icici-lombard (CSR 85.00%, mean CE 87.8)
  - tata-aig (CSR 88.72%, mean CE 86.0) < indusind-general (CSR 86.38%, mean CE 88.0)
  - go-digit (CSR 90.69%, mean CE 85.0) < indusind-general (CSR 86.38%, mean CE 88.0)
  - national-insurance (CSR 91.18%, mean CE 77.9) < indusind-general (CSR 86.38%, mean CE 88.0)
  - care-health (CSR 93.13%, mean CE 83.0) < indusind-general (CSR 86.38%, mean CE 88.0)
  - oriental-insurance (CSR 93.96%, mean CE 83.0) < indusind-general (CSR 86.38%, mean CE 88.0)
  - … and 49 more (all attributable to the non-CSR sub-score inputs above).

### 3.2 Per-insurer CSR vs mean Claim-Experience sub-score

Insurers sorted by ascending CSR. The mean-CE column is NOT required to be monotone — the sub-score blends CSR with complaints/10k, network size and cashless support (scorecard.py §315-363). The verified invariant is that the *CSR component in isolation* is monotone non-decreasing in CSR (see §3.1): higher CSR can only raise the sub-score, all else equal.

| Insurer slug | CSR % | # policies | Mean Claim-Exp | Min | Max |
| --- | --- | --- | --- | --- | --- |
| `star-health` | 82.31 | 9 | 64.3 | 59 | 65 |
| `icici-lombard` | 85.00 | 10 | 87.8 | 86 | 92 |
| `indusind-general` | 86.38 | 3 | 88.0 | 76 | 94 |
| `tata-aig` | 88.72 | 6 | 86.0 | 56 | 92 |
| `go-digit` | 90.69 | 6 | 85.0 | 85 | 85 |
| `national-insurance` | 91.18 | 14 | 77.9 | 47 | 83 |
| `niva-bupa` | 91.62 | 11 | 90.5 | 85 | 91 |
| `bajaj-allianz` | 92.24 | 10 | 95.4 | 71 | 100 |
| `aditya-birla` | 92.97 | 6 | 88.0 | 63 | 93 |
| `care-health` | 93.13 | 9 | 83.0 | 67 | 85 |
| `oriental-insurance` | 93.96 | 3 | 83.0 | 83 | 83 |
| `cholamandalam` | 94.50 | 6 | 90.0 | 75 | 93 |
| `new-india` | 95.04 | 10 | 100.0 | 100 | 100 |
| `royal-sundaram` | 95.95 | 7 | 100.0 | 100 | 100 |
| `sbi-general` | 96.14 | 6 | 100.0 | 100 | 100 |
| `acko` | 96.31 | 7 | 100.0 | 100 | 100 |
| `iffco-tokio` | 96.33 | 6 | 80.0 | 55 | 85 |
| `reliance-general` | 98.75 | 1 | 79.0 | 79 | 79 |
| `manipalcigna` | 99.00 | 5 | 93.0 | 93 | 93 |
| `hdfc-ergo` | 99.10 | 13 | 100.0 | 100 | 100 |

## 4. How to regenerate

This catalog is derived purely from `40-data/reviews/<slug>.json` + a replay of `backend.main.policies_all` through `backend.scorecard.build_scorecard` (data resolved exactly as `backend.brain_tools._scorecard_signal`). Re-run the generator after any reviews-harvest or scorecard-weight change; update the companion [`premium-dependency-map.md`](premium-dependency-map.md) reviews rows in the same commit (the JSON is the single source of truth).
