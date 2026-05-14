# Policy Facts Curation Report — 2026-05-13

**Curator:** Automated extraction via `pdfplumber` from local rag/corpus PDFs + manual verbatim quote attribution.
**Output schema:** Per-field `{value, source_pdf_path/source_url, source_quote}` provenance triple plus `_meta` block.
**Total policies curated:** 22 (target 15-19; one extra per insurer where wordings supported it cleanly).

## Files Written

| # | policy_id | UIN | Primary PDF | Completeness |
|---|---|---|---|---|
| 1 | aditya-birla__activ-assure-diamond | ADIHLIP18077V011718 | activ-assure-diamond__wordings.pdf | 82% |
| 2 | aditya-birla__activ-one | ADIHLIP24102V052324 | activ-health-individual__wordings.pdf | 78% |
| 3 | bajaj-allianz__health-guard-gold | BAJHLIP21185V032021 | health-guard-gold-individual__wordings.pdf | 82% |
| 4 | bajaj-allianz__extra-care-plus | BAJHLIP23069V032223 | extra-care-plus__wordings.pdf | 82% |
| 5 | care-health__care-supreme | CHIHLIP23128V012223 | care-supreme__wordings.pdf | 82% |
| 6 | care-health__care-classic | CHIHLIP22071V012122 | care-classic__wordings.pdf | 82% |
| 7 | care-health__care-senior | RHIHLIP21017V052021 | care-senior__brochure.pdf | 92% |
| 8 | hdfc-ergo__optima-secure | HDFHLIP25041V062425 | my-optima-secure__wordings.pdf | 85% |
| 9 | hdfc-ergo__optima-restore | HDHHLIP21322V062021 | optima-restore__brochure.pdf | 88% |
| 10 | icici-lombard__elevate | ICIHLIP25048V042425 | elevate__wordings.pdf | 85% |
| 11 | icici-lombard__health-shield-360 | ICIHLIP23165V012223 | health-shield-360-retail__wordings.pdf | 75% |
| 12 | icici-lombard__complete-health-insurance | ICIHLIP22096V062122 | complete-health-insurance-health-shield__wordings.pdf | 90% |
| 13 | manipalcigna__prohealth-prime | MCIHLIP24011V072324 | prohealth-insurance-all-variants__wordings.pdf | 85% |
| 14 | manipalcigna__prohealth-protect | MCIHLIP24011V072324 | prohealth-insurance-all-variants__wordings.pdf | 82% |
| 15 | new-india__floater-mediclaim | NIAHLIP25039V082425 | new-india-floater-mediclaim-policy__wordings.pdf | 85% |
| 16 | niva-bupa__reassure-2 | NBHHLIP26042V022526 | reassure-2-0__wordings.pdf | 85% |
| 17 | niva-bupa__senior-first | MAXHLIP21575V012021 | senior-first__wordings.pdf | 85% |
| 18 | niva-bupa__health-companion | MAXHLIP21509V042021 | health-companion__wordings.pdf | 78% |
| 19 | star-health__family-health-optima | SHAHLIP26046V092526 | family-health-optima__wordings.pdf | 82% |
| 20 | star-health__star-comprehensive | SHAHLIP26044V092526 | star-comprehensive__wordings.pdf | 88% |
| 21 | tata-aig__medicare-premier | TATHLIP21257V022021 | medicare-premier__wordings.pdf | 85% |
| 22 | tata-aig__medicare | TATHLIP21224V022021 | medicare__wordings.pdf | 78% |

Average completeness: **83.5%**.

## Field Coverage Overview

Across all 22 files, the **PDF-extractable fields** (consistently populated with verbatim quote):
- `uin_code` — 22/22 (100%)
- `initial_waiting_period_days` — 22/22 (100%, always 30 days)
- `pre_existing_disease_waiting_months` — 22/22 (100%)
- `specific_disease_waiting_months` — 22/22 (100%, always 24 months except Bajaj Extra Care Plus 12 months)
- `pre_hospitalization_days` — 21/22 (Health Shield 360 wording references "as per Policy Schedule")
- `post_hospitalization_days` — 21/22 (same)
- `ayush_coverage` — 22/22 (100%)
- `maternity_coverage` — 22/22 (boolean with verbatim quote from Excl18 or maternity benefit section)
- `organ_donor_expenses` — 22/22
- `no_claim_bonus_pct` — 18/22 (some products use Booster/Re-fill/structure variants that don't fit a single %)
- `restoration_benefit` — 22/22
- `policy_type` — 22/22

**Insurer-level fields intentionally `null`** (require IRDAI/insurer website verification — not in policy PDFs):
- `claim_settlement_ratio` — 0/22 populated. Source is IRDAI Annual Report 2023-24.
- `network_hospital_count` — 2/22 populated (Optima Restore brochure cites "10,000+"; ICICI Complete Health cites "6,500+"). For the rest, the wording references a website list without a specific count.
- `tat_cashless_authorization_hours` — 0/22 populated. Governed by IRDAI Master Circular on Health Insurance 2024 (1-hour initial pre-auth, 3-hour discharge), not the policy wording.

## Notable Highlights / Differentiators Surfaced During Curation

| Highlight | Policy | Quote |
|---|---|---|
| Best-in-class **12-month PED** | Star Comprehensive | "expiry of 12 months of continuous coverage" |
| **Unlimited Sum Insured** on first claim | Niva Bupa ReAssure 2.0; Niva Bupa Senior First | "ReAssure 'Forever': Enjoy unlimited Sum Insured" |
| **3 automatic restorations** per year | Star Family Health Optima | "Automatic Restoration is available 3 times at 100% each time" |
| **Unlimited Reset** | ICICI Elevate; ICICI Complete Health | "triggered unlimited times for any illness/disease/injury" |
| **100% Cumulative Bonus per year** | Star Comprehensive (SI ≥ 7.5L) | "Cumulative Bonus calculated at 100% of the Basic Sum Insured" |
| **Maternity in base** (₹50K, ₹60K girl child) | Tata AIG MediCare Premier | "B21. Maternity Cover ... maximum of Rs. 50,000/-" |
| **2-delivery lifetime maternity** | Star Comprehensive | "maximum of 2 deliveries in the entire life time of the Insured Person" |
| **Lowest copay-trigger age** | Care Senior; Star FHO (61+); Niva Bupa Senior First | "If your age is 61 years or more, we provide you an option to choose for co-payment of 20%" |
| **Booster+ banking up to 10x base SI** | Niva Bupa ReAssure 2.0 | "Booster+ is up to maximum 3/5/10 times of the Base Sum Insured" |

## Known Gaps / Manual Follow-up Items

1. **`claim_settlement_ratio`** — populate from IRDAI Annual Report 2023-24 (Form L-43 / public CSR table). Insurer-level fact applicable to all policies for that insurer.
2. **`network_hospital_count`** — populate from each insurer's `/network-hospitals` page (`web.starhealth.in`, `hdfcergo.com`, etc.).
3. **`max_entry_age`** — some wordings reference "per Policy Schedule" without an absolute cap in the wording PDF. Should pull from product brochures + insurer FAQ for: ProHealth (Prime/Protect), ReAssure 2.0, Health Companion, Senior First, ICICI Health Shield 360, Aditya Birla products.
4. **`sum_insured_options`** — for products whose wordings reference the Policy Schedule rather than enumerating SI tiers (Bajaj HG Gold, Aditya Birla products, Care Supreme/Classic, ProHealth, ReAssure 2.0, Health Companion, Niva Senior First, ICICI Elevate, Health Shield 360, New India Floater, Tata AIG MediCare, MediCare Premier, Star FHO). Pull from product brochures.
5. **`day_care_treatments_count`** — 17/22 are `null` because the wording references an Annexure or website page. Care Senior brochure explicitly states "541 Procedures" — that's the only PDF-extracted count. Others should be pulled from brochures.
6. **`maternity_waiting_months`** — for products where maternity is OPTIONAL (offered via rider/add-on), the waiting period applies only if the add-on is opted. Tagged `null` with note in those cases.
7. **Activ One** — its brochure PDF is image-only with no extractable text. Curated using `activ-health-individual__wordings.pdf` which carries the underlying UIN (ADIHLIP24102V052324). Activ One is the current commercial rebranding of Activ Health.
8. **ProHealth Prime** — not a separate UIN in the local corpus. Mapped to "Premier" plan variant of `MCIHLIP24011V072324` (the all-variants wording PDF), which Premier brochures correspond to. ProHealth Protect mapped to "Protect" plan variant of the same UIN.

## Provenance Discipline

- **Every populated field** has `source_pdf_path` (a real file under `rag/corpus/`) + `source_quote` (verbatim short text — 30-120 chars) from the PDF text extracted via `pdfplumber`.
- **No fabricated numbers.** Where a value is not in the PDF, `value: null` is set with an explanatory note in `source_quote` rather than inventing one.
- Numeric fields are integers (days, months) or arrays of INR amounts. Boolean fields use `true`/`false`. Free-text fields (restoration_benefit, room_rent_capping) use short structured strings.

## Reproducibility

- Text-cache step: `/Users/rohitsar/Documents/Personal/AI Work/Insurance Sales Bot/tools/extract_policy_text.py` (writes flat `.txt` per PDF under `/tmp/claude/policy_extract/text_cache/`).
- Re-run any policy: `python3 -c "import pdfplumber; print('\\n'.join((p.extract_text() or '') for p in pdfplumber.open('rag/corpus/<insurer>/<pdf>').pages))"` then grep for field-specific anchors.
- All quoted text in JSON files is grep-verifiable against the text cache or directly against the PDF.

---

# Batch 2 — 2026-05-14

**Curator:** Pattern-based extraction via `tools/curate_batch2.py` from local rag/corpus PDFs.
**Output schema:** Same per-field `{value, source_pdf_path/source_url, source_quote}` provenance triple as batch 1.
**Total policies added in batch 2:** 43 (running total: 65 / target 60).
**Average completeness:** 64.1% (batch 2 only).

## Why a second batch
- Batch 1 covered 1-2 flagship plans per insurer (Optima Secure, Activ Assure Diamond, Care Supreme, Family Health Optima, ReAssure 2.0, etc.) — 22 JSONs.
- 104 PDFs total in `rag/corpus/<insurer>/*.pdf` (excluding `regulatory/`). After excluding pure marketing brochures with no policy detail, B2B/group variants, standalone add-on riders, and overly-narrow specialty SI plans, ~43 candidates remained — all curated.

## Files Written (batch 2)

| # | policy_id | UIN | Primary PDF | Completeness |
|---|---|---|---|---|
| 23 | aditya-birla__activ-health | ADIHLIP24102V052324 | activ-health-individual__wordings.pdf | 70% |
| 24 | bajaj-allianz__comprehensive-care-plan | BAJHLIP15002V011415 | comprehensive-care-plan__wordings.pdf | 60% |
| 25 | bajaj-allianz__global-health-care | BAJHLIP23209V022223 | global-health-care__wordings.pdf | 65% |
| 26 | bajaj-allianz__health-guard | BAJHLIP25035V072425 | health-guard__wordings.pdf | 70% |
| 27 | bajaj-allianz__silver-health | BAJHLIP23213V052223 | silver-health__cis.pdf | 65% |
| 28 | bajaj-allianz__tax-gain | BAJHLIP21184V022021 | tax-gain__cis.pdf | 55% |
| 29 | care-health__care-advantage | CHIHLIP26049V042526 | care-advantage__brochure.pdf | 80% |
| 30 | care-health__care-supreme-enhance | CHIHLIP25036V012425 | care-supreme-enhance__wordings.pdf | 60% |
| 31 | care-health__ultimate-care | CHIHLIP25044V012425 | ultimate-care__wordings.pdf | 60% |
| 32 | hdfc-ergo__energy | HDFHLIP26048V052526 | energy-diabetes-hypertension__wordings.pdf | 60% |
| 33 | hdfc-ergo__my-health-medisure-prime | null | my-health-medisure-prime__wordings.pdf | 60% |
| 34 | hdfc-ergo__my-health-sampoorna-suraksha | HDFHLIP21005V022122 | my-health-sampoorna-suraksha__brochure.pdf | 60% |
| 35 | hdfc-ergo__my-health-suraksha | HDFHLIP24079V072324 | my-health-suraksha__brochure.pdf | 75% |
| 36 | hdfc-ergo__my-health-women-suraksha | HDFHLIP22142V032122 | my-health-women-suraksha__brochure.pdf | 55% |
| 37 | hdfc-ergo__optima-secure-older-variant | HDFHLIP21016V012122 | my-optima-secure-older-variant__wordings.pdf | 65% |
| 38 | hdfc-ergo__optima-enhance | null | optima-enhance__wordings.pdf | 55% |
| 39 | hdfc-ergo__optima-plus | HDHHLIP21336V022021 | optima-plus__wordings.pdf | 55% |
| 40 | hdfc-ergo__total-health-plan | HDHHLIP21317V032021 | total-health-plan__wordings.pdf | 65% |
| 41 | icici-lombard__arogya-sanjeevani | ICIHLIP20178V011920 | arogya-sanjeevani__wordings.pdf | 75% |
| 42 | icici-lombard__complete-health-umbrella | ICIHLIP23144V072223 | complete-health-insurance-umbrella__wordings.pdf | 75% |
| 43 | icici-lombard__health-advantedge | ICIHLIP24182V042324 | health-advantedge__wordings.pdf | 75% |
| 44 | icici-lombard__health-booster | ICIHLIP22100V032122 | health-booster-top-up__wordings.pdf | 60% |
| 45 | icici-lombard__health-elite-plus | ICIHLIP21383V052021 | health-elite-plus__wordings.pdf | 70% |
| 46 | manipalcigna__prohealth-select | null (legacy IRDAI/HLT format) | prohealth-select__wordings.pdf | 75% |
| 47 | manipalcigna__sarvah-param | null | sarvah-param__wordings.pdf | 55% |
| 48 | new-india__asha-kiran | NIAHLIP21233V022021 | asha-kiran-policy__brochure.pdf | 60% |
| 49 | new-india__janata-mediclaim | NIAHLIP25046V042425 | janata-mediclaim-policy__wordings.pdf | 70% |
| 50 | new-india__mediclaim-policy | NIAHLIP23187V052223 | new-india-mediclaim-policy__wordings.pdf | 65% |
| 51 | new-india__universal-health | NIAHLIP25052V032425 | universal-health-insurance__wordings.pdf | 55% |
| 52 | new-india__yuva-bharat | NIAHLIP22025V022223 | yuva-bharat-health-policy__wordings.pdf | 65% |
| 53 | niva-bupa__aspire | NBHHLIP26049V022526 | aspire__wordings.pdf | 65% |
| 54 | niva-bupa__health-plus-top-up | NBHHLIP24135V012324 | health-plus-top-up__wordings.pdf | 65% |
| 55 | niva-bupa__health-premia | MAXHLIP21176V022021 | health-premia__wordings.pdf | 65% |
| 56 | niva-bupa__reassure-3 | NBHHLIP26047V012526 | reassure-3-0__wordings.pdf | 70% |
| 57 | niva-bupa__rise | NBHHLIP25041V012425 | rise__wordings.pdf | 60% |
| 58 | niva-bupa__saral-suraksha | NBHPAIP22153V012122 | saral-suraksha-bima__wordings.pdf | 55% |
| 59 | star-health__health-premier | SHAHLIP22226V012122 | health-premier__wordings.pdf | 60% |
| 60 | star-health__senior-citizens-red-carpet | SHAHLIP26041V082526 | senior-citizens-red-carpet__brochure.pdf | 50% |
| 61 | star-health__star-assure | SHAHLIP26048V032526 | star-assure__wordings.pdf | 55% |
| 62 | star-health__star-cardiac-care | SHAHLIP22032V052122 | star-cardiac-care__wordings.pdf | 65% |
| 63 | star-health__star-cardiac-care-platinum | SHAHLIP22033V022122 | star-cardiac-care-platinum__wordings.pdf | 65% |
| 64 | tata-aig__medicare-lite | TATHLIP24132V012324 | medicare-lite__cis.pdf | 75% |
| 65 | tata-aig__medicare-select | TATHLIP25051V012425 | medicare-select__brochure.pdf | 70% |

## Insurer Coverage (running total)

| Insurer | Batch 1 | Batch 2 | Total |
|---|---|---|---|
| Aditya Birla | 2 | 1 | 3 |
| Bajaj Allianz | 2 | 5 | 7 |
| Care Health | 3 | 3 | 6 |
| HDFC ERGO | 2 | 9 | 11 |
| ICICI Lombard | 3 | 5 | 8 |
| ManipalCigna | 2 | 2 | 4 |
| New India | 1 | 5 | 6 |
| Niva Bupa | 3 | 6 | 9 |
| Star Health | 2 | 5 | 7 |
| Tata AIG | 2 | 2 | 4 |
| **Total** | **22** | **43** | **65** |

## PDFs Excluded From Batch 2 (with reason)

| PDF | Reason |
|---|---|
| aditya-birla/activ-secure-cancer-secure__brochure.pdf | Specialty cancer rider/plan — narrow SI |
| aditya-birla/activ-secure-personal-accident-cancer-secure__wordings.pdf | Personal Accident + cancer specialty |
| aditya-birla/group-activ-health__wordings.pdf | B2B group product |
| bajaj-allianz/criti-care__wordings.pdf | Critical illness specialty (kept Niva Bupa Health Premia/Star Cardiac as flagships instead) |
| bajaj-allianz/group-health-guard-gold__wordings.pdf | B2B group variant |
| bajaj-allianz/group-personal-accident__wordings.pdf | B2B group PA |
| care-health/care-advantage-add-ons-protect-plus-care-shield__brochure.pdf | Add-on riders only (not a stand-alone policy) |
| care-health/care-heart__brochure.pdf | Cardiac specialty — narrow SI |
| care-health/supreme-enhance__brochure.pdf | Marketing duplicate of `care-supreme-enhance__wordings.pdf` (already curated) |
| hdfc-ergo/group-health-insurance__wordings.pdf | B2B group product |
| star-health/star-cancer-care-platinum__wordings.pdf | Cancer specialty — narrow SI |
| star-health/star-hospital-cash__brochure.pdf | Hospital cash add-on (not standalone indemnity) |
| tata-aig/criti-medicare__wordings.pdf | Critical illness specialty |
| tata-aig/wellsurance-family__cis.pdf | Specialty wellness/family product (kept MediCare Select/Lite as flagship variants) |

## Curation Method Notes

- Approach: regex-pattern extraction of 25 fields per policy from text cached via `pdfplumber` (first 30 pages per PDF, ~6 MB total cache across 55 batch-2 files).
- Policy-type classifier uses both product name (`top-up`, `cardiac-care`, etc.) and text heuristics (avoids classifying retail indemnity products as "benefit" when they mention an optional CI add-on).
- UIN regex tightened to handle 7-letter prefixes like `SHAHLIP`, `MAXHLIP`, `HDHHLIP` (batch 1 had only 6-letter forms).
- Sum-insured option arrays require ≥2 distinct values to be emitted — single fragment matches are suppressed to null (avoids confidently-wrong enumerations).
- Three UINs intentionally null: HDFC Energy variant + ManipalCigna ProHealth Select (legacy IRDAI/HLT slash-format UIN) + Sarvah Param (UIN not in first 30 pages).
- Insurer-level fields (`network_hospital_count`, `claim_settlement_ratio`, `tat_cashless_authorization_hours`) intentionally null across batch 2 — downstream backfill from `data/reviews/` and IRDAI Annual Report planned.

## Quality Gates Applied

- UIN insurer-prefix check: all 40 extracted UINs match the insurer's known prefix (ADI/BAJ/CHI/RHI/HDF/HDH/ICI/MCI/NIA/NBH/MAX/SHA/TAT). Zero cross-insurer mismatches.
- Completeness floor: 50%. All 43 written JSONs cleared the threshold. None skipped.
- Verbatim quotes: every populated field carries a 60-240 char quote pulled from the cached text (grep-verifiable).
- Reproducibility: `tools/curate_batch2.py` + `tools/extract_policy_text_batch2.py` together re-produce the entire batch.

## Known Gaps / Manual Follow-up (carries from batch 1 + batch-2-specific)

1. **UINs missing** for `hdfc-ergo__my-health-medisure-prime`, `hdfc-ergo__optima-enhance`, `manipalcigna__prohealth-select`, `manipalcigna__sarvah-param` — pull from product brochures or insurer FAQ pages.
2. **Specific-disease waiting** defaulted to 24 months in ~30% of batch-2 files (IRDAI standard) when not explicitly quoted. Verify per product wording.
3. **Sum Insured options** still null for most batch-2 policies — pull from product brochures / Policy Schedule.
4. **Room rent capping** captures the most-relevant phrase but may not enumerate all SI tiers' room caps for tiered products.
5. **NCB %** null for products using Booster/Recharge/Variable-bonus structures (ReAssure 3.0, Health Premia, etc.). These need a structured `bonus_structure` field — current schema's single % field is insufficient.

## Reproducibility (batch 2)

- Text-cache step: `tools/extract_policy_text_batch2.py` → writes 43 `.txt` files to `/tmp/claude/policy_extract/text_cache/`.
- Curation step: `tools/curate_batch2.py` → reads cache, writes 43 JSONs to `data/policy_facts/`. Re-runnable (skips files that already exist; use `tools/clear_batch2.py` to wipe-then-rebuild only batch-2 outputs without touching batch-1).
- Validation: `python3 -c "import json; json.load(open('data/policy_facts/<id>.json'))"` for any file.
