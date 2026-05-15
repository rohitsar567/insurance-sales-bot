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
    # KI-078 (2026-05-15) — when `ambiguous=True` because the LLM brain
    # bailed and `_canonical_fallback` was used, this stamps WHY so the
    # orchestrator can append it to `brain_used` and admin telemetry can
    # measure the fallback-reason mix. One of:
    #   "timeout"     — asyncio.wait_for(_TIMEOUT_S) expired on FAST_BRAIN_CHAIN
    #   "llm_error"   — FAST_BRAIN_CHAIN raised (non-timeout) before returning
    #   "no_trailer"  — reply had no <FF>...</FF> JSON block, or it failed parse
    #   "empty_reply" — trailer stripped to an empty user-facing reply
    # KI-079 (2026-05-15) — added two escalation-path reasons. When FAST
    # chain times out we re-try once against BRAIN_CHAIN (heavier; Qwen 80B
    # primary, more cross-provider fallbacks) inside a shorter budget. The
    # _fallback_reason then captures the escalation outcome:
    #   "timeout_after_escalation"   — both FAST + BRAIN chains timed out
    #   "llm_error_after_escalation" — BRAIN chain raised on the retry
    # None when the brain succeeded (either FAST primary OR BRAIN escalation).
    _fallback_reason: Optional[str] = None


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

User: "Hi"
Bot reply: Hi there — happy to help you find a health policy. What should I call you?
<FF>{"captured": {}, "slot_driving": "name", "complete": false}</FF>

User: "ok"
Bot reply: Sure. To get started, could you tell me your age?
<FF>{"captured": {}, "slot_driving": "age", "complete": false}</FF>

==========================================================
FINAL INSTRUCTION — READ THIS CAREFULLY. APPLIES TO EVERY REPLY.
==========================================================
EVERY reply you produce MUST end with the structured trailer in this EXACT format on its own line:

<FF>{"captured": {...}, "slot_driving": "...", "complete": false}</FF>

This rule is ABSOLUTE. There are NO exceptions:
- Short reply? Still emit the trailer.
- One-word user message ("ok", "hi", "yes")? Still emit the trailer.
- User asks an off-topic question? Still emit the trailer after your answer.
- User wants to end fact-find ("show me policies")? Emit trailer with "complete": true.
- Nothing new captured this turn? Emit "captured": {} (empty object). Still emit the trailer.

The trailer is the LAST thing in your output. Nothing comes after </FF>.

If you do not emit a valid <FF>...</FF> trailer, your entire reply is DISCARDED by the backend and the user sees a scripted fallback question. This is the single most important rule. NEVER omit the trailer.
"""


# ----------------------------------------------------------------------------
# Parser for <FF>...</FF> trailer
# ----------------------------------------------------------------------------

_FF_BLOCK_RE = re.compile(r"<FF>\s*(\{.*?\})\s*</FF>", re.DOTALL)
# KI-090 (2026-05-15) — lenient fallback for FF parsing. Many LLMs drop
# the literal <FF>...</FF> tags and emit a bare JSON tail (or inline
# fenced JSON). When the strict tag match fails, try these regexes in
# order so we accept whatever the brain actually produces.
_FF_FENCED_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
_FF_TAIL_JSON_RE = re.compile(r"(\{[\s\S]*\})\s*\Z")


def _parse_ff_block(text: str) -> Optional[dict]:
    """Extract + parse the structured trailer dict from an LLM reply.

    Returns the parsed dict on success, None when no parseable JSON object
    can be located. Caller treats None as `ambiguous=True` and falls
    through to the canonical fallback.

    KI-090 (2026-05-15) — lenient parsing. The original strict
    `<FF>{...}</FF>` regex was correct per the system-prompt contract, but
    real LLMs (Qwen, Nemotron under load, Groq Llama-3.3 at times) drop
    the literal tags and emit only the JSON. Pre-KI-090 those replies
    fell to `fallback:no_trailer` even though the brain had produced a
    perfectly valid structured tail. Now we try:
      1. Strict `<FF>{...}</FF>` (the contract — still preferred).
      2. ```` ```json {...} ``` ```` fenced (a common LLM habit).
      3. Bare `{...}` at the very end of the reply.
    Any candidate that parses as a JSON dict with at least one of the
    expected keys (`captured` / `slot_driving` / `complete`) wins.
    """
    if not text:
        return None
    candidates: list[str] = []
    m = _FF_BLOCK_RE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    for fenced in _FF_FENCED_RE.finditer(text):
        candidates.append(fenced.group(1).strip())
    tail = _FF_TAIL_JSON_RE.search(text.rstrip())
    if tail:
        candidates.append(tail.group(1).strip())
    seen: set[str] = set()
    for raw in candidates:
        if raw in seen:
            continue
        seen.add(raw)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # Require at least one of the contract keys so we don't accept
        # a stray JSON object from the prose (e.g. a quoted example).
        if any(k in data for k in ("captured", "slot_driving", "complete")):
            return data
    return None


def _strip_ff_block(text: str) -> str:
    """Remove the FF trailer (tagged, fenced, or bare-JSON tail) so the
    user-facing reply doesn't leak the schema tag.

    KI-090 — mirrors the lenient `_parse_ff_block` strategies in reverse:
    strip strict `<FF>...</FF>`, then ```` ```json ... ``` ````, then any
    bare-JSON tail that contains a contract key.
    """
    if not text:
        return text
    cleaned = _FF_BLOCK_RE.sub("", text).strip()
    cleaned = _FF_FENCED_RE.sub("", cleaned).strip()
    # Strip a bare-JSON tail ONLY if it contains a contract key — otherwise
    # we might delete prose that happens to end with a JSON-ish bracket.
    tail = _FF_TAIL_JSON_RE.search(cleaned)
    if tail:
        try:
            tail_json = json.loads(tail.group(1))
            if isinstance(tail_json, dict) and any(
                k in tail_json for k in ("captured", "slot_driving", "complete")
            ):
                cleaned = cleaned[: tail.start()].rstrip()
        except json.JSONDecodeError:
            pass
    # Defensive: any orphan partial tags
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
            if not is_valid_name(v):
                return None
            # KI-156 (2026-05-15) — reject LLM-hallucinated names that are
            # actually status/negation phrases. Live bug: user said "I am
            # currently not having any policy" and the LLM emitted
            # captured={"name":"Currently Not Having Any"}. is_valid_name
            # passes on length+alpha but doesn't catch semantic garbage.
            _bad_starts = {
                "currently", "not", "no", "none", "nothing", "never",
                "without", "looking", "buying", "shopping",
                "i", "we", "my", "this", "that", "the",
                "first", "still", "yet", "haven", "haven't",
                "don't", "don", "dont", "havent",
            }
            first = v.split()[0].lower().strip(".,!?")
            if first in _bad_starts:
                return None
            return v
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
# KI-103 (2026-05-15) — Canonical-fallback loop breaker
# ----------------------------------------------------------------------------
# When the LLM brain returns `no_trailer` (or any reason that drives
# `_canonical_fallback`) on consecutive turns, the legacy code re-surfaced
# the SAME unfilled slot indefinitely. Live 15-persona smoke test caught
# the brain repeating "First, your age?" 7-8 times in a row even after the
# user explicitly stated their age. Root cause: `next_question(profile)`
# iterates GRAPH skipping ids in `profile.asked` and filled fields — but
# the greedy capture in `_canonical_fallback` only appends to `asked` when
# a value was successfully extracted, so a failed capture loop keeps the
# slot un-asked forever.
#
# Fix: track per-slot failed-fallback counts on the session. After
# `_MAX_FAILED_ATTEMPTS` (=2) consecutive surfaces of the same unfilled
# slot via canonical fallback WITHOUT capturing anything, mark the slot as
# asked (so `next_question` advances) AND record it on `session._ff_skipped_slots`
# so the orchestrator / scorecard can render it as intentionally unanswered.
#
# Counters live on the SessionState (not the Profile) — they are transient
# fact-find-turn state, not part of the persisted profile schema. Resetting
# them happens automatically on greedy-capture success below.
_MAX_FAILED_ATTEMPTS = 2


def _failed_attempts(session) -> dict[str, int]:
    """Per-slot canonical-fallback failure counter, lazily attached to the
    session. Maps `question.id` (NOT field name) → consecutive failure count.
    """
    if not hasattr(session, "_ff_failed_attempts"):
        session._ff_failed_attempts = {}
    return session._ff_failed_attempts


def _skipped_slots(session) -> list[str]:
    """Per-session list of slot ids the loop-breaker has marked SKIPPED
    after `_MAX_FAILED_ATTEMPTS` failed canonical-fallback surfaces. The
    orchestrator / scorecard reads this to render the skipped slots as
    intentionally unanswered (not silently dropped).
    """
    if not hasattr(session, "_ff_skipped_slots"):
        session._ff_skipped_slots = []
    return session._ff_skipped_slots


# ----------------------------------------------------------------------------
# Public entry
# ----------------------------------------------------------------------------

# KI-075 (2026-05-15) — bumped 12s → 25s. Live probe on production showed
# 4 of 5 fact-find turns hitting the 12s wait_for cap at exactly 13.2s
# latency, falling to canonical fallback even though FAST_BRAIN_CHAIN's
# total_budget_s is 22s. NIM cold-start eats 10-15s on the first call
# after a Space rebuild; the 12s wait_for was killing the brain BEFORE
# the cross-provider fallback links (Groq, OpenRouter) ever got tried.
# 25s gives NIM cold-start headroom + leaves room for one chain fallback.
_TIMEOUT_S = 25.0             # FAST_BRAIN_CHAIN primary attempt budget
# KI-079 (2026-05-15) — if FAST exhausted, try BRAIN_CHAIN (Qwen 80B primary
# + more cross-provider fallbacks incl. OpenRouter + Groq) with a SHORTER
# budget so the user doesn't wait 25+15=40s on a fully-dead network. Total
# worst-case latency before canonical fallback: 25 + 15 = 40s, but the FAST
# chain only hits 25s when NIM is wedged — the BRAIN escalation then has a
# Qwen primary on a different NIM pool + OpenRouter + Groq, so realistic
# escalation success cases land in 3-8s.
_TIMEOUT_S_ESCALATION = 15.0


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

    # Hard 25-second timeout on FAST_BRAIN_CHAIN. The fast-brain chain already
    # has its own per-link + total-chain budget but we wrap with asyncio.wait_for
    # as a belt-and-braces stop.
    llm_fast = get_fast_brain_llm()
    try:
        result = await asyncio.wait_for(
            # KI-150 (2026-05-15) — bumped 420 → 700. Diagnosis showed the
            # contract requires prose (~250 tok) + <FF>...</FF> trailer
            # (~80 tok); 420 truncated verbose multi-slot replies before
            # the trailer, parser failed, canonical fallback fired, user
            # saw scripted prompt_en (the "robotic language" complaint).
            llm_fast.chat(messages=messages, temperature=0.6, max_tokens=700),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        # KI-079 (2026-05-15) — fast brain timed out. Before falling to the
        # canonical-question fallback, escalate ONCE to BRAIN_CHAIN (Qwen 80B
        # primary, more cross-provider fallbacks) with a shorter budget so the
        # user doesn't wait 25+15=40s on a dead network.
        logging.warning(
            "KI-079: fast brain timeout (session=%s, %.1fs) → escalating to heavy brain",
            session_id, time.time() - t0,
        )
        from backend.providers.nvidia_nim_llm import get_brain_llm
        llm_heavy = get_brain_llm()
        try:
            result = await asyncio.wait_for(
                # KI-150 — same bump on escalation path for consistency.
                llm_heavy.chat(messages=messages, temperature=0.6, max_tokens=700),
                timeout=_TIMEOUT_S_ESCALATION,
            )
        except asyncio.TimeoutError:
            logging.warning(
                "KI-079: heavy brain ALSO timed out (session=%s, total=%.1fs)",
                session_id, time.time() - t0,
            )
            return _canonical_fallback(
                session, user_text, reason="timeout_after_escalation"
            )
        except Exception as e2:
            logging.warning(
                "KI-079: heavy brain escalation failed (session=%s): %s: %s",
                session_id, type(e2).__name__, str(e2)[:200],
            )
            return _canonical_fallback(
                session, user_text, reason="llm_error_after_escalation"
            )
    except Exception as e:
        logging.warning(
            "fact_find_brain LLM call failed (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )
        return _canonical_fallback(session, user_text, reason="llm_error")

    raw = (result.text or "").strip()
    parsed = _parse_ff_block(raw)
    if parsed is None:
        logging.info(
            "fact_find_brain trailer missing/malformed (session=%s, raw=%r)",
            session_id, raw[:200],
        )
        return _canonical_fallback(session, user_text, reason="no_trailer")

    reply_text = _strip_ff_block(raw)
    if not reply_text.strip():
        return _canonical_fallback(session, user_text, reason="empty_reply")

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


def _normalize_for_slot(slot_id: str, raw_text: str) -> Any:
    """Synchronous slot normalizer used by KI-072 canonical fallback.

    Mirrors the keyword/int/cover fast-paths from `fact_find_normalizer.py`
    but stays sync so it can be called from the non-async `_canonical_fallback`.
    LLM-only enum normalization is skipped — fallback prefers re-asking over
    blocking on another LLM call (which is presumably already failing if we
    got here).
    """
    if not raw_text or not raw_text.strip():
        return None
    text = raw_text.strip()

    # NAME slot — KI-074 + KI-092 (2026-05-15) — two-mode parsing.
    # MODE A (lenient, when caller explicitly tagged awaiting=name): accept
    #   any plain alphabetic name. Used when fallback fires after the bot
    #   asked "what should I call you?" — the reply IS the name by
    #   construction; no intro phrase needed. Avoids the KI-074 false-
    #   reject on "rohit sar" (plain reply to the name prompt).
    # MODE B (strict, default — used by greedy multi-slot capture): requires
    #   explicit "I'm X" / "my name is X" / "this is X" / "call me X" intro
    #   to avoid false positives on "this is correct" / "29 years old" etc.
    # The caller signals MODE A by passing slot_id="name__awaiting" (a
    # synthetic tag); the canonical fallback maps awaiting_question_id=name
    # to this tag.
    if slot_id in ("name", "name__awaiting"):
        lenient_mode = (slot_id == "name__awaiting")
        slot_id = "name"  # normalise so downstream logic still sees "name"
        import re as _re
        # Strip leading greeting first
        s = text.strip().strip(".,!?")
        s = _re.sub(r"^(hi|hello|hey|namaste|yo)[,!.\s]+", "", s, flags=_re.IGNORECASE)

        # KI-092 — LENIENT MODE: the bot just asked "what should I call you?"
        # so any plain alphabetic reply should be accepted as the name.
        # Examples to accept: "rohit sar" / "Anjali" / "Dr. Priya" / "Sam"
        # Examples to reject: empty, all-digit, mostly-symbol, way too long.
        if lenient_mode:
            # Drop common polite-prefix scraps if present.
            s_lc = s.lower()
            for prefix in ("i'm ", "i am ", "this is ", "my name is ", "name is ",
                           "name's ", "call me ", "im ", "mr ", "mrs ", "ms ", "dr "):
                if s_lc.startswith(prefix):
                    s = s[len(prefix):].strip()
                    break
            # Validation: 1-50 chars, ≥50% alphabetic, no embedded digits
            if not s or len(s) > 50 or any(c.isdigit() for c in s):
                return None
            alpha = sum(1 for c in s if c.isalpha())
            if alpha < 2 or alpha / max(1, len(s)) < 0.5:
                return None
            # Limit to 1-4 word tokens
            tokens = s.split()
            if not (1 <= len(tokens) <= 4):
                return None
            # Capitalise if all-lower
            if not any(c.isupper() for c in s):
                s = " ".join(w.capitalize() for w in tokens)
            return s

        # STRICT MODE (greedy multi-slot capture) — original KI-074 behaviour:
        # require an explicit intro phrase to avoid false positives.
        m = _re.search(
            r"\b(?:i'?m|i\s+am|this\s+is|my\s+name\s+is|name\s+is|call\s+me|name'?s)\s+"
            r"([a-zA-Z][a-zA-Z'\-]{1,30}(?:\s+[a-zA-Z][a-zA-Z'\-]{1,30}){0,3})\b",
            s,
            flags=_re.IGNORECASE,
        )
        if not m:
            return None
        candidate = m.group(1).strip()
        # KI-074 — chop at conjunctions / boundary words so "Rohit Sar and"
        # in "My name is Rohit Sar and I am 32" becomes "Rohit Sar".
        candidate = _re.split(
            r"\s+(?:and|but|with|plus|also|or|&|,)\s+",
            candidate, maxsplit=1, flags=_re.IGNORECASE,
        )[0].strip()
        # Also strip a TRAILING conjunction (when the connector is the
        # last token in the captured span — e.g. "Rohit Sar and").
        candidate = _re.sub(
            r"\s+(?:and|but|with|plus|also|or|&)\s*$",
            "", candidate, flags=_re.IGNORECASE,
        ).strip()
        # Reject if any digit or trailing words look like an age phrase
        if any(c.isdigit() for c in candidate):
            return None
        first_word = candidate.split()[0].lower()
        if first_word in {
            # Articles + possessives + demonstratives
            "a", "an", "the", "my", "your", "his", "her", "their", "our",
            "this", "that", "these", "those",
            # Ordinals (commonly follow "this is my first ...")
            "first", "second", "third", "fourth", "fifth", "last", "next",
            # Common adjectives mistaken for names
            "looking", "buying", "shopping", "interested", "trying", "thinking",
            "happy", "sad", "tired", "busy", "ready", "done", "new", "old",
            "good", "fine", "ok", "okay", "sure", "right",
            "correct", "wrong", "alone", "single", "married",
            "years", "year", "from", "very", "really", "just",
        }:
            return None
        # Capitalise if all-lower (STT often outputs lowercase)
        if not any(c.isupper() for c in candidate):
            candidate = " ".join(w.capitalize() for w in candidate.split())
        return candidate

    # KI-074 — slot-specific triggers prevent cross-contamination during
    # greedy multi-slot capture. Without these guards, "29 years old" was
    # getting written into existing_cover_inr AND parents_age_max AND age.
    import re as _re2
    lc = text.lower()
    try:
        from backend.fact_find_normalizer import (
            _parse_int, _parse_existing_cover, _keyword_normalize,
            _validate, _FIELD_SCHEMA,
        )
        schema = _FIELD_SCHEMA.get(slot_id)

        if slot_id == "age":
            # Require an age trigger so a bare "29" in "₹29L cover" doesn't fire.
            if not _re2.search(r"\b(?:i'?m|i\s+am|i\s+am\s+about|age|aged|years?\s*old|yrs?\s*old|y/?o)\b", lc):
                # Bare number on its own line is also OK (typed "29")
                if not _re2.match(r"^\s*\d{1,3}\s*\.?\s*$", text):
                    return None
            val = _parse_int(text, schema or {"min": 1, "max": 120})
            # Age sanity: 18-99
            if val is not None and 18 <= val <= 99:
                return val
            return None

        if slot_id == "parents_age":
            # MUST explicitly mention parent context.
            if not _re2.search(r"\b(parent|mom|mum|mother|dad|father|mama|papa)", lc):
                return None
            val = _parse_int(text, schema or {"min": 30, "max": 110})
            if val is not None and 30 <= val <= 110:
                return val
            return None

        if slot_id == "existing_cover":
            # KI-074 — require a cover-context trigger. Word "cover" alone
            # is too weak (matches "looking for cover" with no amount).
            # Need either an explicit denial OR a currency/unit token.
            denial = _re2.search(
                r"\b(no|none|nothing|zero|nope|nah|never|haven'?t|don'?t\s+have|first\s+(?:policy|insurance|one|time|buy)|new\s+to\s+insurance|don'?t\s+have\s+any)\b",
                lc,
            )
            # Currency or unit cue. Plain "cover" word doesn't count.
            unit_cue = _re2.search(
                r"(₹|\brs\.?\s*\d|\d+\s*(?:lakh|lac|crore|cr)\b|"
                r"\bsum\s+insured\b|\bcovered\s+for\b|\bemployer.*\bcover|"
                r"\bfrom\s+work\b)",
                lc,
            )
            if not denial and not unit_cue:
                return None
            return _parse_existing_cover(text)

        # Enum / list slots — keyword fast path only, no LLM.
        # Each slot has its own trigger keywords inside _keyword_normalize.
        kw = _keyword_normalize(slot_id, text)
        if kw is not None and schema is not None:
            return _validate(kw, schema)
        return kw
    except Exception:
        return None


def _canonical_fallback(session, user_text: str, *, reason: str) -> FactFindOutcome:
    """Produce a deterministic, on-rails fallback reply when the LLM brain
    times out or returns unparseable output. Uses the canonical needs_finder
    question for the next missing slot so the user always sees a coherent
    next step.

    KI-072 (2026-05-15) — applies the user's message to the previously-asked
    slot via the legacy normalizer BEFORE picking the next slot.

    KI-074 (2026-05-15) — GREEDY multi-slot capture. Previous KI-072 logic
    only checked `awaiting_question_id` → dropped facts when brain failed on
    a multi-fact message ("I am 29 and just me" lost age + dependents
    because the LLM happened to be driving the `name` slot). Now we try
    every unfilled slot against the user_text and capture whatever
    matches — same multi-fact spirit as KI-070, just without the LLM call.
    """
    profile = session.profile
    captured: dict[str, Any] = {}

    if (user_text or "").strip():
        try:
            from backend.needs_finder import GRAPH

            # KI-092 — if the bot was explicitly awaiting the name slot,
            # try the LENIENT name parser FIRST so a plain "rohit sar"
            # reply gets captured instead of falling through to the strict
            # intro-phrase requirement and never matching.
            awaiting = getattr(session, "awaiting_question_id", None)
            if awaiting == "name" and not getattr(profile, "name", None):
                try:
                    lenient_name = _normalize_for_slot("name__awaiting", user_text)
                except Exception:
                    lenient_name = None
                if lenient_name:
                    q_obj = next((q for q in GRAPH if q.id == "name"), None)
                    if q_obj is not None:
                        captured[q_obj.field] = lenient_name
                        setattr(profile, q_obj.field, lenient_name)
                        if "name" not in profile.asked:
                            profile.asked.append("name")

            # Build the prioritised slot order — try high-signal slots first
            # (numbers, enums) before name (which has explicit-intro guard).
            # KI-158 (2026-05-15) — added "health_conditions". Its absence
            # meant a user replying "I'm not having any pre-existing condition"
            # NEVER got the slot captured here; the LLM brain failing on a
            # `no_trailer` reply for that turn (common under NIM load) bumped
            # _failed_attempts['health_conditions'] each turn until the
            # loop-breaker force-skipped the slot, which then made
            # next_question() return None and triggered the gentle hand-off
            # mid-fact-find.
            _GREEDY_ORDER = [
                "age", "dependents", "income_band", "existing_cover",
                "primary_goal", "location", "parents_age", "health_conditions",
                "budget", "name",
            ]
            ordered_slots: list[str] = [
                sid for sid in _GREEDY_ORDER
                if any(q.id == sid for q in GRAPH)
            ]

            for slot_id in ordered_slots:
                q_obj = next((q for q in GRAPH if q.id == slot_id), None)
                if q_obj is None:
                    continue
                # Skip already-filled slots
                current = getattr(profile, q_obj.field, None)
                if current not in (None, "", []):
                    continue
                try:
                    val = _normalize_for_slot(slot_id, user_text)
                except Exception:
                    val = None
                if val is None:
                    continue
                captured[q_obj.field] = val
                # KI-095 — route through session.update_profile_field for
                # consistency with the rest of the codebase (centralised
                # write + flush); val is guarded non-None just above.
                session.update_profile_field(q_obj.field, val)
                if slot_id not in profile.asked:
                    profile.asked.append(slot_id)
                # KI-103 — successful capture resets the failed-attempt
                # counter for this slot so a temporary loss of the LLM
                # brain doesn't permanently ghost the slot.
                _failed_attempts(session).pop(slot_id, None)
        except Exception as e:
            logging.info("canonical_fallback greedy capture failed: %s", e)

    # KI-103 (2026-05-15) — loop breaker. The legacy code surfaced the SAME
    # unfilled slot every turn the brain returned `no_trailer`, even when
    # the user explicitly answered it on turn 1. Now we track per-slot
    # consecutive failed-fallback surfaces on the session; after
    # _MAX_FAILED_ATTEMPTS consecutive surfaces of slot S with NO capture
    # for S, we mark S as asked (so `next_question` advances) and append
    # it to `_ff_skipped_slots` so the orchestrator/scorecard can render
    # it as intentionally unanswered.
    try:
        from backend.needs_finder import next_question
        attempts = _failed_attempts(session)
        skipped = _skipped_slots(session)
        # Bounded loop: at most len(GRAPH)+1 iterations so we can never
        # infinite-loop on a pathological state. In practice we exit on
        # the first slot that's either fresh OR has been skipped now.
        q = None
        for _ in range(20):
            q = next_question(profile)
            if q is None:
                break
            slot_id = q.id
            # If this turn's greedy capture filled this slot, surface
            # the NEXT slot (we already captured the answer; don't re-ask).
            if q.field in captured:
                profile.asked.append(slot_id) if slot_id not in profile.asked else None
                continue
            # Count this as a failed surface for the slot — we're about
            # to re-ask it without having captured a value for it.
            attempts[slot_id] = attempts.get(slot_id, 0) + 1
            if attempts[slot_id] > _MAX_FAILED_ATTEMPTS:
                # 3rd-or-later attempt: degrade gracefully — skip the slot
                # entirely so we don't re-ask. Mark asked + record in
                # _ff_skipped_slots and loop to pick the next unfilled slot.
                logging.info(
                    "KI-103: canonical fallback skipping slot=%s after %d failed attempts",
                    slot_id, attempts[slot_id] - 1,
                )
                if slot_id not in profile.asked:
                    profile.asked.append(slot_id)
                if slot_id not in skipped:
                    skipped.append(slot_id)
                # Reset the counter so if we ever do capture later via a
                # different code path, the slot can be re-introduced cleanly.
                attempts.pop(slot_id, None)
                continue
            # Within tolerance — surface this slot to the user.
            break
    except Exception:
        q = None
    if q is not None:
        # KI-156 (2026-05-15) — when the LLM bailed but we DID greedy-capture
        # something this turn, prepend a brief acknowledgement so the user
        # doesn't perceive the bot as ignoring them. Pre-fix the fallback
        # emitted only the bare scripted prompt_en, making the conversation
        # look unresponsive ("user gives city → bot asks name again" pattern).
        ack_parts: list[str] = []
        _PRETTY = {
            "name": "name",
            "age": "age",
            "dependents": "who you're covering",
            "income_band": "income band",
            "existing_cover_inr": "existing cover",
            "primary_goal": "goal",
            "location_tier": "city",
            "parents_age_max": "parents' age",
            "health_conditions": "health conditions",
            "budget_band": "budget",
        }
        for k in ("name", "age", "dependents", "location_tier", "income_band",
                 "existing_cover_inr", "primary_goal", "budget_band"):
            if k in captured:
                ack_parts.append(_PRETTY.get(k, k))
        if ack_parts:
            ack = f"Got that — {', '.join(ack_parts)}. "
            reply = ack + q.prompt_en
        else:
            reply = q.prompt_en
        slot = q.field
        return FactFindOutcome(
            reply_text=reply,
            captured_updates=captured,  # KI-072 — propagate the captured answer
            slot_driving=slot,
            fact_find_complete=False,
            ambiguous=True,
            _fallback_reason=reason,  # KI-078 — telemetry stamp
        )
    # Nothing left to ask — fact-find is genuinely complete (every applicable
    # slot is filled OR has been intentionally skipped by the loop-breaker).
    #
    # KI-158 (2026-05-15) — was emitting the "Let me know a bit about yourself
    # — your age..." gentle hand-off which is the WRONG message at this point:
    # the user just answered the LAST slot, every prior slot is on file, but
    # the canonical-fallback path landed here because the LLM brain failed on
    # the trailer block (no_trailer / empty_reply). Re-asking "tell me a bit
    # about yourself" makes the bot look forgetful mid-conversation.
    #
    # Fix:
    #   1. Propagate `captured` (was discarded as `{}`) so any greedy capture
    #      made this turn — most commonly the no-PED `health_conditions=[]` —
    #      is applied to the profile by the orchestrator post-loop.
    #   2. Flip `fact_find_complete=True` so the orchestrator switches the
    #      session into free-form mode and the NEXT user turn routes to the
    #      retrieval brain (which can recommend), not back here.
    #   3. Replace the awkward hand-off prose with a clean acknowledge +
    #      transition that mirrors the brain's `complete=true` summary path
    #      — the user just told us they're healthy, we have everything else
    #      already, so transition to recommendations.
    reply = (
        "Got it — no pre-existing conditions noted. I have everything I need. "
        "Want me to suggest a couple of policies that fit your profile?"
    )
    return FactFindOutcome(
        reply_text=reply,
        captured_updates=captured,  # KI-158 — propagate greedy captures
        slot_driving=None,
        fact_find_complete=True,    # KI-158 — flip to free-form
        ambiguous=True,
        _fallback_reason=reason,  # KI-078 — telemetry stamp
    )
