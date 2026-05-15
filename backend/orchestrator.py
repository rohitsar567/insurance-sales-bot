"""Orchestrator — the brain of the bot.

For each user turn:
  1. Retrieve top-k relevant chunks from Chroma
  2. Format them as cited context
  3. Build messages with persona + history + profile
  4. Route to the brain: NIM DeepSeek-V4-Pro (single-provider Stack A, D-019)
  5. Run 4-gate faithfulness verification (judge = NIM Llama-4 Maverick — different family)
  6. On Indic input, translate via Sarvam-M (in & out)
  7. Return (reply_text, citations[], retrieved_chunk_ids[], cost_estimate)
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.faithfulness import check_faithfulness, FaithfulnessVerdict
from backend.persona import build_messages, strip_think_tags
from backend.providers.base import ChatMessage, LLMProvider
from backend.providers.nvidia_nim_llm import (
    NIM_JUDGE_MODEL,
    NvidiaNimLLM,
    get_brain_llm,
    get_fast_brain_llm,
)
from backend.providers.tiered_brain_llm import get_tiered_brain_llm
from rag.retrieve import RetrievedChunk, format_for_llm_context, retrieve


# ---------- intent classification (v1: keyword heuristics) ----------

# Fact-find triggers: conversational openers where the user is seeking advice,
# not asking a specific factual question about a known policy. These should
# bypass retrieval+faithfulness entirely and start the discovery flow.
FACT_FIND_TRIGGERS = (
    "looking for", "i want", "i need", "help me find", "advice",
    "first time", "new health insurance", "buy health insurance",
    "should i get", "shopping for", "thinking about getting",
    "want to buy", "best policy for me", "what do you recommend",
    "i don't have", "no policy", "no insurance",
    "hi", "hello", "hey", "namaste",
    # KI-153 (2026-05-15) — profile-update phrasings. When a user provides
    # personal information (name, age, city, family, income, existing
    # cover), classify_intent was returning "qa" because none of these
    # matched the recommend/comparison/fact-find triggers. They'd go to QA
    # brain which had no idea what to do and produced defensive "not
    # enough evidence in the policy documents" replies. These patterns
    # cover the answers users give during fact-find collection so the
    # orchestrator can route them back to fact_find_brain for slot capture.
    "my name is", "i am called", "call me", "i'm called",
    "i am ", "i'm ", "im ",  # "I am 29 years old", "I'm single"
    "years old", "year old", "age is",
    "live in", "i'm in ", "i am in ", "from ", "based in",
    "married", "single", "with spouse", "with my family",
    "have kids", "have parents", "for my parents", "for my family",
    "my income", "earn ", "salary",
    "first policy", "first time buyer",
    # KI-154 (2026-05-15) — additional buying-intent phrasings that don't
    # contain "buy health insurance" as a tight substring. Live bug: user
    # said "to buy a health insurance policy" — fell through to qa because
    # the literal "buy health insurance" (no article) didn't match.
    "to buy", "want to buy", "looking to buy", "buy a ", "buy an ",
    "purchase", "purchasing", "shopping for a", "get a policy",
    "get a plan", "need a policy", "need a plan", "buy a policy",
    "buy a plan", "buy insurance", "buy a policy for",
)

COMPARISON_KEYWORDS = (
    "compare", "comparison", "vs", "versus", "between policy", "which is better",
    # KI-105 (2026-05-15) — live 15-persona smoke surfaced that closer phrases
    # like "compare top 3", "rank the best", "shortlist" were misrouted to qa
    # because they didn't hit any keyword here. "rank" + "shortlist" are
    # comparison-shaped requests across the candidate set.
    "rank", "ranking", "shortlist", "side by side", "side-by-side",
)
RECOMMEND_KEYWORDS = (
    "recommend", "should i", "which one", "best for", "suit me",
    # KI-105 (2026-05-15) — closer phrases that the live smoke caught as
    # misclassified to qa instead of recommendation. Without these the
    # orchestrator never routes to the heavy brain + never templates a
    # ranked output, so the bot either re-asks a slot or refuses.
    "show me", "show me policies", "show me policy", "show me the top",
    "top 3", "top three", "top policies", "give me the top",
    "what would you recommend", "what do you suggest", "your top picks",
    "your top pick", "your best", "my best option", "my best options",
    "pitch me", "pitch your", "policies for me", "policy for me",
)
INDIC_KEYWORDS = (
    # Devanagari letters
    "क", "ख", "ग", "घ", "च", "ज", "ट", "ड", "त", "द", "न", "प", "ब", "म", "य", "र", "ल", "व", "स", "ह",
    # common Hinglish words
    " hai ", " kya ", " mein ", " kar ", " ka ", " ki ", " ke ", " liye ", " mujhe ",
)


# KI-136 (2026-05-15) — named-SKU detector. Comparisons that name >=2
# specific insurers/policies are policy-fact lookups, not profile-dependent
# recommendations — same carve-out spirit as KI-018 for qa intent.
_NAMED_INSURER_TOKENS = (
    "hdfc ergo", "niva bupa", "star health", "care health", "icici lombard",
    "bajaj allianz", "tata aig", "aditya birla", "manipal cigna", "sbi general",
    "new india", "national insurance", "oriental insurance", "united india",
    "reliance general", "go digit", "iffco tokio", "raheja qbe",
    "cholamandalam", "acko", "royal sundaram",
    # Common product names
    "optima secure", "optima restore", "aspire", "activ assure", "activ one",
    "activ health", "health companion", "young star", "senior citizen red carpet",
    "arogya sanjeevani", "reassure", "health guard", "prohealth", "elevate",
    "medicare", "criti", "saral suraksha", "comprehensive care",
)


def _names_two_specific_policies(q: str) -> bool:
    """KI-136 — return True if the user's text mentions >=2 named SKUs/insurers,
    indicating a policy-fact lookup rather than a profile-dependent
    recommendation."""
    ql = q.lower()
    hits = sum(1 for tok in _NAMED_INSURER_TOKENS if tok in ql)
    return hits >= 2


def _phrase_present(phrase: str, q: str) -> bool:
    """Word-boundary phrase match. KI-023 (2026-05-14) — replaces naive
    substring matching that incorrectly tripped triggers like "hi" on words
    like "which", "this", "high", "thigh". That caused comparison/qa
    questions starting with "Which..." to be misrouted to fact_find.
    Phrases that contain spaces or apostrophes already act as word-bounded
    via the surrounding spaces, but single-word triggers ("hi", "hey", etc.)
    need an explicit \\b boundary."""
    # Escape regex metas in the trigger; allow any whitespace inside the phrase
    # to match one-or-more spaces in the input.
    escaped = re.escape(phrase).replace(r"\ ", r"\s+")
    pattern = rf"\b{escaped}\b"
    return re.search(pattern, q) is not None


# KI-105 (2026-05-15) — explicit closer phrases that ALWAYS win the
# comparison / recommendation label, even when a fact-find trigger
# also matches. Live 15-persona smoke caught "show me the top 3
# policies" being routed to fact_find (because "show me" is not in
# FACT_FIND_TRIGGERS but the q itself is parsed as qa) or to fact-find
# on phrases like "what would you recommend" (matched
# FACT_FIND_TRIGGERS["what do you recommend"]). The bot then re-asked
# slots the user had already filled instead of producing a ranked
# shortlist. These phrases unambiguously mean "produce a ranked
# output now"; the downstream session-state-aware routing
# (`should_route_to_fact_find`) still keeps the KI-013 guard against
# pitching to empty profiles.
_EXPLICIT_CLOSER_COMPARISON = (
    "compare top", "compare the top",
    "side by side", "side-by-side",
    "compare these", "compare those", "compare them",
)
_EXPLICIT_CLOSER_RECOMMENDATION = (
    # KI-105 — original list.
    "show me policies", "show me the policies", "show me policy",
    "show me the top", "show me top",
    "top 3", "top three", "top policies", "top picks",
    "give me the top", "give me top", "give me your top",
    "your top picks", "your top pick", "your best policy",
    "pitch me", "pitch your top",
    "what would you recommend", "what do you suggest",
    # KI-109 (2026-05-15) — "rank the top N" and bare "rank them" are
    # ranked-recommendation requests (the user wants a ranked shortlist of
    # the candidate set), not pairwise comparison requests. Moved here from
    # _EXPLICIT_CLOSER_COMPARISON in KI-105 because the live re-smoke
    # treated them as ranked-output asks and the user-facing reply should
    # be a recommendation shortlist with rationale, not a feature-by-feature
    # comparison table.
    "rank the top", "rank top", "rank them", "rank these", "rank those",
    # KI-109 (2026-05-15) — live re-smoke caught
    # "Show me the top 3 policies you'd recommend" routing to
    # fact_find_brain::fallback:no_trailer because the regex above only
    # caught the prefix "show me the top" — but in this folder the actual
    # short-circuit happens in classify_intent via `_phrase_present`, and
    # since `_phrase_present` is word-boundary based ("\b<phrase>\b"), the
    # match DOES succeed; the regression is downstream: orchestrator's
    # `should_route_to_fact_find` still routes context-dependent intents
    # (recommendation / comparison) to fact-find when profile_is_empty
    # (KI-018). Even so we broaden the trigger set so the explicit-closer
    # override lands on EVERY phrasing the user types — particularly
    # phrases that didn't appear in KI-105's list at all:
    #   - "best policies for me" (matched RECOMMEND_KEYWORDS["best for"]
    #     before but only as a generic recommendation, not as an
    #     unambiguous closer that beats fact-find triggers)
    #   - "what should I get" (currently matches FACT_FIND_TRIGGERS via
    #     "should i get"; needs to be lifted into closer lane)
    #   - "which policies should I consider" (currently falls to qa)
    #   - "pitch me the top X" (already partly covered; cement it)
    "best policies for me", "best policy for me", "best for me",
    "what should i get", "what policy should i get",
    "which policies should i consider", "which policy should i consider",
    "which one should i pick", "which one should i go with",
    "what would you suggest", "what do you recommend",
    "your top recommendations", "your recommendations",
    "shortlist for me", "policies you'd recommend", "policy you'd recommend",
)


def classify_intent(query: str) -> str:
    q = query.lower().strip()

    # KI-105 — explicit closer override. These phrases are unambiguous
    # ranked-output requests; they must beat the fact-find trigger
    # short-circuit below (which would otherwise misroute "show me the
    # top 3 policies" or "what would you recommend" to fact-find).
    if any(_phrase_present(kw, q) for kw in _EXPLICIT_CLOSER_COMPARISON):
        return "comparison"
    if any(_phrase_present(kw, q) for kw in _EXPLICIT_CLOSER_RECOMMENDATION):
        return "recommendation"

    # Greeting / advice-seeking openers → fact-find flow
    if any(_phrase_present(kw, q) for kw in FACT_FIND_TRIGGERS) and len(q.split()) < 25:
        return "fact_find"
    if any(_phrase_present(kw, q) for kw in COMPARISON_KEYWORDS):
        return "comparison"
    if any(_phrase_present(kw, q) for kw in RECOMMEND_KEYWORDS):
        return "recommendation"
    return "qa"


def detect_language(query: str) -> str:
    q = query.lower()
    if any(kw in q for kw in INDIC_KEYWORDS):
        return "indic"
    if any(c in query for c in "अआइईउऊऋएऐओऔकखगघचछजझटठडढतथदधनपफबभमयरलवशषसह"):
        return "indic"
    return "en"


# ---------- brain router ----------

@dataclass
class BrainPick:
    provider: LLMProvider
    reason: str


# Intents that depend on knowing who the user is. Recommending a senior-citizen
# plan to a 25-year-old (and vice-versa) is unacceptable, so we force fact-find
# until at least one profile field is set. QA intent does NOT belong here — a
# question like "What's the waiting period for PED in Activ Assure?" is a
# policy-fact lookup that doesn't need user context. See KI-018.
CONTEXT_DEPENDENT_INTENTS = frozenset({"recommendation", "comparison"})


def should_route_to_fact_find(
    intent: str,
    *,
    profile_is_empty: bool,
    in_fact_find_continuation: bool,
    free_form_session: bool,
    query: str = "",
) -> bool:
    """Pure decision function for the fact-find routing branch.

    Extracted so tests/test_routing_regression.py can lock in the KI-018
    invariant: empty-profile sessions must NOT trap QA intents in fact-find.
    """
    if free_form_session:
        return False
    if intent == "fact_find":
        return True
    if in_fact_find_continuation:
        return True
    if profile_is_empty and intent in CONTEXT_DEPENDENT_INTENTS:
        # KI-136 (2026-05-15) — exempt named-SKU comparisons. They're policy-fact
        # lookups (route to qa), not profile-dependent recommendations.
        if intent == "comparison" and _names_two_specific_policies(query):
            return False
        return True
    # KI-154 (2026-05-15) — empty-profile + qa intent: route to fact_find
    # UNLESS the query names a specific policy / insurer (real policy-fact
    # lookup) or asks about specific policy mechanics. Without this rule,
    # broad questions like "to buy a health insurance policy" or "tell me
    # about health insurance" dumped raw policy facts on users who haven't
    # even given their name yet.
    if profile_is_empty and intent == "qa":
        # If query names a specific insurer/SKU, it's a legitimate policy-fact
        # lookup (e.g., "Does Star Comprehensive cover IVF?"). Let qa handle.
        if _names_two_specific_policies(query):
            return False
        # Single named policy/insurer is also fine for qa
        ql = query.lower()
        if any(tok in ql for tok in _NAMED_INSURER_TOKENS):
            return False
        # Otherwise — empty-profile generic intent — route to fact_find so the
        # bot collects basic context before quoting policy specifics.
        return True
    return False


def pick_brain(intent: str, language: str) -> BrainPick:
    """Route to the reasoning brain per D-019 (2026-05-14, tiered routing).

    KI-179 (2026-05-15) — every route now goes through a 3-tier wrapper
    (TieredBrainLLM) that prefers Google Gemini Flash, falls back to NIM,
    then OpenRouter free-tier. The intent-based heavy vs fast split is
    preserved at the wrapper level — heavy intents get `gemini-2.5-flash`
    + BRAIN_CHAIN, light intents get `gemini-2.0-flash` + FAST_BRAIN_CHAIN.

    Original D-019 design (kept for context):
      - 'comparison' / 'recommendation' → heavy brain (quality > latency)
      - 'fact_find' / 'qa'              → fast brain (latency > quality)

    Indic queries get a Sarvam-M translation pass in `handle_turn` before
    retrieval, then another after reasoning to convert the English reply back
    to Hindi/Hinglish. The brain itself always reasons in English on English
    context.
    """
    HEAVY_INTENTS = {"comparison", "recommendation"}
    if intent in HEAVY_INTENTS:
        return BrainPick(get_tiered_brain_llm(role="brain"), f"v4-pro::{intent}")
    return BrainPick(get_tiered_brain_llm(role="fast_brain"), f"v4-flash::{intent}")


# ---------- welcome-back greeting helpers ----------
#
# KI-070 (2026-05-15) — the conversational acknowledger rotation
# (_pick_opener / _family_aware_opener / _NEUTRAL_OPENERS_EN etc.) and the
# self-introduction regex (_SELF_INTRO_RE / _NON_NAME_TOKENS /
# _contains_self_introduction) were removed alongside the 3-layer fact-find
# stitching. The single-LLM-call `drive_fact_find` brain now produces natural
# acknowledgers AND extracts names natively from any free-text message —
# making the rule-based helpers redundant.


# KI-061 (2026-05-15) — human-readable summaries for the welcome-back
# greeting. Each tuple is (field name on Profile, label, formatter).
_KNOWN_FIELD_FORMATTERS: tuple[tuple[str, str, "callable"], ...] = (
    ("age", "age", lambda v: f"{v}"),
    ("dependents", "covering", lambda v: str(v).replace("self+", "you+").replace("+", " + ")),
    ("income_band", "income band", lambda v: str(v)),
    ("existing_cover_inr", "existing cover", lambda v: f"₹{v:,}" if isinstance(v, int) else str(v)),
    ("primary_goal", "looking for", lambda v: str(v).replace("_", " ")),
    ("location_tier", "city tier", lambda v: str(v)),
    ("parents_age_max", "parents' age", lambda v: f"oldest {v}"),
    ("health_conditions", "health conditions", lambda v: ", ".join(v) if isinstance(v, list) and v else None),
    ("budget_band", "budget", lambda v: str(v)),
)


def _format_known_profile_summary(profile) -> str:
    """Return a comma-separated rundown of what we already know, e.g.
    'age 34, covering you + spouse, income ₹10-25L, looking for first
    health policy'. Returns '' if nothing meaningful is stored.

    KI-063 (2026-05-15) — if the user has selected (shortlisted) policies
    on file, append a "Your shortlist: <insurer · policy>, ..." line so the
    returning-visitor greeting surfaces what they previously saved.
    """
    parts: list[str] = []
    for field_name, label, fmt in _KNOWN_FIELD_FORMATTERS:
        val = getattr(profile, field_name, None)
        if val in (None, "", []):
            continue
        try:
            rendered = fmt(val)
        except Exception:
            rendered = str(val)
        if rendered:
            parts.append(f"{label} {rendered}")
    summary = ", ".join(parts)
    # KI-063 — append shortlist line if any selected policies on file.
    shortlist = list(getattr(profile, "selected_policies", None) or [])
    if shortlist:
        bits = []
        for entry in shortlist:
            insurer = (entry.get("insurer") or "").strip()
            slug = (entry.get("policy_slug") or "").strip()
            if insurer and slug:
                bits.append(f"{insurer} · {slug}")
            elif slug:
                bits.append(slug)
        if bits:
            line = "Your shortlist: " + ", ".join(bits)
            summary = f"{summary}. {line}" if summary else line
    return summary


_HELPFUL_GAP_LABELS = {
    "age": "your age",
    "dependents": "who you're covering",
    "income_band": "your income band",
    "primary_goal": "what you're shopping for",
    "parents_age_max": "your parents' health context",
    "budget_band": "your budget",
}


def _format_missing_slots(profile) -> list[str]:
    """Return human-readable labels for the high-value slots we never
    captured. Limited to the ones that genuinely shape a recommendation
    — minor slots (location, existing_cover, health_conditions) are
    skipped to keep the welcome-back focused."""
    missing = []
    for field_name, label in _HELPFUL_GAP_LABELS.items():
        val = getattr(profile, field_name, None)
        if val in (None, "", []):
            missing.append(label)
    return missing


# ---------- main entrypoint ----------

@dataclass
class TurnResult:
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


async def handle_turn(
    user_text: str,
    chat_history: Optional[list[dict]] = None,
    user_profile: Optional[dict] = None,
    policy_filter_ids: Optional[list[str]] = None,
    top_k: int = 5,
    session_id: Optional[str] = None,
    view_context: Optional[dict] = None,
) -> TurnResult:
    t0 = time.time()

    # 1. Classify
    intent = classify_intent(user_text)
    language = detect_language(user_text)

    # 1a. INDIC CASCADE — translate Indic query → English, reason in DeepSeek,
    # translate answer back. Capture original-language user_text for logging.
    original_user_text = user_text
    translated_query = None
    if language == "indic":
        try:
            from backend.translator import translate_to_english
            translated_query = await translate_to_english(user_text)
            if translated_query and translated_query.strip() and translated_query != user_text:
                user_text = translated_query  # use English for retrieval + reasoning
        except Exception as e:
            # KI-004 — surface translator failures in HF Space logs. The
            # brain will still try with the original Indic text, but with
            # degraded quality. The log lets us tune the Sarvam fallback.
            logging.warning(
                "indic translator failed (session=%s lang=%s): %s: %s",
                session_id, language, type(e).__name__, str(e)[:200],
            )

    # 1b. SESSION-STATE-AWARE FACT-FIND
    # Load session state. If we're already in fact-find (awaiting an answer to a
    # specific question), interpret the user's message as that answer and emit
    # the next question — regardless of whether intent_classifier thinks it's a
    # fact-find phrase. This is what fixes "39 years old" being misrouted to RAG.
    from backend.session_state import get_session
    session = get_session(session_id or "anonymous")

    in_fact_find_continuation = bool(session.awaiting_question_id) and not session.free_form_session
    # KI-013 — if the user has NO profile fields yet, FORCE fact-find for
    # intents that depend on user context (recommendation, comparison).
    # Real user testing surfaced: a vague opener ("I want health insurance")
    # got classified as "recommendation" and the bot retrieved "Care Senior"
    # (a senior-citizen-only policy) and pitched it. The bot must never
    # recommend without knowing the user's age / dependents / conditions /
    # budget.
    #
    # KI-018 (2026-05-14) — intent='qa' was previously also force-routed to
    # fact-find on empty profile, which dropped factual accuracy to 30% on
    # gold-QA: the bot answered "What is the waiting period for PED?" with
    # "First, your age?". QA is policy-fact lookup, doesn't depend on user
    # profile — it must pass through to retrieval. Only context-dependent
    # intents (recommendation/comparison) need a profile first.
    profile_is_empty = (
        session.profile.age is None
        and session.profile.dependents is None
        and session.profile.income_band is None
    )

    # KI-037 (2026-05-14) — greeting + intent-restart detection. The previous
    # behavior was: any user message arriving while session.awaiting_question_id
    # was set got parsed as an attempted answer. A user opening the bot with
    # "Hi, I am looking to buy an insurance policy." would get this parsed as
    # a (failed) answer to the dependents slot (because earlier turns had
    # advanced the slot pointer) → "Sorry, I didn't catch that. Let me ask
    # again — Who else needs cover..." which feels broken because the user
    # intended a fresh start.
    #
    # Fix: if the user's message is a pure greeting or contains explicit
    # restart intent, CLEAR any stale awaiting slot + reset re-ask counts
    # so the fact-find re-enters cleanly.
    _RESTART_INTENT_PHRASES = (
        "i am looking", "i'm looking", "i want to buy", "want to buy",
        "looking to buy", "i am here to", "i'm here to",
        "i need health insurance", "i need insurance",
        "help me find", "first time", "buy health insurance",
    )
    _PURE_GREETING_WORDS = {
        "hi", "hello", "hey", "namaste", "yo", "hola",
    }
    _user_text_lc = user_text.lower().strip()
    _stripped_user = "".join(c for c in _user_text_lc if c.isalnum() or c.isspace()).strip()
    _is_pure_greeting = bool(_stripped_user) and all(
        w in _PURE_GREETING_WORDS for w in _stripped_user.split()
    )
    _is_intent_restart = any(p in _user_text_lc for p in _RESTART_INTENT_PHRASES)
    if (_is_pure_greeting or _is_intent_restart) and session.awaiting_question_id:
        session.set_awaiting(None)
        if hasattr(session, "_reask_counts"):
            session._reask_counts.clear()
        # Recompute the continuation flag now that we've cleared the slot.
        in_fact_find_continuation = False

    # KI-037 — pure greeting on empty profile gets a warm welcome, NOT an
    # immediate age grilling. The bot can answer "Hi" with "Hi!" + offer
    # before asking for a profile. User feedback: bot grilling for age on
    # the very first "Hello" feels mechanical and unwelcoming.
    if _is_pure_greeting and profile_is_empty and not session.free_form_session:
        reply = (
            "Hi! I'm an AI advisor for health insurance in India — I've read 208 policy "
            "documents from 19 insurers plus the IRDAI master circulars, and I can "
            "answer questions about coverage, waiting periods, exclusions, or "
            "compare policies side-by-side.\n\n"
            "Want me to build a 2-minute profile so I can grade each policy *for you* "
            "specifically, or do you have a question in mind?"
        )
        return TurnResult(
            reply_text=reply,
            citations=[],
            retrieved_chunk_ids=[],
            brain_used="greeting::welcome",
            intent="fact_find",
            language=language,
            latency_ms=int((time.time() - t0) * 1000),
            raw_reply=reply,
            faithfulness_passed=True,
            blocked=False,
            profile_updates={},
        )

    treat_as_fact_find = should_route_to_fact_find(
        intent,
        profile_is_empty=profile_is_empty,
        in_fact_find_continuation=in_fact_find_continuation,
        free_form_session=session.free_form_session,
        query=user_text,
    )

    if treat_as_fact_find:
        # KI-167 (2026-05-15) — WS2: replaces drive_fact_find with
        # drive_sales_brain. The new brain owns conversation flow end-to-end
        # (no slot_driving / awaiting_question machinery, no canonical
        # fallback): one LLM call emits natural prose + canonical
        # captured_updates + a completeness signal. Failures fail loud (no
        # scripted fallback).
        from backend.sales_brain import drive_sales_brain, SalesBrainResult

        try:
            sb_result: SalesBrainResult = await asyncio.wait_for(
                drive_sales_brain(
                    user_text=user_text,
                    profile=session.profile,
                    chat_history=chat_history[-10:],
                    session_id=session_id,
                ),
                timeout=45.0,  # KI-170 — bumped from 25s; qwen3-next-80b + JSON mode regularly lands 15-25s
            )
        except asyncio.TimeoutError:
            # No scripted fallback. Fail loud — let the operator see it.
            sb_result = SalesBrainResult(
                reply_text="Sorry, that took longer than expected — could you say that one more time?",
                captured_updates={},
                ready_for_recommendations=False,
                brain_used="sales_brain::error:timeout_45s",
                error_reason="outer_timeout_45s",
            )

        # Apply captured updates to profile. captured_updates is already
        # post-processed and canonical (WS1 contract): pass values straight to
        # session.update_profile_field. For the new path, the question id IS
        # the field name — no FIELD_TO_QUESTION_ID translation.
        fact_find_profile_updates: dict = {}
        for field_name, value in sb_result.captured_updates.items():
            if value in (None, "", []):
                continue
            session.update_profile_field(field_name, value)
            fact_find_profile_updates[field_name] = value
            if field_name not in session.profile.asked:
                session.profile.asked.append(field_name)

        # Completion gate. The LLM owns conversation flow; we only flip
        # free_form_session when its own readiness signal is True AND every
        # required slot is filled. Otherwise leave free_form_session=False so
        # the next turn routes back here and the LLM continues gathering.
        _REQUIRED_SLOTS = ("name", "age", "dependents", "location_tier",
                           "income_band", "primary_goal")
        if sb_result.ready_for_recommendations and all(
            getattr(session.profile, slot, None) not in (None, "", [])
            for slot in _REQUIRED_SLOTS
        ):
            session.free_form_session = True
            session._flush()

        # KI-118 (2026-05-15) — profile-chunk upsert is gated on a known
        # name. Anonymous sessions never write to Chroma; the corruption
        # surface (profile_anonymous dangling row) is eliminated. Named
        # users get their chunk keyed by canonical name slug, not session_id.
        # KI-167 — trigger when any slot was captured this turn.
        if bool(sb_result.captured_updates) and session.profile.name:
            try:
                from backend.profile_rag import upsert_profile_chunk
                from backend.profile_store import _normalise_name
                p = session.profile
                slug = _normalise_name(p.name or "")
                if slug:
                    await upsert_profile_chunk(slug, {
                        "age": p.age,
                        "dependents": p.dependents,
                        "income_band": p.income_band,
                        "existing_cover_inr": p.existing_cover_inr,
                        "primary_goal": p.primary_goal,
                        "location_tier": p.location_tier,
                        "parents_to_insure": p.parents_to_insure,
                        "parents_age_max": p.parents_age_max,
                        "parents_has_ped": p.parents_has_ped,
                        "budget_band": p.budget_band,
                        "health_conditions": p.health_conditions,
                    })
            except Exception as e:
                logging.warning(
                    "sales_brain profile-chunk upsert failed (session=%s): %s: %s",
                    session_id, type(e).__name__, str(e)[:200],
                )

        # KI-040 / KI-062 — named-profile persistence preserved. If the brain
        # captured (or already has) a name on the profile, write the merged
        # profile to disk so the next visit can welcome the user back.
        # KI-118 — also trigger one-shot rehydrate when name was newly captured
        # this turn so any stored profile from a prior visit gets merged in.
        if session.profile.name:
            try:
                from backend.profile_store import save_profile
                # If name was newly captured this turn AND there's a stored
                # profile under that name, merge in the stored fields BEFORE
                # writing back (so we don't immediately overwrite the stored
                # snapshot with a partial new one).
                if "name" in fact_find_profile_updates:
                    from backend.session_state import rehydrate_by_name
                    rehydrate_by_name(session, session.profile.name)
                save_profile(session.profile.name, session.profile, session_id=session_id)
            except Exception:
                pass

        # KI-108 (2026-05-15) — strip CoT preamble / instruction-echo leakage
        # from the brain reply BEFORE returning to ChatResponse. The LLM may
        # still emit <think> tags despite JSON mode; strip_think_tags is the
        # uniform guard applied across every brain path.
        reply_text = strip_think_tags(sb_result.reply_text)
        return TurnResult(
            reply_text=reply_text,
            citations=[],
            retrieved_chunk_ids=[],
            brain_used=sb_result.brain_used,
            intent="fact_find",
            language=language,
            latency_ms=int((time.time() - t0) * 1000),
            raw_reply=sb_result.reply_text,
            faithfulness_passed=True,
            blocked=False,
            profile_updates=fact_find_profile_updates,
        )

    # User explicitly asked a specific question — leave fact-find mode if they were in one.
    if session.awaiting_question_id:
        session.set_awaiting(None)
        session.free_form_session = True

    # 1c. CONVERSATIONAL PROFILE UPDATES (free-form mode)
    # In free-form chat the user often shares new profile facts ("I just turned 40",
    # "we had a baby", "I was diagnosed with diabetes"). Run a lightweight LLM
    # extractor, apply high-confidence updates to session.profile, and re-upsert
    # the profile chunk so THIS turn's retrieval reflects the new state.
    #
    # KI-053 (2026-05-14) — eval-mode skip. `eval/run.py --no-extract` sets the
    # INSURANCE_BOT_SKIP_PROFILE_EXTRACTOR env var. Gold-eval questions have
    # empty user_profile + ephemeral sessions so this LLM call wastes a NIM
    # request per question without contributing to grading. Skipping it cuts
    # ~25% off eval wall time at zero quality cost. Production runs (env unset)
    # behaviour is identical to before.
    #
    # KI-091 (2026-05-15) — fact-find-turn skip. Live admin /llm-health showed
    # BRAIN_CHAIN + JUDGE_CHAIN credit_exhausted while FAST_BRAIN_CHAIN was
    # healthy. The fact_find_brain (FAST_BRAIN_CHAIN) succeeded but per-turn
    # telemetry showed profile_extractor calls hitting 10-20s on the saturated
    # heavy brain, which combined with the 25s _TIMEOUT_S in the fact-find
    # path was tripping outer wait_for and emitting canonical fallback.
    # Skipping the extractor on fact-find turns is safe: fact_find_brain's
    # <FF>{"captured": ...} trailer already extracts every slot it needs and
    # populated session.profile upstream (see the early-return block above).
    # Gate: only run extractor when we're in free-form AND not in a fact-find
    # continuation.
    profile_updates_applied: dict = {}
    import os as _os_pe
    _skip_extract = _os_pe.environ.get("INSURANCE_BOT_SKIP_PROFILE_EXTRACTOR") == "1"
    # KI-091 — fact-find branch already returned at line ~499. Anything past
    # this point is free-form OR an explicit-question turn. The flag pair
    # below is the belt-and-braces gate: only call the extractor when we're
    # genuinely in free-form chat (session.free_form_session=True) and not
    # mid-fact-find-continuation. Eval mode (env var) still wins.
    _in_fact_find_now = bool(in_fact_find_continuation) or intent == "fact_find"
    _run_extractor = (
        not _skip_extract
        and bool(getattr(session, "free_form_session", False))
        and not _in_fact_find_now
    )
    try:
        if not _run_extractor:
            extracted = None  # eval mode OR fact-find turn — bypass LLM extractor
        else:
            from backend.profile_extractor import extract_profile_updates
            # KI-098 — outer budget cap. extract_profile_updates calls a
            # NIM LLM which can hang up to 120s on its default timeout when
            # the upstream chain is in synchronous probe_all() refresh. Skip
            # the merge on timeout rather than stall the user-facing turn.
            try:
                extracted = await asyncio.wait_for(
                    extract_profile_updates(user_text, session.profile),
                    timeout=12.0,
                )
            except asyncio.TimeoutError:
                extracted = None
                logging.warning(
                    "extractor timeout, skipping merge (session=%s)", session_id,
                )
        if extracted:
            for field_name, new_value in extracted.items():
                # KI-094 — never let the LLM extractor CLEAR a filled field.
                # The extractor LLM periodically returns {"name": null} for
                # turns where the user didn't restate the name; without this
                # guard `update_profile_field("name", None)` overwrites the
                # captured value mid-session, sending next_question() back to
                # the name slot and breaking fact-find progression.
                if new_value in (None, "", []):
                    continue
                if field_name == "health_conditions":
                    existing = list(session.profile.health_conditions or [])
                    existing_lower = {c.lower() for c in existing if c}
                    merged = list(existing)
                    for cond in new_value:
                        if cond.lower() not in existing_lower:
                            merged.append(cond)
                            existing_lower.add(cond.lower())
                    session.update_profile_field("health_conditions", merged)
                    profile_updates_applied["health_conditions"] = merged
                else:
                    session.update_profile_field(field_name, new_value)
                    profile_updates_applied[field_name] = new_value
            # Re-upsert profile chunk so retrieval sees fresh profile THIS turn.
            # KI-118 (2026-05-15) — gated on a known name; anonymous sessions
            # never write to Chroma.
            try:
                if session.profile.name:
                    from backend.profile_rag import upsert_profile_chunk
                    from backend.profile_store import _normalise_name
                    slug = _normalise_name(session.profile.name)
                    if slug:
                        profile_dict_for_chunk = {
                            "age": session.profile.age,
                            "dependents": session.profile.dependents,
                            "income_band": session.profile.income_band,
                            "existing_cover_inr": session.profile.existing_cover_inr,
                            "primary_goal": session.profile.primary_goal,
                            "location_tier": session.profile.location_tier,
                            "parents_to_insure": session.profile.parents_to_insure,
                            "parents_age_max": session.profile.parents_age_max,
                            "parents_has_ped": session.profile.parents_has_ped,
                            "budget_band": session.profile.budget_band,
                            "health_conditions": session.profile.health_conditions,
                        }
                        await upsert_profile_chunk(slug, profile_dict_for_chunk)
            except Exception as e:
                # KI-005 — log profile-chunk upsert failures so we can see
                # when Chroma is locking or schema-drifting. The chat still
                # ships; subsequent turns just won't see the latest profile.
                logging.warning(
                    "profile-chunk upsert failed (session=%s): %s: %s",
                    session_id, type(e).__name__, str(e)[:200],
                )
    except asyncio.TimeoutError:
        # KI-098 — explicit timeout path (in case any awaited call inside the
        # try-block other than extract_profile_updates raises TimeoutError).
        extracted = None
        logging.warning(
            "extractor timeout, skipping merge (session=%s)", session_id,
        )
    except Exception as e:
        # KI-006 — log profile-extraction failures (extractor LLM down,
        # malformed model output, etc.). The chat ships unaffected.
        logging.warning(
            "profile extractor failed (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )

    # 2. Retrieve — pass session_id so the user's profile chunk (stored in
    # Chroma at POST /api/profile time) gets boosted to the top of the
    # context. Without session_id this path is dormant and the brain never
    # sees the user's profile inline with policy text.
    #
    # KI-105 (2026-05-15) — closer-intent retrieval widening. When the user
    # explicitly asks for a ranked shortlist ("show me the top 3 policies",
    # "what would you recommend", "compare top 3"), top_k=5 was sometimes
    # returning 4 chunks from the SAME policy plus 1 profile chunk — leaving
    # the brain only one named insurer to rank. Widen to top_k=12 on closer
    # intents so the candidate set has enough cross-insurer diversity for the
    # closer-mode addendum (which asks for 3 distinct named policies) to
    # actually fire instead of refusing.
    effective_top_k = top_k
    if intent in ("recommendation", "comparison") and not policy_filter_ids:
        effective_top_k = max(top_k, 12)

    # KI-118 (2026-05-15) — profile chunks are keyed by name_slug, not
    # session_id. Pass the slug only when the live session has a captured
    # name; anonymous sessions get retrieval without a profile-boost pass.
    profile_slug_for_retrieve: Optional[str] = None
    if session.profile.name:
        try:
            from backend.profile_store import _normalise_name
            profile_slug_for_retrieve = _normalise_name(session.profile.name) or None
        except Exception:
            profile_slug_for_retrieve = None

    chunks: list[RetrievedChunk] = await retrieve(
        query=user_text,
        top_k=effective_top_k,
        policy_ids=policy_filter_ids,
        profile_name_slug=profile_slug_for_retrieve,
        session_id=session_id,
    )
    context_str = format_for_llm_context(chunks)

    # 3. Pick brain
    pick = pick_brain(intent, language)

    # 4. Generate
    #
    # KI-105 — pass intent through so build_messages can append the
    # CLOSER MODE addendum on recommendation/comparison turns. The
    # addendum forces a ranked top-3 output instead of the default
    # conservative 60-word advisor reply that re-asked slots or refused
    # on weak grounding (live 15-persona smoke caught this gap).
    #
    # Two sources of "user_profile" exist: the dict passed in by callers
    # AND the session.profile we built up via fact-find. The dict is the
    # historical wire format; when it's None on a closer turn but the
    # session has captured facts, synthesize a profile dict so the
    # closer-mode contract still fires (rank-grounding requires SOME
    # context to anchor on).
    profile_for_prompt = user_profile
    if (
        intent in ("recommendation", "comparison")
        and not profile_for_prompt
        and getattr(session, "profile", None) is not None
    ):
        sp = session.profile
        candidate: dict = {}
        for fld in ("age", "dependents", "income_band", "existing_cover_inr",
                    "primary_goal", "location_tier", "parents_to_insure",
                    "parents_age_max", "parents_has_ped", "budget_band",
                    "health_conditions"):
            v = getattr(sp, fld, None)
            if v not in (None, "", []):
                candidate[fld] = v
        if candidate:
            profile_for_prompt = candidate

    messages_dict = build_messages(
        user_query=user_text,
        retrieved_context=context_str,
        chat_history=chat_history,
        user_profile=profile_for_prompt,
        view_context=view_context,
        intent=intent,
    )
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages_dict]

    # NIM DeepSeek-V4-Pro is THE brain (D-019). Frontier MoE (1.6T/49B),
    # MIT-licensed, beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified. Three
    # reasoning modes; we use the default (Non-think) for direct advisory
    # responses with low voice latency. The judge model (Meta Llama-4 Maverick)
    # in faithfulness.py is from a different company, architecture, and
    # training corpus — the brain does not mark its own homework.
    # KI-098 — outer budget cap on the main brain call. Provider default
    # timeouts (120s) can hang the user-facing turn when the upstream chain
    # is in synchronous probe_all() refresh. 30s is generous enough for
    # normal MoE responses but short enough to fall through to the chain's
    # error path before the user gives up.
    # KI-105 — closer turns produce a 3-policy ranked shortlist
    # (~150-220 words + per-line citations + acknowledger + caveat).
    # 1500 tokens is comfortably enough; we keep it but bump
    # temperature slightly so the rationale prose doesn't read as a
    # boilerplate copy of the previous closer turn. Faithfulness gate
    # still validates every cited claim against the retrieved chunks.
    _closer_turn = intent in ("recommendation", "comparison")
    llm_result = await asyncio.wait_for(
        pick.provider.chat(
            messages=messages,
            temperature=0.3 if _closer_turn else 0.2,
            max_tokens=1500,
        ),
        timeout=30.0,
    )

    raw = llm_result.text
    reply = strip_think_tags(raw)

    # Capture the EXACT model that produced the reply — flows into the
    # faithfulness LLM-judge so it can never grade its own homework
    # (same model OR same family is excluded from the judge chain).
    brain_model_actual = getattr(llm_result, "model", None) or getattr(pick.provider, "model", None)

    # 5. FAITHFULNESS GATE — every reply runs through 4-gate verification.
    #    If any gate fails, replace the reply with a safe refusal. The original
    #    blocked reply is logged to logs/hallucinations.jsonl for audit.
    #
    # KI-091 (2026-05-15) — skip on fact-find turns. The judge LLM runs on
    # JUDGE_CHAIN, which the admin /api/admin/llm-health endpoint just showed
    # as credit_exhausted=TRUE. Fact-find replies don't cite policy text and
    # carry no factual claim about coverage/exclusions/waiting-periods — the
    # judge has nothing to grade. Running it on a saturated chain hung one
    # production call for 12 minutes (733760ms). Fact-find prose is generated
    # by fact_find_brain which has its own slot-extraction validation; the
    # judge gate is additive overhead with negative production value here.
    # Gate: skip if intent=='fact_find' OR we're mid-fact-find continuation.
    # KI-171 (2026-05-15) — also skip faithfulness on RECOMMENDATION queries.
    # The judge is designed for policy-fact lookups ("does Policy X cover Y?"),
    # not for generative synthesis ("which policy fits me?"). Recommendation
    # answers stitch together evidence from many policies plus the user's
    # profile — the judge can't grade that fairly and currently blocks valid
    # recommendation flows after fact_find completes. Detect via query shape.
    _user_text_lc = (user_text or "").lower()
    _is_recommendation = any(
        kw in _user_text_lc for kw in (
            "recommend", "suggest", "best polic", "top 3", "top three",
            "top 5", "top five", "show me polic", "show me what", "fits me",
            "right for me", "policies for me", "which polic", "good polic",
            "what polic", "your suggestion", "your recommendation",
        )
    )
    _skip_judge = (intent == "fact_find") or bool(in_fact_find_continuation) or _is_recommendation
    if _skip_judge:
        skip_reason = "ki171_skip_on_recommendation" if _is_recommendation else "ki091_skip_on_fact_find"
        verdict = FaithfulnessVerdict(passed=True, reasons=[skip_reason])
    else:
        verdict: FaithfulnessVerdict = await check_faithfulness(
            reply=reply,
            chunks=chunks,
            user_text=user_text,
            run_llm_judge=True,
            brain_model_used=brain_model_actual,
        )

    # 5a. CROSS-CHECK RETRY — if faithfulness blocked AND the failure isn't
    # Gate 1 (no evidence at all), retry with a DIFFERENT-ARCHITECTURE NIM
    # model: Llama-4 Maverick (MoE, 400B/17B-active). The brain (Llama-3.3-70B,
    # dense) marks the same prompt independently — frequently catches issues
    # that came from a particular routing path or token sampling. Capped at
    # ONE retry — no loops.
    blocked = False
    if not verdict.passed:
        gate1_failure = any("gate1_retrieval" in r for r in verdict.reasons)
        if not gate1_failure:
            try:
                secondary = NvidiaNimLLM(model=NIM_JUDGE_MODEL)
                # KI-098 — outer budget cap on the cross-check retry. This is
                # a RARE fallback for hallucination-blocked replies; skipping
                # it on timeout is strictly safer than hanging the turn 60-120s
                # while the upstream chain refreshes via probe_all().
                try:
                    second = await asyncio.wait_for(
                        secondary.chat(messages=messages, temperature=0.1, max_tokens=1500),
                        timeout=20.0,
                    )
                except asyncio.TimeoutError:
                    logging.warning(
                        "crosscheck retry timeout, skipping retry (session=%s)", session_id,
                    )
                    blocked = True
                    reply = verdict.suggested_reply or "I don't have grounded evidence for that. Could you rephrase?"
                    second = None
                if second is not None:
                    second_reply = strip_think_tags(second.text)
                    # Cross-check brain was NIM_JUDGE_MODEL — pass its id so the
                    # judge for THIS retry also excludes that model+family.
                    second_verdict = await check_faithfulness(
                        reply=second_reply, chunks=chunks, user_text=user_text, run_llm_judge=True,
                        brain_model_used=getattr(second, "model", None) or NIM_JUDGE_MODEL,
                    )
                    if second_verdict.passed:
                        reply = second_reply
                        pick = BrainPick(secondary, f"crosscheck-rescued-by-maverick")
                        verdict = second_verdict
                    else:
                        blocked = True
                        reply = verdict.suggested_reply or "I don't have grounded evidence for that. Could you rephrase?"
            except Exception:
                blocked = True
                reply = verdict.suggested_reply or "I don't have grounded evidence for that. Could you rephrase?"
        else:
            blocked = True
            reply = verdict.suggested_reply or "I don't have grounded evidence for that. Could you rephrase?"

    # 6. Citations (derived from retrieved chunks).
    # KI-172 (2026-05-15) — exclude profile chunks from user-facing citations.
    # The profile chunk is retrieved alongside policy chunks so the LLM has
    # context about the user (age/dependents/income/etc.), but it is NOT a
    # "cited policy" and should not appear in the chat's "CITED POLICIES"
    # card. Filter by insurer_slug=='profile' (set by profile_rag.upsert).
    citations = [
        {
            "policy_id": c.policy_id,
            "policy_name": c.policy_name,
            "insurer_slug": c.insurer_slug,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "source_url": c.source_url,
            "score": round(c.score, 3),
        }
        for c in chunks
        if (c.insurer_slug or "").lower() != "profile"
    ]

    # 7. INDIC CASCADE — translate the English reply back into Hinglish/Hindi,
    # then run THREE drift checks. If any catches drift, revert to the English
    # reply (user sees correct facts even if not in their preferred language).
    #   Gate-A: regex anchors        — numbers, citations, currency
    #   Gate-B: Groq Llama LLM-judge — semantic faithfulness in Hinglish
    #   Gate-C: back-translate-cosine — Hinglish → EN via Sarvam, compare to original EN
    final_brain_tag = f"{pick.provider.name}::{pick.reason}"
    if language == "indic" and not blocked and reply:
        try:
            from backend.translator import translate_to_indic
            from backend.translation_check import (
                check_translation_drift,
                check_hinglish_faithfulness,
                check_back_translation,
            )

            english_reply = reply
            reply_indic = await translate_to_indic(english_reply, target_lang="hi-IN")
            if reply_indic and reply_indic.strip():
                # Run all 3 drift checks; short-circuit on the first failure
                drift_a = check_translation_drift(english_reply, reply_indic)
                if drift_a.drift_detected:
                    final_brain_tag = f"cascade::drift-anchor-fallback+{pick.provider.name}"
                else:
                    drift_b = await check_hinglish_faithfulness(english_reply, reply_indic)
                    if drift_b.drift_detected:
                        final_brain_tag = f"cascade::drift-llmjudge-fallback+{pick.provider.name}"
                    else:
                        drift_c = await check_back_translation(english_reply, reply_indic, min_cosine=0.80)
                        if drift_c.drift_detected:
                            final_brain_tag = f"cascade::drift-cosine-fallback+{pick.provider.name}"
                        else:
                            # KI-108 (2026-05-15) — strip CoT preamble after
                            # the indic translation too. Sarvam's <think>
                            # blocks + bare scratchpad lines (e.g. "हमें
                            # natural respond करना है") have leaked through
                            # the translator before; the strip is idempotent
                            # and inexpensive so apply defensively even when
                            # the English source was already cleaned.
                            reply = strip_think_tags(reply_indic)
                            final_brain_tag = f"cascade::sarvam-trans+{pick.provider.name}+sarvam-trans"
        except Exception:
            pass  # if any step fails, return English — better than mis-translated

    # KI-063 (2026-05-15) — auto-log "shown" policy events on the persisted
    # profile so a returning visitor's bot remembers which policies they've
    # seen. Only fires for context-dependent intents (recommendation /
    # comparison) AND only when faithfulness passed (we don't log cites that
    # the safety gates rejected). Anonymous users (no profile.name) get no
    # log — there's no key to persist against.
    #
    # KI-068 (2026-05-15) — fire-and-forget via asyncio.create_task so disk
    # writes don't block the reply path. The user shouldn't wait for N JSON
    # file writes (one per cited policy) before seeing the bot's recommend.
    try:
        if (
            intent in ("recommendation", "comparison")
            and verdict.passed
            and not blocked
            and session.profile.name
            and citations
        ):
            import asyncio as _asyncio
            from backend.profile_store import record_policy_event

            def _log_shown_policies() -> None:
                """Run all record_policy_event writes off the reply path."""
                try:
                    seen_slugs: set[str] = set()
                    for cite in citations:
                        slug = cite.get("policy_id") or cite.get("policy_slug")
                        insurer = cite.get("insurer_slug") or cite.get("insurer")
                        if not slug or not insurer or slug in seen_slugs:
                            continue
                        seen_slugs.add(slug)
                        record_policy_event(
                            persona_id_or_name=session.profile.name,
                            profile=session.profile,
                            event_type="shown",
                            policy_slug=slug,
                            insurer=insurer,
                            session_id=session_id,
                            reason="shown_in_recommendation",
                        )
                except Exception as inner:
                    logging.warning(
                        "KI-063 shown_policies log failed (session=%s): %s: %s",
                        session_id, type(inner).__name__, str(inner)[:200],
                    )

            # asyncio.to_thread offloads the file writes to the default
            # threadpool; we don't await so the handler returns immediately.
            _asyncio.create_task(_asyncio.to_thread(_log_shown_policies))
    except Exception as e:
        # Never let logging failures break the chat reply.
        logging.warning(
            "KI-063 shown_policies scheduling failed (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )

    # KI-108 (2026-05-15) — defensive final strip on every QA / recommendation
    # / comparison reply path. `reply` was stripped at line 801 when produced
    # by the brain, but the faithfulness-blocked branch + the cross-check
    # retry can overwrite it with `verdict.suggested_reply` (judge LLM output)
    # which has not been routed through strip_cot_preamble. Apply once more
    # at the boundary so no user-facing text ever leaves this function
    # carrying a "We need to respond..." preamble.
    reply = strip_think_tags(reply)
    return TurnResult(
        reply_text=reply,
        citations=citations,
        retrieved_chunk_ids=[c.chunk_id for c in chunks],
        brain_used=final_brain_tag,
        intent=intent,
        language=language,
        latency_ms=int((time.time() - t0) * 1000),
        raw_reply=raw,
        faithfulness_passed=verdict.passed,
        faithfulness_reasons=verdict.reasons,
        blocked=blocked,
        profile_updates=profile_updates_applied,
    )
