# Premium Source Map

| Field | Value |
| --- | --- |
| Document type | Source-methodology catalog (data-provenance audit) |
| Subject data file | `40-data/premiums/illustrative_premiums.json` |
| `last_updated` (data file) | `2026-05-13` |
| Generated (this doc) | 2026-05-18 |
| Raw agent outputs | `/tmp/research_out_1.json` … `/tmp/research_out_6.json` |
| Companion | [`premium-dependency-map.md`](premium-dependency-map.md) |

## 0. Purpose

This document is the **authoritative provenance catalog for every evidenced premium sample** the bot prices against. It is the premium-pricing analogue of [`70-docs/20-data-pipeline/information-source-map.md`](../../20-data-pipeline/information-source-map.md): for each `base_premiums` entry that carries at least one *evidenced* sample (a sample with a verbatim `source_quote` **and** a `fetched_on` timestamp from the 2026-05-18 premium harvest), every sample is listed with its profile, the figure it contributes, the specific source page, the trimmed verbatim quote, the source-quality tag, and the fetch date.

A reviewer can use this file to answer two questions:

1. **"Is this premium number real?"** → look up the policy + profile row; the `source_quote` is the verbatim text the figure was read from.
2. **"Which policies are still model-only?"** → §3 lists every entry with **zero** evidenced samples (priced by `_attribute_base_factor` type-model, never a fabricated quote).

This document does **not** modify the JSON — it is read-only documentation. The integrity gate in the harvest already rejected bare-homepage URLs and quotes lacking a number+profile; §4 independently re-verifies that none slipped through.

## 1. Summary

| Metric | Value |
| --- | --- |
| Total `base_premiums` entries | 100 |
| Entries WITH ≥1 evidenced sample | 73 |
| Entries WITHOUT any evidenced sample (model-only) | 27 |
| Total evidenced samples | 194 |
| Distinct source domains | 22 |
| Samples sourced from an insurer-official PDF / rate-chart | 133 |
| Samples sourced from an aggregator / comparison site | 47 |
| Samples from other insurer-site HTML pages | 14 |
| Evidenced samples carrying a `source_quality` tag | 194 |

`source_quality` distribution: `insurer_site` × 152, `aggregator_quote` × 42.

Top source domains: `acko-cms.ackoassets.com` (27), `joinditto.in` (23), `cms.careinsurance.com` (18), `iffcotokio.co.in` (18), `newindia.co.in` (15), `probusinsurance.com` (14), `assets.ctfassets.net` (11), `nationalinsurance.nic.co.in` (11), `content.sbigeneral.in` (7), `godigit.com` (6), `tataaig.com` (6), `bajajgeneralinsurance.com` (5).

> **Provenance note.** `source_quality` is the harvester's own page-class tag (`insurer_site` = read off an insurer-owned page/PDF; `aggregator_quote` = read off a comparison portal). The *insurer-official-PDF* count above is computed independently here by URL heuristic (`.pdf` and not an aggregator host); the two need not match exactly because some insurer-site samples are HTML rate pages, not PDFs.

## 2. Evidenced samples — grouped by insurer

Each row is one sample inside that policy's `samples[]`. `annual_premium_inr` is the figure the calculator anchors to (before profile normalization — see the dependency map). Quotes are trimmed to ≤160 chars; the untrimmed text is in the JSON.

### Acko

**`acko__acko-health-ii`** · Acko Health Ii · UIN `ACKHLIP26036V012526`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 20 / 3L / tier2 / fs1 | ₹4,234 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,234 3,00,000 ... 21 - 25 6,306 3,00,000 ... 31 - 35 6,306 3,00,000 ... 36 - 40 7,742 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 23 / 3L / tier2 / fs1 | ₹6,306 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,234 3,00,000 ... 21 - 25 6,306 3,00,000 ... 31 - 35 6,306 3,00,000 ... 36 - 40 7,742 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 33 / 3L / tier2 / fs1 | ₹6,306 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,234 3,00,000 ... 21 - 25 6,306 3,00,000 ... 31 - 35 6,306 3,00,000 ... 36 - 40 7,742 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 38 / 3L / tier2 / fs1 | ₹7,742 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,234 3,00,000 ... 21 - 25 6,306 3,00,000 ... 31 - 35 6,306 3,00,000 ... 36 - 40 7,742 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 53 / 3L / tier2 / fs1 | ₹17,152 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,234 3,00,000 ... 21 - 25 6,306 3,00,000 ... 31 - 35 6,306 3,00,000 ... 36 - 40 7,742 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 58 / 3L / tier2 / fs1 | ₹22,559 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,234 3,00,000 ... 21 - 25 6,306 3,00,000 ... 31 - 35 6,306 3,00,000 ... 36 - 40 7,742 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 18 / 3L / metro / fs1 | ₹7,567 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Illustration 2 ... 16 - 20 7,567 3,00,000 ... 21 - 25 7,567 3,00,000 ... 41 - 45 11,357 3,00,000 ... 46 - 50 15,216 3,00,000 ... 71- 75 65,624 3,00,000 ... 7… | `insurer_site` | 2026-05-18 |
| age 43 / 3L / metro / fs1 | ₹11,357 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Illustration 2 ... 16 - 20 7,567 3,00,000 ... 21 - 25 7,567 3,00,000 ... 41 - 45 11,357 3,00,000 ... 46 - 50 15,216 3,00,000 ... 71- 75 65,624 3,00,000 ... 7… | `insurer_site` | 2026-05-18 |
| age 48 / 3L / metro / fs1 | ₹15,216 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_II_93c2be4860.pdf) | Illustration 2 ... 16 - 20 7,567 3,00,000 ... 21 - 25 7,567 3,00,000 ... 41 - 45 11,357 3,00,000 ... 46 - 50 15,216 3,00,000 ... 71- 75 65,624 3,00,000 ... 7… | `insurer_site` | 2026-05-18 |

**`acko__acko-health-iii`** · Acko Health Iii · UIN `ACKHLIP27040V012627`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 20 / 3L / tier2 / fs1 | ₹4,485 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,485 3,00,000 ... 21 - 25 6,680 3,00,000 ... 31 - 35 6,680 3,00,000 ... 36 - 40 8,200 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 23 / 3L / tier2 / fs1 | ₹6,680 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,485 3,00,000 ... 21 - 25 6,680 3,00,000 ... 31 - 35 6,680 3,00,000 ... 36 - 40 8,200 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 33 / 3L / tier2 / fs1 | ₹6,680 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,485 3,00,000 ... 21 - 25 6,680 3,00,000 ... 31 - 35 6,680 3,00,000 ... 36 - 40 8,200 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 38 / 3L / tier2 / fs1 | ₹8,200 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,485 3,00,000 ... 21 - 25 6,680 3,00,000 ... 31 - 35 6,680 3,00,000 ... 36 - 40 8,200 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 53 / 3L / tier2 / fs1 | ₹18,168 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,485 3,00,000 ... 21 - 25 6,680 3,00,000 ... 31 - 35 6,680 3,00,000 ... 36 - 40 8,200 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 58 / 3L / tier2 / fs1 | ₹23,895 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 4,485 3,00,000 ... 21 - 25 6,680 3,00,000 ... 31 - 35 6,680 3,00,000 ... 36 - 40 8,200 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 18 / 3L / metro / fs1 | ₹8,015 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Illustration 2 ... 16 - 20 8,015 3,00,000 ... 21 - 25 8,015 3,00,000 ... 41 - 45 12,030 3,00,000 ... 46 - 50 16,117 3,00,000 ... 71 - 75 69,509 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 43 / 3L / metro / fs1 | ₹12,030 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Illustration 2 ... 16 - 20 8,015 3,00,000 ... 21 - 25 8,015 3,00,000 ... 41 - 45 12,030 3,00,000 ... 46 - 50 16,117 3,00,000 ... 71 - 75 69,509 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 48 / 3L / metro / fs1 | ₹16,117 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Acko_Health_III_8d36488120.pdf) | Illustration 2 ... 16 - 20 8,015 3,00,000 ... 21 - 25 8,015 3,00,000 ... 41 - 45 12,030 3,00,000 ... 46 - 50 16,117 3,00,000 ... 71 - 75 69,509 3,00,000 ...… | `insurer_site` | 2026-05-18 |

**`acko__acko-personal-health`** · Acko Personal Health · UIN `ACKHLIP23114V012223`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 20 / 3L / tier2 / fs1 | ₹2,789 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 23 / 3L / tier2 / fs1 | ₹4,614 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 33 / 3L / tier2 / fs1 | ₹4,614 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 38 / 3L / tier2 / fs1 | ₹5,318 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 53 / 3L / tier2 / fs1 | ₹9,760 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 58 / 3L / tier2 / fs1 | ₹12,286 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 63 / 3L / tier2 / fs1 | ₹14,817 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 68 / 3L / tier2 / fs1 | ₹18,223 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Annexure 2: Benefit Illustration Illustration 1 ... 0 - 15 2,789 3,00,000 ... 21 - 25 4,614 3,00,000 ... 31 - 35 4,614 3,00,000 ... 36 - 40 5,318 3,00,000 ..… | `insurer_site` | 2026-05-18 |
| age 18 / 3L / metro / fs1 | ₹5,075 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Illustration 2 ... 16 - 20 5,075 3,00,000 ... 21 - 25 5,075 3,00,000 ... 41 - 45 6,559 3,00,000 ... 46 - 50 9,390 3,00,000 ... 71- 75 20,046 3,00,000 ... 76… | `insurer_site` | 2026-05-18 |
| age 43 / 3L / metro / fs1 | ₹6,559 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Illustration 2 ... 16 - 20 5,075 3,00,000 ... 21 - 25 5,075 3,00,000 ... 41 - 45 6,559 3,00,000 ... 46 - 50 9,390 3,00,000 ... 71- 75 20,046 3,00,000 ... 76… | `insurer_site` | 2026-05-18 |
| age 48 / 3L / metro / fs1 | ₹9,390 | [assets.ctfassets.net…](https://assets.ctfassets.net/uwf0n1j71a7j/25TEE8WpUiVF72r63DRHdP/e41cdc19d2da5a1ad08a2920ac2d085a/acko-personal-health-policy-prospectus.pdf) | Illustration 2 ... 16 - 20 5,075 3,00,000 ... 21 - 25 5,075 3,00,000 ... 41 - 45 6,559 3,00,000 ... 46 - 50 9,390 3,00,000 ... 71- 75 20,046 3,00,000 ... 76… | `insurer_site` | 2026-05-18 |

**`acko__arogya-sanjeevani`** · Arogya Sanjeevani · UIN `ACKHLIP20183V011920`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 18 / 3L / tier2 / fs1 | ₹3,431 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 23 / 3L / tier2 / fs1 | ₹3,431 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 33 / 3L / tier2 / fs1 | ₹3,897 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 38 / 3L / tier2 / fs1 | ₹3,897 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 53 / 3L / tier2 / fs1 | ₹6,694 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 58 / 3L / tier2 / fs1 | ₹8,219 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 63 / 3L / tier2 / fs1 | ₹9,957 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | ANNEXURE: BENEFIT ILLUSTRATION Illustration 1 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 31 - 35 3,897 3,00,000 ... 36 - 40 3,897 3,00,000 ...… | `insurer_site` | 2026-05-18 |
| age 43 / 3L / tier2 / fs1 | ₹4,491 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | Illustration 2 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 41 - 45 4,491 3,00,000 ... 46 - 50 6,101 3,00,000 | `insurer_site` | 2026-05-18 |
| age 48 / 3L / tier2 / fs1 | ₹6,101 | [acko-cms.ackoassets.com…](https://acko-cms.ackoassets.com/Prospectus_Arogya_Sanjeevani_Policy_d985e9910d.pdf) | Illustration 2 ... 16 - 20 3,431 3,00,000 ... 21 - 25 3,431 3,00,000 ... 41 - 45 4,491 3,00,000 ... 46 - 50 6,101 3,00,000 | `insurer_site` | 2026-05-18 |

### Aditya Birla Health

**`aditya-birla__activ-secure-personal-accident-cancer-secure`** · Activ Secure Personal Accident Cancer Secure · UIN `ADIHLIP18076V011718`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 39 / 10L / metro / fs1 | ₹5,863 | [adityabirlacapital.com…](https://www.adityabirlacapital.com/healthinsurance/assets/pdf/Activ-Secure-Prospectus.pdf) | Mr. Shankar ... they choose to buy P.A plan 3(10 L each all members) and C.I plan 2 (10L for self and 5L each for spouse and children) ... Self( age 39)- 10L… | `insurer_site` | 2026-05-18 |
| age 38 / 5L / metro / fs1 | ₹3,242 | [adityabirlacapital.com…](https://www.adityabirlacapital.com/healthinsurance/assets/pdf/Activ-Secure-Prospectus.pdf) | Mr. Shankar ... they choose to buy P.A plan 3(10 L each all members) and C.I plan 2 (10L for self and 5L each for spouse and children) ... Self( age 39)- 10L… | `insurer_site` | 2026-05-18 |
| age 10 / 5L / metro / fs1 | ₹1,430 | [adityabirlacapital.com…](https://www.adityabirlacapital.com/healthinsurance/assets/pdf/Activ-Secure-Prospectus.pdf) | Mr. Shankar ... they choose to buy P.A plan 3(10 L each all members) and C.I plan 2 (10L for self and 5L each for spouse and children) ... Self( age 39)- 10L… | `insurer_site` | 2026-05-18 |
| age 8 / 5L / metro / fs1 | ₹1,430 | [adityabirlacapital.com…](https://www.adityabirlacapital.com/healthinsurance/assets/pdf/Activ-Secure-Prospectus.pdf) | Mr. Shankar ... they choose to buy P.A plan 3(10 L each all members) and C.I plan 2 (10L for self and 5L each for spouse and children) ... Self( age 39)- 10L… | `insurer_site` | 2026-05-18 |

### Bajaj Allianz

**`bajaj-allianz__extra-care-plus`** · Extra Care Plus · UIN `BAJHLIP23069V032223`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 55 / 10L / metro / fs4 | ₹7,525 | [bajajgeneralinsurance.com…](https://www.bajajgeneralinsurance.com/download-documents/health-insurance/extra-care-plus/Extra_Care_Plus_brochure.pdf) | Beneﬁt Illustration in respect of Policies offered on Floater basis ... Age of the members to be insured 55 50 20 18 ... Premium ... 7,525 ... Sum Insured/De… | `insurer_site` | 2026-05-18 |

**`bajaj-allianz__health-guard-gold-individual`** · Health Guard Gold Individual · UIN `BAJHLIP26073V082526`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 18 / 3L / metro / fs1 | ₹6,894 | [bajajgeneralinsurance.com…](https://www.bajajgeneralinsurance.com/download-documents/health-insurance/health-guard/Health-Guard-Brochure-print.pdf) | Benefit Illustration in respect of Policies offered on Individual & Family Floater basis Age of the members to be insured ... Premium (for Zone A) Sum Insure… | `insurer_site` | 2026-05-18 |
| age 21 / 3L / metro / fs1 | ₹11,244 | [bajajgeneralinsurance.com…](https://www.bajajgeneralinsurance.com/download-documents/health-insurance/health-guard/Health-Guard-Brochure-print.pdf) | Benefit Illustration in respect of Policies offered on Individual & Family Floater basis Age of the members to be insured ... Premium (for Zone A) Sum Insure… | `insurer_site` | 2026-05-18 |
| age 40 / 3L / metro / fs1 | ₹14,805 | [bajajgeneralinsurance.com…](https://www.bajajgeneralinsurance.com/download-documents/health-insurance/health-guard/Health-Guard-Brochure-print.pdf) | Benefit Illustration in respect of Policies offered on Individual & Family Floater basis Age of the members to be insured ... Premium (for Zone A) Sum Insure… | `insurer_site` | 2026-05-18 |
| age 45 / 3L / metro / fs1 | ₹17,653 | [bajajgeneralinsurance.com…](https://www.bajajgeneralinsurance.com/download-documents/health-insurance/health-guard/Health-Guard-Brochure-print.pdf) | Benefit Illustration in respect of Policies offered on Individual & Family Floater basis Age of the members to be insured ... Premium (for Zone A) Sum Insure… | `insurer_site` | 2026-05-18 |

### Care Health

**`care-health__care-advantage-add-ons-protect-plus-care-shield`** · Care Advantage Add Ons Protect Plus Care Shield · UIN `CHIHLIP26049V042526`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 14 / 25L / metro / fs1 | ₹5,111 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 22 / 25L / metro / fs1 | ₹8,588 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 24 / 25L / metro / fs1 | ₹8,588 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 40 / 25L / metro / fs1 | ₹11,864 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 44 / 25L / metro / fs1 | ₹13,654 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 61 / 25L / metro / fs1 | ₹53,659 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 71 / 25L / metro / fs1 | ₹96,095 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |
| age 75 / 25L / metro / fs1 | ₹121,318 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/care-advantage-(health-insurance-product)--prospectus-cum-sales-literature.pdf?rv=0.62429600+1621514455) | Annexure - VI Benefit/ Premium illustration Illustration 1 ... 44 13,654 25,00,000 13,654 NA 13,654 25,00,000 32,647 NA 32,647 25,00,000 40 11,864 25,00,000… | `insurer_site` | 2026-05-18 |

**`care-health__care-supreme-enhance`** · Care Supreme Enhance · UIN `CHIHLIP25036V012425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 10L / metro / fs1 | ₹1,972 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/supreme-enhance---prospectus-cum-sales-literature.pdf) | Annexure IV - Benefit / Premium illustration Illustration 1 ... 28 1972 10,00,000 ... 30 1972 10,00,000 ... Total Premium when policy is opted on floater bas… | `insurer_site` | 2026-05-18 |
| age 30 / 10L / metro / fs1 | ₹1,972 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/supreme-enhance---prospectus-cum-sales-literature.pdf) | Annexure IV - Benefit / Premium illustration Illustration 1 ... 28 1972 10,00,000 ... 30 1972 10,00,000 ... Total Premium when policy is opted on floater bas… | `insurer_site` | 2026-05-18 |
| age 17 / 10L / metro / fs1 | ₹1,750 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/supreme-enhance---prospectus-cum-sales-literature.pdf) | Annexure IV - Benefit / Premium illustration Illustration 1 ... 28 1972 10,00,000 ... 30 1972 10,00,000 ... Total Premium when policy is opted on floater bas… | `insurer_site` | 2026-05-18 |
| age 56 / 10L / metro / fs1 | ₹8,258 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/supreme-enhance---prospectus-cum-sales-literature.pdf) | Annexure IV - Benefit / Premium illustration Illustration 1 ... 28 1972 10,00,000 ... 30 1972 10,00,000 ... Total Premium when policy is opted on floater bas… | `insurer_site` | 2026-05-18 |
| age 60 / 10L / metro / fs1 | ₹8,258 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/supreme-enhance---prospectus-cum-sales-literature.pdf) | Annexure IV - Benefit / Premium illustration Illustration 1 ... 28 1972 10,00,000 ... 30 1972 10,00,000 ... Total Premium when policy is opted on floater bas… | `insurer_site` | 2026-05-18 |

**`care-health__ultimate-care`** · Ultimate Care · UIN `CHIHLIP26058V022526`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 17 / 5L / metro / fs1 | ₹6,491 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/ultimate-care---prospectus-cum-sales-literature.pdf) | Annexure V - Benefit / Premium illustration Illustration 1 ... 46 13,885 5,00,000 ... 51 17,402 5,00,000 ... Illustration 2 ... 46 13,885 5,00,000 ... 51 17,… | `insurer_site` | 2026-05-18 |
| age 46 / 5L / metro / fs1 | ₹13,885 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/ultimate-care---prospectus-cum-sales-literature.pdf) | Annexure V - Benefit / Premium illustration Illustration 1 ... 46 13,885 5,00,000 ... 51 17,402 5,00,000 ... Illustration 2 ... 46 13,885 5,00,000 ... 51 17,… | `insurer_site` | 2026-05-18 |
| age 51 / 5L / metro / fs1 | ₹17,402 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/ultimate-care---prospectus-cum-sales-literature.pdf) | Annexure V - Benefit / Premium illustration Illustration 1 ... 46 13,885 5,00,000 ... 51 17,402 5,00,000 ... Illustration 2 ... 46 13,885 5,00,000 ... 51 17,… | `insurer_site` | 2026-05-18 |
| age 61 / 5L / metro / fs1 | ₹32,898 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/ultimate-care---prospectus-cum-sales-literature.pdf) | Annexure V - Benefit / Premium illustration Illustration 1 ... 46 13,885 5,00,000 ... 51 17,402 5,00,000 ... Illustration 2 ... 46 13,885 5,00,000 ... 51 17,… | `insurer_site` | 2026-05-18 |
| age 64 / 5L / metro / fs1 | ₹39,948 | [cms.careinsurance.com…](https://cms.careinsurance.com/cms/public/uploads/download_center/ultimate-care---prospectus-cum-sales-literature.pdf) | Annexure V - Benefit / Premium illustration Illustration 1 ... 46 13,885 5,00,000 ... 51 17,402 5,00,000 ... Illustration 2 ... 46 13,885 5,00,000 ... 51 17,… | `insurer_site` | 2026-05-18 |

### Cholamandalam MS

**`cholamandalam__super-topup`** · Super Topup · UIN `CHOHLIP21561V012021`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 18 / 10L / metro / fs1 | ₹912 | [irdai.gov.in…](https://irdai.gov.in/documents/37343/931203/CHOHLIP21561V012021_2020-2021.pdf/cace22d1-c5c7-fc4c-3f58-6d80a024512d?version=1.1&t=1668584072276&download=true) | CHOLA FLEXI SUPER TOPUP INSURANCE -GOLD PLAN, Policy Period - ONE Year ... 18 912 Rs 10 Lakhs Sum insured with Rs 5 Lakhs Deductible 912 | `insurer_site` | 2026-05-18 |
| age 48 / 10L / metro / fs1 | ₹1,949 | [irdai.gov.in…](https://irdai.gov.in/documents/37343/931203/CHOHLIP21561V012021_2020-2021.pdf/cace22d1-c5c7-fc4c-3f58-6d80a024512d?version=1.1&t=1668584072276&download=true) | 48 1,949 Rs 10 Lakhs Sum insured with Rs 5 Lakhs Deductible 1,949 ... Total premium for all members of the family is Rs. 6691/-, when each member is covered… | `insurer_site` | 2026-05-18 |
| age 54 / 10L / metro / fs1 | ₹2,795 | [irdai.gov.in…](https://irdai.gov.in/documents/37343/931203/CHOHLIP21561V012021_2020-2021.pdf/cace22d1-c5c7-fc4c-3f58-6d80a024512d?version=1.1&t=1668584072276&download=true) | 54 2,795 Rs 10 Lakhs Sum insured with Rs 5 Lakhs Deductible 2,795 | `insurer_site` | 2026-05-18 |

### Go Digit

**`go-digit__arogya-sanjeevani`** · Arogya Sanjeevani · UIN `GODHLIP20168V011920`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 22 / 5L / metro / fs1 | ₹3,122 | [godigit.com…](https://www.godigit.com/content/dam/godigit/directportal/en/downloads/health/Arogya%20Sanjeevani%20-%20Prospectus%20(with%20premium%20Illustration).pdf) | Family Composition - 1A / Highest member Age Band ... 1 Lakh 2 Lakh 3 Lakh 4 Lakh 5 Lakh 10 Lakh 25 lakh 50 Lakh 1 Crore / 18-25 1,802 2,269 2,682 2,929 3,12… | `insurer_site` | 2026-05-18 |
| age 33 / 5L / metro / fs1 | ₹3,625 | [godigit.com…](https://www.godigit.com/content/dam/godigit/directportal/en/downloads/health/Arogya%20Sanjeevani%20-%20Prospectus%20(with%20premium%20Illustration).pdf) | Family Composition - 1A ... 5 Lakh ... 31-35 2,093 2,635 3,114 3,401 3,625 5,446 7,239 8,458 9,546 | `insurer_site` | 2026-05-18 |
| age 43 / 5L / metro / fs1 | ₹5,468 | [godigit.com…](https://www.godigit.com/content/dam/godigit/directportal/en/downloads/health/Arogya%20Sanjeevani%20-%20Prospectus%20(with%20premium%20Illustration).pdf) | Family Composition - 1A ... 5 Lakh ... 41-45 3,157 3,975 4,698 5,130 5,468 8,213 10,918 12,757 14,399 | `insurer_site` | 2026-05-18 |
| age 58 / 5L / metro / fs1 | ₹13,466 | [godigit.com…](https://www.godigit.com/content/dam/godigit/directportal/en/downloads/health/Arogya%20Sanjeevani%20-%20Prospectus%20(with%20premium%20Illustration).pdf) | Family Composition - 1A ... 5 Lakh ... 56-60 7,775 9,788 11,569 12,634 13,466 20,227 26,887 31,418 35,460 | `insurer_site` | 2026-05-18 |

**`go-digit__digit-health-care-plus`** · Digit Health Care Plus · UIN `GODHLIP21486V022021`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 51 / 5L / metro / fs4 | ₹8,162 | [d2h44aw7l5xdvz.cloudfront.net…](https://d2h44aw7l5xdvz.cloudfront.net/policyDocuments/Health/Digit%20Health%20Care%20Plus%20Policy_Benefit_Illustration.pdf) | 2Adults +2Children / 51 8,162 5,00,000 8,162 10% 7,346 5,00,000 14,568 NA 14,568 5,00,000 | `insurer_site` | 2026-05-18 |
| age 48 / 5L / metro / fs4 | ₹7,014 | [d2h44aw7l5xdvz.cloudfront.net…](https://d2h44aw7l5xdvz.cloudfront.net/policyDocuments/Health/Digit%20Health%20Care%20Plus%20Policy_Benefit_Illustration.pdf) | 48 7,014 5,00,000 7,014 10% 6,313 5,00,000 | `insurer_site` | 2026-05-18 |
| age 18 / 5L / metro / fs4 | ₹3,268 | [d2h44aw7l5xdvz.cloudfront.net…](https://d2h44aw7l5xdvz.cloudfront.net/policyDocuments/Health/Digit%20Health%20Care%20Plus%20Policy_Benefit_Illustration.pdf) | 18 3,268 5,00,000 3,268 10% 2,941 5,00,000 | `insurer_site` | 2026-05-18 |
| age 62 / 3L / metro / fs2 | ₹11,048 | [d2h44aw7l5xdvz.cloudfront.net…](https://d2h44aw7l5xdvz.cloudfront.net/policyDocuments/Health/Digit%20Health%20Care%20Plus%20Policy_Benefit_Illustration.pdf) | 2 Adults / 62 11,048 300,000 11,048 5% 10,496 300,000 20,104 NA 20,104 300,000 | `insurer_site` | 2026-05-18 |

**`go-digit__digit-top-up`** · Digit Top Up · UIN `GODHLIP24056V012324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 18 / 1Cr / metro / fs3 | ₹1,011 | [godigit.com…](https://www.godigit.com/content/dam/godigit/directportal/en/downloads/health/Prospectus_Digit%20Top%20Up%20Policy.pdf) | Plan Name Platinum Super Top Up / Family Composition 2A+1C / Sum Insured 1,00,00,000 / Deductible 10,00,000 / Policy Type Floater Individual ... 18 3,120 1,0… | `insurer_site` | 2026-05-18 |
| age 62 / 1Cr / metro / fs2 | ₹7,302 | [godigit.com…](https://www.godigit.com/content/dam/godigit/directportal/en/downloads/health/Prospectus_Digit%20Top%20Up%20Policy.pdf) | Plan Name Platinum Super Top Up / Family Composition 2A / Sum Insured 1,00,00,000 / Deductible 10,00,000 / Policy Type Floater Individual ... 62 10,953 7,302… | `insurer_site` | 2026-05-18 |

### HDFC ERGO

**`hdfc-ergo__energy-diabetes-hypertension`** · Energy Diabetes Hypertension · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 45 / 15L / metro / fs1 | ₹23,702 | [joinditto.in…](https://joinditto.in/articles/health-insurance/hdfc-ergo-energy-plan-diabetes-health-insurance-review/) | A 45-year-old living in Delhi pays ₹23,702 per year for a ₹15 lakh cover under the Silver variant. | `aggregator_quote` | 2026-05-18 |
| age 25 / 15L / metro / fs1 | ₹18,463 | [joinditto.in…](https://joinditto.in/articles/health-insurance/hdfc-ergo-energy-plan-diabetes-health-insurance-review/) | Premium Comparison Table (₹15 lakh Sum Insured, Delhi) / 25 / ₹18,463 / ₹23,463 | `aggregator_quote` | 2026-05-18 |

**`hdfc-ergo__total-health-plan`** · Total Health Plan · UIN `HDHHLIP21317V032021`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 35 / 5L / metro / fs1 | ₹5,599 | [hdfcergo.com…](https://www.hdfcergo.com/docs/default-source/downloads/policy-wordings/health/total-health-plan.pdf) | Product Name – Total Health Plan / Sum Insured - 5 Lakhs / Tenure – 1 Year ... 35 5,599 5 5,599 560 5,039 5 | `insurer_site` | 2026-05-18 |
| age 40 / 5L / metro / fs1 | ₹6,336 | [hdfcergo.com…](https://www.hdfcergo.com/docs/default-source/downloads/policy-wordings/health/total-health-plan.pdf) | 40 6,336 5 6,336 634 5,702 5 | `insurer_site` | 2026-05-18 |

**`hdfc-ergo__my-health-medisure-prime`** · My Health Medisure Prime · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹9,224 | [hdfcergo.com…](https://www.hdfcergo.com/docs/default-source/downloads/prospectus/myhealth-medisure-prime-insurance.pdf) | PREMIUM CHART ... Zone 1: Mumbai, Thane, Navi Mumbai, Delhi, and NCR Regions / SI (`) 500000 / Age - years 26-35 ... 9,224 | `insurer_site` | 2026-05-18 |

**`hdfc-ergo__my-optima-secure`** · My Optima Secure · UIN `HDFHLIP23123V022223`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 10L / metro / fs1 | ₹22,000 | [healthstatic.policybazaar.com…](https://healthstatic.policybazaar.com/health-insurance/Insurer_Document/HDFC/my-optima-secure-prospectus.pdf) | my: Optima Secure - Optima Secure Plan Gross Premium (Excl. GST) - Tier 1 ... Age 25 ... Sum Insured 10,00,000 ... 22,000 | `insurer_site` | 2026-05-18 |
| age 45 / 10L / metro / fs1 | ₹16,500 | [healthstatic.policybazaar.com…](https://healthstatic.policybazaar.com/health-insurance/Insurer_Document/HDFC/my-optima-secure-prospectus.pdf) | Premium Computation Illustration Illustration 2 Plan Name - Optima Secure Tenure - 1 Year Location - Delhi - Tier 1 ... 45 16,500 10 ... | `insurer_site` | 2026-05-18 |
| age 55 / 10L / metro / fs1 | ₹32,500 | [healthstatic.policybazaar.com…](https://healthstatic.policybazaar.com/health-insurance/Insurer_Document/HDFC/my-optima-secure-prospectus.pdf) | Illustration 2 Plan Name - Optima Secure ... Location - Delhi - Tier 1 ... 55 32,500 10 32,500 3,250 29,250 10 ... | `insurer_site` | 2026-05-18 |
| age 30 / 5L / metro / fs1 | ₹11,000 | [healthstatic.policybazaar.com…](https://healthstatic.policybazaar.com/health-insurance/Insurer_Document/HDFC/my-optima-secure-prospectus.pdf) | Optima Secure Plan Gross Premium (Excl. GST) - Tier 1 ... 30 11,000 13,500 14,400 15,200 15,950 19,000 23,000 29,000 | `insurer_site` | 2026-05-18 |

**`hdfc-ergo__my-optima-secure-older-variant`** · My Optima Secure Older Variant · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹14,130 | [joinditto.in…](https://joinditto.in/articles/health-insurance/hdfc-ergo-health-insurance-premium-chart/) | Popular HDFC ERGO Health Insurance Plans With Premium Charts / (Individual Plan): Age 25 / Optima Secure / Rs.14,130 ... 'Unless mentioned otherwise, the pre… | `aggregator_quote` | 2026-05-18 |
| age 25 / 10L / metro / fs1 | ₹22,172 | [healthstatic.policybazaar.com…](https://healthstatic.policybazaar.com/health-insurance/Insurer_Document/HDFC/my-optima-secure-prospectus.pdf) | my: Optima Secure - Optima Super Secure Plan Gross Premium (Excl. GST) - Tier 1 ... 25 10,571 12,762 13,646 14,420 15,144 18,184 22,172 27,706 | `insurer_site` | 2026-05-18 |

### ICICI Lombard

**`icici-lombard__complete-health-insurance-health-shield`** · Complete Health Insurance Health Shield · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹6,477 | [policyx.com…](https://www.policyx.com/health-insurance/icici-lombard-health-insurance/complete-health-insurance.php) | Let's see how the premium varies across different types and sum insured for an individual at the age of 30 and for a policy term of 1 year. Health Shield / S… | `aggregator_quote` | 2026-05-18 |
| age 30 / 10L / metro / fs1 | ₹7,665 | [policyx.com…](https://www.policyx.com/health-insurance/icici-lombard-health-insurance/complete-health-insurance.php) | Health Shield / SI Options (Rs.) 5L 10L 20L 25L 50L / Premium Payable (Rs.) 6,477 7,665 12,246 13,188 16,580 | `aggregator_quote` | 2026-05-18 |
| age 30 / 20L / metro / fs1 | ₹12,246 | [policyx.com…](https://www.policyx.com/health-insurance/icici-lombard-health-insurance/complete-health-insurance.php) | Health Shield / SI Options (Rs.) 5L 10L 20L 25L 50L / Premium Payable (Rs.) 6,477 7,665 12,246 13,188 16,580 | `aggregator_quote` | 2026-05-18 |

**`icici-lombard__arogya-sanjeevani`** · Arogya Sanjeevani · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹6,719 | [joinditto.in…](https://joinditto.in/articles/health-insurance/hdfc-ergo-health-insurance-premium-chart/) | Popular HDFC ERGO Health Insurance Plans With Premium Charts / (Individual Plan): Age 25 / Arogya Sanjeevani / Rs.6,719 ... 'Unless mentioned otherwise, the… | `aggregator_quote` | 2026-05-18 |

**`icici-lombard__health-elite-plus`** · Health Elite Plus · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹9,644 | [policyx.com…](https://www.policyx.com/health-insurance/icici-lombard-health-insurance/complete-health-insurance.php) | Let's see how the premium varies across different types and sum insured for an individual at the age of 30 and for a policy term of 1 year. Health Elite / SI… | `aggregator_quote` | 2026-05-18 |
| age 30 / 10L / metro / fs1 | ₹13,866 | [policyx.com…](https://www.policyx.com/health-insurance/icici-lombard-health-insurance/complete-health-insurance.php) | Health Elite / SI Options (Rs.) 5L 10L 20L 25L 50L / Premium Payable (Rs.) 9,644 13,866 26,380 27,411 30,697 | `aggregator_quote` | 2026-05-18 |

### IFFCO Tokio

**`iffco-tokio__critical-illness-benefit`** · Critical Illness Benefit · UIN `IFFHLIP19036V011920`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / tier2 / fs1 | ₹3,256 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/critical-illness-benefit-policy.pdf) | Critical Illness Benefit Policy UIN: IFFHLIP19036V011920 Rate chart ... Age/SI ... 1,000,000 ... 25-35 ... 3,256 ... Year 1 Premium | `insurer_site` | 2026-05-18 |
| age 45 / 10L / tier2 / fs1 | ₹7,755 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/critical-illness-benefit-policy.pdf) | Age/SI ... 1,000,000 ... 41-45 ... 7,755 ... Year 1 Premium | `insurer_site` | 2026-05-18 |
| age 22 / 10L / tier2 / fs1 | ₹2,566 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/critical-illness-benefit-policy.pdf) | Age/SI 100,000 ... 1,000,000 ... <25 257 ... 2,566 ... Year 1 Premium | `insurer_site` | 2026-05-18 |

**`iffco-tokio__essential-health-plan`** · Essential Health Plan · UIN `IFFHLIP25035V012425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / tier2 / fs1 | ₹5,620 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/premium-chart.pdf) | ESSENTIAL HEALTH PROTECTOR UIN: IFFHLIP25035V012425 PREMIUM CHART Policy Period 1 year Individual basis ... Age/SI ... 26-35 ... 500,000 4,890 5,620 | `insurer_site` | 2026-05-18 |
| age 30 / 10L / tier2 / fs1 | ₹7,510 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/premium-chart.pdf) | Age/SI 0-25 26-35 ... 1,000,000 6,545 7,510 10,050 | `insurer_site` | 2026-05-18 |
| age 50 / 5L / tier2 / fs1 | ₹10,335 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/premium-chart.pdf) | Age/SI 0-25 26-35 36-45 46-55 ... 500,000 4,890 5,620 7,530 10,335 | `insurer_site` | 2026-05-18 |

**`iffco-tokio__family-health-protector`** · Family Health Protector · UIN `IFFHLIP24013V052324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / tier2 / fs1 | ₹6,208 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/family-health-protector.pdf) | FAMILY HEALTH PROTECTOR WITHOUT CRITICAL ILLNESS Premium Rates for Highest Aged Member ... Age/SI 0-25 26-35 ... 5,00,000 5,382 6,208 8,780 11,566 | `insurer_site` | 2026-05-18 |
| age 30 / 10L / tier2 / fs1 | ₹8,250 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/family-health-protector.pdf) | Age/SI 0-25 26-35 36-45 ... 10,00,000 7,153 8,250 11,669 15,370 | `insurer_site` | 2026-05-18 |
| age 50 / 5L / tier2 / fs1 | ₹11,566 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/family-health-protector.pdf) | Age/SI 0-25 26-35 36-45 46-55 ... 5,00,000 5,382 6,208 8,780 11,566 | `insurer_site` | 2026-05-18 |

**`iffco-tokio__individual-health-protector`** · Individual Health Protector · UIN `IFFHLIP24012V052324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / tier2 / fs1 | ₹7,916 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/individual-health-protector.pdf) | Health Protector UIN: IFFHLIP24012V052324 RATE CHART ... Rate Sheet of Health Protector portfolio without Critical Illness ... 5,00,000 5,934 7,916 9,853 14,948 | `insurer_site` | 2026-05-18 |
| age 30 / 10L / tier2 / fs1 | ₹10,642 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/individual-health-protector.pdf) | Health Protector UIN: IFFHLIP24012V052324 RATE CHART Rate Sheet of Health Protector portfolio without Critical Illness ... 10,00,000 7,977 10,642 13,245 20,094 | `insurer_site` | 2026-05-18 |
| age 50 / 5L / tier2 / fs1 | ₹14,948 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/rate-chart/individual-health-protector.pdf) | Age (yrs.)/SI 3months to 25 26 to 35 36 to 45 46 to 55 ... 5,00,000 5,934 7,916 9,853 14,948 | `insurer_site` | 2026-05-18 |

**`iffco-tokio__health-protector-assure`** · Health Protector Assure · UIN `IFFHLIP24131V012324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / tier2 / fs1 | ₹1,505 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/iffco-pdf/Premium_Table_Health_Protector_Assure.pdf) | HEALTH PROTECTOR ASSURE UIN: IFFHLIP24131V012324 RATING CHART A) SUPER TOP-UP VARIANT PREMIUM RATES (EXCLUDING GST) Super Top-Up Individual Basis (1 Year Pol… | `insurer_site` | 2026-05-18 |
| age 30 / 5L / tier2 / fs1 | ₹1,135 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/iffco-pdf/Premium_Table_Health_Protector_Assure.pdf) | Super Top-Up Individual Basis (1 Year Policy) Sum Insured Deductible 0-25 26-35 ... 500,000 500,000 985 1,135 1,605 2,110 | `insurer_site` | 2026-05-18 |
| age 50 / 10L / tier2 / fs1 | ₹2,805 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/iffco-pdf/Premium_Table_Health_Protector_Assure.pdf) | Sum Insured Deductible 0-25 26-35 36-45 46-55 ... 1,000,000 500,000 1,305 1,505 2,130 2,805 | `insurer_site` | 2026-05-18 |

**`iffco-tokio__health-protector-plus`** · Health Protector Plus · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / tier2 / fs1 | ₹2,899 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/iffco-pdf/RATE%20CHART-%20Health%20Protector%20Plus.pdf) | RATE CHART Health Protector Plus 1) Top-up a. Individual basis for 1 Member: Sum Insured ... 1000000 Deductible ... 500000 Above 3 months to 35 years ... 2,899 | `insurer_site` | 2026-05-18 |
| age 30 / 10L / tier2 / fs1 | ₹3,199 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/iffco-pdf/RATE%20CHART-%20Health%20Protector%20Plus.pdf) | 2) Super Top-up a. Individual basis for 1 Member: Sum Insured ... 1000000 Deductible ... 500000 Above 3 months to 35 years ... 3,199 | `insurer_site` | 2026-05-18 |
| age 50 / 10L / tier2 / fs1 | ₹3,799 | [iffcotokio.co.in…](https://www.iffcotokio.co.in/content/dam/iffcotokio/iffco-pdf/RATE%20CHART-%20Health%20Protector%20Plus.pdf) | 1) Top-up a. Individual basis for 1 Member: ... 1000000 ... 500000 ... 46 to 55 ... 3,799 | `insurer_site` | 2026-05-18 |

### ManipalCigna

**`manipalcigna__prohealth-prime`** · Prohealth Prime · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs2 | ₹13,999 | [manipalcigna.com…](https://www.manipalcigna.com/hospitalization-cover/prohealth-insurance/prohealthprime-protect) | Premium PER ANNUM: 13,999* inclusive of taxes(as applicable) *The Premium value is indicative for the below mentioned profile Age - 30, Zone - 1, Cover type… | `insurer_site` | 2026-05-18 |
| age 35 / 5L / metro / fs1 | ₹12,640 | [manipalcigna.com…](https://www.manipalcigna.com/hospitalization-cover/prohealth-insurance/prohealthprime-advantage) | Premium Per Annum: Rs.12,640* (inclusive of taxes) ... indicative for the below mentioned profile Age 35, Zone 1, Cover type Individual, Tenure 1 year, Premi… | `insurer_site` | 2026-05-18 |
| age 35 / 5L / metro / fs1 | ₹13,118 | [manipalcigna.com…](https://www.manipalcigna.com/hospitalization-cover/prohealth-insurance/prohealthprime-active) | Premium PER ANNUM: 13,118* inclusive of taxes(as applicable) ... indicative for the below mentioned profile Age 35, Zone 1, Cover type Individual, Tenure 1 y… | `insurer_site` | 2026-05-18 |

### National Insurance

**`national-insurance__arogya-sanjeevani`** · Arogya Sanjeevani · UIN `NICHLIP20174V011920`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹7,185 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/ASP%20Rate%20Chart%20up%20to%2010%20Lakhs%20SI_0.pdf) | Arogya Sanjeevani Policy – National Rate Chart (in INR) For Policy on Individual basis – Premium Table for each family Member ... 26-30 2,293 3,031 3,748 4,3… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-critical-illness`** · National Critical Illness · UIN `NICHLIP18086V011718`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 5L / metro / fs1 | ₹1,680 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2024-08/NCIP%20Rate%20Chart%20with%20GST.pdf) | National Critical Illness Policy Rate Chart Rates (in INR) per individual Plan A (covering 11 CIs) Age/ SI ... 26-30 ... 5,00,000 1,056 1,173 1,680 2,441 ... | `insurer_site` | 2026-05-18 |

**`national-insurance__national-hospi-cash`** · National Hospi Cash · UIN `NICHLIP25046V012425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 3.65L / metro / fs1 | ₹614 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2026-04/NHCP%20Rate%20Chart.pdf) | National Hospi Cash Policy Rate Chart (in INR) In-built cover - 1 year Premium Indemnity Period 30 days Time Excess 1 day Upto 45 ... 1000 614 941 1,256 1,08… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-mediclaim-plus`** · National Mediclaim Plus · UIN `NICHLIP21150V022021`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹10,417 | [bankofbaroda.bank.in…](https://bankofbaroda.bank.in/-/media/Project/BOB/CountryWebsites/India/pdfs/nmpp-rate-chart-09-13.pdf) | National Mediclaim Plus Policy Rate Chart Rate without TPA charges (in ₹) SI 3m-5 6 - 17 18 - 25 26-35 36-45 ... 5,00,000 9,191 9,192 10,212 10,417 10,417 15… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-mediclaim`** · National Mediclaim · UIN `NICHLIP25036V082425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 5L / metro / fs1 | ₹7,125 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2024-12/NMP%20Rate%20Chart%20Revised.pdf) | National Mediclaim Policy Rate Chart (in ₹ per Individual, without TPA Charges) Age band / SI 1,00,000 2,00,000 3,00,000 4,00,000 5,00,000 ... 26-30 2,934 3,… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-parivar-plus`** · National Parivar Plus · UIN `NICHLIP25039V032425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 6L / metro / fs1 | ₹11,649 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2024-12/NPMPP%20Rate%20Chart.pdf) | National Parivar Mediclaim Plus Policy Rate chart (in ₹) Rate for senior most member (without TPA) for each policy year for Zone I (Greater Mumbai Metropolit… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-senior-citizen`** · National Senior Citizen · UIN `NICHLIP21083V022021`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 62 / 5L / metro / fs1 | ₹19,746 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/NSCMP%20-%20Rate%20Chart.pdf) | National Senior Citizen Mediclaim Policy RATE CHART Plan A – Premium Table for Individuals / Premium Table for Senior most member (for floater policy) SI 1,0… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-super-top-up`** · National Super Top Up · UIN `NICHLIP24154V042324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / metro / fs1 | ₹1,564 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2025-01/NSTUMP%20Rate%20Chart.pdf) | National Super Top Up Mediclaim Policy Rate Chart (in ₹) Premium (₹) per Individual (for individual policy)/ Senior most member (for floater policy) Threshol… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-young-india-plus`** · National Young India Plus · UIN `NICHLIP24127V012324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 5L / metro / fs1 | ₹7,145 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2024-10/NYIMPP%20Rate%20Chart.pdf) | National Young India Mediclaim Plus Policy Rate Chart (in ₹ for Individual) Zone 1 Eldest Age Band 500000 1000000 1500000 2500000 ... 18-25 7021 8570 10319 1… | `insurer_site` | 2026-05-18 |

**`national-insurance__national-young-india`** · National Young India · UIN `NICHLIP23032V012223`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 5L / metro / fs1 | ₹11,599 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/NYIMP%20Rate%20Chart%20with%20GST.pdf) | National Young India Mediclaim Policy Rate Chart (in ₹ for Individual) Premium without TPA Charges Zone Age-band/SI 3,00,000 5,00,000 10,00,000 1 ... 26-30 9… | `insurer_site` | 2026-05-18 |

**`national-insurance__new-national-parivar`** · New National Parivar · UIN `NICHLIP23033V012223`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 5L / metro / fs1 | ₹10,099 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/NNPMP%20Rate%20Chart.pdf) | New National Parivar Mediclaim Policy Rate Chart (in ₹) Zone 1 : Premium of senior-most member Age band 100000 200000 300000 400000 500000 ... 18-25 4,566 6,… | `insurer_site` | 2026-05-18 |

**`national-insurance__universal-health`** · Universal Health · UIN `NICHLIP21593V042021`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 0.5L / metro / fs1 | ₹597 | [nationalinsurance.nic.co.in…](https://nationalinsurance.nic.co.in/sites/default/files/2024-10/UHIP%20Prospectus.pdf) | Rate Chart (in ₹ per family) Members SI – ₹ 30,000 SI – ₹ 50,000 Individual ₹ 385 ₹ 597 Family up to 5 members (consisting of Insured, spouse and first 3 dep… | `insurer_site` | 2026-05-18 |

### New India Assurance

**`new-india__asha-kiran-policy`** · Asha Kiran Policy · UIN `NIAHLIP25038V012425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 5L / metro / fs1 | ₹7,917 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/asha-kiran-policy/Premium%20chart%20New%20India%20Asha%20Kiran%20Policy-01%20Oct%202024%20(1).pdf) | NEW INDIA ASHA KIRAN POLICY New India Asha Kiran Policy - Premium Chart (Excluding GST) ... PRIMARY MEMBER Premiums applicable at different ages (Rs. per ann… | `insurer_site` | 2026-05-18 |

**`new-india__janata-mediclaim-policy`** · Janata Mediclaim Policy · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 0.5L / metro / fs1 | ₹809 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/janata-mediclaim-policy/Premium%20Chart%20Janata%20Mediclaim.pdf) | Janata Mediclaim (Without GST**) Sum Insured 3 months to 5 yrs 6 yrs to 35 yrs 36 yrs to 40 yrs ... 50000 867 809 924 1271 ... 75000 1299 1213 1386 1906 ...… | `insurer_site` | 2026-05-18 |

**`new-india__new-india-floater-mediclaim-policy`** · New India Floater Mediclaim Policy · UIN `NIAHLIP24010V052324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 28 / 5L / metro / fs1 | ₹5,013 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/floater-mediclaim-policy/NewIndiaFloaterMediclaimPolicyPremiumChart.pdf) | NEW INDIA FLOATER MEDICLAIM POLICY New India Floater Mediclaim Policy - Premium Chart – Per Member (Excluding GST) Age Band / SI 2L 3L 5L 8L 10L 12L 15L 3m-1… | `insurer_site` | 2026-05-18 |

**`new-india__floater-mediclaim`** · Floater Mediclaim · UIN `NIAHLIP24010V052324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 5L / metro / fs1 | ₹5,013 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/floater-mediclaim-policy/NewIndiaFloaterMediclaimPolicyPremiumChart.pdf) | New India Floater Mediclaim Policy - Premium Chart – Per Member (Excluding GST) ... Age Band / SI 2L 3L 5L 8L 10L 12L 15L ... 19-30 Y 3,789 4,463 5,013 5,795… | `insurer_site` | 2026-05-18 |
| age 40 / 10L / metro / fs1 | ₹8,974 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/floater-mediclaim-policy/NewIndiaFloaterMediclaimPolicyPremiumChart.pdf) | Age Band / SI 2L 3L 5L 8L 10L 12L 15L ... 36-40 Y 5,505 6,510 7,331 8,475 8,974 9,578 10,277 | `insurer_site` | 2026-05-18 |

**`new-india__janata-mediclaim`** · Janata Mediclaim · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 0.5L / metro / fs1 | ₹809 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/janata-mediclaim-policy/Premium%20Chart%20Janata%20Mediclaim.pdf) | Janata Mediclaim (Without GST**) Sum Insured 3 months to 5 yrs / 6 yrs to 35 yrs / 36 yrs to 40 yrs ... 50000 867 809 924 1271 1617 1791 2079 2368 2657 | `insurer_site` | 2026-05-18 |
| age 30 / 0.75L / metro / fs1 | ₹1,213 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/janata-mediclaim-policy/Premium%20Chart%20Janata%20Mediclaim.pdf) | Janata Mediclaim (Without GST**) ... 75000 1299 1213 1386 1906 2426 2715 3119 3523 3985 | `insurer_site` | 2026-05-18 |

**`new-india__new-india-mediclaim-policy`** · New India Mediclaim Policy · UIN `NIAHLIP25040V082425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹8,189 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/new-india-mediclaim-policy/Premium%20chart-New%20India%20Mediclaim%20Policy.pdf) | New India Mediclaim Policy- Premium Chart (Excluding Gst**) Zone 1: Maharashtra and Gujarat Age/SI 1L 2L 3L 4L 5L 6L 7L 8L 10L 12L 15L ... 30 4051 6079 7133… | `insurer_site` | 2026-05-18 |
| age 30 / 5L / tier2 / fs1 | ₹6,976 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/new-india-mediclaim-policy/Premium%20chart-New%20India%20Mediclaim%20Policy.pdf) | Zone 2: Rest of India Age/SI 1L 2L 3L 4L 5L 6L 7L 8L 10L 12L 15L ... 30 3451 5178 6076 6526 6976 7532 8090 8650 9779 10631 12053 | `insurer_site` | 2026-05-18 |

**`new-india__mediclaim-policy`** · Mediclaim Policy · UIN `NIAHLIP25040V082425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 45 / 5L / metro / fs1 | ₹13,521 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/new-india-mediclaim-policy/Premium%20chart-New%20India%20Mediclaim%20Policy.pdf) | Zone 1: Maharashtra and Gujarat Age/SI 1L 2L 3L 4L 5L 6L 7L 8L 10L 12L 15L ... 45 6484 10210 12023 12772 13521 14601 15680 16786 18944 20593 23343 | `insurer_site` | 2026-05-18 |
| age 45 / 5L / tier2 / fs1 | ₹11,518 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/new-india-mediclaim-policy/Premium%20chart-New%20India%20Mediclaim%20Policy.pdf) | Zone 2: Rest of India Age/SI 1L 2L 3L 4L 5L 6L 7L 8L 10L 12L 15L ... 45 5524 8697 10242 10880 11518 12438 13357 14299 16137 17542 19885 | `insurer_site` | 2026-05-18 |

**`new-india__yuva-bharat`** · Yuva Bharat · UIN `NIAHLIP25043V022425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 5L / metro / fs1 | ₹4,712 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/yuva-bharat-health-policy/Premium%20chart%20Yuva%20Bharat%20Health%20Policy%20.pdf) | Premium Chart for Yuva Bharat Health Policy - Basic Plan Premium Per Member (Excluding GST) ... Yuva Bharat Basic -Annual Premium - Zone -1 Age Band/Sum Insu… | `insurer_site` | 2026-05-18 |
| age 25 / 5L / tier2 / fs1 | ₹3,856 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/yuva-bharat-health-policy/Premium%20chart%20Yuva%20Bharat%20Health%20Policy%20.pdf) | Yuva Bharat Basic -Annual Premium – Zone 2 (Rest of India) Age Band/Sum Insured 500000 1000000 1500000 2500000 5000000 ... 18-30 3,856 5,106 6,274 8,324 12,474 | `insurer_site` | 2026-05-18 |

**`new-india__universal-health-insurance`** · Universal Health Insurance · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 0.3L / tier2 / fs1 | ₹383 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/new-india-health-insurance/universal-health-insurance-scheme-apl/) | Individual Person Rs.383/- per annum. | `aggregator_quote` | 2026-05-18 |
| age 35 / 0.3L / tier2 / fs5 | ₹575 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/new-india-health-insurance/universal-health-insurance-scheme-apl/) | Family (not exceeding five members) consisting of Insured, Spouse, and first 3 dependent children Rs.575/- per annum. | `aggregator_quote` | 2026-05-18 |
| age 35 / 0.3L / tier2 / fs7 | ₹767 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/new-india-health-insurance/universal-health-insurance-scheme-apl/) | Family not exceeding 7 members consisting of Insured, Spouse, first 3 dependent children and dependent parents Rs.767/- per annum. | `aggregator_quote` | 2026-05-18 |

**`new-india__yuva-bharat-health-policy`** · Yuva Bharat Health Policy · UIN `NIAHLIP25043V022425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 35 / 10L / metro / fs1 | ₹7,614 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/yuva-bharat-health-policy/Premium%20chart%20Yuva%20Bharat%20Health%20Policy%20.pdf) | Yuva Bharat Basic -Annual Premium - Zone -1 Age Band/Sum Insured 500000 1000000 1500000 2500000 5000000 ... 31-35 5,731 7,614 9,374 12,462 18,709 | `insurer_site` | 2026-05-18 |
| age 35 / 10L / tier2 / fs1 | ₹6,230 | [newindia.co.in…](https://www.newindia.co.in/assets/docs/know-more/health/yuva-bharat-health-policy/Premium%20chart%20Yuva%20Bharat%20Health%20Policy%20.pdf) | Yuva Bharat Basic -Annual Premium – Zone 2 (Rest of India) Age Band/Sum Insured 500000 1000000 1500000 2500000 5000000 ... 31-35 4,689 6,230 7,670 10,196 15,307 | `insurer_site` | 2026-05-18 |

### Niva Bupa

**`niva-bupa__reassure-3`** · Reassure 3 · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹12,119 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/) | ReAssure 3.0 Elite (Unlimited SI) ... (Individual Plan): Age 25 ₹12,119 ... premiums above are for a Delhi resident (pin code: 110001) ... Sum Insured: ₹15 lakh | `aggregator_quote` | 2026-05-18 |
| age 32 / 15L / metro / fs2 | ₹21,599 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/) | ReAssure 3.0 Elite (Unlimited SI) ... (Family Floater, 2A): Ages (31, 32) ₹21,599 ... Delhi resident (pin code: 110001) ... Sum Insured: ₹15 lakh | `aggregator_quote` | 2026-05-18 |
| age 63 / 15L / metro / fs2 | ₹79,642 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/) | ReAssure 3.0 Elite (Unlimited SI) ... (Family Floater, 2A): Ages (62, 63) ₹79,642 ... Delhi resident (pin code: 110001) ... Sum Insured: ₹15 lakh | `aggregator_quote` | 2026-05-18 |

**`niva-bupa__reassure-2-0`** · Reassure 2 0 · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹11,535 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-health-insurance-premium-chart-pdf/) | ReAssure 2.0 Titanium+ ... (Individual Plan): Age 25 ₹11,535 ... Unless otherwise mentioned, the premiums above are for a person living in Delhi (110010) and… | `aggregator_quote` | 2026-05-18 |
| age 32 / 15L / metro / fs2 | ₹19,627 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-health-insurance-premium-chart-pdf/) | ReAssure 2.0 Titanium+ ... (Family Floater, 2A): Ages (31, 32) ₹19,627 ... person living in Delhi (110010) ... ₹15 lakh sum insured with an added consumables… | `aggregator_quote` | 2026-05-18 |
| age 63 / 15L / metro / fs2 | ₹69,783 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-health-insurance-premium-chart-pdf/) | ReAssure 2.0 Titanium+ ... (Family Floater, 2A): Ages (62, 63) ₹69,783 ... person living in Delhi (110010) ... ₹15 lakh sum insured with an added consumables… | `aggregator_quote` | 2026-05-18 |

**`niva-bupa__reassure-3-0`** · Reassure 3 0 · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 34 / 15L / metro / fs3 | ₹27,366 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/) | ReAssure 3.0 Elite (Unlimited SI) ... (Family Floater, 2A 1C): Ages (35, 34, 5) ₹27,366 ... Delhi resident (pin code: 110001) ... Sum Insured: ₹15 lakh | `aggregator_quote` | 2026-05-18 |
| age 25 / 15L / metro / fs1 | ₹12,119 | [joinditto.in…](https://joinditto.in/articles/health-insurance/niva-bupa-reassure-2-0-premium-chart/) | ReAssure 3.0 Elite (Unlimited SI) ... (Individual Plan): Age 25 ₹12,119 ... Delhi resident (pin code: 110001) ... Sum Insured: ₹15 lakh | `aggregator_quote` | 2026-05-18 |

### Oriental Insurance

**`oriental-insurance__oriental-mediclaim-individual`** · Oriental Mediclaim Individual · UIN `OICHLIP25048V052425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹7,095 | [probitasdocument-public.s3.ap-south-1.amazonaws.com…](https://probitasdocument-public.s3.ap-south-1.amazonaws.com/Public/Brochure/oriental%20insurance-individual%20mediclaim-insurance-brochure.pdf) | Oriental Mediclaim Insurance Policy (Individual) Premium Chart 2024 Office Premium per Insured (INR) (Excluding GST) (Yearly) ... Age is 21-35 yrs ... 500,00… | `insurer_site` | 2026-05-18 |
| age 30 / 5L / metro / fs1 | ₹8,372 | [probitasdocument-public.s3.ap-south-1.amazonaws.com…](https://probitasdocument-public.s3.ap-south-1.amazonaws.com/Public/Brochure/oriental%20insurance-individual%20mediclaim-insurance-brochure.pdf) | Office Premium per Insured (INR) (Including GST) (Yearly) ... Age is 21-35 yrs ... 500000 5785 8372 11140 18517 24222 34361 42271 48600 | `insurer_site` | 2026-05-18 |
| age 50 / 10L / metro / fs1 | ₹24,256 | [probitasdocument-public.s3.ap-south-1.amazonaws.com…](https://probitasdocument-public.s3.ap-south-1.amazonaws.com/Public/Brochure/oriental%20insurance-individual%20mediclaim-insurance-brochure.pdf) | Office Premium per Insured (INR) (Excluding GST) (Yearly) ... Age is 46-55 yrs ... 1,000,000 6590 10287 15193 24256 34459 50718 59099 67954 | `insurer_site` | 2026-05-18 |

### Royal Sundaram

**`royal-sundaram__family-plus`** · Family Plus · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 29 / 5L / metro / fs3 | ₹12,713 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/royal-sundaram-health-insurance/family-plus-plan/) | Premium Calculation of Royal Sundaram Family Plus Plan / Rakhi (29 Years) / 2 members / Age 7 Years and 6 years / Individual SI 3 Lakhs / Floater SI 5 Lakhs… | `aggregator_quote` | 2026-05-18 |
| age 39 / 20L / metro / fs2 | ₹24,814 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/royal-sundaram-health-insurance/family-plus-plan/) | Himanshu (39 Years) / 2 members / Age 38 years and 12 years / Individual SI 10 Lakhs / Floater SI 20 Lakhs / Proposer No / Rs. 24814 | `aggregator_quote` | 2026-05-18 |

**`royal-sundaram__lifeline`** · Lifeline · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 36 / 15L / tier1 / fs1 | ₹12,474 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/royal-sundaram-health-insurance/lifeline-plan/) | Suppose, Mr. Basu, 36 years old, Pune based businessman has purchased Lifeline health insurance policy for himself. / Supreme / Individual / Rs. 15 lakhs / P… | `aggregator_quote` | 2026-05-18 |
| age 36 / 20L / tier1 / fs1 | ₹13,167 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/royal-sundaram-health-insurance/lifeline-plan/) | Supreme / Individual / Rs. 20 lakhs / ... / 1-year premium Rs. 13,167 / 2-year Rs. 24,999 / 3-year Rs. 36,637 (Mr. Basu, 36 years old, Pune based businessman) | `aggregator_quote` | 2026-05-18 |

**`royal-sundaram__multiplier`** · Multiplier · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / metro / fs1 | ₹7,641 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/royal-sundaram-health-insurance/multiplier-policy/) | Premium Illustration Of Royal Sundaram Multiplier Health Insurance / Age 30 Years / Members Covered 1 / Policy Tenure 1 Year / Location New Delhi / Sum Insur… | `aggregator_quote` | 2026-05-18 |

**`royal-sundaram__presecure-advantage`** · Presecure Advantage · UIN `RSAHLIP25036V012425`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹21,021 | [royalsundaram.in…](https://www.royalsundaram.in/assets/forms-central/premium/PSA_Gross_Premium_Tables.pdf) | Premium Rate Table for PreSecure Advantage (Exclusive of Goods and Service Tax) - (Zone 1 rates without optional benefits) UIN: RSAHLIP25036V012425 / Individ… | `insurer_site` | 2026-05-18 |
| age 35 / 5L / metro / fs1 | ₹23,809 | [royalsundaram.in…](https://www.royalsundaram.in/assets/forms-central/premium/PSA_Gross_Premium_Tables.pdf) | Individual / Age (yrs.) 35 / 1 Lakh 7,143 / 2 Lakhs 11,905 / 3 Lakhs 16,666 / 4 Lakhs 20,238 / 5 Lakhs 23,809 | `insurer_site` | 2026-05-18 |

### SBI General

**`sbi-general__arogya-supreme`** · Arogya Supreme · UIN `SBIHLIP21043V012122`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 35 / 25L / tier2 / fs6 | ₹12,004 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/4e9c3cc343f242619a4160459ca1b442.pdf) | Benefit Illustration in respect of individual and family floater basis / 35 yrs 12,004 25,00,000 12,004 5% 11,404 25,00,000 / The above illustration is for P… | `insurer_site` | 2026-05-18 |
| age 30 / 30L / metro / fs1 | ₹19,223 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/4e9c3cc343f242619a4160459ca1b442.pdf) | PREMIUM CHART- ZONE 1 (EXCLUSIVE OF TAXES) Individual / Age 19Y-35Y / 30 Lakhs ₹19,223 / 40 Lakhs ₹22,042 / 50 Lakhs ₹24,986 / 1 Crore ₹29,501 / Zone 1 – Mum… | `insurer_site` | 2026-05-18 |

**`sbi-general__arogya-top-up`** · Arogya Top Up · UIN `SBIHLIP14005V011314`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / metro / fs1 | ₹782 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/9d179c5e9027490ba8416c398df6a302.pdf) | Premium Chart for Individual (Inclusive of GST) / Age 19Y-35Y / Deductible 5,00,000 / Sum Insured 10,00,000 -> 782 | `insurer_site` | 2026-05-18 |
| age 30 / 5L / metro / fs1 | ₹597 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/9d179c5e9027490ba8416c398df6a302.pdf) | Premium Chart for Individual (Inclusive of GST) / Age 19Y-35Y / Deductible 5,00,000 / Sum Insured 5,00,000 -> 597 | `insurer_site` | 2026-05-18 |

**`sbi-general__health-edge`** · Health Edge · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹14,276 | [joinditto.in…](https://joinditto.in/articles/health-insurance/sbi-health-insurance-premium-chart-pdf/) | Profile Individual (Age: 25) / SBI Health Edge (₹15L) ₹14,276 / SBI Health Edge (₹25L) ₹16,180 ... figures apply to healthy individuals in Delhi (110010) | `aggregator_quote` | 2026-05-18 |
| age 25 / 25L / metro / fs1 | ₹16,180 | [joinditto.in…](https://joinditto.in/articles/health-insurance/sbi-health-insurance-premium-chart-pdf/) | Profile Individual (Age: 25) / SBI Health Edge (₹25L) ₹16,180 (Delhi 110010, healthy individual) | `aggregator_quote` | 2026-05-18 |
| age 32 / 15L / metro / fs2 | ₹23,398 | [joinditto.in…](https://joinditto.in/articles/health-insurance/sbi-health-insurance-premium-chart-pdf/) | Profile 2 Adults (Ages: 32 & 31) / SBI Health Edge (₹15L) ₹23,398 (Delhi 110010) | `aggregator_quote` | 2026-05-18 |

**`sbi-general__health-alpha`** · Health Alpha · UIN `SBIHLIP26038V012526`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 10L / metro / fs1 | ₹7,219 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/a89c49840e1c4effb49f248035447d10.pdf) | Illustration 3- Benefit Illustration in respect of Individual and Family Floater Basis / 30 yrs 7,219 10L 7,219 361 6,858 10L / 35 yrs 7,872 10L 7,872 394 7,… | `insurer_site` | 2026-05-18 |
| age 35 / 10L / metro / fs1 | ₹7,872 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/a89c49840e1c4effb49f248035447d10.pdf) | Illustration 3 ... / 35 yrs 7,872 10L 7,872 394 7,478 10L | `insurer_site` | 2026-05-18 |

**`sbi-general__super-top-up`** · Super Top Up · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 35 / 5L / metro / fs1 | ₹1,018 | [content.sbigeneral.in…](https://content.sbigeneral.in/uploads/2457a6c0a9b54f47a98f5274dd68819b.pdf) | ANNEXURE - I - BENEFIT ILLUSTRATION ... 35 yrs 1,018 500000 1,018 5% 967 500000 ... Sum Insured of ₹5,00,000/- and Deductible of ... ₹5,00,000/- ... Total Pr… | `insurer_site` | 2026-05-18 |

**`sbi-general__super-health-insurance`** · Super Health Insurance · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹20,602 | [joinditto.in…](https://joinditto.in/articles/health-insurance/sbi-health-insurance-premium-chart-pdf/) | a 25-year-old buying the SBI Super Health Platinum plan with a ₹15 lakh individual cover pays around ₹20,602 annually. ... figures apply to healthy individua… | `aggregator_quote` | 2026-05-18 |
| age 25 / 25L / metro / fs1 | ₹22,727 | [joinditto.in…](https://joinditto.in/articles/health-insurance/sbi-health-insurance-premium-chart-pdf/) | Profile Individual (Age: 25) / SBI Super Health Platinum (₹25L) ₹22,727 (Delhi 110010) | `aggregator_quote` | 2026-05-18 |
| age 32 / 15L / metro / fs2 | ₹28,486 | [joinditto.in…](https://joinditto.in/articles/health-insurance/sbi-health-insurance-premium-chart-pdf/) | Profile 2 Adults (Ages: 32 & 31) / SBI Super Health Platinum (₹15L) ₹28,486 (Delhi 110010) | `aggregator_quote` | 2026-05-18 |

### Star Health

**`star-health__star-assure`** · Star Assure · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 15L / metro / fs1 | ₹11,714 | [joinditto.in…](https://joinditto.in/articles/health-insurance/star-health-insurance-premium-chart/) | These annual premiums are calculated for a ₹15 lakh sum insured for a policyholder residing in Delhi. / Individual (Age 25) / Star Assure ₹11,714 | `aggregator_quote` | 2026-05-18 |
| age 31 / 15L / metro / fs2 | ₹20,241 | [joinditto.in…](https://joinditto.in/articles/health-insurance/star-health-insurance-premium-chart/) | These annual premiums are calculated for a ₹15 lakh sum insured for a policyholder residing in Delhi. / Couple (Ages 30 and 32) / Star Assure ₹20,241 | `aggregator_quote` | 2026-05-18 |
| age 45 / 10L / metro / fs4 | ₹27,767 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/star-health-insurance/health-assure-policy/) | Age 45 / Self (Primary member) / Sum Insured Rs.10,00,000 / Policy Term 1 Year / Family Size 2 Adults+2 Children / Zone A / Premium Excl. GST (Rs.) 27,767 /… | `aggregator_quote` | 2026-05-18 |

**`star-health__star-cancer-care-platinum`** · Star Cancer Care Platinum · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹19,104 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/star-health-insurance/cancer-care-platinum-policy/) | Sample Premium Illustration of Star Cancer Care Platinum Insurance Policy / Age 30 Years / Optional Cover No / Health Insurance Cover Rs. 5 Lakhs / Premium A… | `aggregator_quote` | 2026-05-18 |
| age 30 / 7.5L / metro / fs1 | ₹24,037 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/star-health-insurance/cancer-care-platinum-policy/) | Age 30 Years / Optional Cover No / Health Insurance Cover Rs. 7.5 Lakhs / Premium Amount Rs. 24,037 | `aggregator_quote` | 2026-05-18 |

**`star-health__star-comprehensive`** · Star Comprehensive · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 25 / 25L / metro / fs1 | ₹21,566 | [joinditto.in…](https://joinditto.in/articles/health-insurance/star-health-comprehensive-plan-premium-chart/) | Individual Plan, Age 25 / Star Comprehensive Plan ₹21,566 ... ₹25 lakh sum insured ... Delhi (Zone I) | `aggregator_quote` | 2026-05-18 |
| age 31 / 25L / metro / fs2 | ₹35,405 | [joinditto.in…](https://joinditto.in/articles/health-insurance/star-health-comprehensive-plan-premium-chart/) | Family Floater (2A), Ages (31, 32) / Star Comprehensive Plan ₹35,405 (₹25 lakh SI, Delhi Zone I) | `aggregator_quote` | 2026-05-18 |
| age 28 / 5L / metro / fs1 | ₹10,832 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/star-health-insurance/comprehensive-plan/) | 28-year-old female, Rs. 5 Lakhs, 1 Year: Rs. 10,832 | `aggregator_quote` | 2026-05-18 |

**`star-health__health-premier`** · Health Premier · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 51 / 50L / metro / fs1 | ₹32,951 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/star-health-insurance/health-premier-policy/) | Age 51 years / Policy type Individual policy / Policy period 1 year / Sum insured Rs. 50 lakhs / Payment option Full payment / Premium amount, including tax… | `aggregator_quote` | 2026-05-18 |

**`star-health__star-hospital-cash`** · Star Hospital Cash · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 36 / 10L / metro / fs1 | ₹13,199 | [probusinsurance.com…](https://www.probusinsurance.com/health-insurance/star-health-insurance/hospital-cash-policy/) | Premium Illustration Of Star Hospital Cash Insurance Policy / Age 36 Years / PED cover No / Policy for Self / Policy period 1 year / Insurance cover Rs. 10 l… | `aggregator_quote` | 2026-05-18 |

### Tata AIG

**`tata-aig__criti-medicare`** · Criti Medicare · UIN `TATHLIP22176V012122`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 35 / 5L / metro / fs1 | ₹1,965 | [tataaig.com…](https://www.tataaig.com/s3/Tata_AIG_Criti_Medicare_Brochure_0a0ed36a23.pdf) | Premium Calculation Illustration 1: Cover: Critical Illness - Smart Half Century Plan (with Health Check-up & PA cover of 3 Lakh); Survival Period of 15 days… | `insurer_site` | 2026-05-18 |
| age 35 / 5L / metro / fs1 | ₹3,340 | [tataaig.com…](https://www.tataaig.com/s3/Tata_AIG_Criti_Medicare_Brochure_0a0ed36a23.pdf) | Illustration 2: Cover: Critical Illness- Smart Half Century Plan (with Health Check-up & PA cover of 3 Lakh; Survival Period of 15 days + Cancer 360 Degree-I… | `insurer_site` | 2026-05-18 |

**`tata-aig__medicare-lite`** · Medicare Lite · UIN `TATHLIP24132V012324`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹7,937 | [tataaig.com…](https://www.tataaig.com/s3/tata_aig_medicare_lite_rate_chart_3ab7cf41c0.pdf) | Rate Chart PER PERSON ANNUAL PERMIUM IN Zone A / Age(in years)/Sum Insured / 18-35 : 5 Lacs 7,937 / 7.5 Lacs 8,359 / 10 Lacs 8,521 / 15 Lacs 9,511 / 20 Lacs… | `insurer_site` | 2026-05-18 |
| age 30 / 10L / metro / fs1 | ₹8,521 | [tataaig.com…](https://www.tataaig.com/s3/tata_aig_medicare_lite_rate_chart_3ab7cf41c0.pdf) | Zone A / 18-35 / 5 Lacs 7,937 / 7.5 Lacs 8,359 / 10 Lacs 8,521 / 15 Lacs 9,511 / 20 Lacs 10,158 | `insurer_site` | 2026-05-18 |

**`tata-aig__medicare-select`** · Medicare Select · UIN `—`

| Profile | Annual ₹ | Source page | Verbatim quote (trimmed) | Quality | Fetched |
| --- | --- | --- | --- | --- | --- |
| age 30 / 5L / metro / fs1 | ₹9,463 | [tataaig.com…](https://www.tataaig.com/s3/tata_aig_medicare_select_rate_chart_final_10b44c2d1c.pdf) | TATA AIG MediCare Select Rate Chart / Zone A Annual Per Person Rates in ₹ / Entry Age 30 / 5 Lakhs 9,463 / 7.5 Lakhs 10,060 / 10 Lakhs 10,379 / 15 Lakhs 11,7… | `insurer_site` | 2026-05-18 |
| age 30 / 10L / metro / fs1 | ₹10,379 | [tataaig.com…](https://www.tataaig.com/s3/tata_aig_medicare_select_rate_chart_final_10b44c2d1c.pdf) | Zone A / Entry Age 30 / 5 Lakhs 9,463 / 7.5 Lakhs 10,060 / 10 Lakhs 10,379 / 15 Lakhs 11,701 / 20 Lakhs 12,491 | `insurer_site` | 2026-05-18 |
| age 25 / 10L / metro / fs1 | ₹6,717 | [joinditto.in…](https://joinditto.in/articles/health-insurance/tata-aig-health-insurance-premium-rate-chart/) | a 25-year-old male (non-smoker, residing in Delhi) purchasing a TATA AIG Medicare Select with a sum insured of Rs 10 lakh needs to pay an annual premium of ₹… | `aggregator_quote` | 2026-05-18 |

## 3. Model-only entries (no evidenced sample)

These %d entries have **no** sample carrying a `source_quote`+`fetched_on`. The calculator does **not** invent a quote for them — it prices via `premium_calculator._attribute_base_factor` (product-type model) and labels the output *"Indicative estimate modelled from this plan's product type … NOT a quote"*. They are pricing-functional but not source-backed; a future harvest should target them.

| # | Entry key | Insurer | Policy name |
| --- | --- | --- | --- |
| 1 | `aditya-birla__activ-assure-diamond` | Aditya Birla Health | Aditya Birla Activ Assure Diamond |
| 2 | `aditya-birla__group-activ-health` | Aditya Birla Health | Aditya Birla Group Activ Health |
| 3 | `bajaj-allianz__health-guard` | Bajaj Allianz | Bajaj Allianz Health Guard / Comprehensive Care Plan |
| 4 | `bajaj-allianz__silver-health` | Bajaj Allianz | Bajaj Allianz Silver Health (Senior 46-80) |
| 5 | `bajaj-allianz__tax-gain` | Bajaj Allianz | Bajaj Allianz Tax Gain |
| 6 | `care-health__care-advantage` | Care Health | Care Health Care Advantage |
| 7 | `care-health__care-classic` | Care Health | Care Health Care Classic |
| 8 | `care-health__care-senior` | Care Health | Care Health Care Senior |
| 9 | `care-health__care-supreme` | Care Health | Care Health Care Supreme |
| 10 | `hdfc-ergo__energy` | HDFC ERGO | HDFC ERGO Energy (Diabetes/Hypertension focused) |
| 11 | `hdfc-ergo__optima-plus` | HDFC ERGO | HDFC ERGO Optima Plus |
| 12 | `hdfc-ergo__optima-restore` | HDFC ERGO | HDFC ERGO Optima Restore |
| 13 | `hdfc-ergo__optima-secure` | HDFC ERGO | HDFC ERGO my:Optima Secure |
| 14 | `icici-lombard__elevate` | ICICI Lombard | ICICI Lombard Elevate |
| 15 | `icici-lombard__health-advantedge` | ICICI Lombard | ICICI Lombard Health AdvantEdge |
| 16 | `manipalcigna__prohealth-prime-active` | ManipalCigna | ManipalCigna ProHealth Prime Active |
| 17 | `new-india__asha-kiran` | New India Assurance | New India Assurance Asha Kiran Policy |
| 18 | `new-india__mediclaim` | New India Assurance | New India Mediclaim |
| 19 | `niva-bupa__aspire` | Niva Bupa | Niva Bupa Aspire |
| 20 | `niva-bupa__health-premia` | Niva Bupa | Niva Bupa Health Premia |
| 21 | `niva-bupa__reassure` | Niva Bupa | Niva Bupa ReAssure 2.0 |
| 22 | `royal-sundaram__advanced-top-up` | Royal Sundaram | Advanced Top Up |
| 23 | `star-health__comprehensive` | Star Health | Star Comprehensive Health Insurance |
| 24 | `star-health__family-health-optima` | Star Health | Star Family Health Optima |
| 25 | `star-health__senior-citizens-red-carpet` | Star Health | Star Senior Citizens Red Carpet |
| 26 | `tata-aig__medicare` | Tata AIG | Tata AIG MediCare |
| 27 | `tata-aig__medicare-premier` | Tata AIG | Tata AIG MediCare Premier |

## 4. Integrity-gate re-verification

The harvest's integrity gate is claimed to reject (a) bare-homepage `source_url`s and (b) quotes lacking a number+profile. Independently re-checked here over all 194 evidenced samples:

| Check | Rule | Violations found |
| --- | --- | --- |
| Bare-homepage URL | `urlparse(url).path in ('', '/')` and no query string | **0** |
| Quote lacks a number | `re.search(r'\d', source_quote)` is None | **0** |

Both classes are **clean** — the integrity gate held; no evidenced sample is a homepage link or a number-free quote. (Profile presence is implied by every accepted sample carrying an `age`+`sum_insured_inr`+`city_tier`+`family_size` tuple; the quote text is the human-readable witness, not the structured profile itself.)

## 5. How to regenerate

This catalog is derived purely from `40-data/premiums/illustrative_premiums.json`. After any premium-harvest run, re-derive the §1 counts and §2/§3 tables by re-walking `base_premiums` for samples with both `source_quote` and `fetched_on`. Update the companion [`premium-dependency-map.md`](premium-dependency-map.md) §"if you change X" rows in the same commit (the JSON is the single source of truth for both docs).

