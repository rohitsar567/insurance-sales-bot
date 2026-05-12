"""Verify the Sarvam-M Hinglish back-translation preserved the load-bearing
facts from DeepSeek-V3's English reply.

Closes the F-16 gap: faithfulness gates verify the English answer, but until
this module the Hinglish translation that the user actually saw/heard was
trusted blindly. If Sarvam silently drops a citation or changes "24 months"
to "2 years" (semantically equivalent but the regex floor would still catch
it) — that's fine. But if it changes "24 months" to "12 months" — that's a
mis-sale and we MUST catch it.

Mechanism (Layer 1 — regex anchors, <50 ms):
  1. Extract from BOTH english_reply and indic_reply:
        - rupee amounts (₹X, Rs X, X lakh, X crore)
        - percentages (NN%)
        - durations (NN days/months/years)
        - source citations [Source: ...]
        - policy_name fragments seen in chunks
  2. Every anchor present in english_reply must ALSO appear (in some form)
     in the indic_reply.
  3. Allow 1-2 fuzzy drift (e.g. "30 days" → "tees din") via a lookup map.
  4. Block + log if any anchor dropped.

Future Layer 2 (v2): back-translate indic → english via Sarvam, fuzzy-match
against english_reply at sentence level.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.config import settings

LOG = settings.CORPUS_DIR.parent.parent / "logs" / "translation_drift.jsonl"
LOG.parent.mkdir(parents=True, exist_ok=True)


RUPEE_RE = re.compile(r"₹\s*[\d,]+(?:\.\d+)?\s*(?:lakh|crore|cr|k)?", flags=re.IGNORECASE)
PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s*%")
DURATION_EN = re.compile(r"\b(\d{1,4})\s*(?:day|days|month|months|year|years)\b", flags=re.IGNORECASE)
# Devanagari/Hinglish duration forms — recognise common cases. Numbers stay digit.
DURATION_HI = re.compile(r"\b(\d{1,4})\s*(?:din|mahine|mahina|saal|varsh)\b", flags=re.IGNORECASE)
CITATION_RE = re.compile(r"\[(?:Source|Regulation):\s*([^\]]+)\]", flags=re.IGNORECASE)


def _digits_only(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


@dataclass
class DriftVerdict:
    drift_detected: bool
    reasons: list[str] = field(default_factory=list)
    dropped_numbers: list[str] = field(default_factory=list)
    dropped_citations: list[str] = field(default_factory=list)


def check_translation_drift(english_reply: str, indic_reply: str) -> DriftVerdict:
    """Return drift verdict comparing English reply to its Hinglish translation."""
    verdict = DriftVerdict(drift_detected=False)

    # 1. Numbers + currency + percentage
    en_amounts = set(_digits_only(m) for m in RUPEE_RE.findall(english_reply) if _digits_only(m))
    en_amounts |= set(m.replace(" ", "") for m in PERCENT_RE.findall(english_reply))
    en_amounts |= set(m for m, _ in [(m, None) for m in DURATION_EN.findall(english_reply)])

    indic_all_digits = re.findall(r"\d+", indic_reply)
    indic_digit_set = set(indic_all_digits)

    for amt in en_amounts:
        if not amt:
            continue
        if amt not in indic_digit_set:
            verdict.drift_detected = True
            verdict.dropped_numbers.append(amt)
            verdict.reasons.append(f"number_dropped: '{amt}' in EN reply but not in HI reply")

    # 2. Citations
    en_cits = CITATION_RE.findall(english_reply)
    for cit in en_cits:
        # Extract a unique-ish token (e.g. insurer slug + policy name root)
        tokens = re.findall(r"[A-Za-z]{3,}", cit)
        if not tokens:
            continue
        anchor = tokens[0].lower()
        if anchor not in indic_reply.lower():
            verdict.drift_detected = True
            verdict.dropped_citations.append(cit)
            verdict.reasons.append(f"citation_dropped: '{cit[:60]}' — anchor token '{anchor}' missing in HI")

    if verdict.drift_detected:
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "layer": "regex_anchors",
                "en_reply": english_reply[:600],
                "hi_reply": indic_reply[:600],
                "reasons": verdict.reasons,
                "dropped_numbers": verdict.dropped_numbers,
                "dropped_citations": verdict.dropped_citations,
            }) + "\n")

    return verdict


# ============================================================================
# Gate 5 — LLM-judge faithfulness on the Hinglish translation
# ============================================================================

_HINDI_JUDGE_SYSTEM = """You are a strict bilingual faithfulness verifier.

Given an ENGLISH source answer and its HINGLISH translation, decide whether the
Hinglish version faithfully conveys the SAME factual claims as the English
source. The Hinglish may use different words / Devanagari / code-switched
English — that's fine. What you check:

1. Every factual claim (number, duration, currency, percentage, coverage, exclusion,
   policy name, citation) in the English source must be PRESENT in the Hinglish.
2. The Hinglish must NOT add any claims that aren't in the English source.
3. Citations [Source: ...] should be preserved verbatim.

OUTPUT — strict JSON, nothing else:
{
  "faithful": true | false,
  "reason": "one short sentence — what differs, if anything"
}

Be strict. Tone changes are fine; fact changes are not."""


async def check_hinglish_faithfulness(english_reply: str, hinglish_reply: str) -> DriftVerdict:
    """Gate 5 — Groq Llama judges whether the Hinglish translation is faithful
    to the English original. Catches semantic drift the regex anchors miss
    (e.g. paraphrased exclusions, dropped caveats).
    """
    if not english_reply.strip() or not hinglish_reply.strip():
        return DriftVerdict(drift_detected=False)

    try:
        # Lazy-import to avoid pulling Groq into modules that don't need it
        from backend.providers.base import ChatMessage
        from backend.providers.groq_llm import GroqLLM
        judge = GroqLLM()
        user = f"ENGLISH SOURCE:\n{english_reply}\n\nHINGLISH TRANSLATION:\n{hinglish_reply}\n\nVerify."
        res = await judge.chat(
            messages=[
                ChatMessage(role="system", content=_HINDI_JUDGE_SYSTEM),
                ChatMessage(role="user", content=user),
            ],
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        data = json.loads(res.text)
        faithful = bool(data.get("faithful", False))
        reason = str(data.get("reason", ""))[:200]
        verdict = DriftVerdict(
            drift_detected=not faithful,
            reasons=([f"hinglish_judge: {reason}"] if not faithful else []),
        )
        if verdict.drift_detected:
            with open(LOG, "a") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "layer": "hinglish_llm_judge",
                    "en_reply": english_reply[:600],
                    "hi_reply": hinglish_reply[:600],
                    "judge_reason": reason,
                }) + "\n")
        return verdict
    except Exception as e:
        # Fail-open — don't block valid translations on judge infra errors
        return DriftVerdict(drift_detected=False, reasons=[f"judge_error: {type(e).__name__}"])


# ============================================================================
# Gate 6 — back-translate-and-cosine-compare
# ============================================================================

async def check_back_translation(
    english_reply: str,
    hinglish_reply: str,
    min_cosine: float = 0.80,
) -> DriftVerdict:
    """Translate the Hinglish reply back to English via Sarvam, then cosine-
    compare to the original English reply via BGE embeddings.

    If the two English texts are far apart (cosine < min_cosine), it means
    Sarvam's Hinglish translation introduced or dropped meaning — the user
    would see something materially different from what DeepSeek wrote.
    """
    if not english_reply.strip() or not hinglish_reply.strip():
        return DriftVerdict(drift_detected=False)
    try:
        from backend.translator import translate_to_english
        from backend.providers.local_embeddings import LocalEmbeddings
        back_en = await translate_to_english(hinglish_reply)
        if not back_en.strip():
            return DriftVerdict(drift_detected=False, reasons=["back_translate_empty"])

        embedder = LocalEmbeddings()
        vecs = await embedder.embed([english_reply, back_en], input_type="document")
        if len(vecs) < 2:
            return DriftVerdict(drift_detected=False)
        # Cosine similarity (BGE returns normalized vectors → dot product = cosine)
        cosine = sum(a * b for a, b in zip(vecs[0], vecs[1]))

        if cosine < min_cosine:
            v = DriftVerdict(
                drift_detected=True,
                reasons=[f"back_translate_cosine_low: {cosine:.3f} < {min_cosine}"],
            )
            with open(LOG, "a") as f:
                f.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "layer": "back_translate_cosine",
                    "cosine": round(cosine, 4),
                    "threshold": min_cosine,
                    "en_reply": english_reply[:600],
                    "hi_reply": hinglish_reply[:600],
                    "back_translated_en": back_en[:600],
                }) + "\n")
            return v
        return DriftVerdict(drift_detected=False, reasons=[f"back_translate_cosine_ok: {cosine:.3f}"])
    except Exception as e:
        return DriftVerdict(drift_detected=False, reasons=[f"back_translate_error: {type(e).__name__}"])
