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
