"""Retrieval-side filters + guards (A3 / A6 audit fixes).

This module is a SIDECAR to `rag/retrieve.py` and `backend/orchestrator.py`.
It is deliberately a separate file so concurrent edits to orchestrator.py
don't conflict with the retrieval-correctness work.

Public API:
    apply_profile_filter(chunks, profile)       -> list[RetrievedChunk]
        Drop chunks for policies the user is demographically ineligible for.
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
    """Drop chunks missing any of the three citation-critical fields.

    The "chunk_offset" requirement maps onto either `chunk_offset` (newer
    ingestions) or the existing `chunk_idx` (legacy + current). At least
    one must be a non-negative int.
    """
    kept: list[Any] = []
    for ch in chunks:
        m = _meta(ch)
        pid = m.get("policy_id")
        pname = m.get("policy_name")
        # accept either field name; chunk_idx is the existing schema in rag/retrieve.py
        offset = m.get("chunk_offset")
        if offset is None:
            offset = m.get("chunk_idx")

        if not pid or not isinstance(pid, str):
            continue
        if not pname or not isinstance(pname, str):
            continue
        if not isinstance(offset, int) or offset < 0:
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
    """Run the standard A3/A6 retrieval-side pipeline:

        1. Citation grounding   (reject malformed chunks)
        2. Profile pre-filter   (drop demographically inappropriate policies)
        3. Exact-match bypass   (if query has a UIN / quoted name, swap in)
        4. Dedup by policy_id
        5. Empty-retrieval guard

    Returns (filtered_chunks, guard_signal_or_None). When guard_signal is
    set, the caller should NOT pass `filtered_chunks` to the brain — they
    should surface the clarifier instead.
    """
    grounded = enforce_citation_grounding(chunks)
    fitted = apply_profile_filter(grounded, profile)

    exact = bypass_cosine_for_exact_match(grounded, query)
    if exact is not None and exact:
        # Exact match wins over cosine when the user clearly named a target.
        # Still apply profile filter to exact matches (the user might ask
        # about a senior plan when they're 25 — show it, but log it).
        fitted = exact

    deduped = dedup_by_policy(fitted)
    guard = empty_retrieval_guard(deduped, intent=intent)
    return deduped, guard
