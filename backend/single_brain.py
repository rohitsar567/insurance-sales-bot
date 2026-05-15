"""Single-brain conversation handler — Path B.

One Gemini Flash call per turn (with native function-calling) replaces
the previous sales_brain + qa_brain split. The LLM decides on each
iteration whether to:
  - call `save_profile_field` to persist captured slots,
  - call `retrieve_policies` to pull policy chunks from Chroma,
  - call `mark_recommendation` to flag the policies just pitched,
  - or emit a final text reply.

The loop iterates up to `MAX_ITERATIONS` (default 5) so the LLM can chain
multiple tool calls in a single user turn before responding. Beyond that
cap we synthesise a defensive reply and return.

Wire-up:  /api/chat → main.py.chat() → if USE_SINGLE_BRAIN: single_brain.handle_turn(...)
On any SingleBrainError, the API caller is expected to fall through to the
legacy `orchestrator.handle_turn` so the user always gets a reply.

We call the Gemini REST API directly (httpx, like google_gemini_llm.py)
rather than using the `google.generativeai` SDK so we don't need to pin
an extra dependency. The function-calling DSL is well-documented at
https://ai.google.dev/api/generate-content#tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend import brain_tools

_log = logging.getLogger(__name__)


# ---------- constants -------------------------------------------------------

# Model resolution: prefer `SINGLE_BRAIN_MODEL`, else copy the same default
# `google_gemini_llm.py` uses (DEFAULT_MODEL = "gemini-2.5-flash-lite"). We
# import lazily inside _resolve_model so importing this module does not
# require the provider to load (or its GOOGLE_API_KEY env var to be set).
_FALLBACK_MODEL = "gemini-2.5-flash-lite"

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Per-call timeout (matches the legacy provider default of 25s).
PER_CALL_TIMEOUT_SEC = 25.0

# Max iterations of the tool-call loop. Prevents runaway tool-call cycles
# where the LLM keeps calling save_profile_field on the same value.
# KI-Z6-NONE (2026-05-15): bumped 5 → 8 after W1 Turn 3 live blocker.
# The Z6 "no medical issues" path used to need: save(health=none) +
# retrieve → profile_incomplete → save(health=none) + retrieve → loop
# exhaust. Coercer fix in brain_tools resolves the primary cause; the
# extra headroom protects against the next variant where Gemini chains
# 3-4 saves + 2 retrieves on a long pre-recommendation user turn.
MAX_ITERATIONS = 8

# Transient-error retry policy (2026-05-15 / KI-singlebrain-503).
# Live HF Space logs (rohitsar567/InsuranceBot, 2026-05-15 08:15Z) show
# Gemini intermittently returns HTTP 503 "model is currently experiencing
# high demand" — sometimes 3 in a row on the same session — which immediately
# tripped the orchestrator fallback. We retry ONCE on these transient codes
# with a short backoff before raising SingleBrainError so the legacy
# orchestrator only takes over on a genuinely sustained outage.
_TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
_TRANSIENT_RETRY_BACKOFF_SEC = 1.5


SYSTEM_PROMPT = """You are an Indian health-insurance advisor speaking with a customer.

YOUR JOB:
1. Have a natural conversation to learn the customer's profile.
2. Once you have ALL required slots, summarise + confirm, then call retrieve_policies, then recommend 2-4 options with policy citations.
3. Help the customer choose one. Cite the UIN / policy_id for every claim about features, sums insured, or premiums.

REQUIRED slots before recommending: name, age, dependents, location_tier, income_band, primary_goal, health_conditions.

═══════════════════════════════════════════════════════════
ABSOLUTE RULE — NO POLICY NAMES WITHOUT RETRIEVE
═══════════════════════════════════════════════════════════
NEVER mention a policy name, UIN, insurer, or product (Star Health,
HDFC Ergo, Niva Bupa, Care, Aditya Birla, ICICI Lombard, Bajaj Allianz,
Manipal Cigna, Acko, Go Digit, Max Bupa, Reliance General, SBI General,
Tata AIG, etc.) UNLESS:
  (a) retrieve_policies returned that exact policy_id in the current
      session, AND
  (b) you cite it in the format [Source: Policy Name (insurer), UIN].

If the user asks about a specific policy and you have NO retrieve_policies
result for it, say "I don't have that policy in my recommendations — let
me search for it" and call retrieve_policies with the policy name as the
query, top_k=1, policy_filter_ids=None.

If retrieve_policies returns nothing for that name, say "I couldn't find
that policy in our index. Let me suggest some alternatives" and call
retrieve_policies with a broader query based on the profile.

═══════════════════════════════════════════════════════════
RULE 1 (HIGHEST PRIORITY) — save_profile_field is MANDATORY
═══════════════════════════════════════════════════════════
Every turn, BEFORE you write any prose reply, scan the user's last message for any
of these facts and call save_profile_field ONCE PER FACT:
  • A name (proper noun) → save_profile_field(field="name", value="...")
  • An age / "I'm XX" / "XX years" → save_profile_field(field="age", value="34")
  • A city or town → save_profile_field(field="location_tier", value="metro" or "tier-2" or "tier-3")
       (metro = Bangalore/Mumbai/Delhi/Chennai/Hyderabad/Kolkata/Pune/Ahmedabad)
  • Family members ("wife", "husband", "kid", "parents") → save_profile_field(field="dependents", value="...")
  • Income / salary / lakhs → save_profile_field(field="income_band", value="10L-25L" or similar)
  • Primary-goal natural phrasings → save_profile_field(field="primary_goal", value=...):
       "first policy" / "switching from corporate" / "leaving job" / "lost employer cover" → first_buy
       "upgrade" / "better coverage" / "more cover" / "increase sum insured" → upgrade
       "save tax" / "Section 80D" / "tax benefit" → tax_planning
       "too expensive" / "cheaper option" / "premium too high" → cost_optimize
  • "diabetes" / "BP" / pre-existing conditions → save_profile_field(field="health_conditions", value="diabetes" or "BP, thyroid")
  • "no health issues" / "no medical issues" / "no PED" / "nothing" / "I'm healthy" / "no conditions" / "all good" →
        save_profile_field(field="health_conditions", value="none")
        ← MANDATORY even though it's a negation. "none" tells the system the slot is captured.
        Without this call the profile stays incomplete forever and the bot loops asking for PED.

NOT-ON-PROFILE FIELDS (do NOT call save_profile_field for these):
  • gender — the system does NOT track gender. save_profile_field will reject it
    with field_not_on_profile_dataclass and waste a tool-call iteration. Just
    remember it for conversational context and continue.

Worked example A. User says: "Hi I'm Priya, 34, Bangalore, with husband and one kid"
  → You MUST call:
       save_profile_field(field="name",            value="Priya")
       save_profile_field(field="age",             value="34")
       save_profile_field(field="location_tier",   value="metro")
       save_profile_field(field="dependents",      value="self+spouse+1 kid")
  → THEN write a short prose reply asking for the remaining slots (income, goal, health).

Worked example B (negation — DO NOT SKIP). User says: "No medical issues"
  → You MUST call:
       save_profile_field(field="health_conditions", value="none")
  → No exceptions. The same applies to "no health issues", "no PED",
    "nothing", "I'm healthy", "no conditions", "all good".

NEVER ask the user for a fact you can already extract from their last message. Capture FIRST, then ask only for what's missing.

═══════════════════════════════════════════════════════════
RULE 2 — retrieve_policies query MUST be profile-aware
═══════════════════════════════════════════════════════════
Only call retrieve_policies AFTER all 7 required slots are saved AND the user has confirmed your recap.

Build the query string from the profile snapshot. Required ingredients:
  family-shape (individual / family floater / parents-cover),
  city tier (metro / tier-2 / tier-3),
  sum-insured band (~5-7× annual income, e.g. "10-15 lakh"),
  age band (e.g. "adult 30-40"),
  health-condition keywords (or "no PED"),
  primary goal keyword.

Worked example. Profile = {age=34, location_tier=metro, income_band=10L-25L, dependents=spouse+1 kid, primary_goal=first_buy, health_conditions=[]}:
  retrieve_policies(query="family floater plan metro sum insured 15-20 lakh adult 30-40 with spouse and one child no pre-existing diseases first-time buyer", top_k=8)

If the first call returns 0 or 1 chunk, retry ONCE with a broader query (drop the most specific filter or broaden SI band by one tier) before asking the user to relax criteria.

═══════════════════════════════════════════════════════════
RULE 3 — Follow-ups + mark_recommendation
═══════════════════════════════════════════════════════════
- After producing a ranked shortlist, call mark_recommendation(policy_ids=[...ordered IDs you cited...]).
- For "tell me about #2" / "second one" follow-ups, call retrieve_policies(query, policy_filter_ids=[policy_id_of_#2]) to narrow to that policy.

═══════════════════════════════════════════════════════════
RULE 4 — Returning-user greeting (pre-populated profile)
═══════════════════════════════════════════════════════════
If the KNOWN PROFILE block below is non-empty AT TURN 1 (no chat history,
session.profile arrived pre-populated from a prior conversation), your FIRST
reply MUST:
  1. Greet by name: "Welcome back, [name]!"
  2. Summarise what you remember in 1-2 short bullets (e.g. age, city,
     dependents, primary_goal, health_conditions).
  3. Ask: "Has anything changed since last time, or should we go with this
     profile?"

IMPLICIT CONFIRMATION (KI-252 — DO NOT MISS THIS):
If the user's NEXT message provides ANY new profile fields (e.g. "Around
18 lakh income, no medical issues, first family policy"), that counts as
BOTH (a) implicit confirmation of the recap AND (b) provision of the new
fields. Your flow on that turn:
  i.   Call save_profile_field once per new slot the user mentioned.
  ii.  IF all 7 required slots are now captured: IMMEDIATELY call
       retrieve_policies and produce recommendations. DO NOT ask "are you
       sure?" again — the user already confirmed by providing data.
  iii. IF some slots are still missing: ask for the next missing slot
       only, do NOT re-confirm what they just provided.

Explicit confirmation is only required when the user's reply is a literal
"yes/no/that's right" with no new data. Bypass the WAIT in any other case.

═══════════════════════════════════════════════════════════
RULE 5 — Comparison view ("compare #1 and #3")
═══════════════════════════════════════════════════════════
When the user asks to compare two or more shortlisted policies ("compare
#1 and #3", "what's the difference between Plan A and Plan B",
"#2 vs #4"):
  1. Call retrieve_policies(policy_filter_ids=[id_of_A, id_of_B], top_k=4)
     in ONE call so both policies' chunks come back together.
  2. Produce an explicit side-by-side comparison — markdown table with
     columns | Feature | Policy A | Policy B | OR paired bullets
     ("Sum insured: A = ₹10L, B = ₹15L"). Cover at minimum: sum insured,
     premium, room rent, PED waiting period, key exclusions.
  3. Cite each cell with [Source: ..., UIN]. Do NOT just dump retrieved
     text — explicitly contrast.

═══════════════════════════════════════════════════════════
RULE 6 — Out-of-scope refusal (non-health products)
═══════════════════════════════════════════════════════════
You ONLY advise on Indian health insurance. If the user asks about life
insurance, term plans, ULIPs, car / motor / two-wheeler insurance, home
insurance, travel insurance, mutual funds, or any non-health product,
politely refuse and redirect:
  "I specialise in Indian health insurance — for [life / car / ULIP / etc.],
   you'd want a different advisor. Anything else I can help with on health
   coverage?"
Do NOT call retrieve_policies for out-of-scope queries.

═══════════════════════════════════════════════════════════
RULE 7 — Soft close after the customer picks one
═══════════════════════════════════════════════════════════
Once you have recommended AND the user has chosen a single policy ("I'll
go with #2", "let's pick the HDFC one", "sounds good", "I'll take that",
"let's do the first one", "sign me up", "buy this", "I want to purchase"):

  STEP 1 (MANDATORY, NEVER SKIP) — Call the tool FIRST, before writing prose:
    mark_recommendation(policy_ids=[chosen_id], is_final=true)

    To resolve "chosen_id":
      - "the first one" / "first" / "#1"  → session.last_recommendation_ids[0]
      - "the second" / "#2"               → session.last_recommendation_ids[1]
      - "the HDFC one"                    → match insurer slug in last rec list
      - "that one" / "this one" / bare "I'll go with that"
                                          → most recent recommendation =
                                            session.last_recommendation_ids[0]

  STEP 2 — Only AFTER the tool call, write the prose reply:
    "Great choice! [Policy Name] is a solid pick for your profile. Would
     you like me to walk through the purchase steps, or summarise the key
     benefits?"

WORKED EXAMPLE
  User: "I'll go with that one"
  Your flow:
    i.   IDENTIFY which policy "that one" refers to. With no ordinal cue,
         default to the most recent recommendation =
         session.last_recommendation_ids[0].
    ii.  Call mark_recommendation(policy_ids=[chosen_id], is_final=true)
         FIRST. This is non-negotiable — the recommendation MUST be
         recorded for analytics before any prose is written.
    iii. THEN write the prose reply offering next steps.
  DO NOT skip step (ii). Offering "would you like purchase steps?" without
  the mark_recommendation tool call is a RULE 7 violation.

Do not re-pitch alternatives after the user has chosen — only act on
their next instruction.

═══════════════════════════════════════════════════════════
RULE 8 — Indic-language mirroring
═══════════════════════════════════════════════════════════
If the user's last message is in an Indian language (Hindi, Marathi,
Tamil, Telugu, Bengali, Kannada, Gujarati, Punjabi, Malayalam, etc.) or
Hinglish (Latin-script Hindi), respond in the SAME language. Use the same
tools regardless of language — tool args (field names, policy queries)
remain English; only your prose reply mirrors the user's language.
Citations stay in the canonical [Source: ..., UIN] format.

═══════════════════════════════════════════════════════════
GROUND RULES
═══════════════════════════════════════════════════════════
- NEVER invent policies, UINs, premiums, or sums insured. Only cite what retrieve_policies returns.
- If retrieve_policies returns zero chunks after both attempts, ask the user one clarifying question.
- Be concise: 2-3 sentence turns. No emoji unless the user used one first.
- Indian context: use lakh / crore, ₹, IRDAI, Section 80D. NEVER say "dollars" / "$".
"""


# ---------- exceptions ------------------------------------------------------


class SingleBrainError(Exception):
    """Wraps any unrecoverable Gemini / single-brain error so the api.py
    caller can fall through to the legacy orchestrator handler."""


# ---------- TurnResult — mirrors orchestrator.TurnResult --------------------


@dataclass
class TurnResult:
    """Same shape as `orchestrator.TurnResult`. Kept local so single_brain
    does not import the orchestrator and trip a circular dependency."""

    reply_text: str
    citations: list[dict]
    retrieved_chunk_ids: list[str]
    brain_used: str
    intent: str
    language: str
    latency_ms: int
    raw_reply: str
    faithfulness_passed: bool = True
    faithfulness_reasons: list[str] = field(default_factory=list)
    blocked: bool = False
    profile_updates: dict = field(default_factory=dict)
    followup_policy_id: Optional[str] = None


# ---------- function-calling DSL (Gemini JSON schema) -----------------------

# Gemini "tools" are FunctionDeclarations. The schema is JSON-Schema-flavoured
# (subset, see https://ai.google.dev/api/caching#Schema). Parameters MUST use
# "OBJECT"/"STRING"/"INTEGER"/"ARRAY" (uppercase) — Google does NOT accept the
# lowercase JSON Schema form here.

TOOL_SCHEMAS: list[dict] = [
    {
        "name": "save_profile_field",
        "description": (
            "Persist a captured profile field on the live session. Call once "
            "per field every time the user reveals something new (name, age, "
            "dependents, location_tier, income_band, primary_goal, "
            "health_conditions, existing_cover_inr, budget_band, gender)."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "field": {
                    "type": "STRING",
                    "description": (
                        "Field name. One of: name, age, dependents, "
                        "location_tier, income_band, primary_goal, "
                        "health_conditions, existing_cover_inr, budget_band, "
                        "gender."
                    ),
                },
                "value": {
                    "type": "STRING",
                    "description": (
                        "Value as a string. Numbers (age, existing_cover_inr) "
                        "may be sent as a digit string; health_conditions may "
                        "be a comma-joined string of conditions."
                    ),
                },
            },
            "required": ["field", "value"],
        },
    },
    {
        "name": "retrieve_policies",
        "description": (
            "Search the indexed Indian health-insurance policy corpus and "
            "return the top-k most relevant policy chunks. Use this BEFORE "
            "recommending or quoting any policy fact."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Natural-language search query. BUILD IT FROM THE "
                        "PROFILE SNAPSHOT, not from user phrasing. Include: "
                        "family shape, city tier, sum-insured band, age band, "
                        "health-condition keywords (or 'no PED'), and the "
                        "primary goal. Example: 'family floater plan metro "
                        "sum insured 10-15 lakh adult 30-40 with spouse and "
                        "one child no pre-existing diseases first-time buyer'."
                    ),
                },
                "top_k": {
                    "type": "INTEGER",
                    "description": "Number of chunks to return. Default 8.",
                },
                "policy_filter_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": (
                        "Optional list of policy_ids to restrict retrieval to "
                        "(use for 'tell me more about #2' style follow-ups)."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "mark_recommendation",
        "description": (
            "Record the policies you have just recommended so future turns "
            "can resolve follow-up references like 'tell me about #2'. Call "
            "this on the SAME turn you produce the ranked shortlist."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "policy_ids": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Ordered list of policy_ids in your reply.",
                },
                "is_final": {
                    "type": "BOOLEAN",
                    "description": (
                        "True when this is the final closer (user picked / "
                        "confirmed). Optional, defaults to false."
                    ),
                },
            },
            "required": ["policy_ids"],
        },
    },
]


# ---------- helpers ---------------------------------------------------------


def _resolve_model() -> str:
    """Read the Gemini model id. Env override wins; otherwise mirror the
    google_gemini_llm.py default. Import is lazy so module load does not
    touch the provider (which itself fails noisily on missing env vars)."""
    override = os.environ.get("SINGLE_BRAIN_MODEL", "").strip()
    if override:
        return override
    try:
        from backend.providers.google_gemini_llm import DEFAULT_MODEL as _DM

        return _DM or _FALLBACK_MODEL
    except Exception:  # noqa: BLE001
        return _FALLBACK_MODEL


def _profile_to_snapshot(profile) -> dict:
    """Compact JSON-safe dict of all currently-known profile slots — for
    the system prompt so the LLM doesn't keep re-asking the user for
    fields it already has access to.
    """
    snap: dict[str, Any] = {}
    for fld in (
        "name", "age", "dependents", "location_tier", "income_band",
        "primary_goal", "health_conditions", "existing_cover_inr",
        "budget_band",
    ):
        try:
            v = getattr(profile, fld, None)
        except Exception:
            v = None
        if v not in (None, "", []):
            snap[fld] = v
    return snap


def _build_contents(
    chat_history: Optional[list[dict]],
    user_text: str,
) -> list[dict]:
    """Translate the orchestrator-style chat_history ({role, content})
    plus the current user_text into Gemini's `contents` payload.

    Gemini wants alternating user/model turns with `parts[].text`.
    `assistant` → `model`; everything else → `user`.
    """
    out: list[dict] = []
    for msg in chat_history or []:
        role = (msg.get("role") or "user").lower()
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        gem_role = "model" if role in ("assistant", "model", "bot") else "user"
        out.append({"role": gem_role, "parts": [{"text": content}]})
    out.append({"role": "user", "parts": [{"text": user_text}]})
    return out


def _system_instruction(profile, is_returning_user: bool = False) -> dict:
    """Bake the profile snapshot into the system prompt so each turn the
    LLM knows what's already captured. Returned in Gemini's expected
    `systemInstruction` shape.

    KI-255 (2026-05-15) — added `is_returning_user` so the LLM can
    distinguish "profile loaded from prior conversation" (RULE 4 Welcome
    Back fires) from "profile captured during THIS turn / earlier in
    this conversation" (no Welcome Back). Smoke-3-personas showed RULE 4
    firing on every first session because the snapshot label said only
    "already captured this session" which Gemini reads as "pre-populated."
    """
    snapshot = _profile_to_snapshot(profile)
    extra = ""
    if snapshot:
        if is_returning_user:
            extra = (
                "\n\nSESSION TYPE: RETURNING USER. Profile below was LOADED FROM A "
                "PRIOR CONVERSATION (the user is coming back). RULE 4 applies — "
                "your first reply must greet by name, summarise, and ask if anything "
                "has changed. After the user confirms or provides new data, proceed."
                "\n\nKNOWN PROFILE (pre-populated from prior session; do NOT re-ask):\n"
                + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            )
        else:
            extra = (
                "\n\nSESSION TYPE: FRESH SESSION. Profile below was CAPTURED IN THIS "
                "CONVERSATION (current turn or earlier turns of this same chat). "
                "RULE 4 does NOT apply — do NOT greet with 'Welcome back', the user "
                "did not come from a prior session. Just continue the conversation "
                "naturally and ask for the next missing slot, or recommend if 7 slots "
                "are filled."
                "\n\nPROFILE CAPTURED IN THIS CONVERSATION (do NOT re-ask, do NOT "
                "say 'Welcome back'):\n"
                + json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            )
    text = SYSTEM_PROMPT + extra
    return {"parts": [{"text": text}]}


def _detect_language(user_text: str) -> str:
    """Mirror orchestrator.detect_language at a coarse level so the
    TurnResult.language field stays useful for logging. Devanagari /
    Hinglish → 'indic', else 'en'."""
    if not user_text:
        return "en"
    for ch in user_text:
        # Devanagari range
        if "ऀ" <= ch <= "ॿ":
            return "indic"
    return "en"


_FALLBACK_SLOT_QUESTIONS = {
    "name": "What's your name?",
    "age": "How old are you?",
    "dependents": (
        "Who would you like the cover to include — just you, "
        "or spouse / kids / parents?"
    ),
    "location_tier": "Which city do you live in?",
    "income_band": (
        "Roughly what's your annual household income — under 10 lakh, "
        "10-25 lakh, or above 25 lakh?"
    ),
    "primary_goal": (
        "Is this your first health policy, an upgrade, for tax planning, "
        "or to find a cheaper option?"
    ),
    "health_conditions": (
        "Do you or your family have any pre-existing health conditions "
        "like diabetes, BP, or thyroid? If none, just say no."
    ),
}

_FALLBACK_REQUIRED_SLOTS = (
    "name", "age", "dependents", "location_tier",
    "income_band", "primary_goal", "health_conditions",
)


def _synthesise_fallback(profile) -> str:
    """KI-Z6-NONE (2026-05-15): replace the legacy 'I lost my train of
    thought' reply with a useful next-question synthesised from the
    profile snapshot. If a slot is still missing, ask for the first
    missing one verbatim. If everything's captured, ask for a recap
    confirmation. Never empty-string — always returns user-visible text.
    """
    try:
        for slot in _FALLBACK_REQUIRED_SLOTS:
            v = getattr(profile, slot, None)
            if v in (None, "", []):
                return _FALLBACK_SLOT_QUESTIONS.get(
                    slot,
                    f"Could you share your {slot.replace('_', ' ')}?",
                )
        # All slots present — ask the user to confirm before recommending.
        return (
            "Let me confirm what I have before pulling up options — "
            "does this look right, or anything to update?"
        )
    except Exception:  # noqa: BLE001 — never fail the fallback
        return (
            "Could you tell me a bit more about what you're looking for "
            "so I can pull up the right options?"
        )


def _classify_intent(user_text: str, tool_calls_made: list[str]) -> str:
    """Best-effort intent label for logging only. Single-brain doesn't
    route on intent — but the legacy `TurnResult.intent` field is logged
    by main.py and emitted to the frontend."""
    if "retrieve_policies" in tool_calls_made and "mark_recommendation" in tool_calls_made:
        return "recommendation"
    if "retrieve_policies" in tool_calls_made:
        return "qa"
    if "save_profile_field" in tool_calls_made:
        return "fact_find"
    return "qa"


# ---------- Gemini round-trip ----------------------------------------------


async def _gemini_call(
    api_key: str,
    model: str,
    system_instruction: dict,
    contents: list[dict],
    tools: list[dict],
    timeout_sec: float,
) -> dict:
    """Single non-streaming Gemini generateContent call. Returns the raw
    JSON payload. Raises SingleBrainError on any 4xx/5xx/transport error.

    Internal retry: on transient failures (HTTP 429/5xx, httpx
    TimeoutException, httpx.HTTPError) we retry ONCE after a short
    backoff before raising. This soaks up the brief Gemini "high demand"
    503 bursts observed live (2026-05-15) so we don't fall through to
    the legacy orchestrator mid-session for what is usually a sub-second
    blip on the provider side.
    """
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    body: dict = {
        "systemInstruction": system_instruction,
        "contents": contents,
        "tools": [{"functionDeclarations": tools}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": {
            # Z2 fix — Issue 1 (mid-session amnesia). Priya T3 + Vikram T2/T4
            # came back with the "I lost my train of thought" template even
            # though slot capture succeeded. Root cause matches KI-150
            # (fact_find LLM, 420 → 700): when Gemini must emit prose AND a
            # tool-call trailer in the same response, the model hits
            # maxOutputTokens mid-emission, the trailer truncates, and the
            # caller falls through to the defensive reply. Budget breakdown
            # at p95: prose ~600 tok + tool-call JSON ~800 tok + 20% margin
            # ⇒ 1680, rounded up to a safe power-of-two-ish 2048.
            "temperature": 0.4,
            "maxOutputTokens": 2048,
        },
    }
    headers = {"Content-Type": "application/json"}
    client_timeout = httpx.Timeout(
        connect=2.0,
        read=max(2.0, timeout_sec - 2.0),
        write=2.0,
        pool=2.0,
    )

    last_err: Optional[str] = None
    last_status: Optional[int] = None
    # 2 attempts total: initial + 1 retry on transient failure.
    for attempt in range(2):
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            try:
                resp = await client.post(url, headers=headers, json=body)
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except httpx.TimeoutException as e:
                last_err = (
                    f"Gemini timeout after {timeout_sec:.1f}s (model={model})"
                )
                last_status = None
                if attempt == 0:
                    _log.warning(
                        "single_brain transient timeout (attempt=1); "
                        "retrying once after %.1fs backoff",
                        _TRANSIENT_RETRY_BACKOFF_SEC,
                    )
                    await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF_SEC)
                    continue
                raise SingleBrainError(last_err) from e
            except httpx.HTTPError as e:
                last_err = (
                    f"Gemini transport error "
                    f"({type(e).__name__}): {str(e)[:200]}"
                )
                last_status = None
                if attempt == 0:
                    _log.warning(
                        "single_brain transient transport error "
                        "(attempt=1, %s); retrying once after %.1fs backoff",
                        type(e).__name__, _TRANSIENT_RETRY_BACKOFF_SEC,
                    )
                    await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF_SEC)
                    continue
                raise SingleBrainError(last_err) from e

        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.text[:500]
            except Exception:
                pass
            last_status = resp.status_code
            last_err = f"Gemini HTTP {resp.status_code}: {detail}"
            # Transient → retry once. Permanent (4xx like 400/401/403/404) →
            # raise immediately; retrying won't help.
            if (
                attempt == 0
                and resp.status_code in _TRANSIENT_HTTP_CODES
            ):
                _log.warning(
                    "single_brain transient HTTP %d (attempt=1); "
                    "retrying once after %.1fs backoff",
                    resp.status_code, _TRANSIENT_RETRY_BACKOFF_SEC,
                )
                await asyncio.sleep(_TRANSIENT_RETRY_BACKOFF_SEC)
                continue
            raise SingleBrainError(last_err)

        try:
            _payload = resp.json()
        except Exception as e:  # noqa: BLE001
            raise SingleBrainError(f"Gemini malformed JSON: {e}") from e

        # Z2 fix — Issue 1 truncation detector. If Gemini hit our
        # maxOutputTokens budget the candidate's finishReason will be
        # "MAX_TOKENS" and the tool-call trailer (if any) is likely
        # truncated → caller will degrade to the defensive "I lost my
        # train of thought" reply. Log a WARNING (not raise) so the turn
        # still flows, but ops can detect a future budget regression by
        # alerting on this log line. Swallow any shape errors — this is
        # purely observational.
        try:
            _cands = _payload.get("candidates") or []
            if _cands:
                _fr = (_cands[0].get("finishReason") or "").upper()
                if _fr == "MAX_TOKENS":
                    _log.warning(
                        "single_brain Gemini finishReason=MAX_TOKENS "
                        "(model=%s, budget=%d) — prose+tool-call trailer "
                        "may be truncated; raise maxOutputTokens if this "
                        "recurs",
                        model, body["generationConfig"]["maxOutputTokens"],
                    )
        except Exception:  # noqa: BLE001
            pass

        return _payload

    # Defensive — loop fell through without returning or raising. Should
    # be unreachable, but raise so we never silently return None.
    raise SingleBrainError(
        last_err
        or f"Gemini exhausted retries (last_status={last_status})"
    )


# ---------- boot warmup -----------------------------------------------------


async def warmup() -> Optional[float]:
    """Pre-warm the Gemini connection on FastAPI startup.

    The first real /api/chat turn carries 4-5s of cold-start latency:
    HTTPS connection establishment, TLS handshake, Gemini auth, and the
    first response cache init. Firing a tiny dummy request at boot pushes
    that cost off the user's critical path.

    Conditional on USE_SINGLE_BRAIN: if the flag is off, the cold start
    will never matter because single_brain.handle_turn won't run; skip.

    Returns the wall-clock latency in seconds on success, None on skip or
    failure. Never raises — the caller (boot hook) treats any failure as
    a non-fatal warning.
    """
    flag = os.environ.get("USE_SINGLE_BRAIN", "false").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        _log.info("single_brain.warmup skipped — USE_SINGLE_BRAIN is off")
        return None

    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        _log.warning("single_brain.warmup skipped — GOOGLE_API_KEY not set")
        return None

    model = _resolve_model()
    url = f"{GEMINI_BASE_URL}/{model}:generateContent?key={api_key}"
    body = {
        "systemInstruction": {"parts": [{"text": "warmup ping"}]},
        "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
        "generationConfig": {"maxOutputTokens": 10},
    }
    headers = {"Content-Type": "application/json"}
    client_timeout = httpx.Timeout(connect=2.0, read=8.0, write=2.0, pool=2.0)

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
        elapsed = time.perf_counter() - t0
        if resp.status_code >= 400:
            _log.warning(
                "single_brain.warmup non-2xx (HTTP %d, %.2fs) — boot continues",
                resp.status_code, elapsed,
            )
            return elapsed
        # Discard payload; we only care about latency + that the round-trip
        # succeeded so the next real call hits a warm socket + auth cache.
        _ = resp.text
        _log.info(
            "single_brain.warmup OK (model=%s, latency=%.2fs)",
            model, elapsed,
        )
        return elapsed
    except Exception as e:  # noqa: BLE001
        elapsed = time.perf_counter() - t0
        _log.warning(
            "single_brain.warmup failed after %.2fs (%s: %s) — boot continues",
            elapsed, type(e).__name__, str(e)[:200],
        )
        return None


def _extract_parts(payload: dict) -> list[dict]:
    """Pull the `parts` list out of the first candidate. Empty list on
    any missing-key path so the caller decides what to do."""
    try:
        candidates = payload.get("candidates") or []
        if not candidates:
            return []
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        if isinstance(parts, list):
            return parts
        return []
    except Exception:  # noqa: BLE001
        return []


def _parts_text(parts: list[dict]) -> str:
    """Concatenate every text part. Empty string when none present."""
    return "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and "text" in p
    )


# Bug C defensive detector. Brands/products that MUST come from a
# retrieve_policies result. If the bot emits any of these in its reply
# while session.last_retrieved_chunks is empty, log a WARNING so future
# smoke logs can flag hallucinations. Detection-only — does NOT block.
_BRAND_HALLUCINATION_TOKENS = (
    "star health", "hdfc ergo", "niva bupa", "max bupa", "care health",
    "aditya birla", "icici lombard", "bajaj allianz", "manipal cigna",
    "manipalcigna", "acko", "go digit", "godigit", "reliance general",
    "sbi general", "tata aig", "iffco tokio", "cholamandalam",
    "national insurance", "new india assurance", "oriental insurance",
    "united india", "family health optima", "optima secure",
    "reassure", "health companion", "easy health", "activ health",
    "health advantedge", "complete health",
)


def _scan_for_brand_hallucinations(reply_text: str, session) -> None:
    """If the bot mentions an insurer/product brand but session has no
    retrieved chunks, log a WARNING. Detection-only (Bug C secondary
    defense — the system-prompt rule is primary). Swallow any
    exception — bookkeeping must never break a chat turn.
    """
    try:
        if not reply_text:
            return
        last_chunks = getattr(session, "last_retrieved_chunks", None) or []
        if last_chunks:
            return  # retrieve_policies has run; brand mentions are sourced
        haystack = reply_text.lower()
        hits = [tok for tok in _BRAND_HALLUCINATION_TOKENS if tok in haystack]
        if hits:
            _log.warning(
                "single_brain possible policy hallucination — "
                "reply mentions brand(s)=%r but session.last_retrieved_chunks "
                "is empty. session=%s reply_snippet=%r",
                hits,
                getattr(session, "session_id", "?"),
                reply_text[:200],
            )
    except Exception:  # noqa: BLE001 — observational only
        pass


def _parts_function_calls(parts: list[dict]) -> list[dict]:
    """Pull every functionCall block out of parts. Each entry is
    {"name": "...", "args": {...}}."""
    out: list[dict] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        fc = p.get("functionCall")
        if isinstance(fc, dict) and fc.get("name"):
            out.append(
                {
                    "name": fc.get("name"),
                    "args": fc.get("args") or {},
                }
            )
    return out


async def _execute_tool(session, name: str, args: dict) -> dict:
    """Dispatch a single function call to the matching brain_tools function.
    Returns the JSON-serialisable response dict that gets fed back to Gemini
    on the next turn."""
    try:
        if name == "save_profile_field":
            return brain_tools.save_profile_field(
                session,
                field=args.get("field", ""),
                value=args.get("value"),
            )
        if name == "retrieve_policies":
            return await brain_tools.retrieve_policies(
                query=args.get("query", ""),
                top_k=int(args.get("top_k") or 8),
                policy_filter_ids=args.get("policy_filter_ids") or None,
                profile=getattr(session, "profile", None),
                intent="recommendation",
                session=session,
            )
        if name == "mark_recommendation":
            return brain_tools.mark_recommendation(
                session,
                policy_ids=args.get("policy_ids") or [],
                is_final=bool(args.get("is_final") or False),
            )
        return {"ok": False, "error": f"unknown_tool:{name}"}
    except Exception as e:  # noqa: BLE001 — never crash the loop
        _log.warning(
            "tool=%s args=%r raised %s: %s",
            name, args, type(e).__name__, str(e)[:200],
        )
        return {"ok": False, "error": f"{type(e).__name__}:{str(e)[:200]}"}


# ---------- main entrypoint ------------------------------------------------


async def handle_turn(
    session,
    user_text: str,
    chat_history: Optional[list[dict]] = None,
) -> TurnResult:
    """Single-LLM turn handler — replaces orchestrator.handle_turn behaviour
    when USE_SINGLE_BRAIN is enabled.

    Returns a TurnResult whose shape matches orchestrator.TurnResult.
    Raises SingleBrainError on unrecoverable Gemini failure so the api.py
    caller falls through to the legacy orchestrator.
    """
    t0 = time.time()

    # X7 — monotonic conversation-turn counter; admin Recommendation History
    # renders this as the "Conversation turn" column. Increment BEFORE any
    # tool call so brain_tools.mark_recommendation can stamp the resulting
    # turn_idx onto each shown_policies event written this turn.
    try:
        session.turn_idx = int(getattr(session, "turn_idx", 0) or 0) + 1
    except Exception:  # noqa: BLE001 — never break a chat turn for bookkeeping
        pass

    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise SingleBrainError("GOOGLE_API_KEY not set")

    model = _resolve_model()
    language = _detect_language(user_text)

    # KI-255 — detect "returning user" so RULE 4 (Welcome Back greeting)
    # only fires when the profile was actually loaded from a prior
    # session. Signal: session.turn_idx == 1 (we just incremented above,
    # so this is the FIRST turn of this session_id) AND profile has any
    # captured slot. If turn_idx > 1, slots were populated by prior
    # save_profile_field calls within THIS conversation — not a
    # returning user, do NOT trigger RULE 4 Welcome Back.
    _current_turn = int(getattr(session, "turn_idx", 1) or 1)
    _has_prior_profile = any(
        getattr(session.profile, fld, None) not in (None, "", [])
        for fld in (
            "name", "age", "dependents", "location_tier",
            "income_band", "primary_goal", "health_conditions",
        )
    )
    is_returning_user = (_current_turn == 1) and _has_prior_profile

    system_instruction = _system_instruction(
        session.profile, is_returning_user=is_returning_user,
    )

    # The running `contents` list — we append model turns + function
    # responses to it across loop iterations so Gemini sees the entire
    # tool-call thread when emitting its final text.
    contents = _build_contents(chat_history, user_text)

    # Track each tool call we serve so we can populate citations + the
    # `intent`/`brain_used` log fields at the end.
    tool_calls_made: list[str] = []
    retrieved_chunks_all: list[dict] = []
    last_marked_policy_ids: list[str] = []
    profile_updates: dict[str, Any] = {}

    # Defensive counter to break runaway loops.
    last_text: str = ""
    last_payload: dict = {}

    for it in range(MAX_ITERATIONS):
        # Issue A instrumentation (KI-Z6-LATENCY, 2026-05-15) — Priya T3
        # timed at 18.7s vs an 8s budget. We need per-iteration breakdown
        # of (Gemini call time) vs (tool exec time) to identify whether
        # cold-start, embedding/Chroma, or sequential LLM calls dominate.
        # Wall-clock timers below feed `_log.info("iter %d: ...")` so HF
        # Space logs surface the breakdown without any extra plumbing.
        _t_iter0 = time.perf_counter()
        try:
            payload = await _gemini_call(
                api_key=api_key,
                model=model,
                system_instruction=system_instruction,
                contents=contents,
                tools=TOOL_SCHEMAS,
                timeout_sec=PER_CALL_TIMEOUT_SEC,
            )
        except SingleBrainError:
            raise
        except Exception as e:  # noqa: BLE001 — defensive
            raise SingleBrainError(
                f"gemini_call unexpected error: {type(e).__name__}: {e}"
            ) from e
        _t_gemini = time.perf_counter() - _t_iter0

        last_payload = payload
        parts = _extract_parts(payload)
        function_calls = _parts_function_calls(parts)
        text = _parts_text(parts).strip()

        # CASE A — no function calls: this is the final text reply.
        # Includes the "Gemini just chats on turn 1" path the spec
        # called out — completely valid, return immediately.
        if not function_calls:
            last_text = text
            _log.info(
                "single_brain iter=%d gemini=%.2fs tools=%.2fs "
                "tool_calls=[] final_text=True",
                it, _t_gemini, 0.0,
            )
            break

        # CASE B — one or more function calls. Append the model turn
        # verbatim so Gemini sees its own previous tool-call request,
        # then execute every call and append a single user turn with
        # the matching functionResponse parts.
        contents.append(
            {
                "role": "model",
                "parts": parts,
            }
        )

        _t_tools0 = time.perf_counter()
        _per_tool_latency: list[str] = []  # logged tail for iter summary
        response_parts: list[dict] = []
        for fc in function_calls:
            name = fc["name"]
            args = fc.get("args") or {}
            tool_calls_made.append(name)
            _t_tool0 = time.perf_counter()
            result = await _execute_tool(session, name, args)
            _t_tool = time.perf_counter() - _t_tool0
            _per_tool_latency.append(f"{name}={_t_tool:.2f}s")

            # Issue A — when retrieve_policies dominates iter latency we
            # need to know whether it's the embedding step or the Chroma
            # ANN query. brain_tools.retrieve_policies already returns
            # chunks + count; surface the elapsed wall-clock here so the
            # log line tags retrieve_policies separately. The deeper
            # embedding vs Chroma breakdown lives inside rag.retrieve and
            # is out of scope for this patch; this gives ops enough signal
            # to decide whether to drill further.
            if name == "retrieve_policies":
                _log.info(
                    "single_brain retrieve_policies elapsed=%.2fs "
                    "chunks=%d query_len=%d filter_ids=%s",
                    _t_tool,
                    len(result.get("chunks") or []),
                    len(str(args.get("query") or "")),
                    bool(args.get("policy_filter_ids")),
                )

            # Bookkeeping for the TurnResult fields.
            if name == "save_profile_field" and result.get("saved"):
                fld = result.get("field")
                if fld:
                    profile_updates[fld] = result.get("value")
            elif name == "retrieve_policies":
                for c in result.get("chunks") or []:
                    retrieved_chunks_all.append(c)
            elif name == "mark_recommendation" and result.get("recorded"):
                last_marked_policy_ids = list(result.get("policy_ids") or [])

            response_parts.append(
                {
                    "functionResponse": {
                        "name": name,
                        "response": {"content": result},
                    }
                }
            )
        _t_tools = time.perf_counter() - _t_tools0

        _log.info(
            "single_brain iter=%d gemini=%.2fs tools=%.2fs "
            "tool_calls=[%s] per_tool=[%s]",
            it, _t_gemini, _t_tools,
            ",".join(fc["name"] for fc in function_calls),
            " ".join(_per_tool_latency),
        )

        contents.append({"role": "user", "parts": response_parts})
        # And loop — Gemini gets another shot to either call more
        # tools or emit a final text reply.
        last_text = text  # in case loop hits MAX_ITERATIONS with no text
    else:
        # Hit MAX_ITERATIONS without break — synthesise a defensive reply.
        _log.warning(
            "single_brain hit MAX_ITERATIONS=%d (tool_calls=%s)",
            MAX_ITERATIONS, tool_calls_made,
        )
        last_text = last_text or _synthesise_fallback(session.profile)

    # Build TurnResult.
    reply_text = last_text or _synthesise_fallback(session.profile)

    # Bug C secondary defense — log a WARNING if the reply name-drops an
    # insurer/product brand even though no retrieve_policies result was
    # cached on the session. The system-prompt ABSOLUTE RULE is the
    # primary defense; this only exists so a future regression shows up
    # in smoke logs instead of going silent.
    _scan_for_brand_hallucinations(reply_text, session)

    # Citations: deduped by chunk_id; same shape orchestrator emits.
    seen_ids: set[str] = set()
    citations: list[dict] = []
    retrieved_chunk_ids: list[str] = []
    for c in retrieved_chunks_all:
        cid = c.get("chunk_id") or ""
        if not cid or cid in seen_ids:
            continue
        seen_ids.add(cid)
        retrieved_chunk_ids.append(cid)
        citations.append(
            {
                "chunk_id": cid,
                "policy_id": c.get("policy_id", ""),
                "policy_name": c.get("policy_name", ""),
                "insurer_slug": c.get("insurer_slug", ""),
                "doc_type": c.get("doc_type", ""),
                "source_url": c.get("source_url", ""),
                "score": c.get("score", 0.0),
            }
        )

    intent = _classify_intent(user_text, tool_calls_made)
    brain_used = f"single_brain::{model}"
    if tool_calls_made:
        brain_used += f"::tools={'+'.join(sorted(set(tool_calls_made)))}"

    # follow-up policy id: if the LLM marked exactly one policy this turn,
    # surface it so the frontend can highlight the matching card.
    followup_policy_id = (
        last_marked_policy_ids[0]
        if len(last_marked_policy_ids) == 1
        else None
    )

    return TurnResult(
        reply_text=reply_text,
        citations=citations,
        retrieved_chunk_ids=retrieved_chunk_ids,
        brain_used=brain_used,
        intent=intent,
        language=language,
        latency_ms=int((time.time() - t0) * 1000),
        raw_reply=json.dumps(last_payload)[:4000] if last_payload else reply_text,
        faithfulness_passed=True,
        faithfulness_reasons=[],
        blocked=False,
        profile_updates=profile_updates,
        followup_policy_id=followup_policy_id,
    )


__all__ = [
    "SingleBrainError",
    "TurnResult",
    "handle_turn",
    "SYSTEM_PROMPT",
    "TOOL_SCHEMAS",
    "MAX_ITERATIONS",
    "PER_CALL_TIMEOUT_SEC",
]
