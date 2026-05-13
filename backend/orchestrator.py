"""Orchestrator — the brain of the bot.

For each user turn:
  1. Retrieve top-k relevant chunks from Chroma
  2. Format them as cited context
  3. Build messages with persona + history + profile
  4. Route to a brain LLM (Sarvam-M primary, Llama/DeepSeek fallback for complex)
  5. Strip <think> tags from Sarvam-M output
  6. Return (reply_text, citations[], retrieved_chunk_ids[], cost_estimate)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from backend.faithfulness import check_faithfulness, FaithfulnessVerdict
from backend.persona import build_messages, strip_think_tags
from backend.providers.base import ChatMessage, LLMProvider
from backend.providers.groq_llm import GroqLLM
from backend.providers.openrouter_llm import OpenRouterLLM
from backend.providers.sarvam_llm import SarvamLLM
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


def classify_intent(query: str) -> str:
    q = query.lower().strip()
    # Greeting / advice-seeking openers → fact-find flow
    if any(kw in q for kw in FACT_FIND_TRIGGERS) and len(q.split()) < 25:
        return "fact_find"
    if any(kw in q for kw in COMPARISON_KEYWORDS):
        return "comparison"
    if any(kw in q for kw in RECOMMEND_KEYWORDS):
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


def pick_brain(intent: str, language: str) -> BrainPick:
    """Route to the right brain per Doc decisions.md D-016 (rev 2026-05-13).

    English: DeepSeek-V3 always (reasoning quality > Sarvam-M for English Q&A).
    Indic: handled in handle_turn via translation cascade (Sarvam translates
    in, DeepSeek reasons, Sarvam translates back). This function returns the
    REASONING brain in both cases; the Indic in/out translation is done
    separately by translator.py.
    """
    return BrainPick(OpenRouterLLM(), f"reasoning-{intent}")


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


async def handle_turn(
    user_text: str,
    chat_history: Optional[list[dict]] = None,
    user_profile: Optional[dict] = None,
    policy_filter_ids: Optional[list[str]] = None,
    top_k: int = 5,
    session_id: Optional[str] = None,
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
        except Exception:
            pass  # fall through with original; if Sarvam translator fails, DeepSeek can still try

    # 1b. SESSION-STATE-AWARE FACT-FIND
    # Load session state. If we're already in fact-find (awaiting an answer to a
    # specific question), interpret the user's message as that answer and emit
    # the next question — regardless of whether intent_classifier thinks it's a
    # fact-find phrase. This is what fixes "39 years old" being misrouted to RAG.
    from backend.needs_finder import next_question, record_answer
    from backend.session_state import get_session
    session = get_session(session_id or "anonymous")

    in_fact_find_continuation = bool(session.awaiting_question_id) and not session.free_form_session
    treat_as_fact_find = (intent == "fact_find" and not session.free_form_session) or in_fact_find_continuation

    if treat_as_fact_find:
        # If we were awaiting an answer, parse + record it before picking next Q.
        if session.awaiting_question_id:
            session.record_user_answer(user_text)

        q = next_question(session.profile, language=language)
        if q is not None:
            session.set_awaiting(q.id)
            if in_fact_find_continuation:
                opener_en = "Got it. "
                opener_hi = "ठीक है। "
            else:
                opener_en = "Happy to help. " if not user_text.lower().strip().startswith(("hi", "hello")) else "Hi! "
                opener_hi = "मदद के लिए तैयार हूँ। "
            reply = (opener_hi + q.prompt_hi) if language == "indic" else (opener_en + q.prompt_en)
            brain_tag = "needs_finder::fact_find_continue" if in_fact_find_continuation else "needs_finder::fact_find_start"
        else:
            # Fact-find complete — produce a profile readback + invite next step
            from backend.needs_finder import readback_summary
            session.set_awaiting(None)
            summary = readback_summary(session.profile)
            reply = (
                f"Got it — here's what I've understood: {summary}. Want me to suggest 2-3 policies that fit your profile, "
                "or do you have a specific policy in mind to dig into?"
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
        )

    # User explicitly asked a specific question — leave fact-find mode if they were in one.
    if session.awaiting_question_id:
        session.set_awaiting(None)
        session.free_form_session = True

    # 2. Retrieve
    chunks: list[RetrievedChunk] = await retrieve(
        query=user_text,
        top_k=top_k,
        policy_ids=policy_filter_ids,
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
    )
    messages = [ChatMessage(role=m["role"], content=m["content"]) for m in messages_dict]

    try:
        llm_result = await pick.provider.chat(messages=messages, temperature=0.2, max_tokens=1500)
    except Exception as e:
        # Fallback to Groq Llama if primary brain fails
        fallback = GroqLLM()
        llm_result = await fallback.chat(messages=messages, temperature=0.2, max_tokens=1500)
        pick = BrainPick(fallback, f"fallback-after-{type(e).__name__}")

    # Detect truncated <think> reasoning — if so, retry with Groq (no reasoning tags)
    if "<think>" in llm_result.text.lower() and "</think>" not in llm_result.text.lower():
        try:
            fallback = GroqLLM()
            llm_result = await fallback.chat(messages=messages, temperature=0.2, max_tokens=1500)
            pick = BrainPick(fallback, "fallback-truncated-reasoning")
        except Exception:
            pass

    raw = llm_result.text
    reply = strip_think_tags(raw)

    # 5. FAITHFULNESS GATE — every reply runs through 4-gate verification.
    #    If any gate fails, replace the reply with a safe refusal. The original
    #    blocked reply is logged to logs/hallucinations.jsonl for audit.
    verdict: FaithfulnessVerdict = await check_faithfulness(
        reply=reply,
        chunks=chunks,
        user_text=user_text,
        run_llm_judge=True,
    )

    # 5a. CROSS-CHECK RETRY — if faithfulness blocked AND the failure isn't
    # Gate 1 (no evidence at all), try a DIFFERENT-FAMILY brain. Picks the
    # opposite family of whatever the primary was:
    #   primary Sarvam-M → cross-check DeepSeek-V3
    #   primary DeepSeek-V3 → cross-check Sarvam-M
    # Capped at ONE retry — no loops.
    blocked = False
    if not verdict.passed:
        gate1_failure = any("gate1_retrieval" in r for r in verdict.reasons)
        if not gate1_failure:
            # Pick the OTHER family for the rescue pass
            primary_name = pick.provider.name
            try:
                if primary_name == "sarvam-m":
                    secondary = OpenRouterLLM()
                else:
                    secondary = SarvamLLM()
                second = await secondary.chat(messages=messages, temperature=0.1, max_tokens=1500)
                second_reply = strip_think_tags(second.text)
                second_verdict = await check_faithfulness(
                    reply=second_reply, chunks=chunks, user_text=user_text, run_llm_judge=True,
                )
                if second_verdict.passed:
                    reply = second_reply
                    pick = BrainPick(secondary, f"crosscheck-rescued-{primary_name}")
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
    )
