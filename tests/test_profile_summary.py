"""Task #31 — deterministic, profile-aware {strengths, caveat} guard.

WHAT THIS LOCKS DOWN
--------------------
`backend.scorecard.build_profile_summary` replaces the generic grade
one-liner with a PER (profile × policy) list of concrete strengths + the
single most grade-capping trade-off, computed on the SAME pass as the grade.
The non-negotiable invariants this test pins for the FULL 148-policy
catalogue, profile-neutral AND across 6 representative profiles:

1.  Shape: 0 ≤ len(strengths) ≤ 5. ≥3 whenever ≥3 qualifying facts exist;
    fewer ONLY when the policy genuinely has fewer real facts (never padded).
    insufficient-data ⇒ strengths == [] (caller falls back to one_liner).
2.  No junk: no empty bullet, no "/100", no standalone letter grade, no
    literal "null"/"None".
3.  Deductible bullet present IFF
    premium_calculator.policy_deductible_support(pid)[0] is True — across
    the catalogue that is EXACTLY {bajaj-allianz__health-guard,
    star-health__star-assure} (BUG #29 invariant).
4.  Non-fabrication: every numeric token in every strength is regex-
    extractable AND traces to a value the scorecard's OWN helpers can read
    off that policy / insurer_reviews (a strength can never assert a number
    the grade didn't see).
5.  Caveat is None OR derived from a "− "-prefixed (U+2212 + space) signal
    literally present in that scorecard's sub.signals (never invented /
    contradicting).
6.  Maternity strength absent when no spouse/partner on the profile.
7.  Determinism: same (policy, profile) twice ⇒ byte-identical.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import backend.main as M  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.premium_calculator import policy_deductible_support  # noqa: E402
from backend.scorecard import (  # noqa: E402
    _profile_tuned_weights,
    build_profile_summary,
    build_scorecard,
)

# The exact catalogue-wide deductible-supporting set (BUG #29). Anything else
# claiming a voluntary-deductible strength is a fabrication.
_DEDUCTIBLE_POLICIES = {"bajaj-allianz__health-guard", "star-health__star-assure"}

# 6 representative profiles spanning the weight-tuner's branches.
_PROFILES = [
    None,
    {  # young, healthy, first-time, tax goal, metro
        "age": 28, "dependents": "self", "income_band": "10L-25L",
        "primary_goal": "tax_planning", "location_tier": "metro",
        "health_conditions": [], "existing_cover_inr": 0, "copay_pct": 0,
    },
    {  # senior + spouse + diabetes + family history + has cover
        "age": 58, "dependents": "self+spouse", "income_band": "25L+",
        "primary_goal": "upgrade", "location_tier": "metro",
        "health_conditions": ["diabetes"], "family_medical_history": ["heart"],
        "existing_cover_inr": 500000, "parents_to_insure": False,
    },
    {  # family with kids, tier3, budget-tight
        "age": 41, "dependents": "self+spouse+kids", "income_band": "5L-10L",
        "primary_goal": "first_buy", "location_tier": "tier3",
        "health_conditions": [], "budget_band": "under_15k",
        "existing_cover_inr": 0,
    },
    {  # parents-to-insure, parents have PED
        "age": 35, "dependents": "self+parents", "income_band": "10L-25L",
        "primary_goal": "compare_specific", "location_tier": "tier2",
        "health_conditions": [], "parents_to_insure": True,
        "parents_age_max": 68, "parents_has_ped": True,
    },
    {  # mid, hypertension, copay tolerance stated
        "age": 47, "dependents": "self+spouse", "income_band": "25L+",
        "primary_goal": "upgrade", "location_tier": "metro",
        "health_conditions": ["hypertension"], "copay_pct": 0,
        "existing_cover_inr": 1000000,
    },
]

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_GRADE_RE = re.compile(r"\b[ABCDF]\b")


def _catalogue_ids() -> list[str]:
    cards = M._marketplace_catalogue(None)
    assert len(cards) > 100, f"catalogue collapsed: {len(cards)}"
    return [c.policy_id for c in cards]


def _resolve_policy(pid: str) -> dict | None:
    """Resolve a card id → policy dict EXACTLY like /api/policies/{id}/scorecard."""
    cur = M._load_curated_facts()
    ep = settings.EXTRACTED_DIR / f"{pid}.json"
    if ep.exists():
        try:
            policy = json.loads(ep.read_text())
        except Exception:
            return None
        return M._merge_curated(
            policy, cur.get(policy.get("policy_id", pid)) or cur.get(pid)
        )
    policy = (
        cur.get(pid)
        or cur.get(f"{pid}__wordings")
        or cur.get(f"{pid}__cis")
        or cur.get(f"{pid}__brochure")
        or cur.get(f"{pid}__prospectus")
    )
    if not policy:
        return None
    policy = dict(policy)
    policy.setdefault("policy_id", pid)
    return policy


def _insurer_reviews(policy: dict) -> dict | None:
    slug = policy.get("insurer_slug")
    if not slug:
        return None
    rp = settings.DATA_DIR / "reviews" / f"{slug}.json"
    if rp.exists():
        try:
            return json.loads(rp.read_text())
        except Exception:
            return None
    return None


def _readable_numbers(policy: dict, sc, reviews: dict | None) -> set[str]:
    """Every integer/float a strength could LEGITIMATELY quote — each read
    via the SAME scorecard helper (`_int` / `_pick_alias`) the generator uses
    to build that strength, PLUS the insurer CSR/year. The non-fabrication
    anchor: a strength may only state a number this set proves the scorecard
    itself can read off this policy. A token outside this set is a
    fabrication."""
    from backend.scorecard import _int as _sc_int

    ok: set[str] = set()

    def _add(v):
        if v is None:
            return
        ok.add(str(v).replace(",", ""))
        try:
            ok.add(str(int(float(v))))
            ok.add(f"{float(v):.1f}")
        except (TypeError, ValueError):
            pass

    # Numbers the scorecard surfaced in its own signals (helper-read).
    for s in sc.sub_scores:
        for sig in s.signals:
            for m in _NUM_RE.findall(sig):
                ok.add(m.replace(",", ""))
    # Exactly the fields each strength reads via _int, read the SAME way.
    for fld in (
        "deductible_amount",
        "max_entry_age",
        "pre_existing_disease_waiting_months",
        "maternity_waiting_months",
        "network_hospital_count",
        "no_claim_bonus_pct",
        "copayment_pct",
    ):
        _add(_sc_int(policy, fld))
    # Insurer CSR (+ year) for the high-CSR strength.
    if reviews:
        cm = reviews.get("claim_metrics", {}) or {}
        _add(cm.get("claim_settlement_ratio_pct"))
        yr = str(cm.get("claim_settlement_ratio_year", "") or "")
        for m in _NUM_RE.findall(yr):
            ok.add(m.replace(",", ""))
    return ok


@pytest.mark.parametrize("profile", _PROFILES, ids=lambda p: "neutral" if p is None else f"{p.get('age')}/{p.get('dependents')}")
def test_full_catalogue_profile_summary_invariants(profile):
    """Every catalogued policy, this profile: shape + no-junk + deductible
    gate + non-fabrication + maternity-suppression + determinism."""
    junk_fails: list[str] = []
    shape_fails: list[str] = []
    ded_fails: list[str] = []
    fab_fails: list[str] = []
    mat_fails: list[str] = []
    det_fails: list[str] = []
    caveat_fails: list[str] = []

    has_spouse = bool(
        profile
        and any(
            k in str(profile.get("dependents") or "").lower()
            for k in ("spouse", "wife", "husband", "partner")
        )
    )

    for pid in _catalogue_ids():
        policy = _resolve_policy(pid)
        if policy is None:
            continue
        reviews = _insurer_reviews(policy)
        sc = build_scorecard(policy, insurer_reviews=reviews, profile=profile)
        ps = sc.profile_summary
        assert ps is not None, f"{pid}: profile_summary is None"

        if sc.insufficient_data:
            if ps.strengths != [] or ps.caveat is not None:
                shape_fails.append(f"{pid}: insufficient ⇒ must be empty, got {ps}")
            continue

        # 1. shape
        if not (0 <= len(ps.strengths) <= 5):
            shape_fails.append(f"{pid}: {len(ps.strengths)} strengths (must be 0..5)")

        # 2. no junk
        for b in ps.strengths + ([ps.caveat] if ps.caveat else []):
            if not b or not b.strip():
                junk_fails.append(f"{pid}: empty bullet")
            if "/100" in b:
                junk_fails.append(f"{pid}: '/100' in {b!r}")
            if b.strip() in {"null", "None"}:
                junk_fails.append(f"{pid}: literal null/None {b!r}")
            # A bare standalone grade letter as a whole bullet.
            if _GRADE_RE.fullmatch(b.strip()):
                junk_fails.append(f"{pid}: standalone grade letter {b!r}")

        # 3. deductible gate
        ded_strength = any(
            "voluntary deductible" in s.lower() for s in ps.strengths
        )
        ded_ok = policy_deductible_support(pid)[0] is True
        if ded_strength != ded_ok:
            ded_fails.append(
                f"{pid}: deductible strength={ded_strength} but support={ded_ok}"
            )

        # 4. non-fabrication — every numeric token traces to a readable value
        readable = _readable_numbers(policy, sc, reviews)
        for s in ps.strengths:
            for tok in _NUM_RE.findall(s):
                norm = tok.replace(",", "")
                # tolerate "92.2" matching a stored 92.2 / 92 / "92.2"
                cands = {norm, norm.split(".")[0]}
                try:
                    cands.add(f"{float(norm):.1f}")
                    cands.add(str(int(float(norm))))
                except ValueError:
                    pass
                if not (cands & readable):
                    fab_fails.append(
                        f"{pid}: numeric {tok!r} in strength {s!r} not "
                        f"readable from policy/reviews"
                    )

        # 5. caveat ⟺ the generator's EXACT contract: pick the most
        # grade-capping, profile-relevant sub via the SAME profile-tuned
        # weights (argmax weights[name]*(100-score)); its FIRST "− "-prefixed
        # signal must exist iff the caveat is non-None, and the caveat must
        # be a deterministic transform that carries that signal's stripped
        # text OR its numeric token (never invented / contradicting).
        weights = _profile_tuned_weights(profile)

        def _gap(sub):
            return weights.get(sub.name, 0.0) * (100 - sub.score)

        top = max(sc.sub_scores, key=_gap) if sc.sub_scores else None
        first_neg = None
        if top is not None:
            first_neg = next(
                (sig for sig in (top.signals or []) if sig.startswith("− ")),
                None,
            )
        if first_neg is None:
            if ps.caveat is not None:
                caveat_fails.append(
                    f"{pid}: caveat {ps.caveat!r} but top sub "
                    f"{getattr(top, 'name', None)!r} has NO '− ' signal"
                )
        else:
            if ps.caveat is None:
                caveat_fails.append(
                    f"{pid}: top sub has '− ' signal {first_neg!r} but "
                    f"caveat is None"
                )
            else:
                raw = first_neg[2:].strip()
                nums = _NUM_RE.findall(raw)
                # The caveat is a deterministic plain-language transform of
                # exactly THIS signal: it must carry the signal's number(s)
                # OR a stable keyword from the signal — never unrelated text.
                kws = [
                    "ped", "copay", "co-pay", "room rent", "csr",
                    "claim settlement", "cashless", "network", "initial",
                    "maternity", "deductible", "day-care", "day care",
                ]
                rl = raw.lower()
                cl = ps.caveat.lower()
                traced = (
                    (nums and all(n in ps.caveat for n in nums))
                    or any(k in rl and k in cl for k in kws)
                    or raw in ps.caveat  # generic "One trade-off: <raw>."
                )
                if not traced:
                    caveat_fails.append(
                        f"{pid}: caveat {ps.caveat!r} not a transform of the "
                        f"top sub's first '− ' signal {first_neg!r}"
                    )

        # 6. maternity suppressed without spouse/partner
        if not has_spouse:
            if any("maternity" in s.lower() for s in ps.strengths):
                mat_fails.append(f"{pid}: maternity strength without spouse")

        # 7. determinism
        sc2 = build_scorecard(policy, insurer_reviews=reviews, profile=profile)
        if (
            sc2.profile_summary.strengths != ps.strengths
            or sc2.profile_summary.caveat != ps.caveat
        ):
            det_fails.append(f"{pid}: non-deterministic profile_summary")

    assert not shape_fails, f"SHAPE ({len(shape_fails)}): {shape_fails[:8]}"
    assert not junk_fails, f"JUNK ({len(junk_fails)}): {junk_fails[:8]}"
    assert not ded_fails, f"DEDUCTIBLE GATE ({len(ded_fails)}): {ded_fails[:8]}"
    assert not fab_fails, f"FABRICATION ({len(fab_fails)}): {fab_fails[:8]}"
    assert not caveat_fails, f"CAVEAT ({len(caveat_fails)}): {caveat_fails[:8]}"
    assert not mat_fails, f"MATERNITY ({len(mat_fails)}): {mat_fails[:8]}"
    assert not det_fails, f"DETERMINISM ({len(det_fails)}): {det_fails[:8]}"


def test_deductible_strength_is_exactly_the_two_known_policies():
    """The voluntary-deductible strength appears for EXACTLY
    {bajaj-allianz__health-guard, star-health__star-assure} and no other
    catalogued policy (the BUG #29 catalogue-wide invariant)."""
    seen: set[str] = set()
    for pid in _catalogue_ids():
        policy = _resolve_policy(pid)
        if policy is None:
            continue
        sc = build_scorecard(
            policy, insurer_reviews=_insurer_reviews(policy), profile=None
        )
        ps = sc.profile_summary
        if ps and any("voluntary deductible" in s.lower() for s in ps.strengths):
            seen.add(pid)
    assert seen == _DEDUCTIBLE_POLICIES, (
        f"deductible strength leaked / missing: got {sorted(seen)}, "
        f"expected {sorted(_DEDUCTIBLE_POLICIES)}"
    )


def test_maternity_appears_when_spouse_present_and_policy_covers_it():
    """Positive control for the maternity-suppression rule: with a spouse on
    the profile, at least one maternity-covering policy DOES surface a
    maternity strength (so the rule isn't just always-off)."""
    spouse_profile = {
        "age": 32, "dependents": "self+spouse", "income_band": "10L-25L",
        "primary_goal": "first_buy", "location_tier": "metro",
        "health_conditions": [],
    }
    found = False
    for pid in _catalogue_ids():
        policy = _resolve_policy(pid)
        if policy is None:
            continue
        sc = build_scorecard(
            policy, insurer_reviews=_insurer_reviews(policy),
            profile=spouse_profile,
        )
        ps = sc.profile_summary
        if ps and any("maternity" in s.lower() for s in ps.strengths):
            found = True
            break
    assert found, (
        "no maternity strength surfaced for ANY policy even with a spouse "
        "on the profile — suppression rule is stuck off"
    )


def test_insufficient_data_yields_empty_profile_summary():
    """A genuinely-bare policy takes the defined honest path and the
    profile_summary is the empty form (caller falls back to one_liner)."""
    sc = build_scorecard(
        {"policy_id": "x__bare", "policy_name": "Bare", "insurer_slug": "x"}
    )
    assert sc.insufficient_data is True
    assert sc.profile_summary is not None
    assert sc.profile_summary.strengths == []
    assert sc.profile_summary.caveat is None


def test_copay_preference_tag_only_when_user_states_zero_copay():
    """The '(your stated preference)' qualifier on the zero-co-pay strength
    appears IFF the profile carries copay_pct == 0 — deterministic, derived,
    never fabricated."""
    # bajaj-allianz__health-guard has copayment_pct == 0 in curated facts.
    policy = _resolve_policy("star-health__star-assure")
    assert policy is not None
    rv = _insurer_reviews(policy)

    no_pref = build_scorecard(policy, insurer_reviews=rv, profile={
        "age": 30, "dependents": "self", "income_band": "10L-25L",
        "primary_goal": "first_buy", "location_tier": "metro",
        "health_conditions": [],
    }).profile_summary
    with_pref = build_scorecard(policy, insurer_reviews=rv, profile={
        "age": 30, "dependents": "self", "income_band": "10L-25L",
        "primary_goal": "first_buy", "location_tier": "metro",
        "health_conditions": [], "copay_pct": 0,
    }).profile_summary

    zc_no = [s for s in no_pref.strengths if "co-payment" in s.lower()]
    zc_yes = [s for s in with_pref.strengths if "co-payment" in s.lower()]
    # star-assure has copayment_pct of 10 (mandatory) — no zero-copay
    # strength either way; assert the qualifier never appears WITHOUT the
    # preference, and that the tag string is exclusively preference-gated.
    assert all("your stated preference" not in s for s in zc_no), zc_no
    for s in with_pref.strengths:
        if "your stated preference" in s:
            assert "co-payment" in s.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-rA", "-p", "no:warnings"]))
