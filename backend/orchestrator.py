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
        # KI-070 (2026-05-15) — single-LLM-call fact-find brain replaces the
        # 3-layer stitching of (hardcoded canonical prompts + per-turn
        # paraphraser + acknowledger rotation). One model call now produces
        # the natural reply AND a machine-readable trailer describing what
        # was captured, which slot it's driving toward, and whether
        # fact-find is complete.
        from backend.fact_find_brain import (
            FIELD_TO_QUESTION_ID,
            drive_fact_find,
        )

        outcome = await drive_fact_find(
            user_text=user_text,
            session=session,
            chat_history=chat_history,
            session_id=session_id,
        )

        # Apply captured updates to profile + mark matching slot ids as asked
        # so the orchestrator's downstream `next_question` (used in the
        # canonical fallback + completeness API) doesn't double-ask.
        fact_find_profile_updates: dict = {}
        for field_name, value in outcome.captured_updates.items():
            if value in (None, "", []):
                continue
            session.update_profile_field(field_name, value)
            fact_find_profile_updates[field_name] = value
            matching_slot = FIELD_TO_QUESTION_ID.get(field_name)
            if matching_slot and matching_slot not in session.profile.asked:
                session.profile.asked.append(matching_slot)

        # Track slot_driving so subsequent turns know where we are. The new
        # brain produces FIELD names (e.g., "income_band"); translate to the
        # canonical question id (e.g., "income_band") via FIELD_TO_QUESTION_ID
        # so the rest of the orchestrator (which keys on question ids) stays
        # consistent.
        if outcome.slot_driving:
            slot_qid = FIELD_TO_QUESTION_ID.get(outcome.slot_driving, outcome.slot_driving)
            session.set_awaiting(slot_qid)
        else:
            session.set_awaiting(None)

        # If the brain decided fact-find is complete, flip free-form so
        # subsequent turns route to retrieval+brain (not back here).
        if outcome.fact_find_complete:
            session.free_form_session = True
            session._flush()

        # KI-063 — opportunistic profile-chunk re-upsert if any field changed
        # so this turn's downstream retrieval (when complete=true triggers a
        # follow-up recommendation) sees the latest profile.
        if fact_find_profile_updates:
            try:
                from backend.profile_rag import upsert_profile_chunk
                p = session.profile
                await upsert_profile_chunk(session_id or "anonymous", {
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
                import logging
                logging.warning(
                    "fact_find_brain profile-chunk upsert failed (session=%s): %s: %s",
                    session_id, type(e).__name__, str(e)[:200],
                )

        # KI-040 / KI-062 — named-profile persistence preserved. If the brain
        # captured (or already has) a name on the profile, write the merged
        # profile to disk so the next visit can welcome the user back.
        if session.profile.name:
            try:
                from backend.profile_store import save_profile
                save_profile(session.profile.name, session.profile, session_id=session_id)
            except Exception:
                pass

        if outcome.ambiguous:
            # KI-078 (2026-05-15) — append fallback reason so admin telemetry
            # can measure the fallback-cause mix (timeout vs llm_error vs
            # no_trailer vs empty_reply). Essential for measuring KI-075 +
            # KI-078 impact in production.
            reason = getattr(outcome, "_fallback_reason", None) or "unknown"
            brain_tag = f"fact_find_brain::fallback:{reason}"
        elif outcome.fact_find_complete:
            brain_tag = "fact_find_brain::complete"
        else:
            brain_tag = "fact_find_brain::continue"

        return TurnResult(
            reply_text=outcome.reply_text,
            citations=[],
            retrieved_chunk_ids=[],
            brain_used=brain_tag,
            intent="fact_find",
            language=language,
            latency_ms=int((time.time() - t0) * 1000),
            raw_reply=outcome.reply_text,
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
                    import logging
                    logging.warning(
                        "KI-063 shown_policies log failed (session=%s): %s: %s",
                        session_id, type(inner).__name__, str(inner)[:200],
                    )

            # asyncio.to_thread offloads the file writes to the default
            # threadpool; we don't await so the handler returns immediately.
            _asyncio.create_task(_asyncio.to_thread(_log_shown_policies))
    except Exception as e:
        # Never let logging failures break the chat reply.
        import logging
        logging.warning(
            "KI-063 shown_policies scheduling failed (session=%s): %s: %s",
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
