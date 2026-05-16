"""SI RATIONALISATION (D1/D2/D3, 2026-05-16).

Pins the three user-approved decisions:

  D1 — DISPLAY: continuous "₹X – ₹Y" band ONLY for a genuine continuous
       offering; otherwise discrete corroborated tiers; "As per policy
       schedule" when nothing corroborates.
  D2 — PREMIUM CLAMP: the global ₹5 L / ₹1 Cr clamp is REMOVED; a policy
       with no published SI prices against desired_sum_insured_inr (else
       ₹10 L) and surfaces the verbatim disclosure.
  D3 — SOURCE-QUOTE CORROBORATION: deterministically drop every SI value
       the field's own source_quote does not genuinely state. No LLM, no
       fabrication.
"""

from backend.sum_insured import (
    corroborate,
    corroborated_values,
    classify,
    rationalise,
)
from backend.premium_calculator import (
    resolve_profile_sum_insured,
    fallback_sum_insured_for_unpublished,
    unpublished_si_disclosure,
)


# ---------------------------------------------------------------------------
# D3 — source-quote corroboration (deterministic, no fabrication)
# ---------------------------------------------------------------------------

def test_inline_lakh_and_crore_units_corroborate():
    # "3L, 5L, 10L" → ₹3/5/10 L all corroborate; a value the quote doesn't
    # state (₹4 L) is dropped.
    q = "options of SI of ₹ 3L, 5L, 10L."
    assert corroborate([300_000, 400_000, 500_000, 1_000_000], q) == [
        300_000, 500_000, 1_000_000
    ]
    # "1 Crore" → ₹1 Cr corroborates.
    assert 10_000_000 in corroborated_values("Maximum Sum insured of ` 1 Crore")


def test_header_unit_governs_a_bare_list():
    # "(Rs. in Lakhs) 3.00 5.00 10.00 …" — the unit label governs the whole
    # list; each bare number is a lakh figure and corroborates.
    q = "Basic Sum Insured (Rs. in Lakh) 3.00 5.00 10.00 15.00 20.00, 25.00, 50.00"
    kept = corroborate([300_000, 500_000, 1_000_000, 1_500_000, 5_000_000], q)
    assert kept == [300_000, 500_000, 1_000_000, 1_500_000, 5_000_000]


def test_indian_grouping_is_collapsed_but_decimal_list_is_not():
    # Indian grouping joins into one number…
    assert 500_000 in corroborated_values("Sum Insured of Rs 5,00,000")
    # …but a decimal list separated by comma-space is NOT mis-joined.
    q = "Sum insured (₹) Lakhs 0.5, 1, 1.5, 2, 2.5, 3"
    kept = corroborate([50_000, 100_000, 150_000, 200_000, 250_000, 300_000], q)
    assert kept == [50_000, 100_000, 150_000, 200_000, 250_000, 300_000]


def test_uncorroborated_values_are_dropped_completely():
    # A daily-cash product: "₹ 500, 1000, 2000…" are per-day amounts, not SI
    # — no lakh/crore unit, all sub-₹10k → nothing corroborates.
    q = "Daily Cash Amount of ₹ 500, 1000, 2000, 3000, 5000 per day"
    assert corroborate([500, 1000, 2000, 3000, 5000], q) == []
    # Truncated quote with no figures → nothing corroborates.
    assert corroborate([500_000, 1_000_000], "Sum Insured options range from Rs") == []


def test_no_fabrication_only_confirms_listed_values():
    # The quote mentions ₹50 L but the policy never lists it → it is NOT
    # added. corroborate() can only ever return a subset of the input list.
    q = "Sum Insured 3 lacs to 50 lacs"
    out = corroborate([300_000, 1_000_000], q)
    assert set(out).issubset({300_000, 1_000_000})
    assert 5_000_000 not in out


# ---------------------------------------------------------------------------
# D1 — band vs tiers vs none classification + display contract
# ---------------------------------------------------------------------------

def test_band_requires_range_language_and_wide_spread():
    # Range language + wide corroborated spread → band.
    assert classify([50_000, 1_000_000], "₹ 50,000 to ₹ 10L, in multiple of ₹ 50,000") == "band"
    # Range language but narrow spread → still discrete tiers.
    assert classify([300_000, 500_000], "Sum Insured 3 lacs to 5 lacs") == "tiers"
    # No range language → discrete tiers even if wide.
    assert classify([300_000, 7_500_000], "3 lacs / 75 lacs") == "tiers"
    # Nothing corroborated → none.
    assert classify([], "anything") == "none"


def test_rationalise_packages_the_display_view():
    band = rationalise([50_000, 1_000_000], "₹ 50,000 to ₹ 10L, in multiples of ₹ 50,000")
    assert band.kind == "band" and band.is_band
    assert band.min_inr == 50_000 and band.max_inr == 1_000_000

    tiers = rationalise([300_000, 750_000, 1_000_000],
                        "Sum Insured 3 lacs to 7.5 lacs and 10 Lacs")
    assert tiers.kind == "tiers" and not tiers.is_band
    assert tiers.tiers == [300_000, 750_000, 1_000_000]

    none = rationalise([500_000], "Sum Insured options range from Rs")
    assert none.kind == "none"
    assert none.min_inr is None and none.max_inr is None and none.tiers == []


# ---------------------------------------------------------------------------
# D2 — clamp removal + unpublished-SI fallback + verbatim disclosure
# ---------------------------------------------------------------------------

def test_clamp_is_removed_real_target_is_priced():
    # Was clamped to ₹1 Cr / ₹5 L respectively pre-D2.
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 20_000_000}) == 20_000_000
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 100_000}) == 100_000
    # Grid snap still applies.
    assert resolve_profile_sum_insured({"desired_sum_insured_inr": 1_234_000}) == 1_250_000


def test_unpublished_si_fallback_precedence():
    # desired_sum_insured_inr wins when present…
    assert fallback_sum_insured_for_unpublished({"desired_sum_insured_inr": 2_500_000}) == 2_500_000
    # …else the ₹10 L default (D2 spec).
    assert fallback_sum_insured_for_unpublished({}) == 1_000_000
    assert fallback_sum_insured_for_unpublished(None) == 1_000_000
    assert fallback_sum_insured_for_unpublished({"desired_sum_insured_inr": 0}) == 1_000_000


def test_disclosure_string_is_verbatim():
    assert unpublished_si_disclosure(1_000_000) == (
        "Estimate shown for ₹10 L cover — this policy's sum insured isn't published."
    )
    assert unpublished_si_disclosure(2_500_000) == (
        "Estimate shown for ₹25 L cover — this policy's sum insured isn't published."
    )
    assert unpublished_si_disclosure(15_000_000) == (
        "Estimate shown for ₹1.5 Cr cover — this policy's sum insured isn't published."
    )


# ---------------------------------------------------------------------------
# End-to-end against the real curated layer (proves it runs on live data)
# ---------------------------------------------------------------------------

def test_end_to_end_partition_on_curated_layer():
    import backend.main as m
    cf = m._load_curated_facts()
    kinds = {"band": 0, "tiers": 0, "none": 0}
    for pid, data in cf.items():
        if pid != data.get("policy_id", pid):
            continue
        sio = data.get("sum_insured_options")
        if not (isinstance(sio, list) and sio):
            continue
        view = rationalise(sio, m._si_source_quote(data))
        kinds[view.kind if view.kind in ("band", "none") else "tiers"] += 1
    # Every curated entry with an SI list is partitioned into exactly one
    # bucket; the corroboration filter must keep real bands non-empty.
    assert kinds["band"] >= 1
    assert kinds["tiers"] >= 1
    assert sum(kinds.values()) >= 60
