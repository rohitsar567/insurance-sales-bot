# Insurance Sales Bot — Information Source Map

Generated: 2026-05-15 (KI-125–KI-150 sync; slug renames applied)
Total claims audited: **2772**

## Verdict Summary

| Category | ✅ verified | ⚠️ url-ok-quote-missing | ❌ url-broken | ⏳ no-claim / no-source |
|---|---:|---:|---:|---:|
| policy_facts | 798 | 321 | 0 | 1385 |
| premiums | 0 | 47 | 9 | 0 |
| reviews | 0 | 203 | 3 | 6 |
| **TOTAL** | **798** | **571** | **12** | **1391** |

## Must Fix — 12 broken source(s)

| Record | Field | Value | Source | Notes |
|---|---|---|---|---|
| `care-health` | `reddit.sample_post_urls[2]` | https://www.quora.com/What-are-peoples-opinions-on-Care-Indi | `https://www.quora.com/What-are-peoples-opinions-on-Care-Indias-health-insurance` | HEAD returned status=403 |
| `star-health` | `reddit.sample_post_urls[0]` | https://www.quora.com/Is-Star-Health-Insurance-good-Has-anyb | `https://www.quora.com/Is-Star-Health-Insurance-good-Has-anybody-experienced-problems-in-claim-settlement` | HEAD returned status=403 |
| `star-health` | `reddit.sample_post_urls[3]` | https://www.quora.com/Is-Star-Health-Insurance-a-good-compan | `https://www.quora.com/Is-Star-Health-Insurance-a-good-company` | HEAD returned status=403 |
| `premiums_meta` | `sources_consulted[2]` | https://www.policybazaar.com/insurance-companies/aditya-birl | `https://www.policybazaar.com/insurance-companies/aditya-birla-health-insurance/activ-assure-diamond-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[3]` | https://www.policybazaar.com/insurance-companies/bajaj-allia | `https://www.policybazaar.com/insurance-companies/bajaj-allianz-health-insurance/individual-health-guard-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[4]` | https://www.policybazaar.com/insurance-companies/star-health | `https://www.policybazaar.com/insurance-companies/star-health-insurance/family-health-optima-insurance-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[5]` | https://www.policybazaar.com/insurance-companies/star-health | `https://www.policybazaar.com/insurance-companies/star-health-insurance/senior-citizens-red-carpet-health-insurance-policy/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[6]` | https://www.policybazaar.com/insurance-companies/icici-lomba | `https://www.policybazaar.com/insurance-companies/icici-lombard-health-insurance/elevate-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[7]` | https://www.policybazaar.com/insurance-companies/tata-aig-he | `https://www.policybazaar.com/insurance-companies/tata-aig-health-insurance/medicare-premier/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[8]` | https://www.policybazaar.com/insurance-companies/manipalcign | `https://www.policybazaar.com/insurance-companies/manipalcigna-health-insurance/prohealth-prime/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[9]` | https://www.policybazaar.com/insurance-companies/max-bupa-he | `https://www.policybazaar.com/insurance-companies/max-bupa-health-insurance/premium-calculator/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[23]` | https://www.policybazaar.com/health-insurance/smokers/ | `https://www.policybazaar.com/health-insurance/smokers/` | HEAD returned status=503 |

## policy_facts

Audited 2504 claims — ✅ 798 verified, ⚠️ 321 quote-missing, ❌ 0 broken.

### Flagged claims

| Record | Field | Verdict | Source | Notes |
|---|---|---|---|---|
| `aditya-birla__activ-assure-diamond` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `max_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `post_hospitalization_days` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-assure-diamond` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-assure-diamond__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-health-individual__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-health` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-health` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-health` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-one` | `max_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-one` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-one` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-one` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-one` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-one` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-health-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-secure-cancer-secure__brochure` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-secure-cancer-secure__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__activ-secure-personal-accident-cancer-secure__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/activ-secure-personal-accident-cancer-secure__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `aditya-birla__group-activ-health__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/aditya-birla/group-activ-health__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__comprehensive-care-plan` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/comprehensive-care-plan__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__comprehensive-care-plan` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/comprehensive-care-plan__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__criti-care__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/criti-care__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__extra-care-plus` | `ayush_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__extra-care-plus` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__extra-care-plus` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__extra-care-plus` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__extra-care-plus` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__extra-care-plus` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/extra-care-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__global-health-care` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/global-health-care__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__global-health-care` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/global-health-care__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__group-health-guard-silver__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/group-health-guard-silver__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__group-personal-accident__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/group-personal-accident__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold-individual__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `max_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard-gold` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard-gold-individual__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__health-guard` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/health-guard__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__silver-health` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/silver-health__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__silver-health` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/silver-health__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__silver-health` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/silver-health__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__tax-gain` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/tax-gain__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__tax-gain` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/tax-gain__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__tax-gain` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/tax-gain__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `bajaj-allianz__tax-gain` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/bajaj-allianz/tax-gain__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-advantage-add-ons-protect-plus-care-shield__brochure` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-advantage-add-ons-protect-plus-care-shield__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-advantage-add-ons-protect-plus-care-shield__brochure` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-advantage-add-ons-protect-plus-care-shield__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-advantage` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-advantage__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-advantage` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-advantage__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `pre_existing_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `maternity_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-classic` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-classic__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-heart__brochure` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-heart__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `pre_hospitalization_days` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `post_hospitalization_days` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-senior` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-senior__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme-enhance` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme-enhance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme-enhance` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme-enhance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme-enhance` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme-enhance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `pre_existing_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__care-supreme` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/care-supreme__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__supreme-enhance__brochure` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/supreme-enhance__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__ultimate-care` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/ultimate-care__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__ultimate-care` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/ultimate-care__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `care-health__ultimate-care` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/care-health/ultimate-care__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__energy-diabetes-hypertension__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/energy-diabetes-hypertension__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__energy` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/energy-diabetes-hypertension__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__energy` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/energy-diabetes-hypertension__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__energy` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/energy-diabetes-hypertension__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__group-health-insurance__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/group-health-insurance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-medisure-prime` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-medisure-prime__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-medisure-prime` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-medisure-prime__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-medisure-prime` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-medisure-prime__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-sampoorna-suraksha` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-sampoorna-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-sampoorna-suraksha` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-sampoorna-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-sampoorna-suraksha` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-sampoorna-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-suraksha` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-suraksha` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-suraksha` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-women-suraksha` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-women-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-women-suraksha` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-women-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-women-suraksha` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-women-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-health-women-suraksha` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-health-women-suraksha__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-optima-secure-older-variant__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure-older-variant__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__my-optima-secure__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-enhance` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-enhance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-enhance` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-enhance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-enhance` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-enhance__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-plus` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-plus` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-restore` | `sum_insured_options` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-restore__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-restore` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-restore__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-restore` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-restore__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-restore` | `network_hospital_count` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-restore__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-restore` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-restore__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure-older-variant` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure-older-variant__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure-older-variant` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure-older-variant__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure-older-variant` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure-older-variant__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure` | `ayush_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/my-optima-secure__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__optima-secure` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/optima-restore__brochure.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__total-health-plan` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/total-health-plan__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__total-health-plan` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/total-health-plan__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `hdfc-ergo__total-health-plan` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/hdfc-ergo/total-health-plan__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__arogya-sanjeevani` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/arogya-sanjeevani__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance-health-shield__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance-umbrella__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-umbrella__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `pre_hospitalization_days` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-insurance` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-health-shield__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__complete-health-umbrella` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/complete-health-insurance-umbrella__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `no_claim_bonus_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__elevate` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/elevate__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-advantedge` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-advantedge__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-advantedge` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-advantedge__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-advantedge` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-advantedge__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-advantedge` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-advantedge__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-booster-top-up__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-booster-top-up__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-booster` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-booster-top-up__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-booster` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-booster-top-up__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-booster` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-booster-top-up__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-elite-plus` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-elite-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-elite-plus` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-elite-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-elite-plus` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-elite-plus__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360-retail__cis` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__cis.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360-retail__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `pre_existing_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `organ_donor_expenses` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `icici-lombard__health-shield-360` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/icici-lombard/health-shield-360-retail__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-insurance-all-variants__wordings` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `maternity_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `maternity_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-prime` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `min_entry_age` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `newborn_coverage` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `restoration_benefit` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `room_rent_capping` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `cashless_treatment_supported` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-protect` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-insurance-all-variants__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-select` | `initial_waiting_period_days` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-select__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-select` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-select__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__prohealth-select` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/prohealth-select__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__sarvah-param` | `specific_disease_waiting_months` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/sarvah-param__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `manipalcigna__sarvah-param` | `copayment_pct` | ⚠️ url-ok-quote-missing | `rag/corpus/manipalcigna/sarvah-param__wordings.pdf` | PDF exists but source_quote not found in extracted text |
| `new-india__asha-kiran-policy__brochure` | `policy_type` | ⚠️ url-ok-quote-missing | `rag/corpus/new-india/asha-kiran-policy__brochure.pdf` | PDF exists but source_quote not found in extracted text |

_... and 121 more rows truncated; see eval/info_source_map.json for full data._

## premiums

Audited 56 claims — ✅ 0 verified, ⚠️ 47 quote-missing, ❌ 9 broken.

### Flagged claims

| Record | Field | Verdict | Source | Notes |
|---|---|---|---|---|
| `premiums_meta` | `sources_consulted[0]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/hdfc-ergo-health-insurance/optima-secure-plan/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[1]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/religare-health-insurance/care-supreme/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[2]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/aditya-birla-health-insurance/activ-assure-diamond-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[3]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/bajaj-allianz-health-insurance/individual-health-guard-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[4]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/star-health-insurance/family-health-optima-insurance-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[5]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/star-health-insurance/senior-citizens-red-carpet-health-insurance-policy/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[6]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/icici-lombard-health-insurance/elevate-plan/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[7]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/tata-aig-health-insurance/medicare-premier/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[8]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/manipalcigna-health-insurance/prohealth-prime/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[9]` | ❌ url-broken | `https://www.policybazaar.com/insurance-companies/max-bupa-health-insurance/premium-calculator/` | HEAD returned status=503 |
| `premiums_meta` | `sources_consulted[10]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/hdfc-ergo-optima-secure-premium-chart/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[11]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[12]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/care-supreme-health-insurance-premium-chart/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[13]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-premium-rate-chart/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[14]` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/tata-aig-medicare-premier-rate-chart.pdf` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[15]` | ⚠️ url-ok-quote-missing | `https://transactions.nivabupa.com/pages/doc/premium_chart/Health%20Premia%20-%20Rate%20Card.pdf` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[16]` | ⚠️ url-ok-quote-missing | `https://www.scribd.com/document/667873826/Annexure-2-Premium-Chart-Family-Health-Optima-Insurance-Plan-Revised` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[17]` | ⚠️ url-ok-quote-missing | `https://www.scribd.com/document/682628786/ReAssure-2-0-Rate-Card-with-Tax` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[18]` | ⚠️ url-ok-quote-missing | `https://www.adityabirlacapital.com/healthinsurance/activ-assure-diamond` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[19]` | ⚠️ url-ok-quote-missing | `https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/hdfc-ergo-optima-secure/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[20]` | ⚠️ url-ok-quote-missing | `https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/care-insurance-care-supreme/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[21]` | ⚠️ url-ok-quote-missing | `https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/bajaj-allianz-health-guard-gold/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[22]` | ⚠️ url-ok-quote-missing | `https://www.acko.com/health-insurance/for-smokers/` | URL reachable but no source_quote provided |
| `premiums_meta` | `sources_consulted[23]` | ❌ url-broken | `https://www.policybazaar.com/health-insurance/smokers/` | HEAD returned status=503 |
| `hdfc-ergo__optima-secure` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/hdfc-ergo-health-insurance/optima-secure-plan/` | URL reachable but no source_quote provided |
| `hdfc-ergo__optima-secure` | `samples[1].age=30_si=1000000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/hdfc-ergo-health-insurance/optima-secure-plan/` | URL reachable but no source_quote provided |
| `hdfc-ergo__optima-plus` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/hdfc-ergo-health-insurance/optima-plus-insurance-plan/` | URL reachable but no source_quote provided |
| `care-health__care-supreme` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/religare-health-insurance/care-supreme/` | URL reachable but no source_quote provided |
| `care-health__care-supreme` | `samples[1].age=30_si=1000000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/religare-health-insurance/care-supreme/` | URL reachable but no source_quote provided |
| `care-health__care-classic` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/religare-health-insurance/` | URL reachable but no source_quote provided |
| `care-health__care-senior` | `samples[0].age=65_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/religare-health-insurance/senior-citizens/` | URL reachable but no source_quote provided |
| `care-health__care-advantage` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.careinsurance.com/product/care-supreme` | URL reachable but no source_quote provided |
| `aditya-birla__activ-assure-diamond` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.adityabirlacapital.com/healthinsurance/activ-assure-diamond` | URL reachable but no source_quote provided |
| `bajaj-allianz__health-guard` | `samples[0].age=30_si=350000` | ⚠️ url-ok-quote-missing | `https://www.bajajfinserv.in/insurance/bajaj-general-health-guard-insurance-plan` | URL reachable but no source_quote provided |
| `bajaj-allianz__silver-health` | `samples[0].age=60_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/bajaj-allianz-health-insurance/silver-health-plan/` | URL reachable but no source_quote provided |
| `bajaj-allianz__tax-gain` | `samples[0].age=35_si=500000` | ⚠️ url-ok-quote-missing | `https://www.bajajgeneralinsurance.com/health-insurance-plans/` | URL reachable but no source_quote provided |
| `icici-lombard__elevate` | `samples[0].age=30_si=1000000` | ⚠️ url-ok-quote-missing | `https://www.icicilombard.com/health-insurance/elevate-health-policy` | URL reachable but no source_quote provided |
| `icici-lombard__health-advantedge` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/icici-lombard-health-insurance/` | URL reachable but no source_quote provided |
| `niva-bupa__reassure` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/` | URL reachable but no source_quote provided |
| `niva-bupa__reassure` | `samples[1].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/` | URL reachable but no source_quote provided |
| `niva-bupa__health-premia` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.nivabupa.com/health-insurance-plan/health-premia` | URL reachable but no source_quote provided |
| `niva-bupa__aspire` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.nivabupa.com/family-health-insurance-plans/aspire.html` | URL reachable but no source_quote provided |
| `new-india__asha-kiran` | `samples[0].age=30_si=300000` | ⚠️ url-ok-quote-missing | `https://www.newindia.co.in/portal/readMore/Pages/Asha-Kiran-Policy` | URL reachable but no source_quote provided |
| `new-india__mediclaim` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.newindia.co.in/portal/readMore/Pages/Mediclaim-Policy` | URL reachable but no source_quote provided |
| `tata-aig__medicare` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/medicare-rate-chart-06-12-22.pdf` | URL reachable but no source_quote provided |
| `tata-aig__medicare` | `samples[1].age=30_si=1000000` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/medicare-rate-chart-06-12-22.pdf` | URL reachable but no source_quote provided |
| `tata-aig__medicare-premier` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/tata-aig-medicare-premier-rate-chart.pdf` | URL reachable but no source_quote provided |
| `tata-aig__medicare-premier` | `samples[1].age=30_si=1000000` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/tata-aig-medicare-premier-rate-chart.pdf` | URL reachable but no source_quote provided |
| `tata-aig__medicare-premier` | `samples[2].age=40_si=500000` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/tata-aig-medicare-premier-rate-chart.pdf` | URL reachable but no source_quote provided |
| `tata-aig__medicare-premier` | `samples[3].age=40_si=1000000` | ⚠️ url-ok-quote-missing | `https://www.axisbank.com/70-docs/default-source/default-document-library/tata-aig-medicare-premier-rate-chart.pdf` | URL reachable but no source_quote provided |
| `manipalcigna__prohealth-prime-active` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.manipalcigna.com/hospitalization-cover/prohealth-insurance/prohealthprime-active` | URL reachable but no source_quote provided |
| `star-health__family-health-optima` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.scribd.com/document/667873826/Annexure-2-Premium-Chart-Family-Health-Optima-Insurance-Plan-Revised` | URL reachable but no source_quote provided |
| `star-health__family-health-optima` | `samples[1].age=40_si=500000` | ⚠️ url-ok-quote-missing | `https://www.scribd.com/document/667873826/Annexure-2-Premium-Chart-Family-Health-Optima-Insurance-Plan-Revised` | URL reachable but no source_quote provided |
| `star-health__family-health-optima` | `samples[2].age=48_si=500000` | ⚠️ url-ok-quote-missing | `https://www.scribd.com/document/667873826/Annexure-2-Premium-Chart-Family-Health-Optima-Insurance-Plan-Revised` | URL reachable but no source_quote provided |
| `star-health__comprehensive` | `samples[0].age=30_si=500000` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/star-health-insurance/star-comprehensive-insurance-policy/` | URL reachable but no source_quote provided |
| `star-health__senior-citizens-red-carpet` | `samples[0].age=60_si=100000` | ⚠️ url-ok-quote-missing | `https://www.starhealth.in/health-insurance/health-insurance-for-senior-citizens/` | URL reachable but no source_quote provided |

## reviews

Audited 212 claims — ✅ 0 verified, ⚠️ 203 quote-missing, ❌ 3 broken.

### Flagged claims

| Record | Field | Verdict | Source | Notes |
|---|---|---|---|---|
| `aditya-birla` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `aditya-birla` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/aditya-birla-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `aditya-birla` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.angelone.in/news/market-updates/aditya-birla-health-insurance-claims-settlement-ratio-analysis-for-2024-25` | URL reachable but quote not found in fetched body |
| `aditya-birla` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/aditya-birla-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `aditya-birla` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/aditya-birla/claim-settlement` | URL reachable but no source_quote provided |
| `aditya-birla` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/aditya-birla-health-insurance-claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `aditya-birla` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/health-insurance/companies/aditya-birla-vs-tata-aig/` | URL reachable but no source_quote provided |
| `aditya-birla` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/health-insurance/comparison/aditya-birla-activ-one-vs-tata-aig-medicare/` | URL reachable but no source_quote provided |
| `aditya-birla` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/is-aditya-birla-health-insurance-good/` | URL reachable but no source_quote provided |
| `aditya-birla` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `aditya-birla` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=d5CIAwrpEdQ` | URL reachable but no source_quote provided |
| `aditya-birla` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=Wb0fvhzzJxQ` | URL reachable but no source_quote provided |
| `aditya-birla` | `youtube[2].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=6cIHkBY8Z7k` | URL reachable but no source_quote provided |
| `aditya-birla` | `youtube[3].Gurleen Kaur Tikku` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=4LTtX0is2GE` | URL reachable but no source_quote provided |
| `aditya-birla` | `youtube[4].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=o5sx-H8_5-M` | URL reachable but no source_quote provided |
| `aditya-birla` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.angelone.in/news/market-updates/aditya-birla-health-insurance-claims-settlement-ratio-analysis-for-2024-25` | URL reachable but no source_quote provided |
| `aditya-birla` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://cafemutual.com/news/insurance/37646-how-much-did-the-non-life-insurers-pay-in-commission-in-april-december-2025` | URL reachable but no source_quote provided |
| `aditya-birla` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://cafemutual.com/news/insurance/37461-which-non-life-insurers-settled-the-highest-number-of-claims-in-april-december-2025` | URL reachable but no source_quote provided |
| `aditya-birla` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/insurance/icici-lombard-care-health-aditya-birla-cut-commissions-post-gst-125100101265_1.html` | URL reachable but no source_quote provided |
| `aditya-birla` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://www.outlookbusiness.com/news/aditya-birla-health-aims-to-outpace-sector-growth-sees-relief-ahead-in-mediclaim-premium` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `bajaj-allianz` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/bajaj-general-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `bajaj-allianz` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.bajajgeneralinsurance.com/about-us/financial-information.html` | URL reachable but quote not found in fetched body |
| `bajaj-allianz` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/bajaj-allianz-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/bajaj-allianz/claim-settlement` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/bajaj-general-health-insurance-claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://1finance.co.in/product-scoring/health-insurance/bajaj-allianz-health-guard-gold-plan` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/bajaj-general-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/bajaj-allianz/health-guard-family-floater/user-reviews` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/bajaj-general-health-insurance-reviews/` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `reddit.sample_post_urls[4]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=DWeVo13Y9ms` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `youtube[1].MJ (BAJAJ ALLIANZ Health Guard Plan Detailed Review)` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=A6KxeOHMPVw` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `youtube[2].Independent (Hindi)` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=d1--TiPtLw4` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.bajajallianz.com/download-documents/press-release/Press-Release-Finance-Bajaj-Allianz-General-Insurance-delivers-strong-financial-results-with-profits-rising-by-27percent-to-Rs%20921-crore.pdf` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-india-are-the-most-profitable-non-life-players-in-fy-2024` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/why-15-200-hospitals-stopped-cashless-care-for-bajaj-allianz-policyholders-125082500103_1.html` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/cashless-crisis-averted-for-bajaj-allianz-customers-money-lessons-for-you-125090100132_1.html` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://www.prnewswire.com/in/news-releases/bajaj-allianz-general-insurance-unveils-herizon-care-indias-first-comprehensive-health-insurance-plan-designed-exclusively-for-women-302371201.html` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[5]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/content/press-releases-ani/bajaj-allianz-launches-state-wise-health-insurance-policies-tailored-to-regional-needs-125061901143_1.html` | URL reachable but no source_quote provided |
| `bajaj-allianz` | `in_news[6]` | ⚠️ url-ok-quote-missing | `https://www.bajajgeneralinsurance.com/about-us/financial-highlights.html` | URL reachable but no source_quote provided |
| `care-health` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `care-health` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/religare-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `care-health` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.careinsurance.com/health-insurance/health-insurance-claim-settlement-ratio` | URL in browser_verified allowlist | quote not found in fetched body |
| `care-health` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/religare-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `care-health` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/care/user-reviews` | URL reachable but no source_quote provided |
| `care-health` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/care-health-insurance-review/` | URL reachable but no source_quote provided |
| `care-health` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/is-care-health-insurance-good/` | URL reachable but no source_quote provided |
| `care-health` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/health-insurance/companies/hdfc-ergo-vs-niva-bupa/` | URL reachable but no source_quote provided |
| `care-health` | `reddit.sample_post_urls[2]` | ❌ url-broken | `https://www.quora.com/What-are-peoples-opinions-on-Care-Indias-health-insurance` | HEAD returned status=403 |
| `care-health` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `care-health` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=2_EhrtJhn44` | URL reachable but no source_quote provided |
| `care-health` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=1rRvcVkcQVw` | URL reachable but no source_quote provided |
| `care-health` | `youtube[2].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=gnJyocoSiMA` | URL reachable but no source_quote provided |
| `care-health` | `youtube[3].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=21jT3Ji-SOI` | URL reachable but no source_quote provided |
| `care-health` | `youtube[4].PolicyX` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=AN8o2zGDJ5w` | URL reachable but no source_quote provided |
| `care-health` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://thesouthfirst.com/health/health-insurance-claims-standalone-insurers-pay-rs-3-07-per-rs-5-claim-general-insurers-offer-rs-4-17/` | URL reachable but no source_quote provided |
| `care-health` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html` | URL reachable but no source_quote provided |
| `care-health` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://www.medboundtimes.com/india/hospitals-suspend-cashless-services-bajaj-allianz-care-policyholders` | URL reachable but no source_quote provided |
| `care-health` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/insurance/icici-lombard-care-health-aditya-birla-cut-commissions-post-gst-125100101265_1.html` | URL reachable but no source_quote provided |
| `care-health` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://unlistedzone.com/h1-fy26-shows-care-health-is-growing-but-its-claims-are-growing-faster` | URL reachable but no source_quote provided |
| `care-health` | `in_news[5]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/care-health-insurance-claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `hdfc-ergo` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `http://web.archive.org/web/20260211155805/https://www.policybazaar.com/insurance-companies/hdfc-ergo-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `hdfc-ergo` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.hdfcergo.com/blogs/health-insurance/hdfc-ergo-claim-settlement-ratio` | URL reachable but quote not found in fetched body |
| `hdfc-ergo` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/hdfc-ergo-general-reviews-903/` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/hdfc-ergo` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/health-insurance/hdfc-ergo/reviews/` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `aggregator_ratings.mouthshut` | ⚠️ url-ok-quote-missing | `https://www.mouthshut.com/product-reviews/hdfc-ergo-health-insurance-reviews-925865222` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `trustpilot.url` | ⚠️ url-ok-quote-missing | `https://www.trustpilot.com/review/www.hdfcergo.com` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://www.quora.com/Have-you-ever-made-any-claim-against-HDFC-Ergo-health-insurance-and-was-it-a-good-experience` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.oneassure.in/insurance/health-insurance-compare/niva-bupa-re-assure-vs-icici-health-shield-vs-hdfc-ergo-my-health-suraksha-gold` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://technofino.in/community/threads/hdfc-ergo-optima-secure-4x-is-there-any-catch.5647/` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://technofino.in/community/threads/hdfc-ergo-optima-secure-health-insurance.20516/` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `reddit.sample_post_urls[4]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=R4ehMh3z9UA` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=Ozq71VRTZUA` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `youtube[2].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=2_EhrtJhn44` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `youtube[3].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=i3xMZGMstzE` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `youtube[4].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=YEMmxjbl2Yw` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.goodreturns.in/news/hdfc-ergo-aims-to-maintain-premium-growth-2024-25-011-1392211.html` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://www.angelone.in/news/market-updates/hdfc-ergo-general-insurance-claims-settlement-ratio-analysis-for-2024-25` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://techobserver.in/news/bfsi/hdfc-ergo-cto-naganathan-eyes-ai-led-hyper-personalised-insurance-in-2026-319763/` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.globenewswire.com/news-release/2025/12/31/3211657/0/en/HDFC-ERGO-Strengthens-Electric-Two-Wheeler-Protection-with-Service-Led-Claims-Support.html` | URL reachable but no source_quote provided |
| `hdfc-ergo` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://www.hdfcergo.com/` | URL reachable but no source_quote provided |
| `icici-lombard` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `icici-lombard` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/icici-lombard-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `icici-lombard` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.icicilombard.com/health-insurance/blogs/icici-lombard-health-insurance-claim-settlement-ratio` | URL in browser_verified allowlist | quote not found in fetched body |
| `icici-lombard` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/icici-lombard-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `icici-lombard` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/icici-lombard/claim-settlement` | URL reachable but no source_quote provided |
| `icici-lombard` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance-good/` | URL reachable but no source_quote provided |
| `icici-lombard` | `aggregator_ratings.mouthshut` | ⚠️ url-ok-quote-missing | `https://www.mouthshut.com/product-reviews/icici-lombard-health-insurance-reviews-925076599` | URL reachable but no source_quote provided |
| `icici-lombard` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://www.oneassure.in/insurance/health-insurance-compare/niva-bupa-re-assure-vs-icici-health-shield-vs-hdfc-ergo-my-health-suraksha-gold` | URL reachable but no source_quote provided |
| `icici-lombard` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/is-icici-lombard-health-insurance-good/` | URL reachable but no source_quote provided |
| `icici-lombard` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://technofino.in/community/threads/50-lakh-cover-for-rs-7-123-too-good-to-be-true.12094/` | URL reachable but no source_quote provided |
| `icici-lombard` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.oneassure.in/insurance/health-insurance-guides/is-icici-health-insurance-good-comparing-plans-premiums-and-claim-experience` | URL reachable but no source_quote provided |
| `icici-lombard` | `reddit.sample_post_urls[4]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `icici-lombard` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=0EJI00BQqCA` | URL reachable but no source_quote provided |
| `icici-lombard` | `youtube[2].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=TjvNNK62oyE` | URL reachable but no source_quote provided |
| `icici-lombard` | `youtube[3].PolicyX` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=ca2bRF_JoLI` | URL reachable but no source_quote provided |
| `icici-lombard` | `youtube[4].Independent` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=ve6sHV2BCM8` | URL reachable but no source_quote provided |
| `icici-lombard` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/companies/quarterly-results/icici-lombard-net-profit-up-29-in-q1fy26-125071501318_1.html` | URL reachable but no source_quote provided |
| `icici-lombard` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-india-are-the-most-profitable-non-life-players-in-fy-2024` | URL reachable but no source_quote provided |
| `icici-lombard` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/insurance/icici-lombard-care-health-aditya-birla-cut-commissions-post-gst-125100101265_1.html` | URL reachable but no source_quote provided |
| `icici-lombard` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.arihantplus.com/blogs/stocks/icici-lombard-q1-fy-26-results-profit-jumps-28-7-strong-margins-and-digital-momentum` | URL reachable but no source_quote provided |
| `manipalcigna` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `manipalcigna` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/manipalcigna-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `manipalcigna` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.manipalcigna.com/blog/claim-settlement-ratio` | URL reachable but quote not found in fetched body |
| `manipalcigna` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/manipalcigna-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `manipalcigna` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/manipalcigna/claim-settlement` | URL reachable but no source_quote provided |
| `manipalcigna` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-review/` | URL reachable but no source_quote provided |
| `manipalcigna` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-review/` | URL reachable but no source_quote provided |
| `manipalcigna` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/manipalcigna-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `manipalcigna` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/manipal-cigna-health-insurance-review/` | URL reachable but no source_quote provided |
| `manipalcigna` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `manipalcigna` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=Yb6zub6V-LY` | URL reachable but no source_quote provided |
| `manipalcigna` | `youtube[1].Gurleen Kaur Tikku` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=DzhGiVxJPKQ` | URL reachable but no source_quote provided |
| `manipalcigna` | `youtube[2].Ditto Insurance / Health Insurance Sahi Hai` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=jUNmj0lKKjs` | URL reachable but no source_quote provided |
| `manipalcigna` | `youtube[3].Gurleen Kaur Tikku` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=uiANfhN1kyM` | URL reachable but no source_quote provided |
| `manipalcigna` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://cafemutual.com/news/insurance/33084-which-companies-are-better-at-settling-health-claims` | URL reachable but no source_quote provided |
| `manipalcigna` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://m.thewire.in/article/ptiprnews/manipalcigna-sarvah-named-product-of-the-year-2025-in-health-insurance-category` | URL reachable but no source_quote provided |
| `manipalcigna` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/content/press-releases-ani/manipalcigna-launches-sarvah-the-complete-health-insurance-plan-with-special-focus-on-bharat-s-missing-middle-124101900282_1.html` | URL reachable but no source_quote provided |
| `manipalcigna` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.dtnext.in/news/business/manipalcigna-health-insurance-expands-in-tn-settles-rs-101-cr-worth-claims-840161` | URL reachable but no source_quote provided |
| `manipalcigna` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://www.renewbuy.com/health-insurance/manipalcigna-health-insurance/claim-settlement-ratio` | URL reachable but no source_quote provided |
| `new-india` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `new-india` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/new-india-assurance-health-insurance/claim-settlement-ratio/` | URL in browser_verified allowlist | quote not found in fetched body |
| `new-india` | `claim_metrics.source_company_url` | ⚠️ url-ok-quote-missing | `https://www.newindia.co.in/public-disclosure` | URL reachable but quote not found in fetched body |
| `new-india` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/new-india-assurance-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `new-india` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/new-india/claim-settlement` | URL reachable but no source_quote provided |
| `new-india` | `aggregator_ratings.policyx` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/new-india-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `new-india` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://www.deccanchronicle.com/business/new-india-assurance-tops-health-insurance-claims-settlement-1841899` | URL reachable but no source_quote provided |
| `new-india` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/new-india-assurance-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `new-india` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/is-new-india-assurance-health-insurance-good/` | URL reachable but no source_quote provided |
| `new-india` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `new-india` | `youtube[0].ET Now` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=OE_844nkUDE` | URL reachable but no source_quote provided |
| `new-india` | `youtube[1].Independent` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=f27o4K47nKU` | URL reachable but no source_quote provided |
| `new-india` | `youtube[2].Independent` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=7um6_5Zmt0M` | URL reachable but no source_quote provided |
| `new-india` | `youtube[3].Independent (Hindi)` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=IOlBJxGwraU` | URL reachable but no source_quote provided |
| `new-india` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.deccanchronicle.com/business/new-india-assurance-tops-health-insurance-claims-settlement-1841899` | URL reachable but no source_quote provided |
| `new-india` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/companies/news/new-india-assurance-q3fy24-results-net-profit-falls-4-38-to-rs-715-crore-124020901758_1.html` | URL reachable but no source_quote provided |
| `new-india` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://cafemutual.com/news/insurance/33076-icici-lombard-bajaj-allianz-and-new-india-are-the-most-profitable-non-life-players-in-fy-2024` | URL reachable but no source_quote provided |
| `new-india` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/insurance/new-india-assurance-increases-premiums-on-health-insurance-by-10-124073001522_1.html` | URL reachable but no source_quote provided |
| `new-india` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/is-new-india-assurance-health-insurance-good/` | URL reachable but no source_quote provided |
| `niva-bupa` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `niva-bupa` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/niva-bupa-health-insurance-claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `niva-bupa` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/max-bupa-health-general-reviews-477/` | URL reachable but no source_quote provided |
| `niva-bupa` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/niva-bupa-health-insurance/user-reviews` | URL reachable but no source_quote provided |
| `niva-bupa` | `aggregator_ratings.mouthshut` | ⚠️ url-ok-quote-missing | `https://www.mouthshut.com/product-reviews/niva-bupa-health-insurance-reviews-925608449` | URL reachable but no source_quote provided |
| `niva-bupa` | `aggregator_ratings.justdial` | ⚠️ url-ok-quote-missing | `https://www.justdial.com/Mumbai/Niva-Bupa-Health-Insurance-Company-Ltd-Customer-Care/022PXX22-XX22-121222183516-A3L5_BZDET/reviews` | URL reachable but no source_quote provided |
| `niva-bupa` | `trustpilot.url` | ⚠️ url-ok-quote-missing | `https://www.mouthshut.com/product-reviews/niva-bupa-health-insurance-reviews-925608449` | URL reachable but no source_quote provided |
| `niva-bupa` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `http://www.quora.com/Has-anyone-taken-Niva-Bupa-health-insurance-in-India-How-reliable-is-it` | URL reachable but no source_quote provided |
| `niva-bupa` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/health-insurance/companies/hdfc-ergo-vs-niva-bupa/` | URL reachable but no source_quote provided |
| `niva-bupa` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://insurancesabha.com/t/niva-bupa-reviews-complaints-csr-2026/26/2` | URL reachable but no source_quote provided |
| `niva-bupa` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.beshak.org/forum/post/niva-bupa-claim-settlement-in-recent-times/` | URL reachable but no source_quote provided |
| `niva-bupa` | `reddit.sample_post_urls[4]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `niva-bupa` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=U1kNh-rRuXo` | URL reachable but no source_quote provided |
| `niva-bupa` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=2_EhrtJhn44` | URL reachable but no source_quote provided |
| `niva-bupa` | `youtube[2].Gurleen Kaur Tikku` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=ZjjIteHHGqI` | URL reachable but no source_quote provided |
| `niva-bupa` | `youtube[3].Insurance Impact` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=892Pbg3XNg4` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/markets/news/niva-bupa-shares-make-positive-debut-on-bourses-list-at-6-premium-124111400326_1.html` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://www.bupa.com/news-and-press/press-releases/2024/bupa-indian-health-insurance-business-niva-bupa-completes-ipo` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/insurance-firm-faces-social-media-backlash-over-61-lakh-claim-denial-125090101059_1.html` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.livelaw.in/consumer-cases/mumbai-consumer-commission-orders-niva-bupa-to-pay-6650-lakh-for-wrongfully-denying-cancer-treatment-claim-311849` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://www.outlookmoney.com/insurance/rs-61-lakh-health-insurance-claim-denied-familys-ordeal-sparks-outrage` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[5]` | ⚠️ url-ok-quote-missing | `https://www.outlookmoney.com/insurance/those-labelling-health-insurance-sector-as-scam-are-highly-irresponsible-misleading-niva-bupa` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[6]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html` | URL reachable but no source_quote provided |
| `niva-bupa` | `in_news[7]` | ⚠️ url-ok-quote-missing | `https://upstox.com/news/market-news/ipo/understanding-niva-bupa-s-market-position-growth-claim-settlement-and-retention-metrics-compared-with-industry-peers/article-127535/` | URL reachable but no source_quote provided |
| `star-health` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `star-health` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html` | URL in browser_verified allowlist | quote not found in fetched body |
| `star-health` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/star-health-insurance-reviews-881/` | URL reachable but no source_quote provided |
| `star-health` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/star/user-reviews` | URL reachable but no source_quote provided |
| `star-health` | `aggregator_ratings.mouthshut` | ⚠️ url-ok-quote-missing | `https://www.mouthshut.com/product-reviews/star-health-insurance-reviews-925865246` | URL reachable but no source_quote provided |
| `star-health` | `trustpilot.url` | ⚠️ url-ok-quote-missing | `https://www.trustpilot.com/review/www.starhealth.in` | URL reachable but no source_quote provided |
| `star-health` | `reddit.sample_post_urls[0]` | ❌ url-broken | `https://www.quora.com/Is-Star-Health-Insurance-good-Has-anybody-experienced-problems-in-claim-settlement` | HEAD returned status=403 |
| `star-health` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://technofino.in/community/threads/star-health-insurance-renew-or-port.35180/` | URL reachable but no source_quote provided |
| `star-health` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `star-health` | `reddit.sample_post_urls[3]` | ❌ url-broken | `https://www.quora.com/Is-Star-Health-Insurance-a-good-company` | HEAD returned status=403 |
| `star-health` | `youtube[0].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=k5ZltTqzSrY` | URL reachable but no source_quote provided |
| `star-health` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=_U81lPwqsk4` | URL reachable but no source_quote provided |
| `star-health` | `youtube[2].MJ (MJ Money)` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=j1YBBmt7VZI` | URL reachable but no source_quote provided |
| `star-health` | `youtube[3].Gurleen Kaur Tikku` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=Vs4OjCEU_gc` | URL reachable but no source_quote provided |
| `star-health` | `in_news[0]` | ⚠️ url-ok-quote-missing | `https://www.businesstoday.in/personal-finance/insurance/story/star-health-under-irdai-lens-over-claim-settlement-insurer-says-routine-process-469373-2025-03-25` | URL reachable but no source_quote provided |
| `star-health` | `in_news[1]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/insurance/irdai-issues-show-cause-notice-to-star-health-for-violating-norms-124120601299_1.html` | URL reachable but no source_quote provided |
| `star-health` | `in_news[2]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/markets/news/star-health-drops-5-as-irdai-issues-show-cause-notice-for-violating-norms-124120900239_1.html` | URL reachable but no source_quote provided |
| `star-health` | `in_news[3]` | ⚠️ url-ok-quote-missing | `https://www.business-standard.com/finance/personal-finance/star-health-care-niva-bupa-record-most-policyholder-complaints-in-fy24-125090100487_1.html` | URL reachable but no source_quote provided |
| `star-health` | `in_news[4]` | ⚠️ url-ok-quote-missing | `https://techcrunch.com/2024/10/09/indias-star-health-confirms-data-breach-after-cybercriminals-post-customers-health-data-online/` | URL reachable but no source_quote provided |
| `star-health` | `in_news[5]` | ⚠️ url-ok-quote-missing | `https://the420.in/star-health-under-fire-irdai-scrutinizes-high-claim-rejections-delays/` | URL reachable but no source_quote provided |
| `star-health` | `in_news[6]` | ⚠️ url-ok-quote-missing | `https://www.outlookmoney.com/insurance/health-insurance/star-health-under-irdai-scrutiny-for-health-insurance-claim-settlement-practices` | URL reachable but no source_quote provided |
| `tata-aig` | `claim_metrics.source_irdai_url` | ⚠️ url-ok-quote-missing | `https://irdai.gov.in/document-detail?documentId=6436847` | URL reachable but quote not found in fetched body |
| `tata-aig` | `claim_metrics.source_secondary_url` | ⚠️ url-ok-quote-missing | `https://www.policyx.com/health-insurance/tata-aig-health-insurance/claim-settlement-ratio/` | URL reachable but quote not found in fetched body |
| `tata-aig` | `aggregator_ratings.policybazaar` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/insurance-companies/tata-aig-health-insurance/claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `tata-aig` | `aggregator_ratings.insuredekho` | ⚠️ url-ok-quote-missing | `https://www.insurancedekho.com/health-insurance/tata-aig/claim-settlement` | URL reachable but no source_quote provided |
| `tata-aig` | `aggregator_ratings.joinditto` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `tata-aig` | `reddit.sample_post_urls[0]` | ⚠️ url-ok-quote-missing | `https://www.beshak.org/insurance/health-insurance/best-health-insurance-plans/tata-aig-medi-care/` | URL reachable but no source_quote provided |
| `tata-aig` | `reddit.sample_post_urls[1]` | ⚠️ url-ok-quote-missing | `https://www.policybazaar.com/health-insurance/companies/aditya-birla-vs-tata-aig/` | URL reachable but no source_quote provided |
| `tata-aig` | `reddit.sample_post_urls[2]` | ⚠️ url-ok-quote-missing | `https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-claim-settlement-ratio/` | URL reachable but no source_quote provided |
| `tata-aig` | `reddit.sample_post_urls[3]` | ⚠️ url-ok-quote-missing | `https://www.indiainvestments.wiki/start-here/insurance-policies/health` | URL reachable but no source_quote provided |
| `tata-aig` | `youtube[1].Ditto Insurance` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=Drg-E3bxekU` | URL reachable but no source_quote provided |
| `tata-aig` | `youtube[2].Insurance Impact` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=hNSQlPwFGdY` | URL reachable but no source_quote provided |
| `tata-aig` | `youtube[3].Gurleen Kaur Tikku / Independent` | ⚠️ url-ok-quote-missing | `https://www.youtube.com/watch?v=696mpVFVehU` | URL reachable but no source_quote provided |

_... and 6 more rows truncated; see eval/info_source_map.json for full data._

## Insurers / Policies with 100% verified claims

_None._

## Records with remaining ⚠️ url-ok-quote-missing

| Record | ✅ | ⚠️ | ❌ |
|---|---:|---:|---:|
| aditya-birla | 0 | 20 | 0 |
| aditya-birla__activ-assure-diamond | 10 | 9 | 0 |
| aditya-birla__activ-health | 11 | 3 | 0 |
| aditya-birla__activ-health-individual__wordings | 6 | 1 | 0 |
| aditya-birla__activ-one | 13 | 6 | 0 |
| aditya-birla__activ-secure-cancer-secure__brochure | 3 | 1 | 0 |
| aditya-birla__activ-secure-personal-accident-cancer-secure__wordings | 4 | 1 | 0 |
| aditya-birla__group-activ-health__wordings | 5 | 1 | 0 |
| bajaj-allianz | 0 | 21 | 0 |
| bajaj-allianz__comprehensive-care-plan | 6 | 2 | 0 |
| bajaj-allianz__criti-care__wordings | 1 | 1 | 0 |
| bajaj-allianz__extra-care-plus | 12 | 6 | 0 |
| bajaj-allianz__global-health-care | 11 | 2 | 0 |
| bajaj-allianz__group-health-guard-silver__wordings | 6 | 1 | 0 |
| bajaj-allianz__group-personal-accident__wordings | 0 | 1 | 0 |
| bajaj-allianz__health-guard | 14 | 2 | 0 |
| bajaj-allianz__health-guard-gold | 10 | 9 | 0 |
| bajaj-allianz__health-guard-gold-individual__wordings | 8 | 1 | 0 |
| bajaj-allianz__silver-health | 6 | 4 | 0 |
| bajaj-allianz__tax-gain | 4 | 5 | 0 |
| care-health | 0 | 20 | 1 |
| care-health__care-advantage | 11 | 3 | 0 |
| care-health__care-advantage-add-ons-protect-plus-care-shield__brochure | 5 | 2 | 0 |
| care-health__care-classic | 6 | 12 | 0 |
| care-health__care-heart__brochure | 7 | 1 | 0 |
| care-health__care-senior | 12 | 8 | 0 |
| care-health__care-supreme | 9 | 10 | 0 |
| care-health__care-supreme-enhance | 9 | 3 | 0 |
| care-health__supreme-enhance__brochure | 7 | 1 | 0 |
| care-health__ultimate-care | 8 | 3 | 0 |
| hdfc-ergo | 0 | 23 | 0 |
| hdfc-ergo__energy | 8 | 3 | 0 |
| hdfc-ergo__energy-diabetes-hypertension__wordings | 3 | 1 | 0 |
| hdfc-ergo__group-health-insurance__wordings | 6 | 1 | 0 |
| hdfc-ergo__my-health-medisure-prime | 8 | 3 | 0 |
| hdfc-ergo__my-health-sampoorna-suraksha | 8 | 3 | 0 |
| hdfc-ergo__my-health-suraksha | 12 | 3 | 0 |
| hdfc-ergo__my-health-women-suraksha | 3 | 4 | 0 |
| hdfc-ergo__my-optima-secure-older-variant__wordings | 7 | 1 | 0 |
| hdfc-ergo__my-optima-secure__wordings | 5 | 1 | 0 |
| hdfc-ergo__optima-enhance | 6 | 3 | 0 |
| hdfc-ergo__optima-plus | 8 | 3 | 0 |
| hdfc-ergo__optima-restore | 15 | 5 | 0 |
| hdfc-ergo__optima-secure | 15 | 6 | 0 |
| hdfc-ergo__optima-secure-older-variant | 9 | 3 | 0 |
| hdfc-ergo__total-health-plan | 10 | 3 | 0 |
| icici-lombard | 0 | 20 | 0 |
| icici-lombard__arogya-sanjeevani | 12 | 1 | 0 |
| icici-lombard__complete-health-insurance | 9 | 10 | 0 |
| icici-lombard__complete-health-insurance-health-shield__wordings | 8 | 1 | 0 |
| icici-lombard__complete-health-insurance-umbrella__wordings | 8 | 1 | 0 |
| icici-lombard__complete-health-umbrella | 14 | 1 | 0 |
| icici-lombard__elevate | 10 | 9 | 0 |
| icici-lombard__health-advantedge | 10 | 5 | 0 |
| icici-lombard__health-booster | 9 | 3 | 0 |
| icici-lombard__health-booster-top-up__wordings | 5 | 1 | 0 |
| icici-lombard__health-elite-plus | 11 | 3 | 0 |
| icici-lombard__health-shield-360 | 4 | 11 | 0 |
| icici-lombard__health-shield-360-retail__cis | 7 | 1 | 0 |
| icici-lombard__health-shield-360-retail__wordings | 5 | 1 | 0 |
| manipalcigna | 0 | 19 | 0 |
| manipalcigna__prohealth-insurance-all-variants__wordings | 10 | 1 | 0 |
| manipalcigna__prohealth-prime | 9 | 9 | 0 |
| manipalcigna__prohealth-prime-active | 0 | 1 | 0 |
| manipalcigna__prohealth-protect | 9 | 8 | 0 |
| manipalcigna__prohealth-select | 10 | 3 | 0 |
| manipalcigna__sarvah-param | 8 | 2 | 0 |
| new-india | 0 | 19 | 0 |
| new-india__asha-kiran | 7 | 4 | 0 |
| new-india__asha-kiran-policy__brochure | 3 | 1 | 0 |
| new-india__asha-kiran-policy__cis | 7 | 1 | 0 |
| new-india__floater-mediclaim | 12 | 4 | 0 |
| new-india__janata-mediclaim | 10 | 2 | 0 |
| new-india__janata-mediclaim-policy__wordings | 7 | 1 | 0 |
| new-india__mediclaim | 0 | 1 | 0 |
| new-india__mediclaim-policy | 9 | 3 | 0 |
| new-india__new-india-floater-mediclaim-policy__wordings | 9 | 1 | 0 |
| new-india__new-india-mediclaim-policy__brochure | 9 | 1 | 0 |
| new-india__new-india-mediclaim-policy__wordings | 9 | 1 | 0 |
| new-india__universal-health | 7 | 2 | 0 |
| new-india__universal-health-insurance__wordings | 3 | 1 | 0 |
| new-india__yuva-bharat | 9 | 3 | 0 |
| new-india__yuva-bharat-health-policy__wordings | 7 | 1 | 0 |
| niva-bupa | 0 | 24 | 0 |
| niva-bupa__aspire | 11 | 2 | 0 |
| niva-bupa__health-companion | 6 | 10 | 0 |
| niva-bupa__health-companion-v2022__brochure | 8 | 1 | 0 |
| niva-bupa__health-plus-top-up | 9 | 3 | 0 |
| niva-bupa__health-premia | 9 | 4 | 0 |
| niva-bupa__reassure | 0 | 2 | 0 |
| niva-bupa__reassure-2 | 10 | 6 | 0 |
| niva-bupa__reassure-2-0__wordings | 3 | 1 | 0 |
| niva-bupa__reassure-3 | 10 | 3 | 0 |
| niva-bupa__reassure-3-0__wordings | 4 | 1 | 0 |
| niva-bupa__rise | 10 | 1 | 0 |
| niva-bupa__saral-suraksha | 6 | 2 | 0 |
| niva-bupa__saral-suraksha-bima__wordings | 4 | 1 | 0 |
| niva-bupa__senior-first | 12 | 5 | 0 |
| premiums_meta | 0 | 15 | 9 |
| star-health | 0 | 19 | 2 |
| star-health__comprehensive | 0 | 1 | 0 |
| star-health__family-health-optima | 8 | 11 | 0 |
| star-health__health-premier | 9 | 1 | 0 |
| star-health__senior-citizens-red-carpet | 5 | 3 | 0 |
| star-health__star-assure | 9 | 2 | 0 |
| star-health__star-cancer-care-platinum__wordings | 8 | 1 | 0 |
| star-health__star-cardiac-care | 8 | 2 | 0 |
| star-health__star-cardiac-care-platinum | 7 | 3 | 0 |
| star-health__star-comprehensive | 10 | 8 | 0 |
| star-health__star-hospital-cash__brochure | 2 | 1 | 0 |
| tata-aig | 0 | 18 | 0 |
| tata-aig__criti-medicare__wordings | 2 | 1 | 0 |
| tata-aig__medicare | 4 | 15 | 0 |
| tata-aig__medicare-lite | 12 | 2 | 0 |
| tata-aig__medicare-premier | 6 | 16 | 0 |
| tata-aig__medicare-select | 11 | 2 | 0 |
| tata-aig__wellsurance-family__cis | 2 | 1 | 0 |

---

**Audit complete: ✅ 798 / ⚠️ 571 / ❌ 12**