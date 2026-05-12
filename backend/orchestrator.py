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
    """Route to the right brain per Doc decisions.md D-016.

    v1 heuristic (will be refined by gold eval results):
      - Indic queries -> Sarvam-M (it's the Indic-strongest)
      - Simple QA in English -> Sarvam-M (it's primary)
      - Comparison/recommendation -> OpenRouter DeepSeek-V3 (strongest reasoning)
      - (Llama-3.3-70B reserved for grader; used as fallback if DeepSeek fails)
    """
    if language == "indic":
        return BrainPick(SarvamLLM(), "indic-query")
    if intent in ("comparison", "recommendation"):
        return BrainPick(OpenRouterLLM(), f"complex-{intent}")
    return BrainPick(SarvamLLM(), "simple-qa")


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
) -> TurnResult:
    t0 = time.time()

    # 1. Classify
    intent = classify_intent(user_text)
    language = detect_language(user_text)

    # 1a. Fact-find branch — conversational openers / advice-seeking queries
    # bypass retrieval + faithfulness; we ask the next discovery question.
    if intent == "fact_find":
        from backend.needs_finder import Profile, next_question
        profile = Profile()
        if user_profile:
            for k, v in user_profile.items():
                if hasattr(profile, k):
                    setattr(profile, k, v)
        q = next_question(profile, language=language)
        if q is not None:
            opener_en = "Happy to help. " if "hi" not in user_text.lower()[:3] else "Hi! "
            opener_hi = "मदद के लिए तैयार हूँ। "
            reply = (opener_hi + q.prompt_hi) if language == "indic" else (opener_en + q.prompt_en)
        else:
            reply = ("Great — sounds like you've thought through your needs. "
                     "Want to ask about a specific policy, or have me compare a few for your profile?")
        return TurnResult(
            reply_text=reply,
            citations=[],
            retrieved_chunk_ids=[],
            brain_used="needs_finder::fact_find",
            intent=intent,
            language=language,
            latency_ms=int((time.time() - t0) * 1000),
            raw_reply=reply,
            faithfulness_passed=True,
            blocked=False,
        )

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
    blocked = False
    if not verdict.passed:
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

    return TurnResult(
        reply_text=reply,
        citations=citations,
        retrieved_chunk_ids=[c.chunk_id for c in chunks],
        brain_used=f"{pick.provider.name}::{pick.reason}",
        intent=intent,
        language=language,
        latency_ms=int((time.time() - t0) * 1000),
        raw_reply=raw,
        faithfulness_passed=verdict.passed,
        faithfulness_reasons=verdict.reasons,
        blocked=blocked,
    )
