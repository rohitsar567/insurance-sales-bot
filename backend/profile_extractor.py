"""Conversational profile updates in free-form chat.

After fact-find onboarding completes, users often share new profile facts
in ordinary conversation — "I just turned 40", "we had a baby last month",
"I was diagnosed with diabetes". Vanilla retrieval doesn't know to update
the session profile from these utterances.

This module runs a lightweight LLM extractor on each free-form user message
to pull out any concrete profile updates the user just revealed. High-
confidence updates get applied to session.profile + re-upserted as the
profile chunk in Chroma, so subsequent retrieval (and the brain's reply
to THIS same turn) reflect the new state.

Design choices:
- Cheap-tier NIM model (Llama 3.3 70B) — extraction doesn't need the frontier.
- Conservative validation: drop any field that fails type/enum/bounds checks.
- Health conditions are MERGED with existing list (additive, deduped).
- Extraction failure NEVER blocks the chat — falls back to no-update silently.
- Enum values match backend/needs_finder.py::Profile exactly (under_5L / first_buy / ...).
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.needs_finder import Profile

_EXTRACTOR_SYSTEM = """You extract profile updates from a single user message.

Output a JSON object containing ONLY fields the user EXPLICITLY revealed in this single message. Use null/omit for unmentioned fields.

Fields and allowed values:
- age: integer (years, 1-120)
- dependents: one of "self", "self+spouse", "self+spouse+kids", "self+parents", "self+spouse+kids+parents"
- income_band: one of "under_5L", "5L-10L", "10L-25L", "25L+"
- existing_cover_inr: integer (current sum insured in rupees, e.g. 500000 for 5 lakh)
- primary_goal: one of "first_buy", "upgrade", "compare_specific", "tax_planning"
- location_tier: one of "metro", "tier1", "tier2", "tier3"
- parents_to_insure: boolean
- parents_age_max: integer (30-120, oldest parent's age in years)
- parents_has_ped: boolean (true if any parent has pre-existing disease)
- budget_band: one of "under_15k", "15k_30k", "30k_60k", "60k+"
- health_conditions: list of NEW condition strings the user just mentioned (additive — do not echo old ones)

Rules:
1. Only extract what the user EXPLICITLY stated in this message. Never infer.
2. Be conservative — when ambiguous, omit the field. Wrong updates are worse than missed ones.
3. If the user said nothing new about their profile, return an empty object: {}
4. Output the JSON object only. No prose. No code fences. No <think> blocks."""


_EXTRACTOR_USER_TEMPLATE = """User just said:
\"\"\"
{user_text}
\"\"\"

Current known profile (for context, do NOT echo unchanged fields back):
{profile_summary}

Return JSON of any NEW profile facts revealed in the user's message above."""


async def extract_profile_updates(
    user_text: str,
    current_profile: Profile,
) -> dict[str, Any]:
    """Return validated dict of {field_name: new_value}.

    Empty dict means nothing extractable. Never raises.
    """
    if not user_text or not user_text.strip():
        return {}

    from backend.providers.nvidia_nim_llm import NvidiaNimLLM
    from backend.providers.base import ChatMessage

    summary_parts = []
    for k, v in current_profile.__dict__.items():
        if v in (None, "", []) or k in ("asked", "free_form_session"):
            continue
        summary_parts.append(f"{k}={v}")
    profile_summary = ", ".join(summary_parts) or "(empty)"

    messages = [
        ChatMessage(role="system", content=_EXTRACTOR_SYSTEM),
        ChatMessage(
            role="user",
            content=_EXTRACTOR_USER_TEMPLATE.format(
                user_text=user_text[:1500],
                profile_summary=profile_summary[:500],
            ),
        ),
    ]

    try:
        llm = NvidiaNimLLM(model="meta/llama-3.3-70b-instruct")
        result = await llm.chat(messages=messages, temperature=0.0, max_tokens=300)
        raw = (result.text or "").strip()
    except Exception as e:
        logging.warning("profile_extractor LLM call failed: %s: %s", type(e).__name__, e)
        return {}

    # Strip code fences / think blocks if model added them despite instructions
    if "<think>" in raw and "</think>" in raw:
        raw = raw.split("</think>", 1)[1].strip()
    if raw.startswith("```"):
        lines = [l for l in raw.split("\n") if not l.startswith("```")]
        raw = "\n".join(lines).strip()
    if not raw.startswith("{"):
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}

    return _validate(parsed)


_ALLOWED_FIELDS: dict[str, type] = {
    "age": int,
    "dependents": str,
    "income_band": str,
    "existing_cover_inr": int,
    "primary_goal": str,
    "location_tier": str,
    "parents_to_insure": bool,
    "parents_age_max": int,
    "parents_has_ped": bool,
    "budget_band": str,
    "health_conditions": list,
}


_ENUM_VALUES: dict[str, set[str]] = {
    "dependents": {"self", "self+spouse", "self+spouse+kids", "self+parents", "self+spouse+kids+parents"},
    "income_band": {"under_5L", "5L-10L", "10L-25L", "25L+"},
    "primary_goal": {"first_buy", "upgrade", "compare_specific", "tax_planning"},
    "location_tier": {"metro", "tier1", "tier2", "tier3"},
    "budget_band": {"under_15k", "15k_30k", "30k_60k", "60k+"},
}


def _validate(updates: dict) -> dict:
    """Coerce types, enforce enums, drop anything that fails."""
    clean: dict[str, Any] = {}
    for k, v in updates.items():
        if k not in _ALLOWED_FIELDS or v is None:
            continue

        expected = _ALLOWED_FIELDS[k]
        try:
            if expected is int:
                if isinstance(v, bool):
                    continue
                v = int(v)
            elif expected is bool:
                if not isinstance(v, bool):
                    continue
            elif expected is str:
                if not isinstance(v, str):
                    continue
                v = v.strip()
                if not v:
                    continue
            elif expected is list:
                if not isinstance(v, list):
                    continue
                v = [str(x).strip().lower() for x in v if x and isinstance(x, (str, int))]
                v = [c for c in v if c]
                if not v:
                    continue
        except (TypeError, ValueError):
            continue

        if k in _ENUM_VALUES and v not in _ENUM_VALUES[k]:
            continue
        if k == "age" and not (1 <= v <= 120):
            continue
        if k == "parents_age_max" and not (30 <= v <= 120):
            continue
        if k == "existing_cover_inr" and v < 0:
            continue

        clean[k] = v

    return clean
