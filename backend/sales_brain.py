"""KI-167 WS1 — LLM-driven sales brain (replaces the scripted slot-walker).

Architecture shift from `backend/fact_find_brain.py`:

  PRE-KI-167 (fact_find_brain.py):
    System prompt asks the LLM for prose + a `<FF>{...}</FF>` JSON trailer.
    Backend strips the trailer, parses it, validates each capture. Works
    but: (a) some models drop the trailer despite the strict instruction
    (KI-090 surfaced this; KI-150 raised max_tokens to 700 to mitigate),
    (b) the canonical-fallback ladder is heavy machinery that competes
    with the brain for the same conversation.

  POST-KI-167 (this module):
    Single LLM call with `response_format={"type":"json_object"}` so the
    NIM model is GUARANTEED to emit a parseable JSON object — no trailer
    tag, no `<FF>` regex, no fallback ladder. JSON contains the natural
    reply, captured slots, and a `ready_for_recommendations` boolean.
    The deterministic post-processor in `sales_brain_normalizer.py`
    cleans the captures dict.

Public API (the CONTRACT — WS2 will call this exactly):

    @dataclass
    class SalesBrainResult:
        reply_text: str
        captured_updates: dict
        ready_for_recommendations: bool
        brain_used: str
        raw_json: Optional[dict] = None
        error_reason: Optional[str] = None

    async def drive_sales_brain(
        user_text: str,
        profile: Profile,
        chat_history: list[dict],
        session_id: Optional[str] = None,
    ) -> SalesBrainResult: ...

Failure mode:
  On JSON parse failure (should be near-zero with `response_format`) OR
  LLM call failure / timeout, this returns a SalesBrainResult with
  `brain_used="sales_brain::error:<reason>"`, empty reply_text, empty
  captured_updates, and `ready_for_recommendations=False`. The caller
  (WS2 orchestrator) decides what to do — NO scripted fallback lives
  here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.needs_finder import Profile
from backend.providers.base import ChatMessage
from backend.providers.google_gemini_llm import (
    get_gemini_llm,
    invalidate_cache as _gemini_invalidate_cache,
)
from backend.providers.nvidia_nim_llm import get_fast_brain_llm
from backend.providers.openrouter_llm import get_openrouter_llm
from backend.sales_brain_normalizer import normalize_captures


# ----------------------------------------------------------------------------
# KI-176 — OpenRouter free-tier frontier pool (PRIMARY tier).
#
# User loaded $10 on OpenRouter which unlocks 1000 req/day on `:free` models.
# These IDs were verified against the LIVE OpenRouter catalog and ALL declare
# `response_format` in their supported_parameters (the first two also declare
# `structured_outputs`). The list is passed to OpenRouter's native `models`
# array for server-side in-pool fallback BEFORE we fall through to NIM.
# ----------------------------------------------------------------------------
_OR_MODELS: list[str] = [
    "nvidia/nemotron-3-super-120b-a12b:free",         # 120B, structured_outputs
    "qwen/qwen3-next-80b-a3b-instruct:free",          # 80B,  structured_outputs
    "google/gemma-4-31b-it:free",                     # 31B,  response_format
]


# ----------------------------------------------------------------------------
# Public result dataclass — the contract WS2 will consume.
# ----------------------------------------------------------------------------

@dataclass
class SalesBrainResult:
    reply_text: str
    captured_updates: dict = field(default_factory=dict)
    ready_for_recommendations: bool = False
    brain_used: str = ""
    raw_json: Optional[dict] = None
    error_reason: Optional[str] = None


# ----------------------------------------------------------------------------
# Timeouts
# ----------------------------------------------------------------------------
# 25s mirrors fact_find_brain's KI-075 setting — gives NIM cold-start headroom
# + leaves room for one chain fallback. The fast-brain chain already has its
# own per-link + total-chain budget; this wait_for is a belt-and-braces stop.
_TIMEOUT_S: float = 45.0         # KI-170 — qwen3-next-80b + JSON mode regularly lands 15-25s; 25s breached periodically
_MAX_TOKENS: int = 500           # KI-197 — was 700; 500 still covers 1-3 sentence sales_brain replies + JSON wrapper. Saves ~200-400ms per turn.
_TEMPERATURE: float = 0.6        # mirrors fact_find_brain — conversational warmth

# KI-169 — strip <think>...</think> blocks the LLM may emit inside the JSON
# reply value (vs. as a preamble before the JSON, which _parse_brain_json
# already handles).
import re as _re_for_think
_REPLY_THINK_BLOCK = _re_for_think.compile(r"<think>.*?</think>", _re_for_think.DOTALL)


# ----------------------------------------------------------------------------
# Slot metadata — descriptions surfaced to the LLM so it knows what to ask for
# ----------------------------------------------------------------------------

_SLOT_DESCRIPTIONS: dict[str, str] = {
    "name": "User's preferred name (1-50 chars, no leading 'currently'/'not'/'no'/'looking', single or two-word first/last name).",
    "age": "User's age as an integer 16-99.",
    "dependents": "Who needs cover. Enum: 'self', 'self+spouse', 'self+spouse+kids', 'self+parents', 'self+spouse+parents', 'self+spouse+kids+parents', 'self+kids'.",
    "income_band": "Annual income band. Enum: 'under_5L', '5L-10L', '10L-25L', '25L+'.",
    "existing_cover_inr": "Current health-insurance sum-insured in rupees. 0 = no existing cover. Integer.",
    "primary_goal": "Why they're shopping. Enum: 'first_buy', 'upgrade', 'compare_specific', 'tax_planning'.",
    "location_tier": "City tier. Enum: 'metro', 'tier2', 'tier3', 'rural'.",
    "parents_to_insure": "Whether to include parents on the policy. Boolean.",
    "parents_age_max": "Older parent's age (40-99). Only relevant if parents_to_insure is true.",
    "parents_has_ped": "Whether either parent has a pre-existing condition. Boolean. Only relevant if parents_to_insure is true.",
    "budget_band": "Annual budget band. Enum: 'under_15k', '15k_30k', '30k_60k', '60k+'.",
    "health_conditions": "User's pre-existing conditions, lowercase list. Common values: diabetes, hypertension, thyroid, asthma, heart, cancer. Empty list = no conditions.",
}

# Order of slots — used to determine "required remaining" so the LLM has a
# stable sense of what to ask next. The first six are the recommendation-
# readiness minimum; the rest are deepening signals.
# KI-216 (2026-05-15) — `health_conditions` PROMOTED from nice-to-have to
# REQUIRED. Pre-existing conditions massively affect premium, waiting
# periods, and claim outcomes — skipping it means recommending policies
# that may exclude the user's actual needs. The brain MUST ask before
# advancing to recommendations.
_REQUIRED_FOR_READY: tuple = (
    "name", "age", "dependents", "location_tier", "income_band",
    "primary_goal", "health_conditions",
)

_NICE_TO_HAVE: tuple = (
    "existing_cover_inr", "budget_band",
    "parents_to_insure", "parents_age_max", "parents_has_ped",
)


# ----------------------------------------------------------------------------
# System prompt
# ----------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """You are a friendly Indian health-insurance broker having a natural, warm conversation with a prospective customer. Your tone is consultative — never pushy, never robotic, never a form. Speak like a trusted family friend who happens to know insurance, not a call-centre script.

YOUR JOB:
1. Get to know the user warmly across a short conversation.
2. Capture the slots listed below at your own pace, in any order that feels natural.
3. When you have enough to recommend (the minimum set is named below), set ready_for_recommendations: true.

CONVERSATION RULES:
- Use the user's first name when you know it. Sound human.
- Ask AT MOST ONE thing per turn — but weave it naturally into the conversation, don't fire it like a survey question.
- Don't say canned acknowledgements like "Got that —", "Noted.", "Perfect!", or "Great!". Acknowledge naturally by reflecting back what they said.
- Don't restart the conversation. Continue from wherever it is — pick up from the user's last message.
- If the user volunteers multiple facts in one message ("Hi I'm Rohit, 29, in Mumbai, looking for my first policy"), CAPTURE ALL OF THEM in this turn — don't re-ask for what they just told you.
- If the user goes off-topic (asks about waiting periods, claims, hospital networks, anything else), give a brief honest answer first, then steer naturally back to what you still need to know.
- BFSI compliance: do NOT promise specific premium quotes. Do NOT pressure-close. Stay advisory.
- Indian English is fine — "₹", "lakh", "metro/tier-2 city", "BP", "diabetes" all natural.
- On health conditions, be straight: hiding a condition lowers premium today but turns into a denied claim later. Encourage honesty without lecturing.
- Never use markdown bold/italics — your reply may be read aloud by TTS.
- NEVER include <think> tags or chain-of-thought reasoning in the "reply" field. The "reply" field is the EXACT prose shown to the user. Any internal reasoning is forbidden in "reply" — keep it natural and conversational.

OUTPUT FORMAT — STRICT:
Return a SINGLE JSON object with exactly these three keys:

  {
    "reply": "<your natural reply to the user — prose, no markdown, no JSON tags, no preamble like 'Got that —'>",
    "captures": { <field>: <value>, ... },
    "ready_for_recommendations": <true | false>
  }

Rules for the JSON:
- "reply" — natural conversational prose. NO scripted prefixes ("Got that —", "Noted —", "Sure —", "Perfect —"). Acknowledge what they said by reflecting it back, then continue.
- "captures" — only fields the user CHANGED or NEWLY revealed in their LAST message. If they didn't reveal anything new this turn, emit `"captures": {}` (empty object). Do NOT re-emit slots that haven't changed. Do NOT include null / empty-string values.
- "ready_for_recommendations" — set to true ONLY when you have ALL of: name, age, dependents, location, income_band, primary_goal, AND health_conditions. **You MUST ask about pre-existing health conditions before setting this to true** — it materially affects which policies fit (waiting periods, exclusions, premium loadings). Empty list `[]` means "no conditions" — that's valid and counts as captured. Otherwise leave false.
- "reply" before flipping ready: when ALL required slots above ARE captured this turn, your reply should EXPLICITLY ask the user "Shall I put together some options for you now?" (or similar natural confirmation). Do NOT auto-recommend or auto-pivot. The user has to affirm before we move to the recommendation phase.
- All enum values must match the slot schema EXACTLY (case-sensitive). All ints as JSON numbers, not strings. Lists as JSON arrays.

EXAMPLES OF GOOD REPLIES (illustrative — the JSON shape is what matters):

User: "Hi, I'm Rohit, 29, looking to buy my first health policy."
{
  "reply": "Hi Rohit — nice to meet you. First health policy is a smart move at your age. To shape the right options, who else would you want covered apart from yourself — spouse, kids, parents, or just you for now?",
  "captures": {"name": "Rohit", "age": 29, "primary_goal": "first_buy"},
  "ready_for_recommendations": false
}

User: "Just me and my wife. We're both in Bangalore."
{
  "reply": "You and your spouse in Bangalore — good, metro cashless networks are deep there. Roughly what's your annual income band: under ₹5L, ₹5-10L, ₹10-25L, or ₹25L+? Helps me size the right sum-insured.",
  "captures": {"dependents": "self+spouse", "location_tier": "metro"},
  "ready_for_recommendations": false
}

User: "around 18 lakh."
{
  "reply": "That puts you in the ₹10-25L band — gives us comfortable room on sum-insured. Last thing before I shortlist some options for you — any pre-existing conditions on either side, like BP, diabetes, thyroid, anything chronic? Being straight here protects your future claim.",
  "captures": {"income_band": "10L-25L"},
  "ready_for_recommendations": false
}

User: "No, both healthy."
{
  "reply": "Good to hear. So Rohit, here's what I'm working with: 29, you and your wife in Bangalore, ₹10-25L income, first-time buyer, no pre-existing conditions. I have enough to put together a tailored shortlist now — want me to suggest 2-3 policies that fit?",
  "captures": {"health_conditions": []},
  "ready_for_recommendations": true
}

User: "What's the waiting period for PED usually?"
{
  "reply": "Most policies in India have a 24-36 month waiting period for pre-existing diseases — IRDAI caps the maximum, and a few insurers offer shorter waits as a premium upgrade. Happy to dig into a specific one if you have it in mind. Meanwhile — what budget range are you comfortable with annually: under ₹15k, ₹15-30k, ₹30-60k, or ₹60k+?",
  "captures": {},
  "ready_for_recommendations": false
}

User: "Hi"
{
  "reply": "Hi there — happy to help you find a health policy that actually fits. What should I call you?",
  "captures": {},
  "ready_for_recommendations": false
}
"""


# ----------------------------------------------------------------------------
# Profile snapshot helpers
# ----------------------------------------------------------------------------

_TRACKED_FIELDS: tuple = (
    "name", "age", "dependents", "income_band", "existing_cover_inr",
    "primary_goal", "location_tier", "parents_to_insure", "parents_age_max",
    "parents_has_ped", "health_conditions", "budget_band",
)


def _profile_known(profile: Profile) -> dict[str, Any]:
    """Return a JSON-friendly dict of currently-captured profile fields.

    OMITS None / empty-string / empty-list fields — so the prompt only
    shows the LLM what's actually known. This avoids polluting the
    context with `"age": null, "income_band": null, ...` etc.
    """
    snap: dict[str, Any] = {}
    for f in _TRACKED_FIELDS:
        v = getattr(profile, f, None)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, list) and not v:
            # Explicit empty health_conditions list IS meaningful ("user said
            # they have nothing"); include it so the LLM won't re-ask.
            if f == "health_conditions":
                snap[f] = []
            continue
        snap[f] = v
    return snap


def _required_remaining(known: dict[str, Any]) -> list[str]:
    """List required slots still missing, in canonical asking order."""
    return [f for f in _REQUIRED_FOR_READY if f not in known]


def _nice_to_have_remaining(known: dict[str, Any], profile: Profile) -> list[str]:
    """List nice-to-have slots still missing. parents_age_max / parents_has_ped
    only count as missing when parents_to_insure is True (or dependents says
    parents)."""
    out: list[str] = []
    parents_in_scope = bool(
        known.get("parents_to_insure") is True
        or (isinstance(known.get("dependents"), str) and "parent" in known["dependents"])
    )
    for f in _NICE_TO_HAVE:
        if f in known:
            continue
        if f in ("parents_age_max", "parents_has_ped") and not parents_in_scope:
            continue
        out.append(f)
    return out


def _format_slot_list(slots: list[str]) -> str:
    """Render a slot list as a bulleted summary the LLM can read."""
    if not slots:
        return "(none)"
    return "\n".join(f"  - {f}: {_SLOT_DESCRIPTIONS.get(f, '')}" for f in slots)


# KI-199 — Cached preamble = the strictly-invariant chunk that's identical on
# every turn for every session: the base tone/output rules + the SLOT SCHEMA
# section. This is what Gemini cachedContents wraps. Everything else (KNOWN,
# REQUIRED REMAINING, NICE-TO-HAVE, recall) varies per turn / per session and
# is sent inline as a system message in `contents[]`.
_CACHED_PREAMBLE: str = (
    _BASE_SYSTEM_PROMPT
    + "\n\n--- SLOT SCHEMA (the fields you may capture) ---\n"
    + _format_slot_list(list(_SLOT_DESCRIPTIONS.keys()))
)


def _dynamic_profile_block(
    profile: Profile,
    pending_profile_recall: Optional[dict] = None,
) -> str:
    """Build the per-turn dynamic system message: KNOWN, REQUIRED, NICE-TO-HAVE,
    and the optional KI-196 welcome-back recall directive.

    Kept disjoint from _CACHED_PREAMBLE — concatenating the two reproduces the
    exact prompt the previous monolithic `_build_system_prompt` emitted, so
    cached + uncached paths are byte-identical when assembled.
    """
    known = _profile_known(profile)
    required_remaining = _required_remaining(known)
    nice_to_have_remaining = _nice_to_have_remaining(known, profile)

    known_block = (
        json.dumps(known, ensure_ascii=False) if known else "{}"
    )

    recall_block = ""
    if pending_profile_recall:
        recall_summary = pending_profile_recall.get("summary") or {}
        recall_name = pending_profile_recall.get("name") or profile.name or "there"
        recall_block = (
            "\n\n--- WELCOME-BACK GATE (HIGHEST PRIORITY THIS TURN) ---\n"
            f"The user just provided the name '{recall_name}' and a stored profile exists under that name with these prior captures:\n"
            f"  {json.dumps(recall_summary, ensure_ascii=False)}\n"
            "Your ONLY job this turn is to ask warmly whether to continue from that stored profile OR start fresh.\n"
            "Mention 2-3 of the most-distinctive prior captures in your reply so the user can recognise their own profile (e.g. age + city + dependents).\n"
            "Do NOT capture any of those stored fields yourself — emit `\"captures\": {}` and `\"ready_for_recommendations\": false`.\n"
            "Do NOT volunteer the next fact-find question on this turn. Wait for the user's yes / no.\n"
            "Example reply: 'Welcome back, "
            f"{recall_name} — I have a profile under your name from before: "
            "age 29, in metro, looking for first policy. Continue from there or start fresh?'\n"
        )

    return (
        f"KNOWN: {known_block}"
        + "\n(These fields are ALREADY captured. Do NOT re-ask for them. Use the user's name when known.)"
        + "\n\nREQUIRED SLOTS STILL MISSING (you need these before setting ready_for_recommendations=true):\n"
        + _format_slot_list(required_remaining)
        + "\n\nNICE-TO-HAVE SLOTS STILL MISSING (capture if the user volunteers; don't force):\n"
        + _format_slot_list(nice_to_have_remaining)
        + (
            "\n\nYou have enough to recommend now. If the user signals they want options "
            "(\"show me policies\", \"what do you suggest\"), set ready_for_recommendations=true."
            if not required_remaining else
            "\n\nKeep gathering naturally — you don't yet have the minimum required set."
        )
        + recall_block
    )


def _build_system_prompt(
    profile: Profile,
    pending_profile_recall: Optional[dict] = None,
) -> str:
    """Backwards-compatible monolithic prompt builder.

    Retained so any non-Gemini tier (NIM, OR) that doesn't understand
    cachedContents still receives the FULL prompt as a single system
    message — preserving the pre-KI-199 wire shape for those tiers. By
    construction this is byte-identical to (_CACHED_PREAMBLE +
    "\\n\\n" + _dynamic_profile_block(...)).

    KI-196 (ADR-041) — when `pending_profile_recall` is supplied, the brain
    receives an extra directive to ASK the user whether to load the prior
    profile.
    """
    return _CACHED_PREAMBLE + "\n\n" + _dynamic_profile_block(
        profile, pending_profile_recall=pending_profile_recall
    )


# ----------------------------------------------------------------------------
# JSON parsing — response_format=json_object should make this near-zero-fail
# ----------------------------------------------------------------------------

def _parse_brain_json(raw_text: str) -> Optional[dict]:
    """Parse the LLM's JSON-object response.

    With NIM `response_format={"type":"json_object"}` the response is
    guaranteed to be a JSON object. Still defensively handle the rare
    cases where a model emits stray whitespace, code fences, or a
    leading `<think>` block.
    """
    if not raw_text:
        return None
    text = raw_text.strip()
    # Strip `<think>` blocks some models emit despite instructions.
    if "<think>" in text and "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    # Strip code fences.
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```")).strip()
    # Direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # Try to find the first JSON object substring.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

async def drive_sales_brain(
    user_text: str,
    profile: Profile,
    chat_history: list[dict],
    session_id: Optional[str] = None,
    pending_profile_recall: Optional[dict] = None,
) -> SalesBrainResult:
    """Single LLM call per turn — replaces the scripted slot-walker.

    Returns a SalesBrainResult. On LLM error or JSON parse failure,
    `brain_used` is `sales_brain::error:<reason>` and `reply_text` is
    empty. Caller (orchestrator) is responsible for any fallback behavior
    — NO scripted reply lives here.

    KI-196 (ADR-041) — `pending_profile_recall` is the staged welcome-back
    snapshot (see `backend.session_state.SessionState.pending_profile_recall`).
    When non-None, the system prompt adds a high-priority directive to ask
    the user whether to load the stored profile or start fresh, and forbids
    the brain from capturing any of the stored fields itself.
    """
    t0 = time.time()

    # KI-199 — split the system prompt into the fixed-preamble (cacheable on
    # Gemini) and the per-turn dynamic block. The Gemini tier sends the
    # dynamic block as a system message inline + references the cache; the
    # NIM and OR tiers receive the full monolithic prompt assembled below
    # since they don't speak cachedContents.
    dynamic_block = _dynamic_profile_block(
        profile, pending_profile_recall=pending_profile_recall
    )
    system_prompt = _CACHED_PREAMBLE + "\n\n" + dynamic_block

    # Build messages: system + last ~10 chat history turns + user_text
    messages: list[ChatMessage] = [ChatMessage(role="system", content=system_prompt)]
    if chat_history:
        # KI-197 — was [-10:]; trimmed to last 6 turns to reduce prompt size
        # by ~40% per call. 6 is still enough context for conversational
        # follow-ups; older turns rarely influence the next slot to ask.
        for turn in chat_history[-6:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append(ChatMessage(role=role, content=str(content)))
    messages.append(ChatMessage(role="user", content=user_text or ""))

    # Gemini-specific message shape (KI-199): keep ONLY the dynamic block
    # inline as a system message; the fixed preamble lives in cachedContents.
    # When the cache isn't available we fall through and reuse the full
    # `messages` list below.
    gemini_messages: list[ChatMessage] = [
        ChatMessage(role="system", content=dynamic_block)
    ]
    if chat_history:
        for turn in chat_history[-6:]:  # KI-197 — match the messages[] trim
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                gemini_messages.append(ChatMessage(role=role, content=str(content)))
    gemini_messages.append(ChatMessage(role="user", content=user_text or ""))

    # KI-179 (2026-05-15) — 3-tier LLM stack with Google Gemini 2.0 Flash
    # PRIMARY (Tier 0), NIM fast-brain chain Tier 1, OpenRouter free-tier
    # pool Tier 2. Google AI Studio's free tier is 1500 req/day with native
    # JSON mode; quality > NIM/OR free tiers for conversational fact-find.
    #
    # Order: Gemini → NIM → OR → final error.
    # `llm` is rebound to whichever provider served so the empty-reply
    # retry path (KI-169, further down) re-uses the SAME provider that
    # just answered — no re-tier on retry.
    llm = None
    result = None
    served_tier: str = ""  # "gemini" | "nim" | "or"
    gemini_exc: Optional[BaseException] = None
    nim_exc: Optional[BaseException] = None
    or_exc: Optional[BaseException] = None

    # ---- Tier 0 — Google Gemini 2.5 Flash Lite ----
    # KI-183 (2026-05-15) — gemini-2.0-flash is retired for new accounts
    # (HTTP 404 "no longer available to new users"). gemini-2.5-flash-lite
    # is the supported replacement: faster (~900ms vs ~1100ms for 2.5-flash),
    # cleaner JSON-mode output, same free quota.
    gemini_llm = None
    try:
        gemini_llm = get_gemini_llm(model="gemini-2.5-flash-lite", timeout=25.0)
    except Exception as e:  # noqa: BLE001
        gemini_exc = e
        logging.info(
            "sales_brain: Gemini unavailable, skipping to NIM (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )

    if gemini_llm is not None:
        # KI-199 — lazily provision a cachedContents resource for the fixed
        # preamble. The cache is shared across sessions (same key for everyone
        # who hits sales_brain Gemini tier with this preamble + model). On
        # ANY provisioning failure (cache too small, network blip, 4xx, etc.)
        # `create_cache` returns None and we proceed uncached. The chat() path
        # also self-heals on a stale-cache 4xx by retrying without the
        # reference, so a cache-server outage can never break the main path.
        cache_name: Optional[str] = None
        try:
            cache_name = await gemini_llm.create_cache(
                _CACHED_PREAMBLE, ttl_seconds=300
            )
        except Exception as e:  # noqa: BLE001 — fail-safe: never blocking
            logging.info(
                "sales_brain: gemini.create_cache raised %s — proceeding uncached (session=%s): %s",
                type(e).__name__, session_id, str(e)[:200],
            )
            cache_name = None

        # Use cache-aware message shape only when the cache is in play;
        # otherwise fall back to the full monolithic prompt so the LLM never
        # sees a partial preamble.
        if cache_name:
            chat_messages = gemini_messages
        else:
            chat_messages = messages

        try:
            result = await asyncio.wait_for(
                gemini_llm.chat(
                    messages=chat_messages,
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                    response_format={"type": "json_object"},
                    cached_content_name=cache_name,
                ),
                timeout=_TIMEOUT_S,
            )
            llm = gemini_llm
            served_tier = "gemini"
        except asyncio.TimeoutError as e:
            gemini_exc = e
            logging.info(
                "sales_brain: Gemini timed out, falling back to NIM (session=%s)",
                session_id,
            )
        except Exception as e:  # noqa: BLE001
            gemini_exc = e
            # On any non-timeout error from the Gemini call, drop our cache
            # ref proactively so the next turn re-provisions cleanly instead
            # of repeatedly slamming a stale handle. (The provider itself
            # already invalidates on a 4xx whose body names the cache, but
            # 5xx / network errors land here and we want to be conservative.)
            if cache_name:
                try:
                    _gemini_invalidate_cache(
                        getattr(gemini_llm, "model", "gemini-2.5-flash-lite"),
                        _CACHED_PREAMBLE,
                    )
                except Exception:  # noqa: BLE001
                    pass
            logging.info(
                "sales_brain: Gemini raised %s, falling back to NIM (session=%s): %s",
                type(e).__name__, session_id, str(e)[:200],
            )

    # ---- Tier 1 — NIM fast-brain chain ----
    if result is None:
        nim_llm = get_fast_brain_llm()
        try:
            result = await asyncio.wait_for(
                nim_llm.chat(
                    messages=messages,
                    temperature=_TEMPERATURE,
                    max_tokens=_MAX_TOKENS,
                    response_format={"type": "json_object"},
                ),
                timeout=_TIMEOUT_S,
            )
            llm = nim_llm
            served_tier = "nim"
        except asyncio.TimeoutError as e:
            nim_exc = e
            logging.info(
                "sales_brain: NIM timed out, falling back to OpenRouter (session=%s)",
                session_id,
            )
        except Exception as e:  # noqa: BLE001
            nim_exc = e
            logging.info(
                "sales_brain: NIM raised %s, falling back to OpenRouter (session=%s): %s",
                type(e).__name__, session_id, str(e)[:200],
            )

    # ---- Tier 2 — OpenRouter free-tier frontier pool ----
    if result is None:
        or_llm = None
        try:
            or_llm = get_openrouter_llm(chain_name="sales_brain")
        except Exception as e:  # noqa: BLE001 — missing API key etc.
            or_exc = e
            logging.info(
                "sales_brain: OpenRouter unavailable (session=%s): %s: %s",
                session_id, type(e).__name__, str(e)[:200],
            )

        if or_llm is not None:
            try:
                result = await asyncio.wait_for(
                    or_llm.chat(
                        messages=messages,
                        temperature=_TEMPERATURE,
                        max_tokens=_MAX_TOKENS,
                        response_format={"type": "json_object"},
                        models=_OR_MODELS,
                    ),
                    timeout=_TIMEOUT_S,
                )
                llm = or_llm
                served_tier = "or"
            except asyncio.TimeoutError as e:
                or_exc = e
            except Exception as e:  # noqa: BLE001
                or_exc = e

    # ---- All three tiers exhausted — final error ----
    if result is None:
        elapsed = time.time() - t0
        logging.warning(
            "sales_brain ALL TIERS FAILED (session=%s, %.1fs, "
            "gemini_exc=%s, nim_exc=%s, or_exc=%s)",
            session_id, elapsed,
            f"{type(gemini_exc).__name__}" if gemini_exc else "n/a",
            f"{type(nim_exc).__name__}" if nim_exc else "n/a",
            f"{type(or_exc).__name__}" if or_exc else "n/a",
        )
        # Classify reason: timeout-everywhere is its own bucket vs mixed errors.
        all_timeouts = all(
            isinstance(e, asyncio.TimeoutError) for e in (gemini_exc, nim_exc, or_exc)
            if e is not None
        )
        reason_tag = "timeout_all_tiers" if all_timeouts else "llm_error_all_tiers"
        return SalesBrainResult(
            reply_text="",
            captured_updates={},
            ready_for_recommendations=False,
            brain_used=f"sales_brain::error:{reason_tag}",
            error_reason=(
                f"gemini_exc={type(gemini_exc).__name__ if gemini_exc else 'n/a'}; "
                f"nim_exc={type(nim_exc).__name__ if nim_exc else 'n/a'}; "
                f"or_exc={type(or_exc).__name__ if or_exc else 'n/a'}; "
                f"elapsed={elapsed:.1f}s"
            ),
        )

    # Parse the JSON object from the LLM response.
    raw_text = (result.text or "").strip()
    parsed = _parse_brain_json(raw_text)
    served_model = getattr(result, "model", "unknown") or "unknown"

    if parsed is None:
        logging.warning(
            "sales_brain JSON parse failed (session=%s, model=%s, raw=%r)",
            session_id, served_model, raw_text[:300],
        )
        return SalesBrainResult(
            reply_text="",
            captured_updates={},
            ready_for_recommendations=False,
            brain_used=f"sales_brain::error:parse_fail",
            error_reason=f"could_not_parse_json from model={served_model}; raw_head={raw_text[:120]!r}",
        )

    # Extract the three contract keys, tolerating mild key drift.
    reply_text = parsed.get("reply") or parsed.get("response") or parsed.get("message") or ""
    if not isinstance(reply_text, str):
        reply_text = str(reply_text)
    reply_text = reply_text.strip()
    # KI-169 — strip <think>...</think> blocks the LLM may have emitted INSIDE
    # the reply field value (vs. as a preamble to the JSON, which
    # _parse_brain_json handles separately). qwen3-next-80b occasionally emits
    # all of its reasoning inside <think>...</think> in the reply string,
    # leaving nothing user-facing after a strip downstream.
    if reply_text and "<think>" in reply_text:
        reply_text = _REPLY_THINK_BLOCK.sub("", reply_text).strip()

    captures_raw = parsed.get("captures") or parsed.get("captured") or {}
    if not isinstance(captures_raw, dict):
        captures_raw = {}

    ready_raw = parsed.get("ready_for_recommendations")
    if ready_raw is None:
        ready_raw = parsed.get("ready")
    ready_for_recommendations = bool(ready_raw) if ready_raw is not None else False

    # Normalize + validate captures via the deterministic post-processor.
    captured_updates = normalize_captures(captures_raw, profile)

    # KI-169 — empty reply text after <think>-strip: retry ONCE with a
    # stricter reminder before failing. The retry adds a system message
    # forbidding <think> tags + reminds the LLM to put prose in "reply".
    if not reply_text:
        logging.info(
            "sales_brain empty reply after think-strip — retrying once (session=%s, model=%s)",
            session_id, served_model,
        )
        # KI-199 — when the original call used Gemini with a cachedContents
        # ref, the retry must keep the dynamic-only message list AND keep
        # passing the cache (the preamble lives server-side; sending it again
        # inline would be redundant and could trip the cached-vs-inline
        # mutex). For NIM/OR we keep the full monolithic prompt.
        retry_base = (
            gemini_messages
            if served_tier == "gemini" and "cache_name" in locals() and cache_name
            else messages
        )
        retry_messages = list(retry_base) + [
            ChatMessage(
                role="system",
                content=(
                    "REMINDER: Your previous response had an empty or <think>-only reply. "
                    "The 'reply' field MUST contain natural user-facing prose. "
                    "Do NOT include any <think> blocks or internal reasoning in 'reply'. "
                    "Try again with a clean conversational reply."
                ),
            ),
        ]
        retry_kwargs: dict = {
            "messages": retry_messages,
            "temperature": _TEMPERATURE,
            "max_tokens": _MAX_TOKENS,
            "response_format": {"type": "json_object"},
        }
        if served_tier == "gemini" and "cache_name" in locals() and cache_name:
            retry_kwargs["cached_content_name"] = cache_name
        try:
            retry_result = await asyncio.wait_for(
                llm.chat(**retry_kwargs),
                timeout=_TIMEOUT_S,
            )
            retry_parsed = _parse_brain_json((retry_result.text or "").strip())
            if retry_parsed:
                retry_reply = retry_parsed.get("reply") or ""
                if isinstance(retry_reply, str):
                    retry_reply = retry_reply.strip()
                    if "<think>" in retry_reply:
                        retry_reply = _REPLY_THINK_BLOCK.sub("", retry_reply).strip()
                    if retry_reply:
                        # Merge new captures from retry on top of first attempt
                        retry_captures = retry_parsed.get("captures") or {}
                        if isinstance(retry_captures, dict):
                            merged = dict(captures_raw)
                            merged.update(retry_captures)
                            captured_updates = normalize_captures(merged, profile)
                        retry_ready = retry_parsed.get("ready_for_recommendations")
                        if retry_ready is not None:
                            ready_for_recommendations = bool(retry_ready)
                        retry_model = getattr(retry_result, "model", served_model) or served_model
                        return SalesBrainResult(
                            reply_text=retry_reply,
                            captured_updates=captured_updates,
                            ready_for_recommendations=ready_for_recommendations,
                            brain_used=f"sales_brain::{served_tier}:{retry_model}::retry",
                            raw_json=retry_parsed,
                            error_reason=None,
                        )
        except (asyncio.TimeoutError, Exception) as retry_exc:  # noqa: BLE001
            logging.warning(
                "sales_brain retry also failed (session=%s): %s",
                session_id, type(retry_exc).__name__,
            )
        # Retry failed — bubble up to orchestrator
        return SalesBrainResult(
            reply_text="",
            captured_updates=captured_updates,
            ready_for_recommendations=ready_for_recommendations,
            brain_used="sales_brain::error:empty_reply",
            raw_json=parsed,
            error_reason=f"reply_field_empty from model={served_model} (retry also empty)",
        )

    return SalesBrainResult(
        reply_text=reply_text,
        captured_updates=captured_updates,
        ready_for_recommendations=ready_for_recommendations,
        brain_used=f"sales_brain::{served_tier}:{served_model}",
        raw_json=parsed,
        error_reason=None,
    )


__all__ = ["SalesBrainResult", "drive_sales_brain"]
