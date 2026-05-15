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
MAX_ITERATIONS = 5

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
  • "First policy" / "upgrade" / "tax planning" → save_profile_field(field="primary_goal", value="first_buy" or "upgrade" or "tax_planning")
  • "diabetes" / "BP" / "no health issues" → save_profile_field(field="health_conditions", value="diabetes" or "" or "none")

Worked example. User says: "Hi I'm Priya, 34, Bangalore, with husband and one kid"
  → You MUST call:
       save_profile_field(field="name",            value="Priya")
       save_profile_field(field="age",             value="34")
       save_profile_field(field="location_tier",   value="metro")
       save_profile_field(field="dependents",      value="self+spouse+1 kid")
  → THEN write a short prose reply asking for the remaining slots (income, goal, health).

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
GROUND RULES
═══════════════════════════════════════════════════════════
- NEVER invent policies, UINs, premiums, or sums insured. Only cite what retrieve_policies returns.
- If retrieve_policies returns zero chunks after both attempts, ask the user one clarifying question.
- Be concise: 2-3 sentence turns. No emoji unless the user used one first.
- Indian context: use lakh / crore, ₹, IRDAI, Section 80D. NEVER say "dollars" / "$".
- Returning users may have a pre-populated profile — greet them by name, summarise what you remember, ask to confirm or update.
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


def _system_instruction(profile) -> dict:
    """Bake the profile snapshot into the system prompt so each turn the
    LLM knows what's already captured. Returned in Gemini's expected
    `systemInstruction` shape."""
    snapshot = _profile_to_snapshot(profile)
    extra = ""
    if snapshot:
        extra = (
            "\n\nKNOWN PROFILE (already captured this session; do NOT re-ask):\n"
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
            "temperature": 0.4,
            "maxOutputTokens": 1024,
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
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise SingleBrainError(f"Gemini malformed JSON: {e}") from e

    # Defensive — loop fell through without returning or raising. Should
    # be unreachable, but raise so we never silently return None.
    raise SingleBrainError(
        last_err
        or f"Gemini exhausted retries (last_status={last_status})"
    )


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
    system_instruction = _system_instruction(session.profile)

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

        last_payload = payload
        parts = _extract_parts(payload)
        function_calls = _parts_function_calls(parts)
        text = _parts_text(parts).strip()

        # CASE A — no function calls: this is the final text reply.
        # Includes the "Gemini just chats on turn 1" path the spec
        # called out — completely valid, return immediately.
        if not function_calls:
            last_text = text
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

        response_parts: list[dict] = []
        for fc in function_calls:
            name = fc["name"]
            args = fc.get("args") or {}
            tool_calls_made.append(name)
            result = await _execute_tool(session, name, args)

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
        last_text = (
            last_text
            or "Let me pause for a second — could you tell me a bit more about "
               "what you're looking for, so I can give you a clean recommendation?"
        )

    # Build TurnResult.
    reply_text = last_text or (
        "Sorry — I lost my train of thought there. Could you say that again?"
    )

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
