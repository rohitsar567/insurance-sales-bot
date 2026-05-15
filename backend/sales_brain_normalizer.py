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
    # KI-221 — additional status / metadata words the LLM occasionally emits
    # as a "name". These were observed live (e.g. "last", "surname", "hi")
    # routing through as candidate names.
    "last", "first", "sur", "lastname", "firstname", "surname", "name",
    "hi", "hey", "ok", "hello", "okay", "yo", "hola",
    "user", "person", "someone", "me", "myself",
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
    # KI-221 — minimum length floor. Single-char "names" (e.g. "a", "x")
    # are never real names and were leaking through the alpha-density check.
    if len(s) < 2:
        return None
    first = s.split()[0].lower().strip(".,!?")
    if first in _NAME_BAD_FIRST_WORDS:
        return None
    alpha = sum(1 for c in s if c.isalpha())
    if alpha < 2 or alpha / max(1, len(s)) < 0.5:
        return None
    # KI-221 — every real name has at least one vowel. Rejects scraps like
    # "Mr", "Dr", "St" (which slipped past the polite-prefix stripper when
    # not followed by a space, e.g. the LLM emitting just "Dr").
    if not any(c in "aeiouy" for c in s.lower()):
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
    # KI-222 — expand the "self" alias set. Live captures showed users
    # answering "single", "unmarried", "no dependents", "just myself" etc.,
    # which previously fell through to None and got silently dropped — the
    # bot then re-asked the same slot on the next turn.
    _SELF_ALIASES = (
        "self", "me", "just me", "only me", "myself", "only self",
        "single", "unmarried", "alone", "bachelor", "no dependents",
        "just myself", "nobody else", "no one else", "nobody",
        "myself only", "by myself", "solo",
    )
    if s in _SELF_ALIASES:
        return "self"
    # Substring fall-through for the same intents when wrapped in extra prose
    # (e.g. "i'm single right now", "just myself for now").
    if any(alias in s for alias in (
        "single", "unmarried", "no dependents", "just myself",
        "by myself", "nobody else", "no one else", "myself only",
        "bachelor", "solo",
    )):
        return "self"
    return None


# ---------- income_band ----------

def _normalize_income_band(value: Any, schema: dict) -> Optional[str]:
    if isinstance(value, str):
        s = value.strip()
        if s in schema["values"]:
            return s
        # KI-225 — natural phrasing pre-pass (covers patterns the centralised
        # `_parse_income_band` doesn't handle: "under 5 lakh", "between 5 and
        # 10", "25 lakh plus", etc.). Lowercase + strip light punctuation, then
        # match phrase patterns BEFORE delegating to the legacy parser.
        s_norm = re.sub(r"[,\?\.]+$", "", s.lower()).strip()
        s_norm = re.sub(r"\s+", " ", s_norm)
        # under_5L family
        if re.search(
            r"\b(?:under|less\s+than|below|<)\s*(?:rs\.?\s*)?5\s*(?:l|lakh|lakhs|lac)\b",
            s_norm,
        ):
            return "under_5L"
        # 25L+ family — check BEFORE 10-25 so "above 25" doesn't match the
        # 10-25 patterns by accident.
        if re.search(
            r"\b(?:25\s*l?\s*\+|25\s*(?:l|lakh|lakhs|lac)\s*plus|"
            r"(?:above|over|more\s+than|>)\s*(?:rs\.?\s*)?25\s*(?:l|lakh|lakhs|lac)?|"
            r"25\s*\+\s*(?:l|lakh|lakhs|lac))\b",
            s_norm,
        ):
            return "25L+"
        # 10L-25L family
        if re.search(
            r"\b(?:10\s*[-–to]+\s*25\s*(?:l|lakh|lakhs|lac)?|"
            r"between\s+10\s+and\s+25(?:\s*(?:l|lakh|lakhs|lac))?)\b",
            s_norm,
        ):
            return "10L-25L"
        # 5L-10L family
        if re.search(
            r"\b(?:5\s*[-–to]+\s*10\s*(?:l|lakh|lakhs|lac)?|"
            r"between\s+5\s+and\s+10(?:\s*(?:l|lakh|lakhs|lac))?)\b",
            s_norm,
        ):
            return "5L-10L"
        # KI-149 — let the centralised parser handle free text.
        parsed = _parse_income_band(s)
        if parsed in schema["values"]:
            return parsed
        return None
    if isinstance(value, (int, float)):
        # Bare number → rupees → bucket. KI-223 — require ≥1 lakh floor; bare
        # numbers below that are almost always age / dependents-count noise
        # (e.g. "I'm 29 years old" → LLM extracted 29 as "income"). Reject
        # rather than mis-bucket as under_5L.
        amt = int(value)
        if amt < 100_000:
            return None
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
        # KI-225 — explicit no-cover phrasings (covers "no insurance",
        # "no policy", "no existing", "first time so nothing", etc.). These
        # mostly route through the original \b(no|none|...)\b pattern below
        # but the more-specific multi-word phrasings need a dedicated pass so
        # "no insurance currently" doesn't accidentally fall through to the
        # digit fallback (which would extract '0' from no digits → None).
        _no_cover_phrases = (
            "no insurance", "no policy", "no policies", "no existing",
            "no existing cover", "no existing policy", "no cover",
            "first time so nothing", "nothing currently", "nothing right now",
            "no pre-existing", "don't have any", "dont have any",
        )
        if any(p in s for p in _no_cover_phrases):
            return 0
        # Negative answers → 0
        if re.search(
            r"\b(no|none|nothing|zero|nope|nah|haven'?t|don'?t|never|"
            r"not\s+having|not\s+got|not\s+have|without\s+(?:any|a))\b",
            s,
        ):
            return 0
        # KI-225 — employer / corporate cover phrasings. The amount IS in the
        # string; let `_parse_inr_amount` extract it (₹5L / 5 lakh / etc.).
        # Phrases like "5L from work" / "5 lakh employer" don't trip the
        # negative-answer regex above (they don't contain a no/none token).
        # Spelled-out small numbers ("five lakh", "ten lakh") aren't covered
        # by the centralised parser — do a light word-to-digit pre-pass here.
        _SPELLED_DIGITS = {
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "fifteen": "15", "twenty": "20", "twenty-five": "25", "fifty": "50",
        }
        _s_for_parse = s
        for _word, _digit in _SPELLED_DIGITS.items():
            # word-boundary substitution so "tense" doesn't become "10se"
            _s_for_parse = re.sub(rf"\b{re.escape(_word)}\b", _digit, _s_for_parse)
        amt = _parse_inr_amount(_s_for_parse)
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
    # KI-225 — natural phrasing keyword fall-through. Order matters: check the
    # more-specific phrases (tax / compare) BEFORE the broader first_buy / upgrade
    # buckets so e.g. "compare specific policies for tax savings" lands in
    # tax_planning, not compare_specific, when both keyword sets match.
    # tax_planning — most specific (section refs + explicit tax keywords)
    if any(k in s for k in (
        "tax planning", "for tax", "section 80d", "80d", "tax savings",
        "tax saving", "tax benefit", "tax deduction", " tax ", "deduction",
    )):
        return "tax_planning"
    # compare_specific — explicit comparison phrasing
    if any(k in s for k in (
        "compare specific", "comparing", "compare", "comparison",
        "looking at specific policies", "looking at specific polic",
        " vs ", " vs.", "versus", "between policy", "between policies",
    )):
        return "compare_specific"
    # upgrade — has existing cover, wants better
    if any(k in s for k in (
        "upgrade", "upgrading", "want to upgrade", "improve my existing",
        "better than what i have", "replace my current", "replace my existing",
        "better cover", "more cover", "increase cover",
    )):
        return "upgrade"
    # first_buy — broadest bucket, check last
    if any(k in s for k in (
        "first time", "first policy", "first time buyer", "first one",
        "first buy", "new policy", "buying my first", "buy my first",
        "looking for my first", "looking for first",
        "starting out", "new to insurance",
    )):
        return "first_buy"
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
        # KI-223 — require ≥₹5,000 floor; bare numbers below that are almost
        # always age / dependents-count noise (e.g. age 29 leaking into the
        # budget slot). Reject rather than mis-bucket as under_15k.
        if amt < 5_000:
            return None
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
    # KI-225 — alias coverage expanded per spec. Lay-terms ("sugar", "BP",
    # "type 2 diabetes", "blood sugar", "respiratory") + clinical-adjacent
    # phrasings ("cardiac", "heart problem", "tumor") all map onto the same
    # 6 canonical buckets the downstream sales brain understands.
    "diabetes":     (
        "diabetes", "diabetic", "sugar", "blood sugar",
        "diabetes type 1", "diabetes type 2", "type 1 diabetes",
        "type 2 diabetes", "type-1 diabetes", "type-2 diabetes",
    ),
    "hypertension": (
        "hypertension", "blood pressure", "high blood pressure",
        "high bp", "bp issue", "bp problem",
    ),
    "thyroid":      (
        "thyroid", "hypothyroid", "hyperthyroid",
        "thyroid problem", "thyroid issue", "thyroid disorder",
    ),
    "asthma":       (
        "asthma", "asthmatic", "respiratory", "respiratory issue",
        "respiratory problem",
    ),
    "heart":        (
        "heart problem", "heart disease", "heart issue", "heart condition",
        "cardiac", "cardiac issue", "cardiac problem",
    ),
    "cancer":       (
        "cancer", "tumor", "tumour", "cancer history", "had cancer",
        "cancer survivor",
    ),
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
            # Skip explicit "no" markers if they leaked into a list shape.
            if s in ("no", "none", "nothing", "nil", "negative"):
                continue
            # Map keyword hits to canonical names
            mapped = None
            for canon, kws in _COND_KEYWORDS.items():
                if s == canon or any(k.strip() == s for k in kws):
                    mapped = canon
                    break
            # KI-225 — also run the word-boundary BP regex so list items like
            # ["BP"] / ["high BP"] map to "hypertension" (the kw tuple uses
            # multi-word strings, none of which exact-match the bare token).
            if mapped is None and _BP_REGEX.search(s):
                mapped = "hypertension"
            # Substring fallback for "type 2 diabetes" style values where the
            # full string doesn't exact-match a kw but contains one.
            if mapped is None:
                for canon, kws in _COND_KEYWORDS.items():
                    if any(k in s for k in kws):
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
