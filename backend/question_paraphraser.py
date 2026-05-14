"""LLM-driven per-turn paraphrasing for the fact-find question graph.

Real-user testing on 2026-05-14 flagged the bot as "mechanical and robotic":
the 9 fact-find questions (backend/needs_finder.py::GRAPH) are hardcoded
strings asked in fixed order with fixed wording. This module wraps each
canonical question with a fast NIM/Groq paraphrase pass + a verifier that
rejects any paraphrase which drifts off-slot, so the worst case degrades
gracefully to the canonical text (no UX regression).

Caching: a module-level dict keyed by (session_id, slot_id) holds the
paraphrase across the lifetime of a session. Each slot is therefore
LLM-paraphrased AT MOST ONCE per session → max 9 paraphrase calls per
30-turn audit persona, even though the orchestrator can call this
function on every fact-find turn.

KI-032 (2026-05-14).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from backend.providers.base import ChatMessage
from backend.providers.nvidia_nim_llm import FAST_BRAIN_CHAIN, NimChainLLM

# Slot IDs match Question.id in needs_finder.GRAPH — keep this in lock-step.
ALLOWED_SLOTS = frozenset({
    "age", "dependents", "income_band", "existing_cover", "primary_goal",
    "location", "parents_age", "health_conditions", "budget",
})


_PARAPHRASER_SYSTEM = """You are rewriting fact-find questions for a health insurance bot in India.

The bot asks 9 fixed questions about the customer's age, dependents, income,
existing insurance, goals, city, parents' health, own pre-existing conditions,
and budget. Your job is to rephrase the next question so it sounds warm and
conversational instead of robotic — without changing what's being asked.

NON-NEGOTIABLE CONSTRAINTS:
  1. The paraphrase must ask about the SAME piece of information (same slot).
  2. Preserve any educational context the original explains ("this matters
     because…"). Rewrite the explanation in your own words, but keep its
     meaning.
  3. Warm and conversational, not verbose. 1-3 sentences max.
  4. Must end with a question mark.
  5. Indian English is fine — "₹", "lakh", "metro/tier-2 city" are OK.
  6. Do NOT add new questions or ask for additional information beyond the slot.

OUTPUT — strict JSON with exactly two fields, NO code fences, NO commentary:
  {"paraphrase": "...", "asks_about_slot": "<slot_id>"}

Valid slot IDs: age | dependents | income_band | existing_cover | primary_goal | location | parents_age | health_conditions | budget
"""


_USER_TEMPLATE = (
    "ORIGINAL CANONICAL: {original}\n"
    "SLOT: {slot_id}\n"
    "USER'S MOST RECENT MESSAGE (for tone calibration): {recent_user_text}\n\n"
    "Rewrite the question now. Return strict JSON only."
)


# (session_id, slot_id) → cached paraphrase string. None means "tried + failed,
# fall back to canonical for this session" so we don't retry the LLM each turn.
_PARAPHRASE_CACHE: dict[tuple[str, str], Optional[str]] = {}


def clear_session_cache(session_id: str) -> int:
    """Drop all cached paraphrases for one session. Called from
    session_state.reset_session() so a "Start fresh" click produces fresh
    paraphrase wording. Returns count of dropped keys."""
    keys = [k for k in _PARAPHRASE_CACHE.keys() if k[0] == session_id]
    for k in keys:
        _PARAPHRASE_CACHE.pop(k, None)
    return len(keys)


def _parse_json_lenient(raw: str) -> Optional[dict]:
    raw = raw.strip()
    if not raw:
        return None
    # Strip code fences some models add despite instructions
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Last-resort: pull the first balanced {...}
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def paraphrase_question(
    canonical: str,
    slot_id: str,
    session_id: Optional[str] = None,
    recent_user_text: str = "",
    *,
    total_budget_s: float = 6.0,
) -> Optional[str]:
    """LLM-rewrite the canonical fact-find question + verify it still targets
    `slot_id`. Returns the paraphrase if it passes the verifier, None otherwise.

    Callers should fall back to the canonical text on None.

    Result is cached per (session_id, slot_id) so the LLM is only called once
    per slot per session. A None result is also cached so we don't keep
    retrying a flaky model mid-session.
    """
    if slot_id not in ALLOWED_SLOTS:
        return None

    cache_key = (session_id or "_anon", slot_id)
    if cache_key in _PARAPHRASE_CACHE:
        return _PARAPHRASE_CACHE[cache_key]

    user_msg = _USER_TEMPLATE.format(
        original=canonical,
        slot_id=slot_id,
        recent_user_text=(recent_user_text or "(no prior message yet)")[:300],
    )

    llm = NimChainLLM(
        chain=FAST_BRAIN_CHAIN,
        timeout=4.0,
        role="paraphraser",
        total_budget_s=total_budget_s,
    )

    try:
        res = await llm.chat(
            messages=[
                ChatMessage(role="system", content=_PARAPHRASER_SYSTEM),
                ChatMessage(role="user", content=user_msg),
            ],
            temperature=0.7,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        logging.info("paraphraser LLM call failed slot=%s err=%s — falling back",
                     slot_id, type(e).__name__)
        _PARAPHRASE_CACHE[cache_key] = None
        return None

    data = _parse_json_lenient(res.text or "")
    if not data:
        logging.info("paraphraser JSON unparseable slot=%s — falling back", slot_id)
        _PARAPHRASE_CACHE[cache_key] = None
        return None

    paraphrase = (data.get("paraphrase") or "").strip()
    claimed_slot = (data.get("asks_about_slot") or "").strip()

    # ---- Verifier ---------------------------------------------------------
    if claimed_slot != slot_id:
        logging.info("paraphraser slot drift slot=%s claimed=%s — falling back",
                     slot_id, claimed_slot)
        _PARAPHRASE_CACHE[cache_key] = None
        return None
    if "?" not in paraphrase:
        logging.info("paraphraser missing '?' slot=%s — falling back", slot_id)
        _PARAPHRASE_CACHE[cache_key] = None
        return None
    if not (30 <= len(paraphrase) <= 500):
        logging.info("paraphraser length oob slot=%s len=%d — falling back",
                     slot_id, len(paraphrase))
        _PARAPHRASE_CACHE[cache_key] = None
        return None

    _PARAPHRASE_CACHE[cache_key] = paraphrase
    return paraphrase
