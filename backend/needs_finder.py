"""User profile state + slot-order hint for the fact-find.

KI-167 WS3 (2026-05-15) — the legacy rules-engine question GRAPH and the
matching `Question` dataclass have been removed. Fact-find phrasing now
lives in `backend/sales_brain.py` (the single-LLM-call brain); this module
keeps only:

  - `Profile`              — accumulated user state (still imported widely)
  - `record_answer`        — slot-write helper used by session_state
  - INR / budget / income parsers — used by `sales_brain_normalizer.py`
  - `is_field_set`         — local helper for record_answer + next_question
  - `next_question`        — returns the field NAME (str) of the next
                             missing slot in canonical order. Used by
                             `/api/profile/completeness`'s `next_question_hint`.

Public API:
  - Profile dataclass
  - next_question(profile) -> str | None   (field name, None = complete)
  - record_answer(profile, field_name, raw_value) -> Profile
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Profile:
    """User profile accumulated during fact-find."""
    name: Optional[str] = None  # KI-040 — humanise + key for cross-session lookup
    age: Optional[int] = None
    dependents: Optional[str] = None  # "self", "self+spouse", "self+spouse+kids", "self+parents", etc.
    income_band: Optional[str] = None  # "under_5L", "5L-10L", "10L-25L", "25L+"
    existing_cover_inr: Optional[int] = None  # 0 means none
    primary_goal: Optional[str] = None  # "first_buy", "upgrade", "compare_specific", "tax_planning"
    location_tier: Optional[str] = None  # "metro", "tier1", "tier2", "tier3"
    parents_to_insure: Optional[bool] = None
    parents_age_max: Optional[int] = None  # if parents_to_insure
    parents_has_ped: Optional[bool] = None  # if parents_to_insure
    budget_band: Optional[str] = None  # "under_15k", "15k_30k", "30k_60k", "60k+"
    desired_sum_insured_inr: Optional[int] = None  # SOFT pricing input (post-recap)
    health_conditions: Optional[list[str]] = field(default_factory=list)  # ["diabetes", "hypertension", ...]
    asked: list[str] = field(default_factory=list)  # question IDs / field names already asked
    free_form_session: bool = False  # True = user asks free questions, not driven by us
    # KI-063 (2026-05-15) — per-user policy interaction log so the bot
    # remembers which policies were shown / selected / rejected across
    # sessions. Each entry is a dict with shape:
    #   {policy_slug, insurer, event_at (ISO Z), session_id, reason}
    # Dedup at write-time on (policy_slug, event_type) — re-events just
    # bump event_at + session_id rather than appending duplicates.
    shown_policies: list[dict] = field(default_factory=list)     # KI-063
    selected_policies: list[dict] = field(default_factory=list)  # KI-063
    rejected_policies: list[dict] = field(default_factory=list)  # KI-063


# ----------------------------------------------------------------------------
# KI-149 (2026-05-15) — free-text INR amount parser for budget + income.
# User said "I maximum 30000 I can pay" → bot re-asked budget because no
# parser was attached to the budget Question and the LLM brain failed to
# capture it. Bare digits ("30000"), "30 thousand", "30 grand", "₹30,000",
# "1 lakh", "1.5L" must all map cleanly to a rupee amount.
# ----------------------------------------------------------------------------

def _parse_inr_amount(text: str) -> Optional[int]:
    """Extract an INR amount in rupees from free text.

    Handles:
      - "30000", "30,000", "₹30,000", "Rs 30000", "rs. 30000"
      - "30k", "30 k", "30K"
      - "30 thousand", "30 grand"
      - "1 lakh", "1.5 lakh", "1L", "1.5L", "1 lac"
      - "1 crore", "1cr"
      - strips fluff: "maximum 30000", "I can pay 30000", "around 25000"
      - tolerates per-year qualifiers: "/year", "per year", "p.a."

    KI-161 (2026-05-15) — REJECTS bare digits below ₹1000 (no plausible
    annual health insurance budget/income falls there) and REJECTS any
    text whose only number is in an age context ("29 years old", "age 29",
    "I am 29"). Origin: user answered the age question with "I am 29
    years old" and the parser wrote ₹29 into both budget_band and
    income_band, leading the bot to claim it captured age + income + budget
    from a single utterance.

    Returns the integer rupee amount, or None if no number is recognisable
    or if the only numbers in the text are clearly not currency.
    """
    if not text:
        return None
    s = str(text).lower().strip()
    # Strip currency symbols + thousands separators so "₹30,000" parses.
    s = s.replace("₹", " ").replace("rs.", " ").replace("rs", " ")
    s = s.replace(",", "")
    # Crore (highest unit first so longer alternation wins).
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:cr|crore|crores)\b", s)
    if m:
        try:
            return int(float(m.group(1)) * 10_000_000)
        except ValueError:
            return None
    # Lakh / lac.
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:l(?:akh|ac)?s?)\b", s)
    if m:
        try:
            return int(float(m.group(1)) * 100_000)
        except ValueError:
            return None
    # Thousand / grand / k.
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:thousand|grand|k)\b", s)
    if m:
        try:
            return int(float(m.group(1)) * 1_000)
        except ValueError:
            return None
    # KI-161 — bare-digit fallback now guarded against age contexts.
    # If the text is clearly about age, refuse to interpret any number
    # as a currency amount.
    if re.search(
        r"\b(?:year|years|yr|yrs|y\s*o)\s*(?:old)?\b|\bage\b|\bi\s*am\s+\d{1,3}\b",
        s,
    ):
        return None
    # Bare digit run — pick the largest number-like token (handles
    # "maximum 30000", "around 25000", "I can pay 30000"). Magnitude
    # floor of ₹1000 — anything smaller is implausible for an annual
    # health-insurance budget or income.
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if nums:
        try:
            amt = int(float(max(nums, key=lambda x: float(x))))
        except ValueError:
            return None
        if amt < 1_000:
            return None
        return amt
    return None


def _parse_budget_band(text: str) -> Optional[str]:
    """Map free-text budget text → one of under_15k / 15k_30k / 30k_60k / 60k+.

    KI-149 (2026-05-15). Falls back to range hints ("15-30k", "30 to 60k")
    before delegating to `_parse_inr_amount` for a single number.
    """
    if not text:
        return None
    s = str(text).lower()
    # Explicit bucket hints first — order matters (more specific wins).
    if re.search(r"60\s*k\s*\+|>\s*60|more\s+than\s+60|above\s+60|over\s+60", s):
        return "60k+"
    if re.search(r"30\s*[-to]+\s*60\s*k?|30k\s*[-_]\s*60k|30\s*to\s*60", s):
        return "30k_60k"
    if re.search(r"15\s*[-to]+\s*30\s*k?|15k\s*[-_]\s*30k|15\s*to\s*30", s):
        return "15k_30k"
    if re.search(r"under\s*15|less\s+than\s+15|below\s+15|<\s*15", s):
        return "under_15k"
    # Single amount → bucket.
    amt = _parse_inr_amount(s)
    if amt is None:
        return None
    if amt < 15_000:
        return "under_15k"
    if amt < 30_000:
        return "15k_30k"
    if amt < 60_000:
        return "30k_60k"
    return "60k+"


def _parse_income_band(text: str) -> Optional[str]:
    """Map free-text income text → one of under_5L / 5L-10L / 10L-25L / 25L+.

    KI-149 (2026-05-15). Same approach as `_parse_budget_band`: explicit
    bucket hints first, then a single rupee amount → bucket.
    """
    if not text:
        return None
    s = str(text).lower()
    if re.search(r"25\s*l\s*\+|>\s*25|more\s+than\s+25|above\s+25|over\s+25", s):
        return "25L+"
    if re.search(r"10\s*[-to]+\s*25\s*l?|10l\s*[-_]\s*25l|10\s*to\s*25", s):
        return "10L-25L"
    if re.search(r"5\s*[-to]+\s*10\s*l?|5l\s*[-_]\s*10l|5\s*to\s*10", s):
        return "5L-10L"
    if re.search(r"under\s*5|less\s+than\s+5|below\s+5|<\s*5", s):
        return "under_5L"
    amt = _parse_inr_amount(s)
    if amt is None:
        return None
    # Income is parsed in rupees; 5 lakh = 500_000.
    if amt < 500_000:
        return "under_5L"
    if amt < 1_000_000:
        return "5L-10L"
    if amt < 2_500_000:
        return "10L-25L"
    return "25L+"


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------

# KI-167 WS3 (2026-05-15) — canonical slot order for the fact-find hint API.
# The actual question phrasing lives in `sales_brain.py`; this list only
# encodes which Profile attribute we want to fill next when nothing else
# is driving the conversation.
_SLOT_ORDER: list[str] = [
    "name",
    "age",
    "dependents",
    "location_tier",
    "income_band",
    "primary_goal",
    "existing_cover_inr",
    "budget_band",
    "health_conditions",
]


def is_field_set(profile: Profile, field_name: str) -> bool:
    v = getattr(profile, field_name, None)
    if v is None:
        return False
    if isinstance(v, (list, str)) and len(v) == 0:
        return False
    return True


def next_question(profile: Profile) -> Optional[str]:
    """Return the field NAME of the next missing slot, or None if complete.

    KI-167 WS3 (2026-05-15) — refactored from `Optional[Question]` to
    `Optional[str]` after the rules-engine GRAPH was deleted. The caller in
    `backend/main.py:/api/profile/completeness` uses this only to hint to the
    frontend which slot to ask next; the actual question phrasing is now
    produced by `sales_brain.py`.

    A free-form session (user driving free questions) returns None so the
    hint endpoint reports "nothing to ask".
    """
    if profile.free_form_session:
        return None

    for slot in _SLOT_ORDER:
        if not is_field_set(profile, slot):
            return slot
    return None


# Legacy GRAPH question-id → Profile field-name aliases. Some callers
# (notably `session_state.record_answer` driven by `awaiting_question_id`)
# still pass the old question IDs from the deleted GRAPH. Mapping them here
# keeps that call path working without resurrecting the GRAPH.
_QID_TO_FIELD: dict[str, str] = {
    "existing_cover": "existing_cover_inr",
    "location": "location_tier",
    "parents_age": "parents_age_max",
    "budget": "budget_band",
}


def record_answer(profile: Profile, question_id: str, raw_answer: Any) -> Profile:
    """Mutate profile in place with a raw answer for a named slot.

    KI-167 WS3 (2026-05-15) — parser dispatch used to live on `Question`
    objects in the deleted GRAPH; with the GRAPH gone we apply the same
    parser map inline. `question_id` may be either a Profile attribute name
    (preferred) or one of the legacy GRAPH question IDs from `_QID_TO_FIELD`.
    """
    field_name = _QID_TO_FIELD.get(question_id, question_id)
    if not hasattr(profile, field_name):
        return profile
    value: Any = raw_answer
    parser = _PARSERS.get(field_name)
    if parser is not None:
        try:
            value = parser(raw_answer)
        except Exception:
            value = None
    if value is not None and value != "":
        setattr(profile, field_name, value)
        # KI-095 — only mark slot asked once setattr succeeds, so a parse
        # failure doesn't leave the slot in an asked-but-empty desync state.
        if question_id not in profile.asked:
            profile.asked.append(question_id)
    return profile


def _parse_age(s: Any) -> Optional[int]:
    digits = "".join(c for c in str(s) if c.isdigit())[:3]
    if not digits:
        return None
    try:
        return int(digits) or None
    except ValueError:
        return None


def _parse_existing_cover(s: Any) -> Optional[int]:
    text = str(s).lower()
    if any(k in text for k in ("no", "none", "nothing", "zero", "not")):
        return 0
    digits = "".join(c for c in text if c.isdigit())[:6]
    if not digits:
        return None
    try:
        amt = int(digits)
    except ValueError:
        return None
    if any(k in text for k in ("l", "lakh", "lac")):
        amt *= 100_000
    return amt or None


# Parser dispatch by Profile field name. Slots not listed accept the raw value.
_PARSERS: dict[str, Any] = {
    "age": _parse_age,
    "income_band": _parse_income_band,
    "existing_cover_inr": _parse_existing_cover,
    "budget_band": _parse_budget_band,
}
