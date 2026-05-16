"""Retrieval-side filters + guards (A3 / A6 audit fixes).

This module is a SIDECAR to `rag/retrieve.py` and `backend/orchestrator.py`.
It is deliberately a separate file so concurrent edits to orchestrator.py
don't conflict with the retrieval-correctness work.

Public API:
    apply_profile_filter(chunks, profile)       -> list[RetrievedChunk]
        Drop chunks for policies the user is demographically ineligible for.
    apply_eligibility_filter(chunks, profile)   -> list[RetrievedChunk]
        Drop chunks the user is STRUCTURALLY ineligible for / that clearly
        contradict an explicit stated need (KI-278):
          - top-up / super-top-up when the user has no existing base cover
          - plans whose max sum-insured cannot meet the requested SI
          - high co-pay plans when the user explicitly wants zero co-pay
    rank_by_profile_fit(chunks, profile)        -> list[RetrievedChunk]
        Re-order surviving chunks so the plans that best match the stated
        needs (grade, co-pay, SI headroom) rank above weak-fit plans whose
        only advantage was raw cosine similarity (KI-278).
    bypass_cosine_for_exact_match(chunks, query) -> list[RetrievedChunk] | None
        If the query contains an IRDAI UIN or an exact policy name, return a
        substring-matched chunk list (caller can use this instead of cosine).
        Returns None if no exact-match signal in the query.
    empty_retrieval_guard(chunks, intent)        -> dict | None
        Return a structured "empty_retrieval" signal when filtered chunk count
        is below the minimum for a recommendation intent.
    enforce_citation_grounding(chunks)           -> list[RetrievedChunk]
        Reject chunks missing policy_id / policy_name / chunk_offset.
    dedup_by_policy(chunks)                      -> list[RetrievedChunk]
        Within a top-K, keep highest-score chunk per policy_id.

All functions are pure / side-effect-free; safe to call from the orchestrator
or from rag/retrieve.py without import-cycle risk.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# We avoid importing RetrievedChunk at module-import time to keep this file
# import-cycle-safe. Instead we type-duck on attributes.

# ---------------------------------------------------------------------------
# Constants — keep tunables here so they're greppable from one place.
# ---------------------------------------------------------------------------

# Age tolerance for the profile pre-filter. The catalog's min_entry_age /
# max_entry_age is the regulator-filed entry age, but real underwriters allow
# a small grace band; we mirror that grace here so we don't accidentally
# drop a perfectly fine policy because the user is one year off the edge.
PROFILE_AGE_TOLERANCE = 2

# A "senior-only" policy is one whose marketing/eligibility makes it
# inappropriate for adults under 50. We detect this from the policy name
# AND from the min_entry_age metadata (>=60 ⇒ senior-only).
SENIOR_ONLY_NAME_RE = re.compile(
    r"\b(senior|red\s*carpet|silver|elder|varisht|varistha|"
    r"sixty\s*plus|60\s*plus|seniority|golden\s*years)\b",
    flags=re.IGNORECASE,
)

# Adult-only / young-adult-only plans typically cap at 50 or 55 and have
# no senior variant. If the user is >=60 these are inappropriate
# (they need a senior variant of the SAME insurer/family instead).
ADULT_ONLY_NAME_RE = re.compile(
    r"\b(young\s*star|young\s*adult|millennial|gen\s*z|under\s*45|"
    r"early\s*career|first\s*time)\b",
    flags=re.IGNORECASE,
)

# Maternity-themed policies. If the profile has no female adult AND no
# maternity goal, these are noise.
MATERNITY_NAME_RE = re.compile(
    r"\b(maternity|mother\s*&?\s*baby|mother\s*to\s*be|"
    r"new\s*born|joy|stork|baby\s*shield|pregnancy)\b",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# KI-278 — Eligibility / profile-fit tunables (2026-05-16)
# ---------------------------------------------------------------------------
# A plan is a TOP-UP / SUPER-TOP-UP (only useful ALONGSIDE an existing base
# policy or above a large out-of-pocket deductible) when ANY of these hold:
#   1. policy_type_indemnity_or_fixed contains "top_up" / "top-up" / "super"
#   2. the policy NAME matches TOP_UP_NAME_RE
#   3. it carries a large aggregate `deductible_amount` (the de-facto base
#      cover the user must already hold) — see TOP_UP_DEDUCTIBLE_FLOOR_INR
TOP_UP_NAME_RE = re.compile(
    r"\b(super[\s\-]?top[\s\-]?up|top[\s\-]?up|topup|super[\s\-]?top)\b",
    flags=re.IGNORECASE,
)

# An aggregate deductible at/above this rupee floor means the plan only pays
# AFTER the insured has already spent this much (from a base policy or
# pocket) — i.e. it is functionally a top-up and unusable as a sole first
# policy. ₹1,00,000 is conservative: real top-ups deductibles start ~₹2-3L;
# a genuine non-top-up plan never carries a six-figure aggregate deductible.
TOP_UP_DEDUCTIBLE_FLOOR_INR = 100_000

# When the user explicitly wants zero co-pay AND is not income-constrained
# (can afford full cover), any plan whose mandatory co-payment exceeds this
# percentage is a hard mismatch and is dropped. A 0 here means "drop ANY
# plan with a non-zero mandatory co-pay for a strict zero-copay user".
ZERO_COPAY_USER_MAX_COPAY_PCT = 0

# Even when the user did NOT explicitly demand zero co-pay, a punitive
# co-pay (>= this) is dropped because no metric recovers from a 50% claim
# haircut for a metro / high-income first-time buyer.
PUNITIVE_COPAY_PCT = 40

# SI headroom: a plan is SI-eligible only if its largest sum-insured option
# is at least the requested SI. (We do NOT require an exact tier — most
# insurers interpolate; but a plan whose ceiling is below the ask cannot
# deliver the cover the user said they need.)
# Minimum chunk count to attempt a recommendation. Below this we ask for
# one more clarifier instead of letting the brain hallucinate.
MIN_CHUNKS_FOR_RECOMMENDATION = 3

# Recommendation-style intents — the empty-retrieval guard fires only on these.
# Other intents (faq, regulatory, smalltalk) tolerate sparse retrieval.
_RECOMMENDATION_INTENTS = {
    "recommend",
    "recommendation",
    "compare",
    "comparison",
    "suggest",
    "shortlist",
    "best_policy",
    "pick_for_me",
}

# IRDAI UIN pattern. Real UINs look like "IRDA/HLT/HDFC/V.I/188/14-15"
# or "IRDAI/HLT/HDFC/V.I/188/14-15" — we accept both.
UIN_RE = re.compile(
    r"\b(?:IRDAI?|UIN)[/:]\s*[A-Z0-9./\-]{6,}",
    flags=re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta(chunk: Any) -> dict:
    """Pull metadata off a chunk regardless of whether the caller hands us a
    RetrievedChunk dataclass or a raw dict. RetrievedChunk stores fields as
    top-level attributes; ingestion-side code sometimes passes a dict."""
    if isinstance(chunk, dict):
        return chunk
    out = {}
    for k in (
        "policy_id", "policy_name", "insurer_slug", "doc_type",
        "chunk_idx", "chunk_offset", "min_entry_age", "max_entry_age",
        "score", "text",
    ):
        if hasattr(chunk, k):
            out[k] = getattr(chunk, k)
    return out


def _meta_full(chunk: Any) -> dict:
    """Like `_meta` but also surfaces the KI-278 enriched structured-fact
    keys (policy_type_indemnity_or_fixed / deductible_amount /
    co_payment_pct / sum_insured_options / _grade / _overall_score).

    `brain_tools.retrieve_policies` passes plain dicts, so the common path
    is just `return chunk`. For dataclass chunks we duck-type the union of
    citation + enriched fields so the eligibility/ranking rules still work
    if a future caller hands us a dataclass.
    """
    if isinstance(chunk, dict):
        return chunk
    out = _meta(chunk)
    for k in (
        "policy_type_indemnity_or_fixed", "deductible_amount",
        "co_payment_pct", "sum_insured_options", "_grade", "_overall_score",
    ):
        if hasattr(chunk, k):
            out[k] = getattr(chunk, k)
    return out


def _profile_get(profile: Any, key: str, default=None):
    """Profile may be a dataclass (needs_finder.Profile) OR a dict — accept either."""
    if profile is None:
        return default
    if isinstance(profile, dict):
        return profile.get(key, default)
    return getattr(profile, key, default)


def _profile_has_female_adult(profile: Any) -> bool:
    """Heuristic: profile covers a female adult if dependents includes spouse
    (the user might be the female adult, or spouse may be). We treat
    'spouse' tokens as a positive signal and 'self+spouse', 'family' shapes
    likewise. Conservative: return True when uncertain (better to keep
    maternity chunks than to wrongly drop them)."""
    dep = _profile_get(profile, "dependents") or ""
    if not isinstance(dep, str):
        return True
    dep_lower = dep.lower()
    if any(tok in dep_lower for tok in ("spouse", "wife", "family", "kids", "child")):
        return True
    # No spouse / no family signal AND explicit self-only → no female adult
    if dep_lower in ("self", "self_only", "individual", "just_me"):
        return False
    return True  # default permissive


def _profile_has_maternity_goal(profile: Any) -> bool:
    goal = _profile_get(profile, "primary_goal") or ""
    if not isinstance(goal, str):
        return False
    return "maternity" in goal.lower() or "pregnan" in goal.lower() or "baby" in goal.lower()


# ---------------------------------------------------------------------------
# (1) Profile-fit pre-filter (A3 / A6)
# ---------------------------------------------------------------------------

def apply_profile_filter(chunks: Iterable[Any], profile: Any) -> list[Any]:
    """Drop chunks that are demographically inappropriate for this user.

    Rules:
      - min_entry_age > age + 2          → drop
      - max_entry_age < age - 2          → drop
      - senior-only plan AND age < 50    → drop (user is too young)
      - adult-only plan AND age >= 60    → drop (user needs senior variant)
      - maternity plan AND no female adult / no maternity goal → drop

    Profile chunks (doc_type == 'profile') and regulatory chunks
    (doc_type == 'regulatory' / 'review') are NEVER dropped here — those
    aren't policies and the demographic rules don't apply.

    Conservative on missing data: if a chunk doesn't expose min/max entry
    age metadata, we DO NOT drop it on those rules (we still apply the
    name-based senior/adult/maternity rules where the name pattern matches).
    """
    chunks_list = list(chunks)
    if not chunks_list:
        return chunks_list

    age = _profile_get(profile, "age")
    # If age unknown, only the maternity rule can fire — keep everything else.
    has_age = isinstance(age, int)

    has_female_adult = _profile_has_female_adult(profile)
    maternity_goal = _profile_has_maternity_goal(profile)

    kept: list[Any] = []
    for ch in chunks_list:
        m = _meta(ch)
        doc_type = (m.get("doc_type") or "").lower()
        # Never drop non-policy chunks via demographic filter
        if doc_type in ("profile", "regulatory", "review"):
            kept.append(ch)
            continue

        name = (m.get("policy_name") or "").strip()
        min_age = m.get("min_entry_age")
        max_age = m.get("max_entry_age")

        # Numeric age-range gate
        if has_age:
            try:
                if isinstance(min_age, (int, float)) and min_age > age + PROFILE_AGE_TOLERANCE:
                    continue
                if isinstance(max_age, (int, float)) and max_age < age - PROFILE_AGE_TOLERANCE:
                    continue
            except TypeError:
                pass  # metadata corruption — fall through to name rules

        # Senior-only inferred from name OR from min_entry_age >= 60
        is_senior_only = bool(SENIOR_ONLY_NAME_RE.search(name)) or (
            isinstance(min_age, (int, float)) and min_age >= 60
        )
        if is_senior_only and has_age and age < 50:
            continue

        # Adult-only inferred from name (no metadata signal exists for this).
        # If user is 60+ AND policy looks adult-only AND has max_age < 60, drop.
        is_adult_only_name = bool(ADULT_ONLY_NAME_RE.search(name))
        if has_age and age >= 60 and is_adult_only_name:
            continue
        if has_age and age >= 60 and isinstance(max_age, (int, float)) and max_age < 60:
            continue

        # Maternity gate — only drop if BOTH conditions fail
        if MATERNITY_NAME_RE.search(name) and not maternity_goal and not has_female_adult:
            continue

        kept.append(ch)

    return kept


# ---------------------------------------------------------------------------
# (1b) Eligibility filter (KI-278) — structural mismatch hard-drop
# ---------------------------------------------------------------------------

def _as_int(v: Any) -> Optional[int]:
    """Best-effort int coercion (bool excluded — it's an int subclass)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _max_sum_insured(chunk_meta: dict) -> Optional[int]:
    """Largest SI the policy can offer, from the enriched
    `sum_insured_options` list. None when the fact is absent (don't drop on
    missing data)."""
    opts = chunk_meta.get("sum_insured_options")
    if not opts:
        return None
    nums: list[int] = []
    if isinstance(opts, (list, tuple)):
        for o in opts:
            n = _as_int(o)
            if n is not None:
                nums.append(n)
    else:
        n = _as_int(opts)
        if n is not None:
            nums.append(n)
    return max(nums) if nums else None


def _is_top_up(chunk_meta: dict) -> bool:
    """A plan is a top-up / super-top-up when ANY structural signal fires:
      1. policy_type_indemnity_or_fixed says top_up / super
      2. the policy name matches TOP_UP_NAME_RE
      3. it carries a large aggregate deductible (>= TOP_UP_DEDUCTIBLE_FLOOR)
    """
    ptype = str(chunk_meta.get("policy_type_indemnity_or_fixed") or "").lower()
    if "top" in ptype or "super_top" in ptype or "super-top" in ptype:
        return True
    name = str(chunk_meta.get("policy_name") or "")
    if TOP_UP_NAME_RE.search(name):
        return True
    ded = _as_int(chunk_meta.get("deductible_amount"))
    if ded is not None and ded >= TOP_UP_DEDUCTIBLE_FLOOR_INR:
        return True
    return False


def _user_has_base_cover(profile: Any) -> bool:
    """True when the user already holds base health cover. A first-time
    buyer (existing_cover_inr == 0 / None / falsy, OR primary_goal looks
    like a first purchase) has NO base cover, so a top-up is unusable."""
    existing = _as_int(_profile_get(profile, "existing_cover_inr"))
    if existing and existing > 0:
        return True
    goal = str(_profile_get(profile, "primary_goal") or "").lower()
    # "first_buy" / "first policy" / "first-time" → definitively no base.
    if any(tok in goal for tok in ("first", "new policy", "fresh")):
        return False
    # No explicit existing cover and no first-buy signal → treat as no base
    # cover (conservative: a top-up only helps someone who KNOWS they have a
    # base; absent that signal, don't surface an unusable product).
    return False


def _user_wants_zero_copay(profile: Any) -> bool:
    copay = _as_int(_profile_get(profile, "copay_pct"))
    return copay is not None and copay <= ZERO_COPAY_USER_MAX_COPAY_PCT


def apply_eligibility_filter(chunks: Iterable[Any], profile: Any) -> list[Any]:
    """Drop chunks the user is STRUCTURALLY ineligible for, or that plainly
    contradict an explicit stated need (KI-278).

    Hard-drop rules (policy chunks only — profile/regulatory/review chunks
    are never policies and pass through untouched):

      1. TOP-UP GATE — a top-up / super-top-up plan is dropped when the user
         has no existing base cover (first-time buyer). A top-up only pays
         above a base policy / large deductible the user does not have.

      2. SI FLOOR — when the user stated a desired sum insured, drop any
         plan whose largest SI option is below that requirement (it cannot
         deliver the cover the user said they need).

      3. ZERO-COPAY GATE — when the user explicitly wants zero co-pay, drop
         any plan with a mandatory co-payment above
         ZERO_COPAY_USER_MAX_COPAY_PCT. Regardless of stated preference,
         drop plans with a punitive co-pay (>= PUNITIVE_COPAY_PCT).

    Conservative on missing data: a rule only fires when BOTH the profile
    signal AND the policy fact are present. A chunk with no enriched facts
    is never dropped here (it just won't get a fit boost in ranking).
    """
    chunks_list = list(chunks)
    if not chunks_list:
        return chunks_list

    has_base = _user_has_base_cover(profile)
    wants_zero_copay = _user_wants_zero_copay(profile)
    desired_si = _as_int(_profile_get(profile, "desired_sum_insured_inr"))

    kept: list[Any] = []
    for ch in chunks_list:
        m = _meta_full(ch)
        doc_type = (m.get("doc_type") or "").lower()
        if doc_type in ("profile", "regulatory", "review"):
            kept.append(ch)
            continue

        # Rule 1 — top-up unusable without a base policy
        if not has_base and _is_top_up(m):
            continue

        # Rule 2 — SI floor
        if desired_si:
            max_si = _max_sum_insured(m)
            if max_si is not None and max_si < desired_si:
                continue

        # Rule 3 — co-pay gate
        copay = _as_int(m.get("co_payment_pct"))
        if copay is not None:
            if copay >= PUNITIVE_COPAY_PCT:
                continue
            if wants_zero_copay and copay > ZERO_COPAY_USER_MAX_COPAY_PCT:
                continue

        kept.append(ch)

    return kept


# ---------------------------------------------------------------------------
# (1c) Profile-fit ranking (KI-278) — re-order by stated-need match
# ---------------------------------------------------------------------------

_GRADE_POINTS = {"A": 100.0, "B": 70.0, "C": 45.0, "D": 25.0, "F": 5.0}


def _fit_score(chunk_meta: dict, profile: Any, wants_zero_copay: bool,
                desired_si: Optional[int]) -> float:
    """Composite profile-fit score (higher = better). Blends raw cosine
    (so genuinely-relevant chunks still matter) with structural fit:
      • scorecard grade / overall (when enriched)
      • co-pay penalty (steep when the user wants zero co-pay)
      • SI headroom (a plan that comfortably offers the requested SI beats
        one that barely scrapes it)
    """
    try:
        cosine = float(chunk_meta.get("score") or 0.0)
    except (TypeError, ValueError):
        cosine = 0.0
    # Cosine is ~[0,1]; scale so it contributes but never dominates fit.
    score = cosine * 30.0

    # Grade / overall scorecard signal (enriched by brain_tools).
    grade = str(chunk_meta.get("_grade") or "").strip().upper()
    overall = _as_int(chunk_meta.get("_overall_score"))
    if overall is not None:
        score += float(overall)            # 0-100 scorecard points
    elif grade in _GRADE_POINTS:
        score += _GRADE_POINTS[grade]

    # Co-pay penalty.
    copay = _as_int(chunk_meta.get("co_payment_pct"))
    if copay is not None and copay > 0:
        penalty = copay * (3.0 if wants_zero_copay else 1.0)
        score -= penalty

    # SI headroom bonus — reward a plan that offers >= the requested SI.
    if desired_si:
        max_si = _max_sum_insured(chunk_meta)
        if max_si is not None:
            if max_si >= desired_si:
                score += 15.0
            else:
                score -= 25.0  # shouldn't survive eligibility, belt-and-braces

    return score


def rank_by_profile_fit(chunks: Iterable[Any], profile: Any) -> list[Any]:
    """Stable-sort chunks by composite profile-fit (descending). Non-policy
    chunks (profile/regulatory/review) keep their relative position at the
    front so grounding context isn't reordered away.

    Stable: ties preserve the incoming (cosine / dedup) order, so this only
    *promotes* better-fit plans — it never randomly shuffles equals.
    """
    chunks_list = list(chunks)
    if len(chunks_list) <= 1:
        return chunks_list

    wants_zero_copay = _user_wants_zero_copay(profile)
    desired_si = _as_int(_profile_get(profile, "desired_sum_insured_inr"))

    non_policy: list[Any] = []
    policy: list[Any] = []
    for ch in chunks_list:
        dt = (_meta_full(ch).get("doc_type") or "").lower()
        (non_policy if dt in ("profile", "regulatory", "review") else policy).append(ch)

    # Decorate-sort-undecorate with original index as the stable tiebreaker.
    decorated = [
        (-_fit_score(_meta_full(ch), profile, wants_zero_copay, desired_si), i, ch)
        for i, ch in enumerate(policy)
    ]
    decorated.sort(key=lambda t: (t[0], t[1]))
    ranked_policy = [ch for _, _, ch in decorated]
    return non_policy + ranked_policy


# ---------------------------------------------------------------------------
# (2) Hybrid retrieval — exact-match bypass on UIN or policy name
# ---------------------------------------------------------------------------

def _extract_uin(query: str) -> Optional[str]:
    if not query:
        return None
    m = UIN_RE.search(query)
    return m.group(0) if m else None


def _extract_quoted_policy_name(query: str) -> Optional[str]:
    """Pull a quoted policy name out of the query if present."""
    if not query:
        return None
    # "..." or "..." or '...'
    for pat in (r'"([^"]{6,})"', r"“([^”]{6,})”", r"'([^']{6,})'"):
        m = re.search(pat, query)
        if m:
            return m.group(1).strip()
    return None


def bypass_cosine_for_exact_match(
    chunks: Iterable[Any],
    query: str,
) -> Optional[list[Any]]:
    """If the query contains an exact UIN or an exact (quoted) policy name,
    return a substring-matched subset of `chunks`. This is the lexical
    "BM25-style" fallback — when the user clearly knows what they want, we
    should not let cosine similarity reorder away from their literal target.

    Returns None when no exact-match signal is present (caller falls back
    to normal cosine results). Returns an empty list if a signal was present
    but no chunk matched — that's a useful "we know what you mean, but our
    catalog doesn't have it" signal for the orchestrator's empty-retrieval
    guard.
    """
    chunks_list = list(chunks)
    uin = _extract_uin(query)
    quoted = _extract_quoted_policy_name(query)
    if not uin and not quoted:
        return None

    needles: list[str] = []
    if uin:
        needles.append(uin.lower())
    if quoted:
        needles.append(quoted.lower())

    matched: list[Any] = []
    for ch in chunks_list:
        m = _meta(ch)
        haystack = " ".join(
            str(m.get(k, "")) for k in ("policy_id", "policy_name", "insurer_slug", "text")
        ).lower()
        if any(n in haystack for n in needles):
            matched.append(ch)

    return matched


# ---------------------------------------------------------------------------
# (3) Empty-retrieval guard
# ---------------------------------------------------------------------------

def empty_retrieval_guard(
    chunks: Iterable[Any],
    intent: Optional[str] = None,
    min_chunks: int = MIN_CHUNKS_FOR_RECOMMENDATION,
) -> Optional[dict]:
    """Return a structured signal if a recommendation intent has too few
    chunks to ground an answer. The orchestrator should surface this to the
    user as a clarifier question instead of calling the brain.

    Returns None when the retrieval is healthy (or the intent doesn't need
    much grounding).
    """
    chunks_list = list(chunks)
    intent_norm = (intent or "").lower().strip()
    if intent_norm and intent_norm not in _RECOMMENDATION_INTENTS:
        return None  # FAQ / regulatory / smalltalk intents are fine with sparse retrieval

    if len(chunks_list) >= min_chunks:
        return None

    return {
        "reason": "empty_retrieval",
        "fallback": "Ask 1 more clarifier",
        "chunk_count": len(chunks_list),
        "min_required": min_chunks,
    }


# ---------------------------------------------------------------------------
# (4) Citation grounding — require policy_id, policy_name, chunk_offset
# ---------------------------------------------------------------------------

def enforce_citation_grounding(chunks: Iterable[Any]) -> list[Any]:
    """Drop chunks missing citation-critical fields.

    A citable chunk MUST expose:
      - policy_id   (non-empty str)
      - policy_name (non-empty str)

    The chunk offset field (`chunk_offset` or legacy `chunk_idx`) is
    INFORMATIONAL only — it is not required for citation grounding because
    upstream call sites (e.g. brain_tools.retrieve_policies) build pruned
    dicts that intentionally omit it, and the brain cites by policy
    identity, not by chunk offset. Z2 live test (2026-05-15) showed 15/15
    retrieve_policies calls returning 0 chunks because we required an
    offset that the upstream builder never included → every chunk dropped
    here even though raw retrieval was healthy.
    """
    kept: list[Any] = []
    for ch in chunks:
        m = _meta(ch)
        pid = m.get("policy_id")
        pname = m.get("policy_name")
        if not pid or not isinstance(pid, str):
            continue
        if not pname or not isinstance(pname, str):
            continue
        kept.append(ch)
    return kept


# ---------------------------------------------------------------------------
# (5) Dedup by policy_id — keep highest-score chunk per policy
# ---------------------------------------------------------------------------

def dedup_by_policy(chunks: Iterable[Any]) -> list[Any]:
    """Within top-K results, collapse to one chunk per policy_id, keeping
    the highest-scoring chunk. Preserves the original ordering of the kept
    chunks (i.e. ranks chunks by their best-in-policy score)."""
    best: dict[str, Any] = {}
    order: list[str] = []
    for ch in chunks:
        m = _meta(ch)
        pid = m.get("policy_id") or ""
        score = m.get("score")
        if score is None:
            # try attribute path
            score = getattr(ch, "score", 0.0)
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 0.0

        if pid not in best:
            best[pid] = (ch, score_f)
            order.append(pid)
            continue
        prev_chunk, prev_score = best[pid]
        if score_f > prev_score:
            best[pid] = (ch, score_f)

    return [best[pid][0] for pid in order]


# ---------------------------------------------------------------------------
# Convenience — compose the standard retrieval-side pipeline.
# ---------------------------------------------------------------------------

def filter_pipeline(
    chunks: Iterable[Any],
    profile: Any = None,
    query: str = "",
    intent: Optional[str] = None,
) -> tuple[list[Any], Optional[dict]]:
    """Run the standard A3/A6 + KI-278 retrieval-side pipeline:

        1. Citation grounding    (reject malformed chunks)
        2. Profile pre-filter    (drop demographically inappropriate policies)
        3. Eligibility filter     (KI-278 — drop structural mismatches:
                                   top-up w/o base cover, SI floor, co-pay)
        4. Exact-match bypass     (if query has a UIN / quoted name, swap in)
        5. Dedup by policy_id
        6. Profile-fit ranking    (KI-278 — promote best-fit over raw cosine)
        7. Empty-retrieval guard

    Returns (filtered_chunks, guard_signal_or_None). When guard_signal is
    set, the caller should NOT pass `filtered_chunks` to the brain — they
    should surface the clarifier instead.

    KI-278 ordering note: the eligibility filter runs on the demographically-
    fitted set and BEFORE the empty-retrieval guard, so a profile whose only
    matches are ineligible (e.g. a first-time buyer where cosine only found
    top-ups) correctly trips the clarifier guard instead of recommending an
    unusable product. The exact-match bypass still wins when the user names
    a specific policy (they may legitimately want to read about a top-up).
    """
    grounded = enforce_citation_grounding(chunks)
    fitted = apply_profile_filter(grounded, profile)
    eligible = apply_eligibility_filter(fitted, profile)

    exact = bypass_cosine_for_exact_match(grounded, query)
    if exact is not None and exact:
        # Exact match wins over cosine when the user clearly named a target.
        # The user explicitly asked about THIS policy — show it even if it
        # would fail the eligibility/fit gates (they may be researching a
        # top-up or a high-copay plan on purpose). Demographic filter still
        # applies (a 25yo asking about a senior plan still gets steered).
        eligible = apply_profile_filter(exact, profile)

    deduped = dedup_by_policy(eligible)
    ranked = rank_by_profile_fit(deduped, profile)
    guard = empty_retrieval_guard(ranked, intent=intent)
    return ranked, guard
