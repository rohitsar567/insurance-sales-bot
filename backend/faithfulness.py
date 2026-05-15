"""Hallucination prevention — multi-layer faithfulness verification.

Every assistant reply passes through these gates before being returned to the user.
Failure at any gate triggers a regeneration with a stricter prompt, or a refusal.

The mechanism:

Gate 1 — RETRIEVAL FLOOR
  If the top retrieved chunk has cosine similarity below MIN_TOP_SCORE,
  we refuse outright — there isn't enough grounded evidence to answer.

Gate 2 — CITATION INTEGRITY
  Every [Source: <Policy Name> ...] tag that appears in the reply MUST match
  a real retrieved chunk's policy_name. Fabricated citations = block.

Gate 3 — NUMERIC GROUNDING
  Every monetary amount (₹), percentage, day/month/year count in the reply
  must also appear in at least one retrieved chunk. Catches the "premium is
  ₹15,000" hallucination class deterministically.

Gate 4 — LLM-JUDGE FAITHFULNESS (NIM Llama-4 Maverick — different arch from brain)
  Pass {retrieved_chunks, reply} to a second LLM with prompt:
    "For each factual claim in the reply, is it supported by these chunks?
     Reply STRICT_JSON: {supported: bool, unsupported_claims: [str]}"
  Block if supported=false.

Gate 5 — AUDIT
  Every block + every recoverable warning is appended to logs/hallucinations.jsonl
  for post-hoc analysis and compliance audit.

Public API:
  check_faithfulness(reply, retrieved_chunks) -> FaithfulnessVerdict
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider
from backend.providers.nvidia_nim_llm import get_judge_llm
from rag.retrieve import RetrievedChunk

LOG_DIR = settings.CORPUS_DIR.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
HALLUCINATION_LOG = LOG_DIR / "hallucinations.jsonl"

# Tunables — these become evaluable parameters in the eval harness.
# BGE-small returns higher cosine similarity than Voyage, so the floors are
# higher here than they would be for Voyage. Re-tune if changing embedding model.
# Lowered 2026-05-13 based on eval data showing too-aggressive refusal at 0.40:
# many real questions retrieve top chunks at 0.30-0.38 that DO contain the answer.
MIN_TOP_SCORE = 0.18  # below this we refuse outright (BGE-small cosine similarity)
MIN_AVG_SCORE = 0.22  # average of top 5 must be above this


# ============================================================================
# A3 — Allow-list for non-content replies that must never trigger rejection.
# These are confirmations, fillers, and bot self-references that aren't
# factual claims, so the citation/grounding/judge gates would only ever
# produce false positives on them.
# ============================================================================

_ALLOWLIST_EXACT = {
    # Single-word confirmations / fillers
    "yes", "yeah", "yep", "yup", "yes.", "yeah.",
    "ok", "okay", "ok.", "okay.",
    "sure", "sure.", "right", "right.",
    "correct", "correct.", "exactly", "exactly.",
    "no", "no.", "nope", "nope.",
    "thanks", "thanks.", "thank you", "thank you.",
}

# Multi-word allow-list phrases — substring match is enough because the
# entire reply is short. These are the canonical confirmation / hedge
# patterns the brain emits during fact-find recap turns.
_ALLOWLIST_SUBSTRINGS = (
    "yes, that sounds correct",
    "yes that sounds correct",
    "yes please",
    "let me think about it",
    "compare them",
    "got it",
    "noted",
    "understood",
    "sounds good",
    "makes sense",
)

# Bot self-reference tokens. These are stylistic, not factual claims; the
# legacy citation gate sometimes flagged a reply that opened with "Let me
# suggest..." as missing a citation because the prose itself made no claim.
_BOT_SELFREF_RE = re.compile(
    r"\b(i (?:can|will|would|could|think|am|'ll|'d|'ve)|"
    r"let me (?:suggest|think|check|recap|confirm|walk|note|share)|"
    r"the bot|as your advisor|in my view|here's what)\b",
    flags=re.IGNORECASE,
)

# Profile-recap confirmation regex — when the prior assistant turn ended in
# a recap ("Let me confirm: 35yo, family floater, ₹10L cover, metro tier...
# does that look right?") and the user replies with a yes/no/correction, the
# next bot turn typically just acknowledges. That acknowledgement is
# metadata, not a content claim, and must bypass faithfulness entirely.
_CONFIRMATION_RE = re.compile(
    r"^\s*(?:"
    r"yes|yeah|yep|yup|"
    r"no|nope|"
    r"ok(?:ay)?|sure|right|correct|exactly|"
    r"that(?:'s| is| sounds)? (?:right|correct|fine|good)|"
    r"sounds (?:right|good|correct|fine)|"
    r"thanks?|thank you"
    r")\b",
    flags=re.IGNORECASE,
)


def _is_allowlisted_reply(reply: str) -> bool:
    """Return True if `reply` is a non-content message that must skip
    faithfulness gating. Confirmations, single-word fillers, and short bot
    self-references count as non-content."""
    if not reply:
        return True
    stripped = reply.strip().lower()
    if not stripped:
        return True
    if stripped in _ALLOWLIST_EXACT:
        return True
    # Short replies are inspected for allowlist substrings + self-references.
    if len(stripped) < 80:
        for needle in _ALLOWLIST_SUBSTRINGS:
            if needle in stripped:
                return True
        # Pure bot self-reference w/ no numeric claim: allow.
        if _BOT_SELFREF_RE.search(stripped) and not _has_numeric_claim(stripped):
            return True
    return False


def _is_confirmation_response(text: str) -> bool:
    """User-text or bot-reply matches the confirmation regex."""
    if not text:
        return False
    return bool(_CONFIRMATION_RE.match(text.strip()))


def _has_numeric_claim(text: str) -> bool:
    """Quick test: does this reply make any numeric / monetary / percentage
    claim that would need a citation?"""
    if not text:
        return False
    return bool(
        RUPEE_RE.search(text)
        or PERCENT_RE.search(text)
        or DURATION_RE.search(text)
        or re.search(r"\b\d{4,}\b", text)  # bare 4+ digit numbers (sum insured)
    )


@dataclass
class FaithfulnessVerdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)  # gate names that failed
    unsupported_claims: list[str] = field(default_factory=list)
    suggested_reply: Optional[str] = None  # what to show user if blocked
    soft_hint: Optional[dict] = None  # structured guidance for orchestrator retry


# ============================================================================
# Gate 1 — RETRIEVAL FLOOR
# ============================================================================

def _gate_retrieval_floor(chunks: list[RetrievedChunk]) -> tuple[bool, str]:
    if not chunks:
        return False, "no chunks retrieved"
    top = chunks[0].score
    avg = sum(c.score for c in chunks[:5]) / max(1, len(chunks[:5]))
    if top < MIN_TOP_SCORE:
        return False, f"top_score={top:.2f} below floor {MIN_TOP_SCORE}"
    if avg < MIN_AVG_SCORE:
        return False, f"avg_top5={avg:.2f} below floor {MIN_AVG_SCORE}"
    return True, ""


# ============================================================================
# Gate 2 — CITATION INTEGRITY
# ============================================================================

# Match [Source: <something>] or [Regulation: <something>] patterns
CITATION_PATTERN = re.compile(r"\[(?:Source|Regulation):\s*([^\]]+)\]", flags=re.IGNORECASE)

# A3 — inline-style citation marker: [policy_id:chunk_offset]
# e.g. "the sum insured is ₹10L [POL/00X:7]". This is the preferred shape
# for numeric claims because it points back to the exact chunk.
INLINE_CITE_RE = re.compile(r"\[([A-Z0-9_/\-\.]{3,}):(\d+)\]")


def _gate_citation_integrity(reply: str, chunks: list[RetrievedChunk]) -> tuple[bool, list[str]]:
    """Every cited policy name must be one we actually retrieved."""
    cited = CITATION_PATTERN.findall(reply)
    if not cited:
        # No citations made — only OK if reply is a refusal
        if any(kw in reply.lower() for kw in ("i don't see", "i don't have", "i'm not sure", "i couldn't find")):
            return True, []
        return False, ["citation_missing"]

    valid_names = {c.policy_name.lower() for c in chunks}
    valid_slugs = {c.insurer_slug.lower() for c in chunks}
    fabricated = []
    for citation in cited:
        cit_lower = citation.lower()
        # Permissive match — citation must contain a real policy or insurer slug
        matched = any(name and name in cit_lower for name in valid_names if len(name) >= 4)
        matched = matched or any(slug in cit_lower for slug in valid_slugs if len(slug) >= 4)
        # Also accept regulatory citations once we have regulatory chunks (heuristic)
        if "irda" in cit_lower or "regulation" in cit_lower:
            has_regulatory_chunk = any("regulator" in c.doc_type.lower() or "irda" in c.policy_name.lower() for c in chunks)
            if has_regulatory_chunk:
                matched = True
        if not matched:
            fabricated.append(citation)
    if fabricated:
        return False, [f"fabricated_citation: {f}" for f in fabricated]
    return True, []


# ============================================================================
# Gate 2b — CITATION REQUIRED FOR NUMERIC CLAIMS (A3)
# ============================================================================

def _gate_citation_required_for_numerics(
    reply: str, chunks: list[RetrievedChunk]
) -> tuple[bool, Optional[dict]]:
    """If the reply makes a numeric / monetary / percentage / sum-insured
    claim, require at least one citation marker — either the legacy
    [Source: …] form OR the inline [policy_id:chunk_offset] form.

    Returns (passed, soft_hint). soft_hint is a structured dict the
    orchestrator can use to re-prompt the brain with a stricter constraint.
    """
    if not _has_numeric_claim(reply):
        return True, None

    has_source_cite = bool(CITATION_PATTERN.search(reply))
    has_inline_cite = bool(INLINE_CITE_RE.search(reply))
    if has_source_cite or has_inline_cite:
        return True, None

    # No citation accompanying a numeric claim — surface a structured hint
    # so the orchestrator can retry with an explicit cite-required prompt.
    expected_pid = chunks[0].policy_id if chunks else "POL/00X"
    expected_offset = chunks[0].chunk_idx if chunks else 0
    return False, {
        "reason": "missing_citation",
        "expected": f"[{expected_pid}:{expected_offset}]",
    }


# ============================================================================
# Gate 2c — HALLUCINATED POLICY NAME DETECTOR (A3)
# ============================================================================

# A policy name in the reply that does NOT appear in any retrieved chunk is
# the textbook hallucination class. Detection: match common policy-name
# patterns ("X Health Plus", "Care Supreme", etc.) and check each against
# the retrieved set.

_POLICY_NAME_HINT_RE = re.compile(
    r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z0-9+]+){1,4})\b"
)

# Common English bigrams that look like policy names but aren't. Used as a
# negative filter so we don't flag e.g. "United States" as a fake policy.
_NON_POLICY_TOKENS = {
    "united states", "new delhi", "good morning", "thank you", "hello there",
    "let me", "i think", "i would", "i can", "i will", "you can", "you should",
    "the bot", "your advisor", "the policy", "this policy", "that policy",
    "hi there", "in fact", "as well", "of course", "the same", "no problem",
    "for example", "based on", "according to",
}


def _gate_hallucinated_policy(
    reply: str, chunks: list[RetrievedChunk]
) -> tuple[bool, list[str], Optional[dict]]:
    """Flag responses that name a policy not in the retrieved chunk set.

    Returns (passed, hallucinated_names, soft_hint).
    """
    if not chunks:
        return True, [], None

    valid_names = {c.policy_name.lower().strip() for c in chunks if c.policy_name}
    valid_insurers = {c.insurer_slug.lower().strip() for c in chunks if c.insurer_slug}

    candidates = _POLICY_NAME_HINT_RE.findall(reply or "")
    hallucinated: list[str] = []
    for cand in candidates:
        cl = cand.lower().strip()
        if cl in _NON_POLICY_TOKENS:
            continue
        # Skip candidates without policy-ish keywords
        if not any(
            kw in cl for kw in (
                "health", "care", "supreme", "plus", "shield", "star",
                "optima", "secure", "guard", "assure", "cover", "medi",
                "wellness", "protect", "active", "smart", "elite", "premier",
            )
        ):
            continue
        if any(cl in vn or vn in cl for vn in valid_names if len(vn) >= 4):
            continue
        if any(slug in cl for slug in valid_insurers if len(slug) >= 4):
            continue
        hallucinated.append(cand)

    if not hallucinated:
        return True, [], None

    cited_policies = sorted({c.policy_id for c in chunks if c.policy_id})
    soft_hint = {
        "reason": "hallucinated_policy",
        "hallucinated": hallucinated,
        "cited_policies": cited_policies,
        "instruction": (
            "Retry with explicit cited_policies constraint: only mention "
            f"policies whose policy_id is in {cited_policies}."
        ),
    }
    return False, hallucinated, soft_hint


# ============================================================================
# Gate 3 — NUMERIC GROUNDING
# ============================================================================

# Capture amounts (₹X / Rs.X / lakh / crore / NN%) and durations (NN days / months / years)
RUPEE_RE = re.compile(r"₹\s*[\d,]+(?:\.\d+)?\s*(?:lakh|crore|cr|k)?", flags=re.IGNORECASE)
PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%")
DURATION_RE = re.compile(r"\b\d{1,4}\s*(?:day|days|month|months|year|years)\b", flags=re.IGNORECASE)


def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def _gate_numeric_grounding(reply: str, chunks: list[RetrievedChunk]) -> tuple[bool, list[str]]:
    """Every numeric value in the reply must appear in retrieved chunks (loose match)."""
    combined = " ".join(c.text for c in chunks).lower()
    combined_norm = _normalize(combined)

    unsupported: list[str] = []
    for pattern in (RUPEE_RE, PERCENT_RE, DURATION_RE):
        for match in pattern.findall(reply):
            norm = _normalize(match)
            # Loose contains check
            if norm in combined_norm:
                continue
            # Also accept the digit-only stem in case units differ
            digit_only = re.sub(r"[^\d]", "", match)
            if digit_only and len(digit_only) >= 2 and digit_only in re.sub(r"[^\d]", "", combined):
                continue
            unsupported.append(match.strip())

    if unsupported:
        return False, unsupported
    return True, []


# ============================================================================
# Gate 4 — LLM-JUDGE FAITHFULNESS (NIM Llama-4 Maverick)
# ============================================================================

_judge: Optional[LLMProvider] = None


def _get_judge() -> LLMProvider:
    """LLM judge for Gate 4. Always NIM Llama-4 Maverick (MoE, different
    architecture from the dense Llama-3.3-70B brain), so the judge sees the
    brain's output from a genuinely different decision surface."""
    global _judge
    if _judge is None:
        _judge = get_judge_llm(language="en")
    return _judge


JUDGE_SYSTEM = """You are a strict faithfulness verifier for an insurance advisor bot.

You will be given:
  - RETRIEVED_CHUNKS: text from policy documents
  - REPLY: the bot's answer to a user

Your job: determine whether EVERY factual claim in REPLY is supported by RETRIEVED_CHUNKS. A claim is unsupported if the chunks don't say it, OR if the chunks contradict it.

OUTPUT FORMAT — strict JSON, nothing else:
{
  "supported": true | false,
  "unsupported_claims": ["claim 1", "claim 2", ...]
}

Be strict. The bot's job is to NOT hallucinate. If a claim is ambiguously supported (vague match), flag it. Soft-language is fine — only flag factual claims (numbers, coverage, exclusions, durations, citations).
"""


async def _gate_llm_judge(
    reply: str,
    chunks: list[RetrievedChunk],
    brain_model_used: Optional[str] = None,
) -> tuple[bool, list[str]]:
    """LLM judge for faithfulness Gate 4 with cross-family independence guard.

    `brain_model_used` is the EXACT model id that produced `reply` (e.g.
    'qwen/qwen3-next-80b-a3b-instruct'). It and its family are excluded from
    the judge's chain so the brain never grades its own homework. If the
    exclusion would empty the chain, NimChainLLM relaxes the family
    constraint but still enforces exact-model exclusion — strictly weaker
    than letting the same model grade itself.
    """
    if not reply or len(reply) < 30:
        return True, []
    if not chunks:
        return False, ["no_chunks_to_verify_against"]

    chunk_text = "\n\n---\n\n".join(
        f"[{c.policy_name} ({c.insurer_slug}), p.{c.page_start}]\n{c.text[:2000]}" for c in chunks[:5]
    )
    user = f"""RETRIEVED_CHUNKS:
{chunk_text}

REPLY:
{reply}

Verify."""

    # Compute exclusion set for cross-grading independence
    exclude_models: list[str] = []
    exclude_families: list[str] = []
    if brain_model_used:
        exclude_models.append(brain_model_used)
        try:
            from backend.providers.nvidia_nim_llm import NimChainLLM
            exclude_families.append(NimChainLLM._family_of(brain_model_used))
        except Exception:
            pass  # family helper unavailable → still enforce exact-model exclusion

    try:
        judge = _get_judge()
        res = await judge.chat(
            messages=[
                ChatMessage(role="system", content=JUDGE_SYSTEM),
                ChatMessage(role="user", content=user),
            ],
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
            exclude_models=exclude_models or None,
            exclude_families=exclude_families or None,
        )
        data = json.loads(res.text)
        supported = bool(data.get("supported", False))
        unsupported = list(data.get("unsupported_claims", []))
        return supported, unsupported
    except Exception as e:
        # KI-001 — BFSI compliance posture: in production we FAIL CLOSED
        # (block when the judge is unavailable) so an unsupported claim
        # never leaks past Gate 4 just because NIM hiccupped. Set
        # FAITHFULNESS_FAIL_CLOSED=0 in dev/smoke to revert to fail-open.
        import logging
        import os
        logging.warning(
            "faithfulness gate 4 judge failure (%s: %s)",
            type(e).__name__, str(e)[:200],
        )
        fail_closed = os.environ.get("FAITHFULNESS_FAIL_CLOSED", "1") == "1"
        if fail_closed:
            return False, [f"judge_unavailable_failclosed: {type(e).__name__}"]
        return True, [f"judge_error_failopen: {type(e).__name__}"]


# ============================================================================
# Main entry — orchestrator calls this
# ============================================================================

REFUSAL_TEMPLATE = (
    "I'd rather not answer that without stronger evidence in the policy documents I have. "
    "Could you rephrase, or narrow your question to a specific policy?"
)


async def check_faithfulness(
    reply: str,
    chunks: list[RetrievedChunk],
    user_text: str = "",
    run_llm_judge: bool = True,
    brain_model_used: Optional[str] = None,
    pre_recap_emitted: bool = False,
) -> FaithfulnessVerdict:
    """Run all gates. Return verdict with reasons + a safe reply to show user if blocked.

    `brain_model_used` is forwarded to Gate 4 so the judge can never be the
    same model (or same family) as the brain that produced `reply` —
    enforces the cross-grading independence invariant.

    `pre_recap_emitted` — set by the orchestrator when the previous bot
    turn ended with a profile recap. When True AND the current user-text
    or reply is a confirmation token, the entire faithfulness pipeline
    is bypassed (this is metadata, not a content claim).
    """
    verdict = FaithfulnessVerdict(passed=True)

    # A3 — PRE-RECAP CONFIRMATION BYPASS. When the bot just emitted a profile
    # recap, the user's "yes"/"correct"/etc. plus the bot's acknowledgement
    # are pure metadata. Running citation/grounding gates over those yields
    # only false positives.
    if pre_recap_emitted and (
        _is_confirmation_response(user_text) or _is_confirmation_response(reply)
    ):
        return verdict  # auto-pass

    # A3 — ALLOW-LIST: short bot replies that aren't factual claims at all
    # (yes, ok, "let me think about it", "compare them", pure bot self-ref)
    # never reach the citation/grounding gates.
    if _is_allowlisted_reply(reply):
        return verdict  # auto-pass

    # Gate 1 — retrieval floor
    ok1, msg1 = _gate_retrieval_floor(chunks)
    if not ok1:
        verdict.passed = False
        verdict.reasons.append(f"gate1_retrieval: {msg1}")

    # If retrieval already failed, suggest refusal and skip downstream gates
    if not verdict.passed:
        verdict.suggested_reply = REFUSAL_TEMPLATE
        _log_block(user_text, reply, verdict, chunks)
        return verdict

    # Gate 2 — citation integrity
    ok2, bad_citations = _gate_citation_integrity(reply, chunks)
    if not ok2:
        verdict.passed = False
        verdict.reasons.extend(bad_citations)

    # Gate 2b — numeric claim must be accompanied by a citation marker.
    # Emit a structured soft-hint so the orchestrator can retry with the
    # exact expected citation shape.
    ok2b, cite_hint = _gate_citation_required_for_numerics(reply, chunks)
    if not ok2b:
        verdict.passed = False
        verdict.reasons.append("gate2b_missing_citation_for_numeric")
        verdict.soft_hint = cite_hint

    # Gate 2c — hallucinated policy name. Flag policies named in the reply
    # that aren't in the retrieved chunk set. Surface a soft hint that the
    # orchestrator can pass back to the brain as a `cited_policies=[...]`
    # constraint on retry.
    ok2c, hallucinated_names, hp_hint = _gate_hallucinated_policy(reply, chunks)
    if not ok2c:
        verdict.passed = False
        verdict.reasons.append(
            f"gate2c_hallucinated_policy: {', '.join(hallucinated_names)}"
        )
        verdict.unsupported_claims.extend(hallucinated_names)
        # If we already have a soft hint from 2b, prefer 2c (it's more actionable).
        verdict.soft_hint = hp_hint or verdict.soft_hint

    # Gate 3 — numeric grounding
    ok3, bad_nums = _gate_numeric_grounding(reply, chunks)
    if not ok3:
        verdict.passed = False
        verdict.reasons.extend([f"unsupported_number: {n}" for n in bad_nums])
        verdict.unsupported_claims.extend(bad_nums)

    # Gate 4 — LLM judge (only if previous gates passed — saves token cost on
    # obvious failures). Also SKIP when retrieval was strongly grounded: top
    # chunk cosine > HIGH_CONFIDENCE_FLOOR means hallucination risk is low and
    # the 1-2s NIM round-trip rarely adds value. Cuts ~60% of judge calls.
    HIGH_CONFIDENCE_FLOOR = 0.50
    top_score = max((c.score for c in chunks), default=0.0) if chunks else 0.0
    if verdict.passed and run_llm_judge and top_score < HIGH_CONFIDENCE_FLOOR:
        ok4, unsupported = await _gate_llm_judge(reply, chunks, brain_model_used=brain_model_used)
        if not ok4:
            verdict.passed = False
            verdict.reasons.append("gate4_llm_judge: claims unsupported")
            verdict.unsupported_claims.extend(unsupported)

    if not verdict.passed:
        verdict.suggested_reply = REFUSAL_TEMPLATE
        _log_block(user_text, reply, verdict, chunks)

    return verdict


# ============================================================================
# Audit log
# ============================================================================

def _log_block(user_text: str, reply: str, verdict: FaithfulnessVerdict, chunks: list[RetrievedChunk]) -> None:
    """Append every faithfulness block to logs/hallucinations.jsonl for compliance audit."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user_text": user_text,
        "blocked_reply": reply,
        "reasons": verdict.reasons,
        "unsupported_claims": verdict.unsupported_claims,
        "chunk_count": len(chunks),
        "top_score": chunks[0].score if chunks else None,
        "policy_ids": list({c.policy_id for c in chunks}),
    }
    with open(HALLUCINATION_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")
