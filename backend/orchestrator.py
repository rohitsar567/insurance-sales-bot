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

import hashlib
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
)

COMPARISON_KEYWORDS = ("compare", "comparison", "vs", "versus", "between policy", "which is better")
RECOMMEND_KEYWORDS = ("recommend", "should i", "which one", "best for", "suit me")
INDIC_KEYWORDS = (
    # Devanagari letters
    "क", "ख", "ग", "घ", "च", "ज", "ट", "ड", "त", "द", "न", "प", "ब", "म", "य", "र", "ल", "व", "स", "ह",
    # common Hinglish words
    " hai ", " kya ", " mein ", " kar ", " ka ", " ki ", " ke ", " liye ", " mujhe ",
)


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


def classify_intent(query: str) -> str:
    q = query.lower().strip()
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
        return True
    return False


def pick_brain(intent: str, language: str) -> BrainPick:
    """Route to the reasoning brain per D-019 (2026-05-14, tiered routing).

    All routes go through NVIDIA NIM (single provider, $0 cost). Tier picked
    by intent classification:
      - 'comparison' / 'recommendation' → DeepSeek-V4-Pro (1.6T/49B MoE)
            Heavy synthesis, multi-policy reasoning. Quality > latency.
      - 'fact_find' / 'qa'              → DeepSeek-V4-Flash (284B/13B MoE)
            Single-turn voice responses. Latency > quality, still frontier-tier.

    Indic queries get a Sarvam-M translation pass in `handle_turn` before
    retrieval, then another after reasoning to convert the English reply back
    to Hindi/Hinglish. The brain itself always reasons in English on English
    context.
    """
    HEAVY_INTENTS = {"comparison", "recommendation"}
    if intent in HEAVY_INTENTS:
        return BrainPick(get_brain_llm(), f"v4-pro::{intent}")
    return BrainPick(get_fast_brain_llm(), f"v4-flash::{intent}")


# ---------- conversational acknowledgers (KI-056, 2026-05-15) ----------
#
# Previous behaviour: every fact-find continuation turn started with the
# literal "Got it. " — three turns in a row with the same opener felt robotic
# and triggered user feedback. We now rotate through a small set of natural
# acknowledgers (deterministic per session+turn so the wording is stable on
# replay) AND, when the user's message mentions family, swap in a
# family-aware opener that explicitly acknowledges the disclosure.

_FAMILY_DISCLOSURE_RE = re.compile(
    r"\b(wife|husband|spouse|partner|kids?|children|child|parents?|family)\b",
    re.IGNORECASE,
)

# Plain rotation — when nothing special is going on. Trailing space included so
# callers can concatenate directly; `""` lets some turns skip the opener
# entirely and go straight into the next question.
_NEUTRAL_OPENERS_EN: tuple[str, ...] = (
    "Thanks for that. ",
    "Noted. ",
    "Helpful — ",
    "Right, ",
    "OK. ",
    "Got it. ",
    "Makes sense. ",
    "",
)

# Family-aware variants — picked when the user's message references a spouse,
# kids, or parents. The bot should signal that it actually heard the family
# mention rather than mechanically advancing.
_FAMILY_OPENERS_EN: tuple[str, ...] = (
    "Understood — for you and your family, then. ",
    "Noted — covering your family. ",
    "OK, family coverage to think about. ",
)
_SPOUSE_OPENERS_EN: tuple[str, ...] = (
    "Understood — for you and your spouse, then. ",
    "Noted — covering you and your spouse. ",
    "OK, that means two people on the policy. ",
)
_KIDS_OPENERS_EN: tuple[str, ...] = (
    "Noted — covering your kids too. ",
    "OK, family-floater territory then. ",
)
_PARENTS_OPENERS_EN: tuple[str, ...] = (
    "Noted — your parents in the mix as well. ",
    "OK, parent coverage to factor in. ",
)


def _family_aware_opener(user_text: str, fallback: str) -> Optional[str]:
    """Pick a family-aware acknowledger if `user_text` mentions a spouse,
    kids, or parents; otherwise return None so the caller uses `fallback`.

    KI-056 (2026-05-15). The opener picked here is intentionally more
    specific than the neutral rotation so the user feels heard when they
    volunteer family information mid-flow.
    """
    if not user_text:
        return None
    t = user_text.lower()
    has_spouse = bool(re.search(r"\b(wife|husband|spouse|partner)\b", t))
    has_kids = bool(re.search(r"\b(kids?|children|child)\b", t))
    has_parents = bool(re.search(r"\bparents?\b", t))
    has_family_word = "family" in t
    if not (has_spouse or has_kids or has_parents or has_family_word):
        return None
    # Pick the most specific variant available — order matters.
    if has_spouse and (has_kids or has_parents):
        pool = _FAMILY_OPENERS_EN
    elif has_spouse:
        pool = _SPOUSE_OPENERS_EN
    elif has_kids:
        pool = _KIDS_OPENERS_EN
    elif has_parents:
        pool = _PARENTS_OPENERS_EN
    else:  # has_family_word only
        pool = _FAMILY_OPENERS_EN
    # Deterministic pick based on the fallback string so two consecutive calls
    # with similar context don't collide on the same variant.
    idx = (sum(ord(c) for c in (fallback or "x")) % len(pool))
    return pool[idx]


_SELF_INTRO_RE = re.compile(
    # KI-062 (2026-05-15) — widened from 1-2 words to 1-4 words so full
    # names like "Rohit Sar" or "Anjali Devi Kumar" get captured.
    r"\b(?:i'?m|i\s+am|this\s+is|my\s+name\s+is|name\s+is|call\s+me|name'?s)\s+"
    r"([a-zA-Z][a-zA-Z'\-]{1,30}"
    r"(?:\s+[a-zA-Z][a-zA-Z'\-]{1,30}){0,3})\b",
    re.IGNORECASE,
)

# KI-059 (2026-05-15) — leading greeting tokens to ignore so "Hi this is X"
# and "Hello, my name is X" reach the introduction phrase.
_GREETING_LEAD = re.compile(
    r"^\s*(?:hi|hello|hey|namaste|yo|hola)[,!.\s]+",
    re.IGNORECASE,
)


def _contains_self_introduction(text: str) -> bool:
    """True if the message volunteers a name via "I'm X" / "this is X" /
    "my name is X" / "call me X" — with or without a greeting prefix.

    Used by KI-059 to detect when a user has supplied their name in the
    very first turn so we don't ask "what should I call you?" right after
    they told us.
    """
    if not text:
        return False
    stripped = _GREETING_LEAD.sub("", text.strip())
    return bool(_SELF_INTRO_RE.search(stripped))


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


def _pick_opener(
    user_text: str,
    session_id: Optional[str],
    turn_idx: int,
    slot_just_filled: Optional[str],
) -> str:
    """Choose a natural-language acknowledger for the bot's next reply.

    KI-056 (2026-05-15). Replaces the hardcoded literal "Got it. " opener
    that appeared on every fact-find continuation turn. The opener varies
    deterministically by (session_id, turn_idx) so the same user gets a
    different acknowledger each turn — but two replays of the same session
    produce the same wording (testable).

    If the user's message contains a spouse/family/parents disclosure, the
    opener swaps to a family-aware variant that explicitly acknowledges it,
    so the user feels heard rather than ignored.
    """
    # Family-aware override takes precedence over the neutral rotation.
    fam = _family_aware_opener(user_text, fallback=f"{session_id}:{turn_idx}:{slot_just_filled or ''}")
    if fam is not None:
        return fam
    # Deterministic neutral rotation. Hash (session_id, turn_idx, slot)
    # so each turn rotates and different sessions decorrelate.
    seed = f"{session_id or 'anon'}|{turn_idx}|{slot_just_filled or ''}"
    h = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8], 16)
    return _NEUTRAL_OPENERS_EN[h % len(_NEUTRAL_OPENERS_EN)]


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
            import logging
            logging.warning(
                "indic translator failed (session=%s lang=%s): %s: %s",
                session_id, language, type(e).__name__, str(e)[:200],
            )

    # 1b. SESSION-STATE-AWARE FACT-FIND
    # Load session state. If we're already in fact-find (awaiting an answer to a
    # specific question), interpret the user's message as that answer and emit
    # the next question — regardless of whether intent_classifier thinks it's a
    # fact-find phrase. This is what fixes "39 years old" being misrouted to RAG.
    from backend.needs_finder import next_question, record_answer
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
    )

    if treat_as_fact_find:
        # KI-045 (2026-05-14) — natural-conversation classifier. Previously
        # EVERY user message during fact-find got parsed as an answer to the
        # awaiting slot. The bot couldn't gracefully handle off-topic asides,
        # mid-flow questions, or volunteered profile information. Now we
        # classify the user's message first; only "direct_answer" continues
        # into the existing normalizer flow.
        #
        # Heuristic-only (no LLM call — keeps latency negligible):
        #
        #   • question  → message ends with "?" AND has 4+ words AND no
        #                 obvious slot-answer pattern. User is asking
        #                 something off-topic; we exit fact-find and let
        #                 the QA brain answer.
        #   • intent_change → explicit phrases like "never mind", "actually
        #                     let me ask", "wait, can you...", "forget the
        #                     profile, just tell me...". User is steering
        #                     away; we exit fact-find.
        #   • otherwise → direct_answer (existing normalizer flow).
        #
        # In all non-answer cases we keep the awaiting slot pinned so when
        # the user returns to fact-find next turn, we pick up where we
        # left off — and we flip `session.free_form_session = True` so the
        # rest of handle_turn routes to retrieval + brain, not back here.
        if session.awaiting_question_id and session.awaiting_question_id != "name":
            _t = user_text.lower().strip()
            _intent_change_phrases = (
                "never mind", "forget the profile", "forget profile",
                "actually let me ask", "wait, can you", "wait can you",
                "wait, tell me", "skip this", "skip that question",
                "let's switch", "lets switch", "stop asking",
                "i have a question", "just tell me",
            )
            _looks_intent_change = any(p in _t for p in _intent_change_phrases)
            _ends_q = _t.endswith("?") or _t.rstrip(".!").endswith("?")
            _wordy_q = _ends_q and len(_t.split()) >= 4
            # Don't misclassify enum-answer questions like "spouse?" /
            # "diabetes?" (short, often a clarification request)
            _looks_off_topic_q = _wordy_q and not any(
                kw in _t for kw in (
                    "yes", "no", "self", "spouse", "kids", "parents",
                    "diabetes", "bp", "thyroid", "lakh", "lac",
                )
            )
            if _looks_intent_change or _looks_off_topic_q:
                # Exit fact-find: let the rest of handle_turn route to the
                # QA / brain path. Keep awaiting_question_id pinned so we
                # remember where we were for next turn. Flip free-form so
                # the should_route_to_fact_find guard returns False on the
                # follow-up turn.
                session.free_form_session = True
                session._flush()
                # Fall out of the fact-find branch entirely — the user's
                # message is treated as a regular QA turn from here.
                treat_as_fact_find = False  # noqa: F841 — re-checked below

        # If we just exited fact-find via the classifier, skip the answer
        # handling block + the question-emission tail; let execution fall
        # through to the QA / retrieval path below.

    if treat_as_fact_find:
        # If we were awaiting an answer, normalize + record it before picking next Q.
        # Uses backend/fact_find_normalizer.py to map free-text → schema enums.
        #
        # Two safety nets:
        # (1) Keyword fast-path inside normalize_answer() handles ~80% of
        #     answers without needing the NIM LLM (no rate-limit risk).
        # (2) Re-ask cap (`_reask_count` on the session) — after 2 consecutive
        #     failures on the same question we GIVE UP, skip that question,
        #     and proceed to the next. Better to have an incomplete profile
        #     than an infinite reask loop.
        ambiguous_or_failed = False
        # Telemetry: KI-019 (2026-05-14) — populate this dict whenever the
        # slot-filler successfully captures a normalized answer so the API
        # response's `profile_updates` field reflects fact-find captures (it
        # previously only reflected free-form mode captures, which made the
        # 100-persona audit appear to show age captured for only 12/100 when
        # the slot-filler was actually working — see readback summaries in
        # `needs_finder::fact_find_complete` turns).
        fact_find_profile_updates: dict = {}
        # KI-040 — named-profile return-visit detection. Set when we resolve
        # a name and find a stored profile for that name; used at the bottom
        # of this branch to emit a "Welcome back" message instead of the
        # next slot's question.
        returning_visitor_greeting: Optional[str] = None

        # KI-059 (2026-05-15) — opening-turn name capture. If the user
        # volunteered a name in their FIRST message ("Hi this is Rohit",
        # "I'm Anjali", "My name is Ravi") before we asked, route it into
        # the name-slot handler below so the existing extract + save +
        # welcome-back logic fires — instead of asking "what should I
        # call you?" right after they told us. Pre-condition: not yet
        # awaiting any slot, no name on the profile, and not in
        # free-form session.
        if (not session.awaiting_question_id
                and not session.profile.name
                and not session.free_form_session
                and _contains_self_introduction(user_text)):
            session.set_awaiting("name")

        if session.awaiting_question_id:
            from backend.fact_find_normalizer import is_valid_answer, normalize_answer
            qid = session.awaiting_question_id
            # KI-040 — special handling for the name slot: free-text, no enum.
            if qid == "name":
                from backend.profile_store import is_valid_name, load_profile, save_profile
                raw_name = user_text.strip().strip(".,!?")
                # KI-059 — strip a leading greeting + comma so "Hi, this is
                # Rohit" / "Hello I'm Anjali" reduces to the introduction
                # phrase the next loop expects.
                for greet in ("hi there ", "hello there ", "hey there ",
                              "hi, ", "hello, ", "hey, ",
                              "hi ", "hello ", "hey ", "namaste ", "yo "):
                    if raw_name.lower().startswith(greet):
                        raw_name = raw_name[len(greet):].strip()
                        break
                # Tolerate "I'm Rohit" / "My name is Rohit" / "call me Rohit"
                for prefix in ("i'm ", "i am ", "my name is ", "name is ", "call me ", "this is "):
                    if raw_name.lower().startswith(prefix):
                        raw_name = raw_name[len(prefix):].strip()
                        break
                # Capitalize a sensible display form
                if raw_name and not any(c.isupper() for c in raw_name):
                    raw_name = " ".join(w.capitalize() for w in raw_name.split())
                if is_valid_name(raw_name):
                    session.update_profile_field("name", raw_name)
                    fact_find_profile_updates["name"] = raw_name
                    if qid not in session.profile.asked:
                        session.profile.asked.append(qid)
                    session.set_awaiting(None)
                    if hasattr(session, "_reask_counts"):
                        session._reask_counts.pop(qid, None)
                    # Look up an existing profile for this name. If found,
                    # MERGE its fields into the current empty session so the
                    # user doesn't repeat the 9-question walk.
                    stored = load_profile(raw_name)
                    if stored is not None and any(
                        getattr(stored, f, None) not in (None, [], "")
                        for f in ("age", "dependents", "income_band", "primary_goal")
                    ):
                        for field_name in (
                            "age", "dependents", "income_band", "existing_cover_inr",
                            "primary_goal", "location_tier", "parents_to_insure",
                            "parents_age_max", "parents_has_ped", "health_conditions",
                            "budget_band",
                        ):
                            val = getattr(stored, field_name, None)
                            if val not in (None, [], ""):
                                session.update_profile_field(field_name, val)
                                fact_find_profile_updates[field_name] = val
                        # Mark all stored slots as already-asked so next_question
                        # skips them.
                        for slot_id in ("age", "dependents", "income_band",
                                        "existing_cover", "primary_goal", "location",
                                        "parents_age", "health_conditions", "budget"):
                            if slot_id not in session.profile.asked:
                                session.profile.asked.append(slot_id)
                        session.free_form_session = True
                        session._flush()
                        # KI-061 (2026-05-15) — personalized welcome-back:
                        # summarize what's on file, call out helpful gaps,
                        # offer next step. So the returning visitor sees
                        # the bot remembers them precisely, and any missing
                        # info can be filled before recommending.
                        known_summary = _format_known_profile_summary(stored)
                        missing_labels = _format_missing_slots(stored)
                        parts = [f"Welcome back, {raw_name}!"]
                        if known_summary:
                            parts.append(
                                f"Here's what I have on file from your last visit: {known_summary}."
                            )
                        if missing_labels:
                            if len(missing_labels) == 1:
                                gap_phrase = missing_labels[0]
                            elif len(missing_labels) == 2:
                                gap_phrase = " and ".join(missing_labels)
                            else:
                                gap_phrase = ", ".join(missing_labels[:-1]) + f", and {missing_labels[-1]}"
                            parts.append(
                                f"We never got around to {gap_phrase} — happy to fill that in "
                                "so I can grade policies more precisely, or you can jump straight "
                                "to a recommendation or any specific question."
                            )
                        else:
                            parts.append(
                                "Looks like we have everything we need — want me to suggest some "
                                "policies that fit, or do you have a specific question in mind?"
                            )
                        returning_visitor_greeting = " ".join(parts)
                    else:
                        # New visitor — record an initial profile so subsequent
                        # turns persist; orchestrator will continue to next slot.
                        save_profile(raw_name, session.profile, session_id=session_id)
                else:
                    ambiguous_or_failed = True
            elif not is_valid_answer(user_text):
                ambiguous_or_failed = True
            else:
                try:
                    normalized = await normalize_answer(qid, user_text)
                except Exception:
                    normalized = None
                if normalized is None:
                    ambiguous_or_failed = True
                else:
                    # Apply the normalized value to the right Profile field.
                    q_obj = next((q for q in __import__('backend.needs_finder', fromlist=['GRAPH']).GRAPH if q.id == qid), None)
                    if q_obj is not None:
                        session.update_profile_field(q_obj.field, normalized)
                        fact_find_profile_updates[q_obj.field] = normalized
                        if qid not in session.profile.asked:
                            session.profile.asked.append(qid)
                        session.set_awaiting(None)
                        # Reset re-ask counter on success
                        if hasattr(session, "_reask_counts"):
                            session._reask_counts.pop(qid, None)
                        # KI-040 — persist the updated profile to JSON store
                        # so next-visit lookup-by-name has the latest data.
                        if session.profile.name:
                            try:
                                from backend.profile_store import save_profile
                                save_profile(session.profile.name, session.profile, session_id=session_id)
                            except Exception:
                                pass
                    else:
                        ambiguous_or_failed = True

            # ---- Re-ask cap (safety against infinite loops) ----
            if ambiguous_or_failed:
                if not hasattr(session, "_reask_counts"):
                    session._reask_counts = {}
                session._reask_counts[qid] = session._reask_counts.get(qid, 0) + 1
                if session._reask_counts[qid] >= 2:
                    # Give up on this question; mark it asked so next_question moves on.
                    if qid not in session.profile.asked:
                        session.profile.asked.append(qid)
                    session.set_awaiting(None)
                    ambiguous_or_failed = False  # no longer a reask situation

        # KI-056 (2026-05-15) — opportunistic dependents capture from any
        # free-text fact-find turn. If the user mentioned spouse / kids /
        # parents while answering an UNRELATED slot ("my wife also doesn't
        # have anything" in response to existing_cover), pre-fill the
        # dependents slot so we don't waste a turn asking again later.
        # Only fires when the slot is still empty — never overwrites an
        # explicit user-provided answer to the dependents question.
        if session.profile.dependents in (None, ""):
            from backend.needs_finder import infer_dependents_from_text
            inferred = infer_dependents_from_text(user_text)
            if inferred:
                session.update_profile_field("dependents", inferred)
                fact_find_profile_updates["dependents"] = inferred
                if "dependents" not in session.profile.asked:
                    session.profile.asked.append("dependents")

        # KI-040 — returning-visitor short-circuit. If we recognised the user's
        # name and loaded their stored profile, skip directly to the greeting
        # without picking another fact-find question.
        if returning_visitor_greeting:
            return TurnResult(
                reply_text=returning_visitor_greeting,
                citations=[],
                retrieved_chunk_ids=[],
                brain_used="needs_finder::welcome_back",
                intent="fact_find",
                language=language,
                latency_ms=int((time.time() - t0) * 1000),
                raw_reply=returning_visitor_greeting,
                faithfulness_passed=True,
                blocked=False,
                profile_updates=fact_find_profile_updates,
            )

        # If the answer didn't normalize, pick the SAME question again (re-ask
        # with a gentle clarifier) instead of moving on with garbage — UNLESS
        # the cap above just kicked in, in which case we move on.
        if ambiguous_or_failed and session.awaiting_question_id:
            q = next((qq for qq in __import__('backend.needs_finder', fromlist=['GRAPH']).GRAPH if qq.id == session.awaiting_question_id), None)
        else:
            q = next_question(session.profile, language=language)

        if q is not None:
            session.set_awaiting(q.id)
            if ambiguous_or_failed:
                opener_en = "Sorry, I didn't catch that. Let me ask again — "
                opener_hi = "माफ़ कीजिए, समझ नहीं आया। दोबारा पूछता हूँ — "
            elif in_fact_find_continuation:
                # KI-056 (2026-05-15) — dynamic acknowledger. Replaces the
                # literal "Got it. " that previously appeared at the start of
                # every continuation turn. Family disclosures get an explicit
                # acknowledgement; neutral turns rotate through 8 variants
                # deterministic on (session_id, turn_idx, slot).
                opener_en = _pick_opener(
                    user_text=user_text,
                    session_id=session_id,
                    turn_idx=len(session.profile.asked),
                    slot_just_filled=session.awaiting_question_id or None,
                )
                opener_hi = "ठीक है। "
            else:
                opener_en = "Happy to help. " if not user_text.lower().strip().startswith(("hi", "hello")) else "Hi! "
                opener_hi = "मदद के लिए तैयार हूँ। "

            # KI-032 — Per-turn LLM paraphrase + verifier so the bot stops
            # asking the same 9 hardcoded questions with the same wording in
            # every session. English only for now; the indic branch keeps
            # the canonical Hindi text. Re-ask flows ALSO skip paraphrase —
            # we want the literal "let me ask again" to read the canonical
            # so the user has a stable anchor.
            question_text_en = q.prompt_en
            if language != "indic" and not ambiguous_or_failed:
                try:
                    from backend.question_paraphraser import paraphrase_question
                    paraphrased = await paraphrase_question(
                        canonical=q.prompt_en,
                        slot_id=q.id,
                        session_id=session_id,
                        recent_user_text=user_text,
                    )
                    if paraphrased:
                        question_text_en = paraphrased
                except Exception:
                    # Paraphraser must never block fact-find; on any failure
                    # we silently keep the canonical wording.
                    pass

            reply = (opener_hi + q.prompt_hi) if language == "indic" else (opener_en + question_text_en)
            if ambiguous_or_failed:
                brain_tag = "needs_finder::reask_clarify"
            else:
                brain_tag = "needs_finder::fact_find_continue" if in_fact_find_continuation else "needs_finder::fact_find_start"
        else:
            # Fact-find complete — produce a profile readback + invite next step.
            # CRITICAL (KI-012): flip free_form_session=True so subsequent turns
            # don't re-enter the fact-find branch and repeat the readback.
            #
            # KI-015 — explicitly invite corrections in the readback message
            # before recommending. Real user testing surfaced that the bot
            # captured age=30 when user said 31, and the readback only said
            # "Want me to suggest…" — no explicit "correct me if wrong" prompt.
            # Corrections in the next turn flow through the conversational
            # profile-update extractor (free-form mode), so the bot WILL
            # absorb "actually I'm 31" — but the user has to know they can
            # say that. This message tells them.
            from backend.needs_finder import readback_summary
            session.set_awaiting(None)
            session.free_form_session = True
            session._flush()
            summary = readback_summary(session.profile)
            # KI-056 (2026-05-15) — dynamic readback opener. Picks a varied
            # acknowledger so the completion turn doesn't always start with
            # "Got it — here's what I've understood:".
            readback_opener = _pick_opener(
                user_text=user_text,
                session_id=session_id,
                turn_idx=len(session.profile.asked),
                slot_just_filled="__readback__",
            ).rstrip()
            if not readback_opener:
                readback_opener = "Here's what I've understood:"
            else:
                readback_opener = f"{readback_opener} Here's what I've understood:"
            reply = (
                f"{readback_opener} {summary}. "
                f"**If anything's wrong, just tell me** (e.g., \"actually I'm 31\", or "
                f"\"I want to cover my parents too\"). "
                f"Otherwise — want me to suggest 2-3 policies that fit your profile, "
                f"or do you have a specific policy in mind to dig into?"
            )
            brain_tag = "needs_finder::fact_find_complete"

        return TurnResult(
            reply_text=reply,
            citations=[],
            retrieved_chunk_ids=[],
            brain_used=brain_tag,
            intent="fact_find",
            language=language,
            latency_ms=int((time.time() - t0) * 1000),
            raw_reply=reply,
            faithfulness_passed=True,
            blocked=False,
            profile_updates=fact_find_profile_updates,  # KI-019 telemetry fix
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
    profile_updates_applied: dict = {}
    import os as _os_pe
    _skip_extract = _os_pe.environ.get("INSURANCE_BOT_SKIP_PROFILE_EXTRACTOR") == "1"
    try:
        if _skip_extract:
            extracted = None  # eval mode — bypass LLM extractor entirely
        else:
            from backend.profile_extractor import extract_profile_updates
            extracted = await extract_profile_updates(user_text, session.profile)
        if extracted:
            for field_name, new_value in extracted.items():
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
            # Re-upsert profile chunk so retrieval sees fresh profile THIS turn
            try:
                from backend.profile_rag import upsert_profile_chunk
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
                await upsert_profile_chunk(session_id or "anonymous", profile_dict_for_chunk)
            except Exception as e:
                # KI-005 — log profile-chunk upsert failures so we can see
                # when Chroma is locking or schema-drifting. The chat still
                # ships; subsequent turns just won't see the latest profile.
                import logging
                logging.warning(
                    "profile-chunk upsert failed (session=%s): %s: %s",
                    session_id, type(e).__name__, str(e)[:200],
                )
    except Exception as e:
        # KI-006 — log profile-extraction failures (extractor LLM down,
        # malformed model output, etc.). The chat ships unaffected.
        import logging
        logging.warning(
            "profile extractor failed (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )

    # 2. Retrieve — pass session_id so the user's profile chunk (stored in
    # Chroma at POST /api/profile time) gets boosted to the top of the
    # context. Without session_id this path is dormant and the brain never
    # sees the user's profile inline with policy text.
    chunks: list[RetrievedChunk] = await retrieve(
        query=user_text,
        top_k=top_k,
        policy_ids=policy_filter_ids,
        session_id=session_id,
    )
    context_str = format_for_llm_context(chunks)

    # 3. Pick brain
    pick = pick_brain(intent, language)

    # 4. Generate
    messages_dict = build_messages(
        user_query=user_text,
        retrieved_context=context_str,
        chat_history=chat_history,
        user_profile=user_profile,
        view_context=view_context,
    )
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages_dict]

    # NIM DeepSeek-V4-Pro is THE brain (D-019). Frontier MoE (1.6T/49B),
    # MIT-licensed, beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified. Three
    # reasoning modes; we use the default (Non-think) for direct advisory
    # responses with low voice latency. The judge model (Meta Llama-4 Maverick)
    # in faithfulness.py is from a different company, architecture, and
    # training corpus — the brain does not mark its own homework.
    llm_result = await pick.provider.chat(messages=messages, temperature=0.2, max_tokens=1500)

    raw = llm_result.text
    reply = strip_think_tags(raw)

    # Capture the EXACT model that produced the reply — flows into the
    # faithfulness LLM-judge so it can never grade its own homework
    # (same model OR same family is excluded from the judge chain).
    brain_model_actual = getattr(llm_result, "model", None) or getattr(pick.provider, "model", None)

    # 5. FAITHFULNESS GATE — every reply runs through 4-gate verification.
    #    If any gate fails, replace the reply with a safe refusal. The original
    #    blocked reply is logged to logs/hallucinations.jsonl for audit.
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
                second = await secondary.chat(messages=messages, temperature=0.1, max_tokens=1500)
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

    # 6. Citations (derived from retrieved chunks)
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
                            reply = reply_indic
                            final_brain_tag = f"cascade::sarvam-trans+{pick.provider.name}+sarvam-trans"
        except Exception:
            pass  # if any step fails, return English — better than mis-translated

    # KI-063 (2026-05-15) — auto-log "shown" policy events on the persisted
    # profile so a returning visitor's bot remembers which policies they've
    # seen. Only fires for context-dependent intents (recommendation /
    # comparison) AND only when faithfulness passed (we don't log cites that
    # the safety gates rejected). Anonymous users (no profile.name) get no
    # log — there's no key to persist against.
    try:
        if (
            intent in ("recommendation", "comparison")
            and verdict.passed
            and not blocked
            and session.profile.name
            and citations
        ):
            from backend.profile_store import record_policy_event
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
    except Exception as e:
        # Never let logging failures break the chat reply.
        import logging
        logging.warning(
            "KI-063 shown_policies log failed (session=%s): %s: %s",
            session_id, type(e).__name__, str(e)[:200],
        )

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
