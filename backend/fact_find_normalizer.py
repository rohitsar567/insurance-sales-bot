"""LLM-based normalizer for fact-find answers.

Translates raw natural-language user replies into the schema values expected
by `backend/needs_finder.py::Profile`. Plus a non-answer detector that skips
recording when the input is an STT failure template, empty, or off-topic.

This fixes two symptoms surfaced in production on 2026-05-14:

1. Free-text answers being stored verbatim instead of mapped to enums.
   Example: user said "for now, just me" → stored as
   `dependents="Um, for now, just me."` instead of `dependents="self"`.
   The frontend Profile panel's enum-button comparison then never matches,
   so the sidebar shows no selected option even though chat captured it.

2. STT-failure fallback messages (or empty transcripts) being recorded as
   the user's answer to the in-flight question. The next question silently
   moves on with garbage.

Architecture:
  - `is_valid_answer(text)` — cheap guard that filters non-answers BEFORE
    any LLM call.
  - `normalize_answer(question_id, raw)` — async; fast-path regex for
    numeric fields (age, parents_age, existing_cover); LLM call (NIM
    Llama-3.3-70B at temperature 0) for enum and list fields.
  - Returns None when the input can't be mapped → the orchestrator should
    NOT clear `awaiting_question_id` so the bot re-asks the same question
    (the "ask me again" behavior the human asked for).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

# ----------------------------------------------------------------------------
# Field schema — what each fact-find question expects after normalization.
# Question IDs must match `backend/needs_finder.py::GRAPH[i].id` exactly.
# ----------------------------------------------------------------------------

_FIELD_SCHEMA: dict[str, dict] = {
    "age": {"type": "int", "min": 1, "max": 120},
    "dependents": {
        "type": "enum",
        "values": [
            "self",
            "self+spouse",
            "self+spouse+kids",
            "self+parents",
            "self+spouse+kids+parents",
        ],
    },
    "income_band": {
        "type": "enum",
        "values": ["under_5L", "5L-10L", "10L-25L", "25L+"],
    },
    "existing_cover": {"type": "int", "min": 0, "max": 100_000_000},
    "primary_goal": {
        "type": "enum",
        "values": ["first_buy", "upgrade", "compare_specific", "tax_planning"],
    },
    "location": {
        "type": "enum",
        "values": ["metro", "tier1", "tier2", "tier3"],
    },
    "parents_age": {"type": "int", "min": 30, "max": 120},
    "health_conditions": {
        "type": "list",
        "common_values": ["diabetes", "hypertension", "thyroid", "asthma", "heart", "cancer"],
    },
    "budget": {
        "type": "enum",
        "values": ["under_15k", "15k_30k", "30k_60k", "60k+"],
    },
}

# Non-answer fingerprints — these strings will skip the LLM and the recording.
_NON_ANSWER_PATTERNS = [
    "sorry, i couldn't hear",
    "couldn't hear that clearly",
    "transcribe error",
    "transcribe failed",
    "stt failed",
    "no audio",
    "[transcription failed]",
    "please try again",
]


def is_valid_answer(text: str) -> bool:
    """Return False when text is empty, too short, or a known failure template."""
    if not text or not text.strip():
        return False
    s = text.strip().lower()
    if len(s) < 2:
        return False
    if any(p in s for p in _NON_ANSWER_PATTERNS):
        return False
    return True


async def normalize_answer(question_id: str, raw_text: str) -> Any:
    """Map natural-language `raw_text` to the schema value for `question_id`.

    Returns:
      - parsed value (int / enum string / list[str]) on success
      - None when the answer can't be confidently mapped (caller should re-ask)
    """
    if not is_valid_answer(raw_text):
        return None

    schema = _FIELD_SCHEMA.get(question_id)
    if schema is None:
        return raw_text.strip() or None

    # Fast paths — no LLM needed for plain integers / cover-amount parsing.
    if question_id in ("age", "parents_age"):
        return _parse_int(raw_text, schema)
    if question_id == "existing_cover":
        return _parse_existing_cover(raw_text)

    # KEYWORD FAST PATH — robust to NIM rate-limit and to LLM hiccups.
    # Try matching common patterns BEFORE the LLM call. Catches ~80% of
    # answers without consuming a NIM request and is deterministic under
    # load. The LLM is the fall-back for nuanced/edge phrasings.
    kw = _keyword_normalize(question_id, raw_text)
    if kw is not None:
        validated = _validate(kw, schema)
        if validated is not None:
            return validated

    # Enum + list fields — let the LLM map natural language to canonical value.
    return await _llm_normalize(question_id, raw_text, schema)


# ----------------------------------------------------------------------------
# Keyword fast-path — hand-curated common phrasing → schema value.
# Order matters within each field: more-specific patterns first.
# Case-insensitive substring matches on a normalized version of the text.
# ----------------------------------------------------------------------------

def _keyword_normalize(question_id: str, raw_text: str) -> Any:
    s = raw_text.lower()

    if question_id == "dependents":
        # KI-014 — vague terms like "family" / "everyone" must NOT auto-map.
        # User testing surfaced: user said "family" → bot assumed self+spouse+kids
        # → wrong (user may have meant parents, siblings, joint-family etc.).
        # Returning None here forces the LLM normalizer (or re-ask) to clarify.
        VAGUE_TERMS = ["family", "everyone", "all of us", "everybody", "whole family", "joint family"]
        if any(v in s for v in VAGUE_TERMS) and not any(
            k in s for k in ["spouse", "wife", "husband", "kid", "child", "parent"]
        ):
            return None  # force clarification

        if any(k in s for k in ["spouse", "wife", "husband"]) and "kid" in s and "parent" in s:
            return "self+spouse+kids+parents"
        if any(k in s for k in ["spouse", "wife", "husband"]) and "kid" in s:
            return "self+spouse+kids"
        if any(k in s for k in ["spouse", "wife", "husband"]) and "parent" in s:
            return "self+spouse+kids+parents"
        if any(k in s for k in ["spouse", "wife", "husband"]):
            return "self+spouse"
        if "parent" in s and "no" not in s.split():
            return "self+parents"
        if "just me" in s or "only me" in s or "myself" in s or "only self" in s or s.strip() in {"me", "self"}:
            return "self"

    elif question_id == "income_band":
        import re as _re
        if _re.search(r"(more than|above|over|>=?|>)\s*25", s) or "25l+" in s or "25 lakh+" in s:
            return "25L+"
        m = _re.search(r"(\d+(?:\.\d+)?)\s*(?:l|lakh|lac)", s)
        if m:
            val = float(m.group(1))
            if val >= 25: return "25L+"
            if val >= 10: return "10L-25L"
            if val >= 5:  return "5L-10L"
            return "under_5L"
        if "10-25" in s or "10l-25l" in s: return "10L-25L"
        if "5-10"  in s or "5l-10l"  in s: return "5L-10L"
        if "under 5" in s or "<5" in s or "below 5" in s: return "under_5L"

    elif question_id == "primary_goal":
        if any(k in s for k in ["first policy", "first one", "first time", "first buy", "new policy", "buying my first"]):
            return "first_buy"
        if any(k in s for k in ["upgrade", "upgrading", "better cover", "more cover", "increase cover"]):
            return "upgrade"
        if any(k in s for k in ["compare", "comparison", " vs ", " vs.", "versus"]):
            return "compare_specific"
        if any(k in s for k in [" tax ", "80d", "deduction", "tax planning"]):
            return "tax_planning"

    elif question_id == "location":
        metro = ["bangalore", "bengaluru", "mumbai", "delhi", "new delhi", "chennai", "kolkata", "hyderabad", "pune"]
        tier1 = ["ahmedabad", "jaipur", "lucknow", "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam", "patna", "vadodara", "ghaziabad", "ludhiana", "agra", "nashik"]
        tier2 = ["surat", "kochi", "trivandrum", "thiruvananthapuram", "coimbatore", "vijayawada", "madurai", "rajkot", "ranchi", "amritsar", "allahabad", "prayagraj", "jodhpur", "raipur"]
        for c in metro:
            if c in s: return "metro"
        for c in tier1:
            if c in s: return "tier1"
        for c in tier2:
            if c in s: return "tier2"
        if "metro" in s: return "metro"
        if "tier 1" in s or "tier1" in s: return "tier1"
        if "tier 2" in s or "tier2" in s: return "tier2"
        if "tier 3" in s or "tier3" in s or "village" in s or "small town" in s: return "tier3"

    elif question_id == "budget":
        import re as _re
        if "60k+" in s or ">60k" in s or "more than 60" in s or "above 60" in s:
            return "60k+"
        if "30-60" in s or "30k_60k" in s or "30k-60k" in s:
            return "30k_60k"
        if "15-30" in s or "15k_30k" in s or "15k-30k" in s:
            return "15k_30k"
        if "under 15" in s or "<15" in s or "below 15" in s or "under_15" in s:
            return "under_15k"
        m = _re.search(r"(\d+)\s*k", s)
        if m:
            v = int(m.group(1))
            if v >= 60: return "60k+"
            if v >= 30: return "30k_60k"
            if v >= 15: return "15k_30k"
            return "under_15k"

    elif question_id == "health_conditions":
        if any(p in s for p in ["none", "no condition", "nothing", "no pre-exist", "no health", "no chronic"]):
            return []
        canonical = []
        cond_keywords = {
            "diabetes": ["diabetes", "diabetic", "sugar"],
            "hypertension": ["hypertension", " bp ", "blood pressure", "high bp"],
            "thyroid": ["thyroid", "hypothyroid", "hyperthyroid"],
            "asthma": ["asthma"],
            "heart": ["heart problem", "heart disease", "cardiac"],
            "cancer": ["cancer", "tumor"],
        }
        for cond, kws in cond_keywords.items():
            if any(k in s for k in kws):
                canonical.append(cond)
        if canonical:
            return canonical

    return None


# ----------------------------------------------------------------------------
# Fast-path parsers (no LLM)
# ----------------------------------------------------------------------------

def _parse_int(text: str, schema: dict) -> int | None:
    digits = "".join(c for c in str(text) if c.isdigit())
    if not digits:
        return None
    try:
        v = int(digits[:3])
    except ValueError:
        return None
    if v < schema.get("min", 0) or v > schema.get("max", 9_999):
        return None
    return v


def _parse_existing_cover(text: str) -> int | None:
    """Handle "no" / "none" / "5 lakh" / "₹500000" / "5L" / "haven't got any" / "30k"."""
    s = text.lower().strip()
    # Negative answers map to 0 (no existing cover).
    if re.search(r"\b(no|none|nothing|zero|nope|nah|haven'?t|don'?t)\b", s):
        return 0

    # Look for a number followed by a unit suffix (digit-attached OR separated).
    # crore > lakh > thousand priority so longer units win the alternation.
    cr_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:cr|crore|crores)\b", s)
    if cr_match:
        try:
            return int(float(cr_match.group(1)) * 10_000_000)
        except ValueError:
            return None
    lakh_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:l(?:akh|ac)?s?)\b", s)
    if lakh_match:
        try:
            return int(float(lakh_match.group(1)) * 100_000)
        except ValueError:
            return None
    k_match = re.search(r"(\d+(?:\.\d+)?)\s*k\b", s)
    if k_match:
        try:
            return int(float(k_match.group(1)) * 1_000)
        except ValueError:
            return None

    # Plain digit-only amount (e.g., "500000").
    digits = "".join(c for c in text if c.isdigit())
    if not digits:
        return None
    try:
        amount = int(digits[:7])
    except ValueError:
        return None
    if amount < 0 or amount > 100_000_000:
        return None
    return amount


# ----------------------------------------------------------------------------
# LLM-backed normalizer for enum + list fields
# ----------------------------------------------------------------------------

_LLM_SYSTEM_TEMPLATE = """You map a user's natural-language answer to a structured value.

Question ID: {qid}
Expected schema: {schema}

Rules:
1. If type=enum, return EXACTLY one of the allowed values (a JSON string), or null if no clear match.
2. If type=list, return a JSON array of canonical lowercase condition strings. For "no", "none", "nothing" → [].
3. If the user clearly didn't answer the question (off-topic, asking back, gibberish), return null.
4. Output ONLY the JSON value — no prose, no code fences, no <think> blocks.

Examples for guidance:
- dependents enum, user "just me" → "self"
- dependents enum, user "me and my wife" → "self+spouse"
- dependents enum, user "I want coverage for my parents too" → "self+parents"
- income_band enum, user "around 18 lakhs" → "10L-25L"
- income_band enum, user "more than 25 lakhs" → "25L+"
- primary_goal enum, user "I'm buying my first one" → "first_buy"
- primary_goal enum, user "want to compare HDFC and ICICI" → "compare_specific"
- location enum, user "Bangalore" → "metro"
- location enum, user "Patna" → "tier2"
- budget enum, user "around 20k a year" → "15k_30k"
- health_conditions list, user "none" → []
- health_conditions list, user "diabetes and BP" → ["diabetes", "hypertension"]
- health_conditions list, user "I have asthma" → ["asthma"]
"""


async def _llm_normalize(question_id: str, raw_text: str, schema: dict) -> Any:
    from backend.providers.base import ChatMessage
    from backend.providers.nvidia_nim_llm import FAST_BRAIN_CHAIN, NimChainLLM

    sys_msg = _LLM_SYSTEM_TEMPLATE.format(qid=question_id, schema=json.dumps(schema))
    user_msg = f'User said: "{raw_text[:600]}"\n\nReturn the JSON value.'

    try:
        # KI-033 (2026-05-14) — was hardcoded NvidiaNimLLM(meta/llama-3.3-70b);
        # moved to fast-brain chain so when that one NIM pool rate-limits we
        # fall through to Qwen/Nemotron/Groq instead of silently returning None
        # (which made valid Indian-accented answers like "twenty-five" appear
        # to fail the verifier, then trip the 2-reask cap, then move on with
        # no profile captured — the D-005 cascade).
        llm = NimChainLLM(chain=FAST_BRAIN_CHAIN, timeout=10.0,
                          role="fact_find_normalizer", total_budget_s=15.0)
        result = await llm.chat(
            messages=[
                ChatMessage(role="system", content=sys_msg),
                ChatMessage(role="user", content=user_msg),
            ],
            temperature=0.0,
            max_tokens=120,
        )
        raw = (result.text or "").strip()
    except Exception as e:
        logging.warning(
            "fact_find_normalizer LLM call failed (qid=%s, raw=%r): %s",
            question_id, raw_text[:80], e,
        )
        return None

    # Strip <think> blocks and code fences that some models add despite instructions.
    if "<think>" in raw and "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    if raw.startswith("```"):
        raw = "\n".join(l for l in raw.split("\n") if not l.startswith("```")).strip()
    if not raw or raw.lower() == "null":
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Some models return bare strings without JSON quoting; tolerate.
        if schema["type"] == "enum" and raw.strip('"') in schema["values"]:
            return raw.strip('"')
        return None

    return _validate(parsed, schema)


def _validate(value: Any, schema: dict) -> Any:
    """Type + enum + bounds check. Returns None on failure."""
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
        return cleaned  # [] is a valid answer (= "no conditions")

    if t == "bool":
        if isinstance(value, bool):
            return value
        return None

    return value
