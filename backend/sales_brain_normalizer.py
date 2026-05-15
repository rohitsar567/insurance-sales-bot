"""KI-167 WS1 — Deterministic post-processor for the LLM sales brain.

The sales brain (`backend/sales_brain.py`) issues ONE NIM call with the slot
schema in its system prompt + `response_format={"type":"json_object"}`. The
LLM returns a `captures` dict — but real LLMs:

  * Use loose field aliases ("location" instead of "location_tier",
    "income" instead of "income_band").
  * Emit free-text values for things that need to be enums
    ("Bangalore" instead of "metro", "5 lakhs" instead of "5L-10L").
  * Occasionally emit `null` / empty-string for fields they didn't capture.
  * Sometimes hallucinate a name that's actually a status phrase
    ("Currently Not Having Any").

This module is the deterministic shim that takes that raw dict and emits a
clean `{canonical_field: validated_value}` map ready to flow into
`SessionState.update_profile_field()`. NO LLM calls — pure normalization +
validation. Cheap, deterministic, easy to audit.

Cherry-picked / adapted from `backend/fact_find_normalizer.py`:
  - `_FIELD_SCHEMA`              — slot type + bounds + allowed enum values
  - `_validate(value, schema)`   — type/enum/bounds check
  - `_keyword_normalize` ideas   — free-text → enum mapping
  - `_parse_existing_cover`      — INR-amount parser for existing cover

Also delegates to `backend/needs_finder._parse_inr_amount`,
`_parse_budget_band`, `_parse_income_band` so this stays the single source
of truth on numeric parsing.

Public API:
    normalize_captures(raw, current_profile) -> dict
"""

from __future__ import annotations

import re
from typing import Any, Optional

from backend.needs_finder import (
    Profile,
    _parse_budget_band,
    _parse_income_band,
    _parse_inr_amount,
)


# ----------------------------------------------------------------------------
# Slot schema — canonical Profile field name → type / bounds / allowed values
# ----------------------------------------------------------------------------
#
# Mirrors `fact_find_normalizer._FIELD_SCHEMA` but keyed by the PROFILE FIELD
# NAME (not the question id). Includes `self+kids` in dependents (LLM may
# legitimately emit it; absent from the original schema). Mirrors the WS1
# spec verbatim where the spec adds new values (`tier3`, `rural`).

_FIELD_SCHEMA: dict[str, dict] = {
    "name": {"type": "name"},
    "age": {"type": "int", "min": 16, "max": 99},
    "dependents": {
        "type": "enum",
        "values": [
            "self",
            "self+spouse",
            "self+spouse+kids",
            "self+kids",
            "self+parents",
            "self+spouse+parents",
            "self+spouse+kids+parents",
        ],
    },
    "income_band": {
        "type": "enum",
        "values": ["under_5L", "5L-10L", "10L-25L", "25L+"],
    },
    "existing_cover_inr": {"type": "int", "min": 0, "max": 100_000_000},
    "primary_goal": {
        "type": "enum",
        # WS1 spec uses "compare" + "tax_savings"; the existing fact-find
        # codebase uses "compare_specific" + "tax_planning". Accept both
        # via alias-normalization below; canonical-stored value matches the
        # existing Profile (which downstream WS2/WS3 consume).
        "values": ["first_buy", "upgrade", "compare_specific", "tax_planning"],
    },
    "location_tier": {
        "type": "enum",
        # WS1 spec adds "rural"; existing schema uses metro/tier1/tier2/tier3.
        # Accept tier1 as an alias (legacy fact-find emitted it); collapse to
        # tier2 since the WS1 spec drops the tier1 bucket.
        "values": ["metro", "tier2", "tier3", "rural"],
    },
    "parents_to_insure": {"type": "bool"},
    "parents_age_max": {"type": "int", "min": 40, "max": 99},
    "parents_has_ped": {"type": "bool"},
    "budget_band": {
        "type": "enum",
        "values": ["under_15k", "15k_30k", "30k_60k", "60k+"],
    },
    "health_conditions": {
        "type": "list",
        "common_values": [
            "diabetes", "hypertension", "thyroid", "asthma", "heart", "cancer",
        ],
    },
}


# ----------------------------------------------------------------------------
# Field-name alias map — loose LLM key → canonical Profile field
# ----------------------------------------------------------------------------

_FIELD_ALIASES: dict[str, str] = {
    # location aliases
    "location": "location_tier",
    "city": "location_tier",
    "tier": "location_tier",
    "location_tier": "location_tier",
    # income aliases
    "income": "income_band",
    "income_band": "income_band",
    "annual_income": "income_band",
    "salary": "income_band",
    # cover aliases
    "existing_cover": "existing_cover_inr",
    "existing_cover_inr": "existing_cover_inr",
    "current_cover": "existing_cover_inr",
    "current_sum_insured": "existing_cover_inr",
    "sum_insured": "existing_cover_inr",
    # goal aliases
    "goal": "primary_goal",
    "primary_goal": "primary_goal",
    "intent": "primary_goal",
    # budget aliases
    "budget": "budget_band",
    "budget_band": "budget_band",
    "annual_budget": "budget_band",
    # parents aliases
    "parents_age": "parents_age_max",
    "parents_age_max": "parents_age_max",
    "parents_to_insure": "parents_to_insure",
    "cover_parents": "parents_to_insure",
    "include_parents": "parents_to_insure",
    "parents_has_ped": "parents_has_ped",
    "parents_ped": "parents_has_ped",
    "parents_conditions": "parents_has_ped",
    # health
    "health_conditions": "health_conditions",
    "conditions": "health_conditions",
    "ped": "health_conditions",
    "pre_existing": "health_conditions",
    "pre_existing_conditions": "health_conditions",
    # passthrough
    "name": "name",
    "age": "age",
    "dependents": "dependents",
}


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def normalize_captures(raw: dict, current_profile: Profile) -> dict:
    """Map a raw LLM `captures` dict to clean `{canonical_field: value}`.

    Pipeline per entry:
      1. Skip if value is None / empty string / empty list (LLM didn't
         actually capture this turn).
      2. Resolve the field alias → canonical Profile attribute name. Drop
         unknown fields silently.
      3. Run the value through the field's normalizer + validator. Drop
         on validation failure (don't raise — sales brain is fail-open).
      4. Compare against `current_profile.<field>`. Skip no-ops.

    Returns: clean dict ready for `SessionState.update_profile_field()`.
    """
    if not isinstance(raw, dict):
        return {}

    clean: dict[str, Any] = {}
    for raw_key, raw_value in raw.items():
        if raw_value is None:
            continue
        if isinstance(raw_value, str) and not raw_value.strip():
            continue
        # Bare empty list / dict from the LLM = no actual signal; drop.
        # The "user affirmed no conditions" path goes through a STRING value
        # like "all good" that the normalizer parses to [].
        if isinstance(raw_value, (list, dict)) and not raw_value:
            continue

        canonical = _FIELD_ALIASES.get(str(raw_key).strip().lower())
        if canonical is None:
            continue
        schema = _FIELD_SCHEMA.get(canonical)
        if schema is None:
            continue

        normalized = _normalize_value(canonical, raw_value, schema)
        if normalized is None:
            # Could not coerce — drop silently
            continue

        # No-op check — skip fields that match the current profile value.
        # Exception: an explicit "no conditions" capture (empty list parsed
        # from "all good" / "none" / etc.) is meaningful even when the profile
        # already holds an empty list — it tells the orchestrator the slot
        # was just addressed this turn so it won't be re-asked.
        current = getattr(current_profile, canonical, None)
        if current == normalized:
            if (
                canonical == "health_conditions"
                and normalized == []
                and current == []
                and isinstance(raw_value, str)
            ):
                clean[canonical] = normalized
            continue

        clean[canonical] = normalized

    return clean


# ----------------------------------------------------------------------------
# Value normalization — per-field free-text → canonical value
# ----------------------------------------------------------------------------

def _normalize_value(field: str, value: Any, schema: dict) -> Any:
    """Field-specific normalization. Returns None on validation failure."""
    if field == "name":
        return _normalize_name(value)
    if field == "age":
        return _normalize_int(value, schema)
    if field == "dependents":
        return _normalize_dependents(value, schema)
    if field == "income_band":
        return _normalize_income_band(value, schema)
    if field == "existing_cover_inr":
        return _normalize_existing_cover(value, schema)
    if field == "primary_goal":
        return _normalize_primary_goal(value, schema)
    if field == "location_tier":
        return _normalize_location_tier(value, schema)
    if field == "parents_to_insure":
        return _normalize_bool(value)
    if field == "parents_age_max":
        return _normalize_int(value, schema)
    if field == "parents_has_ped":
        return _normalize_bool(value)
    if field == "budget_band":
        return _normalize_budget_band(value, schema)
    if field == "health_conditions":
        return _normalize_health_conditions(value)
    return None


# ---------- name ----------

_NAME_BAD_FIRST_WORDS = {
    "currently", "not", "no", "none", "nothing", "never",
    "without", "looking", "buying", "shopping",
    "yes", "yeah", "nope", "nah",
    "haven", "havent", "haven't",
    "don", "dont", "don't",
    "i", "we", "my", "this", "that", "the",
    "first", "still", "yet",
}


def _normalize_name(value: Any) -> Optional[str]:
    """Validate a candidate name string.

    Reject if:
      - Not a non-empty string.
      - Contains digits.
      - Length > 50 chars.
      - First word is in the status-phrase blocklist.
      - <50% alphabetic chars (e.g. "..." or "@!#").
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or len(s) > 50:
        return None
    if any(c.isdigit() for c in s):
        return None
    # Strip simple polite-prefix scraps the LLM might leave in
    s_lc = s.lower()
    for prefix in (
        "i'm ", "i am ", "this is ", "my name is ", "name is ",
        "name's ", "call me ", "im ", "mr ", "mrs ", "ms ", "dr ",
    ):
        if s_lc.startswith(prefix):
            s = s[len(prefix):].strip()
            s_lc = s.lower()
            break
    if not s:
        return None
    first = s.split()[0].lower().strip(".,!?")
    if first in _NAME_BAD_FIRST_WORDS:
        return None
    alpha = sum(1 for c in s if c.isalpha())
    if alpha < 2 or alpha / max(1, len(s)) < 0.5:
        return None
    # Capitalize if all-lower (LLM convention — names should be Title Case).
    if not any(c.isupper() for c in s):
        s = " ".join(w.capitalize() for w in s.split())
    return s


# ---------- int / bool ----------

def _normalize_int(value: Any, schema: dict) -> Optional[int]:
    if isinstance(value, bool):  # bool is subclass of int — exclude
        return None
    if isinstance(value, (int, float)):
        v = int(value)
    elif isinstance(value, str):
        digits = "".join(c for c in value if c.isdigit())
        if not digits:
            return None
        try:
            v = int(digits[:3])
        except ValueError:
            return None
    else:
        return None
    lo = schema.get("min", -1_000_000_000)
    hi = schema.get("max", 1_000_000_000)
    if v < lo or v > hi:
        return None
    return v


def _normalize_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "yes", "y", "1", "include", "covered", "with"):
            return True
        if s in ("false", "no", "n", "0", "exclude", "skip", "without"):
            return False
    return None


# ---------- dependents ----------

_DEPENDENT_VAGUE_TERMS = (
    "family", "everyone", "all of us", "everybody",
    "whole family", "joint family",
)


def _normalize_dependents(value: Any, schema: dict) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    # Direct hit on a canonical enum value
    if s in schema["values"]:
        return s

    has_spouse = any(k in s for k in ("spouse", "wife", "husband", "partner"))
    has_kids = ("kid" in s) or ("child" in s) or ("children" in s) or ("son" in s) or ("daughter" in s)
    has_parents = "parent" in s

    # Vague terms with no specific signals → cannot coerce
    if any(v in s for v in _DEPENDENT_VAGUE_TERMS) and not (has_spouse or has_kids or has_parents):
        return None

    if has_spouse and has_kids and has_parents:
        return "self+spouse+kids+parents"
    if has_spouse and has_parents:
        return "self+spouse+parents"
    if has_spouse and has_kids:
        return "self+spouse+kids"
    if has_spouse:
        return "self+spouse"
    if has_kids and has_parents:
        # No canonical bucket — fold parents into the wider bundle
        return "self+spouse+kids+parents"
    if has_kids:
        return "self+kids"
    if has_parents:
        return "self+parents"
    if s in ("self", "me", "just me", "only me", "myself", "only self"):
        return "self"
    return None


# ---------- income_band ----------

def _normalize_income_band(value: Any, schema: dict) -> Optional[str]:
    if isinstance(value, str):
        s = value.strip()
        if s in schema["values"]:
            return s
        # KI-149 — let the centralised parser handle free text.
        parsed = _parse_income_band(s)
        if parsed in schema["values"]:
            return parsed
        return None
    if isinstance(value, (int, float)):
        # Bare number → rupees → bucket
        amt = int(value)
        if amt < 500_000:
            return "under_5L"
        if amt < 1_000_000:
            return "5L-10L"
        if amt < 2_500_000:
            return "10L-25L"
        return "25L+"
    return None


# ---------- existing_cover_inr ----------

def _normalize_existing_cover(value: Any, schema: dict) -> Optional[int]:
    if isinstance(value, bool):
        # bool 'no' (False) → 0; 'yes' (True) is ambiguous (don't commit)
        return 0 if value is False else None
    if isinstance(value, (int, float)):
        v = int(value)
        if v < 0 or v > schema.get("max", 100_000_000):
            return None
        return v
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return None
        # Negative answers → 0
        if re.search(
            r"\b(no|none|nothing|zero|nope|nah|haven'?t|don'?t|never|"
            r"not\s+having|not\s+got|not\s+have|without\s+(?:any|a))\b",
            s,
        ):
            return 0
        amt = _parse_inr_amount(s)
        if amt is None:
            # Bare digit fallback (very small values OK here — user might
            # have ₹500 unused; still coerce within bounds).
            digits = "".join(c for c in s if c.isdigit())
            if not digits:
                return None
            try:
                amt = int(digits[:8])
            except ValueError:
                return None
        if amt < 0 or amt > schema.get("max", 100_000_000):
            return None
        return amt
    return None


# ---------- primary_goal ----------

_GOAL_ALIASES: dict[str, str] = {
    # WS1 spec ↔ canonical Profile values
    "compare": "compare_specific",
    "tax_savings": "tax_planning",
    # accept the canonical values themselves
    "first_buy": "first_buy",
    "upgrade": "upgrade",
    "compare_specific": "compare_specific",
    "tax_planning": "tax_planning",
}


def _normalize_primary_goal(value: Any, schema: dict) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    # Direct hit on alias or canonical
    if s in _GOAL_ALIASES:
        return _GOAL_ALIASES[s]
    # Keyword fall-through
    if any(k in s for k in ("first policy", "first one", "first time", "first buy", "new policy", "buying my first")):
        return "first_buy"
    if any(k in s for k in ("upgrade", "upgrading", "better cover", "more cover", "increase cover")):
        return "upgrade"
    if any(k in s for k in ("compare", "comparison", " vs ", " vs.", "versus")):
        return "compare_specific"
    if any(k in s for k in (" tax ", "80d", "deduction", "tax planning", "tax saving")):
        return "tax_planning"
    return None


# ---------- location_tier ----------
# WS1 spec city lists — case-insensitive substring match.
_METRO_CITIES = (
    "mumbai", "delhi", "new delhi", "bangalore", "bengaluru", "chennai",
    "kolkata", "hyderabad", "pune", "ahmedabad",
)
_TIER2_CITIES = (
    "jaipur", "lucknow", "indore", "chandigarh", "kochi", "coimbatore",
    "vadodara", "nagpur", "bhubaneshwar", "bhubaneswar", "visakhapatnam",
    "surat",
)


def _normalize_location_tier(value: Any, schema: dict) -> Optional[str]:
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    # Direct enum hit
    if s in schema["values"]:
        return s
    # Legacy tier1 → collapse to metro (WS1 spec dropped tier1).
    if s == "tier1" or s == "tier 1":
        return "metro"
    if s == "tier 2":
        return "tier2"
    if s == "tier 3":
        return "tier3"
    # City-name substring match (case-insensitive).
    for c in _METRO_CITIES:
        if c in s:
            return "metro"
    for c in _TIER2_CITIES:
        if c in s:
            return "tier2"
    # Explicit small-town / village wording → rural
    if any(k in s for k in ("village", "rural", "gaon")):
        return "rural"
    if any(k in s for k in ("small town", "tier 3", "tier3", "small city")):
        return "tier3"
    # Otherwise default to tier3 only if the string looks like a real
    # place name (alpha-heavy, short). This avoids stamping junk strings
    # like "n/a" or "?" as tier3.
    alpha = sum(1 for c in s if c.isalpha())
    if alpha >= 3 and alpha / max(1, len(s)) >= 0.7:
        return "tier3"
    return None


# ---------- budget_band ----------

def _normalize_budget_band(value: Any, schema: dict) -> Optional[str]:
    if isinstance(value, str):
        s = value.strip()
        if s in schema["values"]:
            return s
        parsed = _parse_budget_band(s)
        if parsed in schema["values"]:
            return parsed
        return None
    if isinstance(value, (int, float)):
        amt = int(value)
        if amt < 15_000:
            return "under_15k"
        if amt < 30_000:
            return "15k_30k"
        if amt < 60_000:
            return "30k_60k"
        return "60k+"
    return None


# ---------- health_conditions ----------

_NO_PED_PATTERNS = (
    r"\b(?:no|none|nothing|nope|nah|negative)\b",
    r"\b(?:not|don'?t|do\s+not|haven'?t|isn'?t)\s+(?:got|have|having|had|got\s+any|have\s+any)\b",
    r"\b(?:zero|nil)\s+(?:pre[-\s]?exist\w*|condition|chronic|health\s+issue|illness)",
    r"\b(?:i\s+am|i'm)\s+(?:healthy|fine|fit|alright|all\s+good|good)\b",
    r"\bnothing\s+(?:chronic|major|serious|to\s+report|like\s+that)\b",
    r"\ball\s+(?:good|fine|clear|healthy)\b",
    r"\bclean\s+bill\s+of\s+health\b",
)

_COND_KEYWORDS: dict[str, tuple] = {
    "diabetes":     ("diabetes", "diabetic", "sugar"),
    "hypertension": ("hypertension", "blood pressure", "high bp"),
    "thyroid":      ("thyroid", "hypothyroid", "hyperthyroid"),
    "asthma":       ("asthma",),
    "heart":        ("heart problem", "heart disease", "cardiac"),
    "cancer":       ("cancer", "tumor", "tumour"),
}
# "bp" as a free-standing token (case-insensitive word-boundary). Kept
# separate from `_COND_KEYWORDS` so it doesn't collide with substrings
# like "BPM" or "abp" while still catching "BP" / "high BP" / "diabetes and BP".
_BP_REGEX = re.compile(r"\bbp\b", re.IGNORECASE)


def _normalize_health_conditions(value: Any) -> Optional[list]:
    """Return a deduped lowercase list of canonical condition names.

    - [] means "no conditions" — VALID capture.
    - None means "could not parse" — caller drops the field.
    """
    # Direct list passthrough (LLM did its job)
    if isinstance(value, list):
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, (str, int)):
                continue
            s = str(item).strip().lower()
            if not s:
                continue
            if s in seen:
                continue
            seen.add(s)
            # Map keyword hits to canonical names
            mapped = None
            for canon, kws in _COND_KEYWORDS.items():
                if s == canon or any(k.strip() == s for k in kws):
                    mapped = canon
                    break
            cleaned.append(mapped if mapped else s)
        # Dedup again after canonicalisation
        return list(dict.fromkeys(cleaned))
    # String → parse out
    if isinstance(value, str):
        s = value.strip().lower()
        if not s:
            return None
        # Explicit "no PED" phrasings → empty list
        if any(re.search(p, s) for p in _NO_PED_PATTERNS):
            # belt-and-braces — if a real condition keyword is ALSO present,
            # fall through to capture it.
            has_condition = any(
                any(k in s for k in kws) for kws in _COND_KEYWORDS.values()
            )
            if not has_condition:
                return []
        canonical: list[str] = []
        for canon, kws in _COND_KEYWORDS.items():
            if any(k in s for k in kws):
                canonical.append(canon)
        # Word-boundary BP catch (independent of substring keywords above).
        if "hypertension" not in canonical and _BP_REGEX.search(s):
            canonical.append("hypertension")
        if canonical:
            return canonical
        return None
    return None


# ----------------------------------------------------------------------------
# Generic validator — kept for callers that want a single dispatch.
# ----------------------------------------------------------------------------

def _validate(value: Any, schema: dict) -> Any:
    """Type + enum + bounds check. Returns None on failure.

    Adapted from `fact_find_normalizer._validate` (line 420).
    """
    t = schema.get("type")
    if t == "enum":
        if isinstance(value, str) and value in schema["values"]:
            return value
        return None
    if t == "int":
        if isinstance(value, bool):
            return None
        try:
            v = int(value)
        except (TypeError, ValueError):
            return None
        if v < schema.get("min", -1_000_000_000) or v > schema.get("max", 1_000_000_000):
            return None
        return v
    if t == "list":
        if not isinstance(value, list):
            return None
        cleaned = [str(x).strip().lower() for x in value if x and isinstance(x, (str, int))]
        cleaned = [c for c in cleaned if c]
        return cleaned
    if t == "bool":
        if isinstance(value, bool):
            return value
        return None
    if t == "name":
        return _normalize_name(value)
    return value


__all__ = ["normalize_captures"]
