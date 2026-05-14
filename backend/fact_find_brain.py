"""KI-070 — Single-LLM-call fact-find brain.

Replaces three stitched layers that previously drove fact-find:
  1. `backend/needs_finder.GRAPH` — hardcoded canonical question prompts
  2. `backend/question_paraphraser.py` — per-turn LLM paraphrase pass
  3. `backend/orchestrator._pick_opener` + `_family_aware_opener` —
     acknowledger rotation prepended to each canonical question

Real-user testing on 2026-05-15 flagged the result as robotic + templated.
The fix is to do the whole job in ONE LLM call per turn that produces a
natural conversational reply AND tags which slots were captured / which
slot it's driving toward / whether fact-find is complete — all in a single
machine-parsable trailer block.

Public entry:
    async def drive_fact_find(user_text, session, chat_history, session_id)
        -> FactFindOutcome

Reliability guardrails:
  1. JSON-block-must-parse — if `<FF>...</FF>` is missing or invalid,
     return `ambiguous=true` so the orchestrator emits a soft canonical
     fallback (`next_question` then prompt_en).
  2. Capture validation — each captured value goes through
     `fact_find_normalizer._validate` before commit. Validation failure
     drops only that field; others still apply.
  3. Slot-not-progressing safeguard — if the SAME `slot_driving` appears
     in 3 consecutive turns, force `next_question(profile)` to pick a
     different slot. State is held on the SessionState (`_ff_brain_history`).
  4. Hard timeout 12 seconds. Timeout → ambiguous=True + the canonical
     `next_question.prompt_en` as a fallback reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.providers.base import ChatMessage
from backend.providers.nvidia_nim_llm import get_fast_brain_llm


# ----------------------------------------------------------------------------
# Outcome dataclass
# ----------------------------------------------------------------------------

@dataclass
class FactFindOutcome:
    reply_text: str
    captured_updates: dict[str, Any] = field(default_factory=dict)
    slot_driving: Optional[str] = None
    fact_find_complete: bool = False
    ambiguous: bool = False


# ----------------------------------------------------------------------------
# System prompt — the ENTIRE behavioural contract lives here. Keep tight.
# ----------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are an insurance advisor having a natural conversation with someone considering a health policy in India. Be warm, brief, consultative — NOT closing-pitchy. Speak like a trusted Independent Financial Advisor, not a call-centre script.

You need to capture these 9 facts before recommending. Don't ask them all at once — weave them into the conversation based on what the user just said.

SLOTS YOU NEED TO FILL (field name : type : allowed values : why it matters)
- name : string (1-50 chars) : for personalisation + saving their profile so they don't repeat fact-find next visit
- age : int 18-99 : drives premium, eligibility, renewability
- dependents : enum {self, self+spouse, self+spouse+kids, self+kids, self+parents, self+spouse+parents, self+spouse+kids+parents} : drives policy structure (parent-specific plans often win when parents are on)
- income_band : enum {under_5L, 5L-10L, 10L-25L, 25L+} : drives sum-insured sizing
- existing_cover_inr : int >= 0 (0 if first-time buyer or no employer plan) : drives whether to recommend top-up vs full base plan
- primary_goal : enum {first_buy, upgrade, compare_specific, tax_planning} : drives whether to grade on price / breadth / claim experience / tax savings
- location_tier : enum {metro, tier1, tier2, tier3} : drives premium + cashless hospital density
- parents_age_max : int 30-99 (only relevant when dependents includes parents) : drives whether parent-specific plans should be included
- health_conditions : list of lowercase canonical names like ["diabetes", "hypertension", "thyroid", "asthma", "heart", "cancer"] (empty list = no conditions) : drives PED waiting + insurer fit. Honesty here protects the user's future claim.
- budget_band : enum {under_15k, 15k_30k, 30k_60k, 60k+} : drives policy shortlist

CONVERSATION RULES
1. Reply naturally. Don't say "Got it." or "Noted." every turn. Don't apologise robotically. Don't number your questions.
2. Acknowledge what the user just told you — reflect it back briefly so they feel heard.
3. Ask for at most ONE missing slot per reply, woven into the conversation.
4. If the user supplied multiple facts in one message (e.g., "I'm Rohit, 29, in Mumbai"), capture them ALL — don't re-ask for what they just volunteered.
5. If they go off-topic, give a brief honest answer, then steer back.
6. If they say "show me policies", "let me see options", "that's enough", "skip the questions" — set complete=true and slot_driving=null. Don't keep fact-finding forever.
7. When the profile feels complete enough to recommend (at minimum age + dependents + income + primary_goal captured), proactively summarise the captured profile + ask if they want a policy suggestion. Set complete=true.
8. Indian English is fine — "₹", "lakh", "metro/tier-2 city" are natural. Never use markdown bold or italics — the reply is read aloud by TTS too.
9. BFSI compliance: do not promise specific premium quotes, do not pressure-close. Light, advisory tone.
10. On health conditions — be straight: hiding a condition lowers premium today but turns into a denied claim later. Encourage honesty without lecturing.

OUTPUT FORMAT (strict)
After your natural reply, on a NEW LINE, emit exactly one machine-readable trailer:
<FF>{"captured": {<field>: <value>, ...}, "slot_driving": "<slot_field_or_null>", "complete": <true_or_false>}</FF>

The <FF> block is INVISIBLE to the user — the backend strips it before sending.

Strict requirements for the <FF> block:
- Captured values MUST match the slot schema (enums exact, ints parsed as JSON numbers, strings trimmed, list as JSON array). Missing slots = omit them; do NOT emit null values for fields you didn't capture this turn.
- `slot_driving` MUST be the FIELD NAME (e.g., "income_band"), not the canonical-question id. Use null when fact-find is complete OR when the user has steered to a different intent.
- `complete` MUST be a JSON boolean (true / false), no quotes.
- If the user's latest message yielded no new facts, emit "captured": {} (empty object, not omitted).
- Emit the <FF> block exactly once and at the END. Nothing after </FF>.

EXAMPLES OF GOOD REPLIES (reply line + trailer)

User: "Hi, I'm Rohit, 29, looking to buy my first health policy."
Bot reply: Hi Rohit — nice to meet you. First health policy is a good place to start. To shape the right options for you, who else would you want covered apart from yourself — spouse, kids, parents, or just you for now?
<FF>{"captured": {"name": "Rohit", "age": 29, "primary_goal": "first_buy"}, "slot_driving": "dependents", "complete": false}</FF>

User: "Just me and my wife. We're both in Bangalore."
Bot reply: Got that — you and your spouse, based in Bangalore (so metro hospital network is a big advantage for cashless). What income band are you in roughly — under ₹5L, ₹5-10L, ₹10-25L, or ₹25L+? Helps me suggest the right sum-insured size.
<FF>{"captured": {"dependents": "self+spouse", "location_tier": "metro"}, "slot_driving": "income_band", "complete": false}</FF>

User: "Around 18 lakh."
Bot reply: That puts you in the ₹10-25L band — gives us comfortable room on sum-insured. Do either of you have any pre-existing conditions — diabetes, BP, thyroid, anything chronic? Be straight here: it protects your future claim.
<FF>{"captured": {"income_band": "10L-25L"}, "slot_driving": "health_conditions", "complete": false}</FF>

User: "Just show me some policies already."
Bot reply: Fair enough — let me put together a shortlist based on what you've shared so far: age 29, covering you and your spouse, metro, income ₹10-25L, first-time buyer. Want me to suggest 2-3 policies that fit, or do you have specific insurers in mind to compare?
<FF>{"captured": {}, "slot_driving": null, "complete": true}</FF>

User: "What's the waiting period for PED usually?"
Bot reply: Most policies in India have a 24-36 month waiting period for pre-existing diseases — IRDAI caps the maximum, and a few insurers offer shorter waits as a premium upgrade. Happy to dig into a specific policy if you have one in mind. Meanwhile, what budget range are you comfortable with annually — under ₹15k, ₹15-30k, ₹30-60k, or ₹60k+?
<FF>{"captured": {}, "slot_driving": "budget_band", "complete": false}</FF>
"""


# ----------------------------------------------------------------------------
# Parser for <FF>...</FF> trailer
# ----------------------------------------------------------------------------

_FF_BLOCK_RE = re.compile(r"<FF>\s*(\{.*?\})\s*</FF>", re.DOTALL)


def _parse_ff_block(text: str) -> Optional[dict]:
    """Extract + parse the <FF>...</FF> JSON trailer from an LLM reply.

    Returns the parsed dict on success, None when the trailer is missing,
    malformed, or fails JSON parse. The caller should treat None as
    `ambiguous=True` and fall through to the canonical fallback.
    """
    if not text:
        return None
    m = _FF_BLOCK_RE.search(text)
    if not m:
        return None
    raw_json = m.group(1).strip()
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _strip_ff_block(text: str) -> str:
    """Remove the <FF>...</FF> trailer (and any trailing whitespace) so the
    user-facing reply doesn't leak the schema tag."""
    if not text:
        return text
    cleaned = _FF_BLOCK_RE.sub("", text).strip()
    # Remove any orphaned partial tags too — defensive against LLMs that emit
    # an opening <FF> without a close, or a malformed inner block.
    cleaned = re.sub(r"<FF>.*$", "", cleaned, flags=re.DOTALL).strip()
    cleaned = re.sub(r"</FF>", "", cleaned).strip()
    return cleaned


# ----------------------------------------------------------------------------
# Schema map — field name → validator schema in fact_find_normalizer terms
# ----------------------------------------------------------------------------
#
# The slot prompt promises enum values like "self+spouse" and "10L-25L", and
# `fact_find_normalizer._FIELD_SCHEMA` already encodes those. Reuse it. Note
# that the orchestrator's Profile fields are keyed by the FIELD NAME (e.g.,
# `income_band`), not the canonical question id (`income_band`), and those
# happen to match for most slots — exceptions handled below.

# Slot field → canonical-question id (for marking `profile.asked` so
# `next_question` doesn't re-pick the same slot).
FIELD_TO_QUESTION_ID: dict[str, str] = {
    "name": "name",
    "age": "age",
    "dependents": "dependents",
    "income_band": "income_band",
    "existing_cover_inr": "existing_cover",
    "primary_goal": "primary_goal",
    "location_tier": "location",
    "parents_age_max": "parents_age",
    "health_conditions": "health_conditions",
    "budget_band": "budget",
}


def _validate_capture(field_name: str, value: Any) -> Any:
    """Run `value` through the fact_find_normalizer schema for the matching
    field. Returns the validated value (possibly coerced) or None when the
    value fails validation."""
    if value is None:
        return None
    # Name is free-text — apply the profile_store name validator.
    if field_name == "name":
        try:
            from backend.profile_store import is_valid_name
            v = str(value).strip()
            return v if is_valid_name(v) else None
        except Exception:
            return None

    # Translate field name → fact_find_normalizer question id for schema lookup.
    qid = FIELD_TO_QUESTION_ID.get(field_name)
    if qid is None:
        # Unknown slot — drop.
        return None

    try:
        from backend.fact_find_normalizer import _FIELD_SCHEMA, _validate
    except Exception:
        return value  # if validator import fails, trust the LLM

    schema = _FIELD_SCHEMA.get(qid)
    if schema is None:
        return value

    # Try the strict validator first.
    validated = _validate(value, schema)
    if validated is not None:
        return validated

    # Last resort — for ints, accept strings that parse cleanly.
    t = schema.get("type")
    if t == "int":
        try:
            iv = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        if iv < schema.get("min", 0) or iv > schema.get("max", 1_000_000_000):
            return None
        return iv
    if t == "list" and isinstance(value, str):
        # LLM may emit a comma-separated string; coerce.
        items = [x.strip().lower() for x in value.split(",") if x.strip()]
        return items
    return None


# ----------------------------------------------------------------------------
# Slot-not-progressing safeguard
# ----------------------------------------------------------------------------

def _bump_brain_history(session, slot_driving: Optional[str]) -> int:
    """Record `slot_driving` on the session and return how many consecutive
    turns the same slot has been driven. Stored on the session under
    `_ff_brain_history` as a list[str] capped at 8."""
    if not hasattr(session, "_ff_brain_history"):
        session._ff_brain_history = []
    history: list[Optional[str]] = session._ff_brain_history
    history.append(slot_driving)
    if len(history) > 8:
        del history[: len(history) - 8]
    # Count run-length of the most recent slot at the tail.
    if not slot_driving:
        return 0
    n = 0
    for s in reversed(history):
        if s == slot_driving:
            n += 1
        else:
            break
    return n


# ----------------------------------------------------------------------------
# Public entry
# ----------------------------------------------------------------------------

_TIMEOUT_S = 12.0


async def drive_fact_find(
    user_text: str,
    session,
    chat_history: Optional[list[dict]],
    session_id: Optional[str],
) -> FactFindOutcome:
    """Single LLM call per fact-find turn.

    Returns a FactFindOutcome the orchestrator applies to session state.

    Behaviour:
      - Calls `get_fast_brain_llm()` with a system prompt that defines the
        slot schema, conversation rules, and trailer-block format.
      - Parses the trailer `<FF>...</FF>` JSON to learn what was captured,
        which slot the brain is driving toward, and whether fact-find is
        complete.
      - Validates each captured value through `fact_find_normalizer`.
      - On timeout / malformed JSON / no trailer → ambiguous=True and a
        canonical-question fallback reply.
    """
    t0 = time.time()
    # Build current profile JSON (only set fields) so the prompt sees what we
    # already know.
    profile = session.profile
    profile_snapshot: dict[str, Any] = {}
    for field_name in (
        "name", "age", "dependents", "income_band", "existing_cover_inr",
        "primary_goal", "location_tier", "parents_age_max", "health_conditions",
        "budget_band",
    ):
        v = getattr(profile, field_name, None)
        if v not in (None, "", []):
            profile_snapshot[field_name] = v

    system_with_state = _SYSTEM_PROMPT + (
        "\n\nCURRENT PROFILE (already captured; do NOT re-ask for these):\n"
        + json.dumps(profile_snapshot, ensure_ascii=False)
    )

    # Build messages: system + last ~10 chat history turns + user_text
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system_with_state)]
    if chat_history:
        for turn in chat_history[-10:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append(ChatMessage(role=role, content=content))
    messages.append(ChatMessage(role="user", content=user_text or ""))

    # Hard 12-second timeout. The fast-brain chain already has its own budget
    # but we wrap with asyncio.wait_for as a belt-and-braces stop.
    llm = get_fast_brain_llm()
    try:
        result = await asyncio.wait_for(
            llm.chat(messages=messages, temperature=0.6, max_tokens=420),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logging.warning(
            "fact_find_brain timeout (session=%s, %.1fs)", session_id, time.time() - t0
        )
        return _canonical_fallback(profile, reason="timeout")
    except Exception as e:
        logging.warning(
            "fact_find_brain LLM call failed (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )
        return _canonical_fallback(profile, reason="llm_error")

    raw = (result.text or "").strip()
    parsed = _parse_ff_block(raw)
    if parsed is None:
        logging.info(
            "fact_find_brain trailer missing/malformed (session=%s, raw=%r)",
            session_id, raw[:200],
        )
        return _canonical_fallback(profile, reason="no_trailer")

    reply_text = _strip_ff_block(raw)
    if not reply_text.strip():
        return _canonical_fallback(profile, reason="empty_reply")

    captured_raw = parsed.get("captured") or {}
    if not isinstance(captured_raw, dict):
        captured_raw = {}
    slot_driving_raw = parsed.get("slot_driving")
    if slot_driving_raw in ("", "null", "None"):
        slot_driving_raw = None
    complete_raw = bool(parsed.get("complete", False))

    # Validate each capture; drop fields that fail.
    captured: dict[str, Any] = {}
    for field_name, raw_value in captured_raw.items():
        validated = _validate_capture(field_name, raw_value)
        if validated is None:
            logging.info(
                "fact_find_brain dropped invalid capture (session=%s, field=%s, raw=%r)",
                session_id, field_name, raw_value,
            )
            continue
        captured[field_name] = validated

    # Slot-not-progressing safeguard — if same slot has been driven 3 turns
    # in a row, force the orchestrator to pick a different slot.
    repeat_count = _bump_brain_history(session, slot_driving_raw)
    if repeat_count >= 3 and slot_driving_raw and not complete_raw:
        try:
            from backend.needs_finder import next_question
            alt = next_question(profile)
            if alt and alt.field != slot_driving_raw:
                logging.info(
                    "fact_find_brain stuck on slot=%s for %d turns → forcing %s",
                    slot_driving_raw, repeat_count, alt.field,
                )
                slot_driving_raw = alt.field
        except Exception:
            pass

    return FactFindOutcome(
        reply_text=reply_text,
        captured_updates=captured,
        slot_driving=slot_driving_raw,
        fact_find_complete=complete_raw,
        ambiguous=False,
    )


def _canonical_fallback(profile, *, reason: str) -> FactFindOutcome:
    """Produce a deterministic, on-rails fallback reply when the LLM brain
    times out or returns unparseable output. Uses the canonical needs_finder
    question for the next missing slot so the user always sees a coherent
    next step."""
    try:
        from backend.needs_finder import next_question
        q = next_question(profile)
    except Exception:
        q = None
    if q is not None:
        reply = q.prompt_en
        slot = q.field
        return FactFindOutcome(
            reply_text=reply,
            captured_updates={},
            slot_driving=slot,
            fact_find_complete=False,
            ambiguous=True,
        )
    # Nothing left to ask — gentle hand-off.
    reply = (
        "Let me know a bit about yourself — your age, who you'd want covered "
        "(just you, family, parents), and your annual income band — and I'll "
        "tailor the options for you."
    )
    return FactFindOutcome(
        reply_text=reply,
        captured_updates={},
        slot_driving=None,
        fact_find_complete=False,
        ambiguous=True,
    )
