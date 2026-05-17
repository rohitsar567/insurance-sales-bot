# Premium Dependency Map

| Field | Value |
| --- | --- |
| Document type | Dependency / change-cascade map (data + code) |
| Subject data file | `40-data/premiums/illustrative_premiums.json` |
| Generated (this doc) | 2026-05-18 |
| Companion | [`premium-source-map.md`](premium-source-map.md) (per-sample provenance) |
| Source of truth for chain | `backend/premium_calculator.py`, `backend/brain_tools.py`, `backend/main.py`, `backend/scorecard.py` |

## 0. Purpose

This document is the **change-cascade contract for the premium pipeline**. It exists so a future edit to the curated JSON, a calculator helper, an API contract, or the reviews/scorecard parity surface does not silently break a downstream consumer.

Every node below names: **what it consumes**, **what depends on it**, and an explicit **"if you change X you must re-verify Y"** rule. Function and constant names are taken verbatim from the source files (read 2026-05-18); they are not paraphrased.

## 1. The pricing chain (end to end)

```
40-data/premiums/illustrative_premiums.json
   │  (base_premiums{}, scaling_factors{})
   ▼
premium_calculator._load_data()                     [reads + json.loads the file; {} on any error]
   ▼
premium_calculator._canonical_sample_key()          [resolve recommended/marketplace id → base_premiums key]
   │   ├─ _SAMPLE_DOCTYPE_SUFFIXES   (__brochure/__cis/__wordings/__prospectus/__policy)
   │   └─ _KNOWN_BAD_SAMPLE_KEYS     (currently EMPTY frozenset — quarantine mechanism, retained)
   ▼
premium_calculator._plausible_samples()             [type-aware ₹/lakh sanity guard, via _per_lakh_band()]
   ▼
premium_calculator._interpolate_from_samples()      [nearest sample in (age, log SI) space]
   ▼
premium_calculator.estimate()                       [#38 FULL ratio-normalization + OUTPUT plausibility ceiling]
   │      └─ NO-sample path → _attribute_base_factor()  (product-TYPE model; no JSON I/O)
   ▼
premium_calculator.bulk_estimate()                  [calls estimate() per policy on the curated path;
   │                                                  flat ₹500/lakh × type-factor on the no-sample path]
   ▼
premium_calculator.estimate_premium_band()          [prices the 26-policy _DEFAULT_BAND_POLICY_IDS basket;
   │                                                  p25–p75 interquartile via resolve_profile_sum_insured()]
   ▼
backend/main.py  POST /api/premium/estimate         (PremiumEstimateRequest → PremiumEstimateResponse)
                 POST /api/premium/bulk             (PremiumBulkRequest → PremiumBulkResponse)
                 GET  /api/profile/predicted-premium-band  (→ PredictedPremiumBandResponse)
   ▼
frontend/src/lib/api.ts  postPremiumEstimate() / PremiumEstimateResponse
   ▼
frontend/src/components/PolicyPremiumWidget.tsx     (point + ±15% band + methodology line + SI disclosure)
        └─ embedded in PolicyCompareModal.tsx; header chip in app/page.tsx (premiumBand state)
```

Parallel, independent chain (claim-experience parity, **not** premium-priced):

```
40-data/reviews/<slug>.json
   ▼
brain_tools._insurer_reviews(slug)        [cached read; None if missing]
   ▼
scorecard.score_claim_experience(p, insurer_reviews=…)   [IRDAI CSR + complaints/10k → sub-score]
   ▼
scorecard.build_scorecard(data, insurer_reviews=…, profile=…)
   ├─ recommendation path:  brain_tools._scorecard_signal()  → cited-card grade
   └─ marketplace path:     main.py /api/policies/all         → marketplace grade
   ▼  PARITY INVARIANT (tests/test_scorecard_parity.py): cited-card GRADE LETTER == marketplace GRADE LETTER
```

## 2. Node-by-node dependency contract

### 2.1 `40-data/premiums/illustrative_premiums.json`

- **Consumes:** nothing — it is the single source of truth. Top-level keys: `last_updated`, `methodology`, `sources_consulted`, `notes`, `base_premiums` (100 entries), `scaling_factors`, `link_rot_repairs`.
- **Depended on by:** `_load_data()` (the ONLY reader), and transitively everything below it. Also documented by [`premium-source-map.md`](premium-source-map.md).
- **Sample shape (post-harvest):** evidenced samples carry `age`, `sum_insured_inr`, `city_tier`, `smoker`, `family_size`, `annual_premium_inr`, `source_url`, `source_quote`, `source_quality`, `fetched_on` (+ optional `source_note`/`derivation_note`/`variant`). 194 evidenced samples across 73 entries; 27 entries are model-only.
- **If you change X you must re-verify Y:**
  - Change/add a `samples[]` entry → re-run `_plausible_samples()` mentally against `_per_lakh_band()` for that policy type (comprehensive 250–6500/L; top-up 10–800/L; benefit plans unbounded). A sample outside the band is silently dropped, so the policy may regress to the model path.
  - Change a `base_premiums` **key** → re-verify `_canonical_sample_key()` still resolves the marketplace/recommended id (suffix + single-hyphen forms) AND that the key appears in `_DEFAULT_BAND_POLICY_IDS` if it should be in the header basket.
  - Change `scaling_factors` (age/SI/city/floater/smoker/PED multipliers) → re-verify the **#38 ratio-normalization** in `estimate()`: it divides the sample's own multipliers out and re-applies the user's, so a changed factor moves every curated-path estimate. Re-run `tests/test_premium_attribute_and_normalization.py` + `tests/test_premium_reconciliation.py`.
  - Edit/add/remove any evidenced sample → **regenerate [`premium-source-map.md`](premium-source-map.md) §1 counts and §2/§3 tables in the same commit** (the JSON is the single source of truth for both docs).

### 2.2 `premium_calculator._load_data()`

- **Consumes:** `PREMIUM_DATA` path (`settings.DATA_DIR / "premiums" / "illustrative_premiums.json"`).
- **Depended on by:** `estimate()`, `bulk_estimate()` (each call re-reads — no module cache).
- **If you change X:** moving/renaming the JSON, or breaking its JSON validity, makes `_load_data()` return `{}` **silently** → every policy falls to `FALLBACK_*` constants (no exception, no log). Re-verify by calling `estimate(policy_id=...)` for a known-curated policy and asserting `base_sample_used is not None`.

### 2.3 `premium_calculator._canonical_sample_key()`

- **Consumes:** the incoming `policy_id`, `base_premiums` keys, `_SAMPLE_DOCTYPE_SUFFIXES`, `_KNOWN_BAD_SAMPLE_KEYS`.
- **Depended on by:** `estimate()` (sample lookup) **and** `bulk_estimate()` (the `if _canonical_sample_key(pid, …) is not None` branch decides curated-anchor vs flat-base). Both must agree or the widget and the per-policy panel diverge.
- **If you change X:** add a doctype suffix or change the hyphen-normalization → re-verify BOTH call sites resolve the same key (a mismatch reintroduces the ₹33,700 collision / the SBI Arogya Supreme double-floater bug). `_KNOWN_BAD_SAMPLE_KEYS` is currently the empty frozenset (SBI Arogya Supreme was unquarantined 2026-05-18 after its bad brochure-extract was physically replaced with real SBI rate-chart figures); to re-quarantine a proven-bad entry, add its key here AND note it in the source map.

### 2.4 `premium_calculator._per_lakh_band()` / `_plausible_samples()`

- **Consumes:** `policy_id` substring (type detection), each sample's `sum_insured_inr` + `annual_premium_inr`.
- **Depended on by:** `estimate()` (input guard before interpolation) **and** the **OUTPUT plausibility ceiling** at the end of `estimate()` (ceiling = `_hi_b * 1.5`).
- **If you change X:** widening/narrowing a band → re-verify (a) no real evidenced sample is now dropped (would silently demote a policy to the model path) and (b) the output ceiling still trips only on genuinely broken data. The product-type substrings (`top-up`, `hospital-cash`, `cancer`, `critical-illness`, …) are matched against the lowercased `policy_id`; renaming a key can flip a plan between bands.

### 2.5 `premium_calculator._attribute_base_factor()` — the no-sample path

- **Consumes:** `policy_id` substring only. No JSON I/O — deterministic on the id.
- **Depended on by:** `estimate()` (no-sample branch), `bulk_estimate()` (flat-base `flat_base = BULK_BASE_INR_PER_LAKH * si_lakhs * _attribute_base_factor(pid)`), and therefore every one of the **27 model-only entries** in the source map §3.
- **Returns:** super-top-up/top-up 0.35×, hospital-cash/fixed-benefit 0.30×, cancer/critical-illness 0.55×, `sanjeevani` 0.70×, comprehensive 1.0× (no regression for the dominant type).
- **If you change X:** changing a factor moves every model-only policy of that type AND the flat-base widget number. Re-verify against the type-band so a model-only estimate stays inside `_per_lakh_band()`; re-run `tests/test_premium_attribute_and_normalization.py`. **Provenance label:** model-path estimates carry the methodology string *"Indicative estimate modelled from this plan's product type … NOT a quote"* — if you ever anchor a model-only policy to a real sample, move it out of source-map §3 into §2 and the label flips to the *"Anchored to a verified public-quote sample"* variant automatically (driven by `sample_used is not None`).

### 2.6 `premium_calculator.estimate()`

- **Consumes:** `_load_data()`, `_canonical_sample_key()`, `_plausible_samples()`, `_interpolate_from_samples()`, `scaling_factors`, the B6/D2/KI-275 loadings (`_health_loading`, `_existing_cover_loading`, `_parents_loading`, `_copay_discount`, `_family_history_loading`, `_copay_multiplier`).
- **Depended on by:** `bulk_estimate()` (curated path calls `estimate()` directly), `POST /api/premium/estimate`, and indirectly the header band (`estimate_premium_band → bulk_estimate → estimate`).
- **Two critical internal invariants:**
  1. **#38 full ratio-normalization** — the sample's own age/SI/city/family multipliers are divided out, then the user's profile is applied exactly once by the unconditional city/floater/smoker/PED block. Sample `family_size` is HEADCOUNT (1=individual); `estimate()`'s floater key is dependents-beyond-self (`max(0, headcount-1)`). Breaking this re-introduces double-counted floater (the SBI ₹149,800 bug).
  2. **OUTPUT plausibility ceiling** — if a sample-anchored point still exceeds `_per_lakh_band(...)[1] * 1.5` per lakh after normalization+loadings, it drops `sample_used`/`sources` and falls back to the policy-blind model base.
- **If you change X:** changing the loading order, the normalization, or the ceiling → re-run BOTH `tests/test_premium_reconciliation.py` and `tests/test_premium_attribute_and_normalization.py`; the header-band p25–p75 contract depends on `estimate()` being stable.

### 2.7 `premium_calculator.bulk_estimate()` / `estimate_premium_band()`

- **Consumes:** `bulk_estimate()` calls `estimate()` on the curated path; `estimate_premium_band()` calls `bulk_estimate()` over `_DEFAULT_BAND_POLICY_IDS` (26 policies) and `resolve_profile_sum_insured()` for the shared SI.
- **Depended on by:** `POST /api/premium/bulk`, `GET /api/profile/predicted-premium-band`, the PolicyCompareModal widget, and the header "Premium range" chip.
- **SI contract (KI-278):** `resolve_profile_sum_insured()` precedence (`desired_sum_insured_inr ?? existing_cover_inr ?? ₹10L`, snapped to ₹50k) MUST stay byte-identical to `PremiumCalculatorPanel`'s slider seed (`frontend/src/app/page.tsx` ~L2417) and `PolicyPremiumWidget`'s `initialSumInsured`. The chip band is the **p25–p75 interquartile** of the basket, NOT raw min–max.
- **If you change X:**
  - Add/remove a policy in `_DEFAULT_BAND_POLICY_IDS` → re-verify each id resolves via `_canonical_sample_key()` (else it silently uses the flat path) and re-check the chip band is still a sane range.
  - Change the SI precedence on either side → change BOTH `resolve_profile_sum_insured()` and the page.tsx slider seed in the same commit, else header ≠ panel returns. Re-run `tests/test_premium_reconciliation.py`.

### 2.8 API layer (`backend/main.py`)

- **Consumes:** `estimate`, `bulk_estimate`, `estimate_premium_band`, `unpublished_si_disclosure`, `_policy_corroborated_si`.
- **Depended on by:** `frontend/src/lib/api.ts` (`postPremiumEstimate`, `PremiumEstimateResponse` type), `PolicyPremiumWidget.tsx`, `PolicyCompareModal.tsx`, `app/page.tsx` (`premiumBand`).
- **Contract surfaces that must not drift:** `PremiumEstimateResponse.base_sample_used` (widget shows/hides its "Estimate" badge off this — it is `e.base_sample_used is not None`), `methodology` (rendered verbatim under the estimate), `sources` (the source URLs), `sum_insured_disclosure` (rendered verbatim only when `_policy_corroborated_si(...).kind == "none"`). `predicted-premium-band` feeds the profile dict via `brain_tools.SLOT_UNION` with the answered-only gate (`profile.asked`).
- **If you change X:** renaming/removing a response field → update `frontend/src/lib/api.ts` types + every `.tsx` consumer in the same commit. Changing `tenure_years`/`deductible_inr` snapping uses `BULK_TENURE_MULT`/`BULK_DEDUCTIBLE_DISCOUNT` from the calculator — keep them in sync.

### 2.9 Reviews → scorecard claim-experience PARITY chain

- **Consumes:** `40-data/reviews/<slug>.json` → `brain_tools._insurer_reviews(slug)` (cached; `None` if missing) → `scorecard.score_claim_experience(p, insurer_reviews=…)` → `scorecard.build_scorecard(data, insurer_reviews=…, profile=…)`.
- **Two paths that MUST stay in parity:**
  - **Recommendation / cited-card grade:** `brain_tools._scorecard_signal()` builds `data` via `_merge_curated(extracted, curated)` (KI-PARITY 2026-05-18 — curated-only made the cited grade systematically lower), resolves `slug`, passes `_insurer_reviews(slug)` into `build_scorecard`.
  - **Marketplace grade:** `/api/policies/all` builds `build_scorecard` on the full curated+reviews layer.
- **PARITY INVARIANT:** the cited-card **grade letter** must equal the marketplace **grade letter** for the same policy. Locked by `tests/test_scorecard_parity.py` (overall scores may differ by a few points because the marketplace overlays `EXTRACTED_DIR`; the LETTER, which `_recommendation_fit` gates on, must match).
- **If you change X:**
  - Edit a `40-data/reviews/<slug>.json` `claim_metrics` value → re-verify `score_claim_experience()` band thresholds (CSR ≥95 +20, ≥90 +12, ≥85 +5, ≥75 −6, else −20; complaints/10k ≤10 +8 … >45 −16) and re-run `tests/test_scorecard_parity.py` — a CSR/complaints change can flip the sub-score enough to move the grade letter, which must move identically on BOTH paths.
  - Change `_scorecard_signal()`'s data assembly (`_merge_curated`, `_candidate_stems`, slug derivation) → re-run `tests/test_scorecard_parity.py`; divergence makes the recommendation path drop policies (empty `citations` → CitedPolicyCards never render).
  - This chain is **independent of premium pricing** — it does NOT read `illustrative_premiums.json`. Document it here only because it shares the source-methodology + dependency-map treatment and the same `_insurer_reviews`/scorecard machinery.

## 3. Quick "change X → re-verify Y" lookup

| If you change… | You must re-verify… | Tests / docs to re-run |
| --- | --- | --- |
| A `samples[]` entry / a premium figure | `_plausible_samples` band, `_interpolate_from_samples` nearest pick, `estimate()` #38 normalization | `test_premium_reconciliation.py`, `test_premium_attribute_and_normalization.py`, regenerate `premium-source-map.md` |
| A `base_premiums` key name | `_canonical_sample_key` both call sites, `_DEFAULT_BAND_POLICY_IDS` membership, `_per_lakh_band`/`_attribute_base_factor` substring match | `test_premium_reconciliation.py`, source map §2/§3 |
| `scaling_factors` multipliers | `estimate()` ratio-normalization output, header band | both premium tests |
| `_attribute_base_factor` | every model-only entry + flat-base widget, type-band sanity | `test_premium_attribute_and_normalization.py`, source map §3 |
| `resolve_profile_sum_insured` precedence | page.tsx slider seed byte-identity, header == panel | `test_premium_reconciliation.py` |
| A `PremiumEstimateResponse` field | `frontend/src/lib/api.ts` types, `PolicyPremiumWidget.tsx`, `PolicyCompareModal.tsx`, `page.tsx` | frontend typecheck/build |
| `_DEFAULT_BAND_POLICY_IDS` | each id resolves via `_canonical_sample_key`, chip band sanity | `test_premium_reconciliation.py` |
| `40-data/reviews/<slug>.json` claim_metrics | `score_claim_experience` thresholds, cited-grade == marketplace-grade | `test_scorecard_parity.py` |
| `_scorecard_signal` data assembly | parity invariant (grade letter both paths) | `test_scorecard_parity.py` |
| Move/rename/corrupt the JSON | `_load_data()` returns `{}` SILENTLY → all-fallback | manual `estimate()` smoke on a known-curated id |

## 4. Regeneration

Both this map and [`premium-source-map.md`](premium-source-map.md) are derived from the same single source of truth (`40-data/premiums/illustrative_premiums.json`) plus the code chain above. After any premium-harvest or calculator-contract change, update the affected §2 node contract and the §3 lookup row in the **same commit** as the code/data change — the "if you change X you must re-verify Y" rules are only useful if they stay in lockstep with the chain.
