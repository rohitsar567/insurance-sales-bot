"""Retrieval-side filters + guards (A3 / A6 audit fixes).

This module is a SIDECAR to the retrieval path (`rag/retrieve.py` and the
single-brain tool layer in `backend/brain_tools.py`). It is deliberately a
separate file so retrieval-correctness work stays isolated from the
turn-handling code.

Public API:
    apply_profile_filter(chunks, profile)       -> list[RetrievedChunk]
        Drop chunks for policies the user is demographically ineligible for.
    apply_eligibility_filter(chunks, profile)   -> list[RetrievedChunk]
        Drop chunks the user is STRUCTURALLY ineligible for / that clearly
        contradict an explicit stated need (KI-278 / KI-279):
          - top-up / super-top-up when the user has no existing base cover
          - plans whose max sum-insured cannot meet the requested SI
          - high co-pay plans when the user explicitly wants zero co-pay
          - fixed-benefit products (hospital daily cash / personal accident /
            critical illness / cancer) when the profile clearly signals the
            user wants comprehensive INDEMNITY cover (KI-279)
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

All functions are pure / side-effect-free; safe to call from the
single-brain tool layer or from rag/retrieve.py without import-cycle risk.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# Shared canonical-identity / dedup rule. The same
# UIN-primary + product_key invariant the marketplace endpoint uses
# (main.py /api/policies/all). Imported (not reimplemented) so the
# recommender and the marketplace agree on "the same policy". The module
# is dependency-free so this stays import-cycle-safe.
from backend.policy_identity import canonical_key

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
# Eligibility / profile-fit tunables
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
# Fixed-benefit exclusion for comprehensive-indemnity intent
# ---------------------------------------------------------------------------
# A FIXED-BENEFIT product (hospital daily cash, personal accident, critical
# illness, cancer / other defined-benefit) pays a fixed lump-sum / per-day
# amount rather than reimbursing actual hospitalisation expenses. It is a
# legitimate SUPPLEMENT but a wrong PRIMARY recommendation for a buyer who
# clearly wants comprehensive INDEMNITY cover (their first health policy,
# a desired sum insured, no existing base cover). This regex mirrors
# scorecard._FIXED_BENEFIT_RE so classification stays consistent across the
# scorecard and the retrieval filter (single behavioural contract).
FIXED_BENEFIT_NAME_RE = re.compile(
    r"hospital[\s_-]*cash|hospi[\s_-]*cash|daily[\s_-]*cash|"
    r"personal[\s_-]*accident|critical[\s_-]*illness|"
    r"criti[\s_-]*(?:care|medicare)|\bcancer\b|wellsurance|hospi[\s_-]*care",
    flags=re.IGNORECASE,
)

# Tokens in policy_type / policy_type_indemnity_or_fixed that mark a
# fixed-benefit / defined-benefit product (NOT indemnity reimbursement).
_FIXED_BENEFIT_TYPE_TOKENS = (
    "fixed", "benefit", "defined",
    "hospital_cash", "hospital cash", "daily_cash", "daily cash",
    "personal_accident", "personal accident",
    "critical_illness", "critical illness", "cancer",
)

# Goal phrases that mean the user EXPLICITLY wants a supplement / add-on /
# top-up / fixed-benefit product — the comprehensive-indemnity intent must
# NOT fire for these (we must keep showing them PA / CI / hospital-cash /
# top-up products on purpose).
_SUPPLEMENT_GOAL_TOKENS = (
    "supplement", "add-on", "add on", "addon", "rider",
    "top-up", "top up", "topup", "super top",
    "critical illness", "critical-illness", "ci only", "ci cover",
    "personal accident", "accident cover", "accident plan",
    "hospital cash", "daily cash", "cash plan", "cancer cover",
    "cancer plan", "defined benefit", "fixed benefit",
    "alongside", "in addition to", "on top of",
)

# Goal phrases that POSITIVELY signal a primary comprehensive health plan.
_COMPREHENSIVE_GOAL_TOKENS = (
    "first", "new policy", "fresh", "primary", "comprehensive",
    "general health", "health cover", "main cover", "base cover",
    "family floater", "indemnity", "hospitalisation", "hospitalization",
    "cover my", "protect my family", "medical cover", "buy health",
)

# ---------------------------------------------------------------------------
# Unified recommendation-fit gate tunables
# ---------------------------------------------------------------------------
# Recommendation-fit is SYSTEMIC: the cited-card list and the advisory prose
# are gated by the SAME fitness logic. Two hard gates (entry-age for the
# INSURED person; required feature = maternity/newborn) plus ranking signals
# for grade/rank ordering and the cost-objective lead.

# Entry-age grace. The catalog's max_entry_age is the regulator-filed entry
# age; underwriters allow a small grace band (mirrors PROFILE_AGE_TOLERANCE).
ENTRY_AGE_TOLERANCE = 2

# Goal phrases that mean COST is the dominant objective (P5). When present,
# ranking must not let a pricier plan win purely on cosine — the lowest-cost
# *appropriate* plan leads. We proxy "cost" by (low/zero co-pay surcharge +
# scorecard Cost Predictability already folded into _overall_score); the
# explicit signal here flips a stronger cosine-discount so a cheap plan is
# not buried under a higher-cosine pricier one.
_COST_OBJECTIVE_TOKENS = (
    "cost_optimize", "cheapest", "cheap", "lowest premium", "low premium",
    "budget", "affordable", "tight", "money is tight", "save money",
    "least expensive", "minimum premium", "low cost",
)

# Profile/goal phrases that say the policy INSURES senior parents (so the
# entry-age gate must use the PARENTS' age, not the paying child's age).
_PARENTS_COVER_TOKENS = (
    "parent", "parents", "mother", "father", "mom", "dad",
    "senior citizen", "senior citizens", "elderly",
)

# Explicit maternity / newborn requirement phrases. When the profile states
# this need, plans whose facts CONFIRM maternity/newborn rank above those
# that do not (and unverified ones are ranked strictly below confirmed).
_MATERNITY_NEED_TOKENS = (
    "maternity", "pregnan", "newborn", "new born", "new-born",
    "delivery cover", "childbirth", "planning a child", "planning another",
    "expecting", "baby cover", "having a baby",
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
        # KI-280 — unified-gate signals (entry-age / required-feature /
        # canonical dedup identity).
        "uin_code", "max_entry_age", "min_entry_age",
        "maternity_coverage", "newborn_coverage", "policy_type",
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

    # The demographic gates (numeric age-range, senior-only, adult-only)
    # reason about the age of the person the policy actually INSURES, not
    # the payer's. When the policy covers parents (~70), using the paying
    # child's age (e.g. 36) would wrongly DROP a senior-citizen plan
    # ("min_entry_age>=60 AND payer<50") and wrongly KEEP a max-entry-65
    # plan. `_oldest_insured_age` returns the eldest parent's age when
    # insuring parents, else the profile's own age. Falls back to the raw
    # profile age if no resolved signal.
    _resolved_age = _oldest_insured_age(profile)
    if _resolved_age is None:
        _resolved_age = _profile_get(profile, "age")
    age = _resolved_age
    # If age unknown, only the maternity rule can fire — keep everything else.
    has_age = isinstance(age, int)

    has_female_adult = _profile_has_female_adult(profile)
    maternity_goal = _profile_has_maternity_goal(profile)

    kept: list[Any] = []
    for ch in chunks_list:
        m = _meta(ch)
        doc_type = (m.get("doc_type") or "").lower()
        # Never drop non-policy chunks via demographic filter.
        # #52 — `user_upload` is a globally-visible uploaded marketplace
        # doc: a Q&A TARGET, not a demographically-ranked recommendable
        # corpus policy. Exempt it exactly like regulatory/review so a
        # question literally about the uploaded document isn't dropped
        # because the (often anonymous) asker's age/eligibility doesn't
        # match the uploaded plan.
        if doc_type in ("profile", "regulatory", "review", "user_upload"):
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


# ---------------------------------------------------------------------------
# KI-279 — fixed-benefit classification + comprehensive-indemnity intent
# ---------------------------------------------------------------------------

def _is_fixed_benefit_chunk(chunk_meta: dict) -> bool:
    """True when the policy is a FIXED-BENEFIT / defined-benefit product
    (hospital daily cash, personal accident, critical illness, cancer)
    rather than an indemnity hospitalisation-reimbursement plan.

    Detection mirrors scorecard._is_fixed_benefit so the scorecard and the
    retrieval filter classify identically (one behavioural contract):

      1. The canonical type key `policy_type_indemnity_or_fixed` contains a
         fixed / benefit / defined token.
      2. Fallback — the raw catalog key `policy_type` contains a
         fixed-benefit token (hospital_cash / personal_accident / ...).
         Critically, several curated files (e.g. Star Hospital Cash) carry
         the type ONLY here.
      3. Last-resort — the policy_id / policy_name matches
         FIXED_BENEFIT_NAME_RE (hospital cash / daily cash / PA / CI /
         cancer / wellsurance).

    Conservative: if NO type signal exists AND the name doesn't match, the
    policy is treated as indemnity (not dropped).
    """
    # Canonical curated key first.
    canon = str(chunk_meta.get("policy_type_indemnity_or_fixed") or "").lower()
    if canon and any(tok in canon for tok in _FIXED_BENEFIT_TYPE_TOKENS):
        return True
    if canon and "indemnity" in canon:
        return False  # explicitly indemnity — never a false positive

    # Raw catalog type key (the Star-Hospital-Cash case).
    raw = str(chunk_meta.get("policy_type") or "").lower()
    if raw and any(tok in raw for tok in _FIXED_BENEFIT_TYPE_TOKENS):
        return True
    if raw and "indemnity" in raw:
        return False

    # Name / id fallback.
    blob = f"{chunk_meta.get('policy_id','')} {chunk_meta.get('policy_name','')}"
    return bool(FIXED_BENEFIT_NAME_RE.search(blob))


def _user_explicitly_wants_supplement(profile: Any) -> bool:
    """True only when primary_goal explicitly names a supplement / add-on /
    top-up / PA / CI / hospital-cash / cancer / defined-benefit product.
    Used to SUPPRESS the fixed-benefit ranking demotion (we must not bury a
    product the user explicitly asked for)."""
    goal = str(_profile_get(profile, "primary_goal") or "").lower().strip()
    if not goal:
        return False
    return any(tok in goal for tok in _SUPPLEMENT_GOAL_TOKENS)


def _insures_parents(profile: Any) -> bool:
    """True when the policy is being bought to cover the user's PARENTS /
    seniors (so the entry-age gate must test the parents' age, not the
    paying child's). Signals (any): parents_to_insure flag, a
    parents_age_max value, dependents mentioning parents, or a
    parents-cover / senior-citizen primary_goal."""
    if _profile_get(profile, "parents_to_insure") is True:
        return True
    if _as_int(_profile_get(profile, "parents_age_max")) is not None:
        return True
    dep = str(_profile_get(profile, "dependents") or "").lower()
    goal = str(_profile_get(profile, "primary_goal") or "").lower()
    blob = f"{dep} {goal}"
    return any(tok in blob for tok in _PARENTS_COVER_TOKENS)


def _oldest_insured_age(profile: Any) -> Optional[int]:
    """The age of the OLDEST person the policy must accept at entry.

    When the policy insures parents, that is `parents_age_max` (the eldest
    parent). Otherwise it is the profile's own `age`. Returns None when no
    usable age signal exists (the gate then stays conservative and does not
    drop on missing data — same philosophy as apply_profile_filter).
    """
    if _insures_parents(profile):
        pa = _as_int(_profile_get(profile, "parents_age_max"))
        if pa is not None:
            return pa
        # Insures parents but their age unknown — fall through to own age
        # only if it is itself senior; otherwise None (don't gate blind).
        own = _as_int(_profile_get(profile, "age"))
        return own if (own is not None and own >= 60) else None
    return _as_int(_profile_get(profile, "age"))


def _profile_requires_maternity(profile: Any) -> bool:
    """True when the profile EXPLICITLY needs maternity / newborn cover.
    Checks primary_goal first (the strongest signal — single_brain folds
    'maternity' into the goal), then a dedicated maternity flag if present.
    Conservative: only fires on an explicit token, never inferred from a
    female adult alone (that would over-trigger and wrongly demote good
    plans for users who never asked for maternity)."""
    if _profile_has_maternity_goal(profile):
        return True
    goal = str(_profile_get(profile, "primary_goal") or "").lower()
    if any(tok in goal for tok in _MATERNITY_NEED_TOKENS):
        return True
    # A dedicated boolean slot, if a future Profile grows one.
    for key in ("needs_maternity", "maternity_required", "wants_maternity"):
        if _profile_get(profile, key) is True:
            return True
    return False


def _cost_is_primary_objective(profile: Any) -> bool:
    """True when the user's stated objective is dominated by cost (P5 —
    'cheapest decent cover, lowest premium is my top priority')."""
    goal = str(_profile_get(profile, "primary_goal") or "").lower()
    band = str(_profile_get(profile, "budget_band") or "").lower()
    blob = f"{goal} {band}"
    return any(tok in blob for tok in _COST_OBJECTIVE_TOKENS)


def _chunk_confirms_maternity(chunk_meta: dict) -> Optional[bool]:
    """Tri-state: True (facts confirm maternity OR newborn cover), False
    (facts explicitly say neither), None (unverified — no fact present).
    None must rank BELOW True but is NOT hard-dropped (the curated facts
    are incomplete; a hard drop would wrongly hide good plans whose
    maternity flag simply wasn't curated yet)."""
    mat = chunk_meta.get("maternity_coverage")
    nb = chunk_meta.get("newborn_coverage")

    def _tri(v):
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "yes", "y", "covered"):
                return True
            if s in ("false", "no", "n", "not covered", "excluded"):
                return False
        if isinstance(v, dict) and "covered" in v:
            return bool(v.get("covered"))
        return None

    tm, tn = _tri(mat), _tri(nb)
    if tm is True or tn is True:
        return True
    if tm is False or tn is False:
        return False
    return None


def _wants_comprehensive_indemnity(profile: Any) -> bool:
    """Conservatively detect that the user wants a PRIMARY COMPREHENSIVE
    INDEMNITY health plan (so a fixed-benefit daily-cash / PA / CI product
    is the wrong top pick).

    Fires ONLY when the signal is clear and NO explicit supplement signal
    is present:

      • NOT an explicit supplement / add-on / top-up / PA / CI / cancer /
        hospital-cash goal  (hard veto — these users WANT fixed-benefit).
      • The user has NO existing base cover (first-time buyer).
      • AND at least one strong positive signal:
          - primary_goal reads as a first / primary / comprehensive
            health-cover goal, OR
          - a desired_sum_insured_inr is present (you only state a target
            cover amount when you want a real indemnity plan).

    Returns False on ambiguity (better to keep a fixed-benefit chunk and
    let ranking demote it than to wrongly hard-drop for an unclear case).
    """
    goal = str(_profile_get(profile, "primary_goal") or "").lower().strip()

    # Hard veto — the user explicitly asked for a supplement / fixed-benefit
    # style product. Never hide those from them.
    if goal and any(tok in goal for tok in _SUPPLEMENT_GOAL_TOKENS):
        return False

    # A user who already holds base cover is plausibly shopping for a
    # supplement; don't aggressively hard-drop fixed-benefit for them.
    if _user_has_base_cover(profile):
        return False

    desired_si = _as_int(_profile_get(profile, "desired_sum_insured_inr"))
    has_desired_si = desired_si is not None and desired_si > 0

    goal_is_comprehensive = bool(goal) and any(
        tok in goal for tok in _COMPREHENSIVE_GOAL_TOKENS
    )

    # Clear signal = (first/primary/comprehensive goal) OR (a desired SI was
    # stated). Either alone, combined with "no base cover" and "no
    # supplement veto", is a confident comprehensive-indemnity intent.
    return goal_is_comprehensive or has_desired_si


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

      4. FIXED-BENEFIT GATE (KI-279) — when the profile clearly signals the
         user wants a PRIMARY COMPREHENSIVE INDEMNITY health plan (first
         policy / general-cover goal + a desired SI + no existing base
         cover, and NO explicit supplement / PA / CI / hospital-cash /
         top-up goal), drop fixed-benefit products (hospital daily cash,
         personal accident, critical illness, cancer / defined-benefit).
         These pay a fixed per-day / lump-sum amount, not actual medical
         expenses, so they are a wrong PRIMARY recommendation. They are
         NEVER dropped for a user who explicitly wants a supplement.

      5. ENTRY-AGE GATE — drop a plan whose max entry age cannot accept
         the OLDEST person the policy must insure. When the profile covers
         parents (~70), the gate uses the parents' age, not the paying
         child's (the demographic pre-filter only sees the payer's age).
         Conservative: fires only when both the insured age AND the policy
         fact are present.

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
    wants_comprehensive = _wants_comprehensive_indemnity(profile)
    # KI-280 — the age of the OLDEST person the plan must accept at entry
    # (parents' age when insuring parents, else the user's own age).
    oldest_age = _oldest_insured_age(profile)

    kept: list[Any] = []
    for ch in chunks_list:
        m = _meta_full(ch)
        doc_type = (m.get("doc_type") or "").lower()
        # #52 — uploaded marketplace docs are Q&A targets; never hard-drop
        # them on eligibility (same class as regulatory/review).
        if doc_type in ("profile", "regulatory", "review", "user_upload"):
            kept.append(ch)
            continue

        # Rule 1 — top-up unusable without a base policy
        if not has_base and _is_top_up(m):
            continue

        # Rule 4 (KI-279) — fixed-benefit is a wrong PRIMARY pick when the
        # user clearly wants comprehensive indemnity cover. Hard-drop only
        # for that intent; supplement/PA/CI seekers keep seeing them.
        if wants_comprehensive and _is_fixed_benefit_chunk(m):
            continue

        # Rule 5 — HARD ELIGIBILITY: a plan whose max entry age cannot
        # accept the oldest insured person is structurally unusable (e.g. a
        # 70yo parent cannot be enrolled in a plan whose max_entry_age is
        # 65). Fires only when BOTH the age and the policy fact are present
        # (conservative on missing data) and only when the profile actually
        # insures someone whose age we know (the demographic
        # apply_profile_filter uses the *payer's* age and never sees the
        # parents' age).
        if oldest_age is not None:
            maxe = _as_int(m.get("max_entry_age"))
            if maxe is not None and maxe + ENTRY_AGE_TOLERANCE < oldest_age:
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
                desired_si: Optional[int], *,
                requires_maternity: bool = False,
                cost_objective: bool = False) -> float:
    """Composite profile-fit score (higher = better). Blends raw cosine
    (so genuinely-relevant chunks still matter) with structural fit:
      • scorecard grade / overall (when enriched)
      • co-pay penalty (steep when the user wants zero co-pay)
      • SI headroom (a plan that comfortably offers the requested SI beats
        one that barely scrapes it)
      • KI-280 required-feature term: when the profile explicitly needs
        maternity/newborn, a plan whose facts CONFIRM it ranks above one
        that does not, and an UNVERIFIED plan ranks strictly below a
        confirmed one (but is not hard-dropped — curated facts are
        incomplete).
      • KI-280 cost-objective term: when cost is the dominant stated
        objective, shrink cosine's pull so a pricier plan can't win purely
        on vector similarity over a cheaper appropriate plan.
    """
    try:
        cosine = float(chunk_meta.get("score") or 0.0)
    except (TypeError, ValueError):
        cosine = 0.0
    # Cosine is ~[0,1]; scale so it contributes but never dominates fit.
    # KI-280: when COST is the primary objective (P5), damp cosine hard so
    # a higher-cosine pricier plan does not out-rank a cheaper appropriate
    # one. The scorecard's Cost Predictability is already inside
    # _overall_score; this just stops raw similarity from overriding it.
    cosine_weight = 6.0 if cost_objective else 30.0
    score = cosine * cosine_weight

    # Grade / overall scorecard signal (enriched by brain_tools).
    grade = str(chunk_meta.get("_grade") or "").strip().upper()
    overall = _as_int(chunk_meta.get("_overall_score"))
    if overall is not None:
        score += float(overall)            # 0-100 scorecard points
    elif grade in _GRADE_POINTS:
        score += _GRADE_POINTS[grade]

    # Co-pay penalty. KI-280: for a COST-primary objective where the user
    # explicitly tolerates a co-pay (P5 — "co-pay is completely fine if it
    # lowers premium"), a co-pay is a PREMIUM-REDUCING feature for this
    # buyer, not a defect — suppress the penalty so the cost-appropriate
    # plan is not wrongly demoted below a pricier zero-copay plan. The
    # zero-copay-user penalty path is untouched (P1 still drops high-copay).
    copay = _as_int(chunk_meta.get("co_payment_pct"))
    if copay is not None and copay > 0:
        if cost_objective and not wants_zero_copay:
            penalty = 0.0
        else:
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

    # BUG #30 (B1-c) — EXISTING-COVER term. When the user already holds ANY
    # base cover (even a small ₹1L employer policy), a top-up / super-top-up
    # is a directly relevant product that the profile-neutral scorecard is
    # blind to. Surface it: the bonus (+22) clears roughly one letter-grade
    # gap so a relevant top-up lands alongside the primary indemnity picks
    # (which are untouched), giving a shortlist that mixes one strong primary
    # plan with one relevant top-up. Inert when the user holds no base cover.
    existing = _as_int(_profile_get(profile, "existing_cover_inr"))
    if existing and existing > 0 and _is_top_up(chunk_meta):
        score += 22.0

    # KI-280 — REQUIRED-FEATURE term. When the profile explicitly needs
    # maternity / newborn cover (P3), a plan whose curated facts CONFIRM it
    # must outrank one that does not, and an UNVERIFIED plan (fact absent)
    # ranks strictly below a confirmed one. We do NOT hard-drop unverified
    # plans — the curated facts are incomplete and a hard drop would wrongly
    # hide good plans whose maternity flag simply wasn't curated. The
    # magnitudes are chosen so a confirmed plan clears a one-letter-grade
    # gap (≈ +25 confirmed vs −40 explicitly-absent) — enough to put a
    # maternity C-grade plan above a non-maternity B-grade plan when
    # maternity is a stated hard requirement, without disturbing ordering
    # for profiles that did not ask for maternity (term is inert then).
    if requires_maternity:
        conf = _chunk_confirms_maternity(chunk_meta)
        if conf is True:
            score += 25.0
        elif conf is False:
            score -= 40.0      # facts say NO maternity — wrong for this need
        else:
            score -= 12.0      # unverified — rank below any confirmed plan

    # KI-279 — fixed-benefit demotion. The eligibility filter already
    # hard-drops fixed-benefit for a strong comprehensive-indemnity intent;
    # this penalty is the belt-and-braces for weaker signals where the
    # intent did NOT fire (so a fixed-benefit chunk survived) but the user
    # still has no explicit supplement goal and an indemnity plan is also
    # present — the indemnity plan must still lead. Suppressed only when
    # the user explicitly wants a supplement / PA / CI / hospital-cash
    # product (then fixed-benefit is exactly what they asked for). Penalty
    # is large enough to sink it below any comparable indemnity plan
    # without affecting indemnity-vs-indemnity ordering.
    if not _user_explicitly_wants_supplement(profile) and _is_fixed_benefit_chunk(
        chunk_meta
    ):
        score -= 120.0

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
    requires_maternity = _profile_requires_maternity(profile)
    cost_objective = _cost_is_primary_objective(profile)

    non_policy: list[Any] = []
    policy: list[Any] = []
    for ch in chunks_list:
        dt = (_meta_full(ch).get("doc_type") or "").lower()
        # #52 — keep uploaded marketplace docs in the non-policy lane so
        # profile-fit re-ranking can't bury them below recommendable corpus
        # policies when the user asked about the uploaded doc itself.
        (non_policy if dt in ("profile", "regulatory", "review", "user_upload")
         else policy).append(ch)

    # Decorate-sort-undecorate with original index as the stable tiebreaker.
    decorated = [
        (
            -_fit_score(
                _meta_full(ch), profile, wants_zero_copay, desired_si,
                requires_maternity=requires_maternity,
                cost_objective=cost_objective,
            ),
            i,
            ch,
        )
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
    identity, not by chunk offset. Requiring an offset here would drop
    every chunk built by those pruned-dict call sites.
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
    """Within top-K results, collapse same-product / marketing-variant
    duplicates to one chunk, keeping the highest-scoring chunk. Preserves
    the order of first appearance of each kept product.

    Keying is by the SHARED canonical identity
    (policy_identity.canonical_key) — UIN-primary, product_key fallback —
    NOT the raw policy_id. The same product can appear under different
    policy_ids: a marketing rename ("my:Optima Secure" vs "my:Optima
    Secure (older variant)" — same UIN) or two doctype siblings
    ("...__wordings" vs "...__brochure" — same product_key). policy_id-only
    dedup would let both through; canonical-identity keying collapses them.
    This reuses the exact rule the marketplace endpoint uses so the
    recommender and the marketplace agree on "the same policy".
    """
    best: dict[str, Any] = {}
    order: list[str] = []
    for ch in chunks:
        m = _meta_full(ch)
        key = canonical_key(m)
        score = m.get("score")
        if score is None:
            # try attribute path
            score = getattr(ch, "score", 0.0)
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 0.0

        if key not in best:
            best[key] = (ch, score_f)
            order.append(key)
            continue
        prev_chunk, prev_score = best[key]
        if score_f > prev_score:
            best[key] = (ch, score_f)

    return [best[key][0] for key in order]


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
                                   top-up w/o base cover, SI floor, co-pay;
                                   KI-279 — drop fixed-benefit products when
                                   the user wants comprehensive indemnity)
        4. Exact-match bypass     (if query has a UIN / quoted name, swap in)
        5. Dedup by policy_id
        6. Profile-fit ranking    (KI-278 — promote best-fit over raw cosine;
                                   KI-279 — demote any surviving fixed-benefit
                                   below indemnity options)
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
