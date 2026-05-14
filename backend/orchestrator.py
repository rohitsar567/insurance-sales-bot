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
    CONTEXT_DEPENDENT_INTENTS = {"recommendation", "comparison"}
    treat_as_fact_find = (
        (intent == "fact_find" and not session.free_form_session)
        or in_fact_find_continuation
        or (
            profile_is_empty
            and not session.free_form_session
            and intent in CONTEXT_DEPENDENT_INTENTS
        )
    )

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
        if session.awaiting_question_id:
            from backend.fact_find_normalizer import is_valid_answer, normalize_answer
            qid = session.awaiting_question_id
            if not is_valid_answer(user_text):
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
                opener_en = "Got it. "
                opener_hi = "ठीक है। "
            else:
                opener_en = "Happy to help. " if not user_text.lower().strip().startswith(("hi", "hello")) else "Hi! "
                opener_hi = "मदद के लिए तैयार हूँ। "
            reply = (opener_hi + q.prompt_hi) if language == "indic" else (opener_en + q.prompt_en)
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
            reply = (
                f"Got it — here's what I've understood: {summary}. "
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
    profile_updates_applied: dict = {}
    try:
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
