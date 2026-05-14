"""Convert an LLM reply (markdown, citations, lists, acronyms) into clean
spoken-language text for Sarvam Bulbul TTS.

Why this exists: an unprocessed LLM reply with markdown bold, inline
[Source: ...] tags, and acronyms reads like a screenshot when spoken. Users
hear "asterisk asterisk bold asterisk asterisk A-Y-U-S-H pp dot 1 dash 2".
That's a UX-killing bug — not a Sarvam limitation, a *us* bug.

KI-104 (2026-05-15) — this module also exposes `strip_cot_preamble`, the
chain-of-thought / instruction-echo stripper that runs on TEXT replies
(not just TTS). Live smoke tests caught NIM reasoning models (e.g.,
Qwen3-Next 80B) and the judge model leaking internal reasoning into
`reply_text`. Examples: "We need to respond to user question…", "We must
ground every factual claim…", "<think>...</think>The answer is X."
`strip_cot_preamble` is called from `persona.strip_think_tags` so every
reply path that already goes through the <think>-tag strip also gets the
preamble strip — no orchestrator.py changes needed (that file is owned
by another lane / KI-101).

The function turns text like:

  "**Direct answer:**
   Yes, HDFC ERGO Optima Secure covers Ayurveda... [Source: my:Optima
   Secure (older variant) (hdfc-ergo), pp.1-2]."

Into spoken-ready:

  "Yes, HDFC ERGO Optima Secure covers Ayurveda treatment at recognized
  Ayush hospitals under specific conditions. For full coverage details
  and exclusions, see the source link below this message."

Rules applied (in order):
  1. Strip [Source: ...] and [Regulation: ...] inline citations
  2. Strip markdown formatting (** bold, * italic, # headings, > quote, - bullet, 1. number)
  3. Expand acronyms common in insurance to pronounceable forms
  4. Compress whitespace
  5. Truncate to first ~60 spoken words; append "More details on screen." if cut
"""

from __future__ import annotations

import re

# ---- markdown / formatting strippers ----

CITATION_INLINE = re.compile(r"\s*\[(?:Source|Regulation):[^\]]+\]", flags=re.IGNORECASE)
MD_BOLD = re.compile(r"\*\*(.+?)\*\*", flags=re.DOTALL)
MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)([^*]+?)\*(?!\*)")
MD_HEADING = re.compile(r"^#{1,6}\s+", flags=re.MULTILINE)
MD_BLOCKQUOTE = re.compile(r"^>\s+", flags=re.MULTILINE)
MD_BULLET = re.compile(r"^[\s]*[-•*]\s+", flags=re.MULTILINE)
MD_NUMBERED = re.compile(r"^\s*\d+\.\s+", flags=re.MULTILINE)
MD_INLINE_CODE = re.compile(r"`([^`]+)`")
MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")  # keep the text, drop the URL

# Acronym expansions — domain-specific so they read naturally aloud.
# Rule of thumb: if the acronym is normally PRONOUNCED AS A WORD in spoken
# Indian English (AYUSH, IRDAI, HDFC ERGO), leave it alone; TTS pronounces
# it fine. If it's normally said as letters (CIS, PED), expand to plain words.
ACRONYMS = {
    r"\bPED\b": "pre-existing disease",
    r"\bOPD\b": "out-patient",
    r"\bICU\b": "I-C-U",
    r"\bTAT\b": "turnaround time",
    r"\bCSR\b": "claim settlement ratio",
    r"\bNCB\b": "no-claim bonus",
    r"\bSI\b": "sum insured",
    r"\bCIS\b": "Customer Information Sheet",
    r"\bKFD\b": "Key Feature Document",
    r"\bUIN\b": "U-I-N",
    r"\bp\.(\d+)": r"page \1",
    r"\bpp\.(\d+)-(\d+)": r"pages \1 to \2",
    r"\bpp\.(\d+)": r"page \1",
}

# KI-066 (2026-05-15) — money / range shorthand that Sarvam Bulbul reads
# letter-by-letter. The user said "₹25L+" was being spoken as "two five L
# plus". Order in `_normalize_money` matters: handle RANGES first, then
# PLUS-SUFFIXES, then bare unit-suffixes, then standalone "+".
_MONEY_RANGE_L = re.compile(
    r"₹?\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*L\b",
    re.IGNORECASE,
)
_MONEY_RANGE_CR = re.compile(
    r"₹?\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*Cr\b",
    re.IGNORECASE,
)
_MONEY_PLUS_L = re.compile(r"₹?\s*(\d+(?:\.\d+)?)\s*L\s*\+", re.IGNORECASE)
_MONEY_PLUS_CR = re.compile(r"₹?\s*(\d+(?:\.\d+)?)\s*Cr\s*\+", re.IGNORECASE)
_MONEY_L = re.compile(r"₹?\s*(\d+(?:\.\d+)?)\s*L\b", re.IGNORECASE)
_MONEY_CR = re.compile(r"₹?\s*(\d+(?:\.\d+)?)\s*Cr\b", re.IGNORECASE)
_MONEY_RS_PREFIX = re.compile(r"\bRs\.?\s*", re.IGNORECASE)
_MONEY_RUPEE_SYMBOL = re.compile(r"₹\s*(\d)")
# Bare year ranges like "29-32" or "24/7" — leave alone; TTS handles dashes.


def _normalize_money(text: str) -> str:
    """Turn currency / range shorthand into spoken-language equivalents.

    Examples:
      "₹5L"      → "5 lakhs"
      "₹25L+"    → "25 lakhs or more"
      "₹5-10L"   → "5 to 10 lakhs"
      "₹2Cr"     → "2 crores"
      "Rs. 5000" → "rupees 5000"
    """
    text = _MONEY_RANGE_L.sub(lambda m: f"{m.group(1)} to {m.group(2)} lakhs", text)
    text = _MONEY_RANGE_CR.sub(lambda m: f"{m.group(1)} to {m.group(2)} crores", text)
    text = _MONEY_PLUS_L.sub(lambda m: f"{m.group(1)} lakhs or more", text)
    text = _MONEY_PLUS_CR.sub(lambda m: f"{m.group(1)} crores or more", text)
    text = _MONEY_L.sub(lambda m: f"{m.group(1)} lakhs", text)
    text = _MONEY_CR.sub(lambda m: f"{m.group(1)} crores", text)
    text = _MONEY_RS_PREFIX.sub("rupees ", text)
    text = _MONEY_RUPEE_SYMBOL.sub(r"rupees \1", text)
    return text


# Strip section labels that LLMs love but ruin voice flow.
# Require the trailing colon so we only catch actual labels, not normal prose
# that happens to start with "Coverage applies..." etc.
SECTION_LABELS = re.compile(
    r"^\s*(?:Direct answer|Key details?|Important notes?|Summary|TL;DR|Exclusions? apply|Caveat|Note|Disclaimer)\s*:\s*",
    flags=re.IGNORECASE | re.MULTILINE,
)


def _strip_markdown(text: str) -> str:
    text = CITATION_INLINE.sub("", text)
    text = MD_BOLD.sub(r"\1", text)
    text = MD_ITALIC.sub(r"\1", text)
    text = MD_HEADING.sub("", text)
    text = MD_BLOCKQUOTE.sub("", text)
    text = MD_BULLET.sub("", text)
    text = MD_NUMBERED.sub("", text)
    text = MD_INLINE_CODE.sub(r"\1", text)
    text = MD_LINK.sub(r"\1", text)
    text = SECTION_LABELS.sub("", text)
    return text


def _expand_acronyms(text: str, language: str = "en") -> str:
    if language == "indic":
        # In Indic mode, keep acronyms — Indic TTS handles them OK
        return text
    for pat, repl in ACRONYMS.items():
        text = re.sub(pat, repl, text, flags=re.IGNORECASE if "ayush" in pat.lower() else 0)
    return text


def _compress_whitespace(text: str) -> str:
    text = re.sub(r"\n{2,}", ". ", text)  # paragraph break → sentence break
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    # collapse repeated punctuation: ".. ." → "."
    text = re.sub(r"\s*\.\s*\.+", ".", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text.strip()


def _truncate_for_voice(text: str, max_words: int = 60) -> str:
    """Keep first N words, then append a cutoff cue if we cut anything."""
    words = text.split()
    if len(words) <= max_words:
        return text
    truncated = " ".join(words[:max_words])
    # End on a sentence boundary near the cut
    last_period = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    if last_period > 0 and last_period > len(truncated) - 80:
        truncated = truncated[: last_period + 1]
    else:
        truncated = truncated + "."
    return truncated + " More details are on screen."


# ============================================================================
# KI-104 (2026-05-15) — chain-of-thought / instruction-echo strip
# ============================================================================
# Live smoke test caught LLM brain replies leaking internal reasoning into
# user-visible reply_text. Three failure modes:
#   1. NIM reasoning models (Qwen3-Next 80B) emit a <think>...</think> block
#      followed by the answer — the <think> tag was sometimes missing /
#      malformed so the existing strip_think_tags in persona.py let it through.
#   2. The faithfulness JUDGE model occasionally returns its own reasoning
#      instead of a clean rescue reply.
#   3. The brain model misunderstands the system prompt and echoes the
#      instruction prose ("We need to respond to user question…").
#
# The strip below is CONSERVATIVE — it only kills CoT preamble lines that
# appear BEFORE the first natural-sounding sentence (within the first ~6
# lines / first 600 chars), so substantive mid-reply content like
# "We have three options: A, B, C" is preserved.

# ---- Sentence-level preamble patterns (KI-104) ----
#
# A CoT preamble can appear as:
#   (a) a full line of its own: "We need to respond carefully.\n<answer>"
#   (b) a leading sentence INSIDE the first line: "We need to respond to
#       user question. Here's the actual answer."
#
# We handle both by sentence-splitting the top of the reply and dropping
# leading sentences that match a CoT-starter pattern, until we hit a
# substantive sentence.
#
# Sentence-starter patterns. These match from the START of a sentence
# (no MULTILINE anchor — we apply them sentence-by-sentence). Keep these
# specific enough to avoid false positives on legitimate prose.
# NOTE: don't append a trailing `\b` to the alternation — `\b` after `:` or
# after a digit followed by `:` is NOT a word boundary, which silently
# breaks `Step \d+\s*:`. Each alternative carries its own anchor where one
# is needed.
_COT_SENTENCE_STARTERS = re.compile(
    r"^\s*(?:"
    r"We need to(?:\s+respond|\s+answer|\s+follow|\s+ground|\s+check|\s+ensure|\s+make sure|\s+consider|\s+think|\s+address)\b"
    r"|We must\b"
    r"|We should (?:respond|answer|follow|ground|check|ensure|make sure|consider|think|address|cite)\b"
    r"|According to (?:conversation rules|the instructions|the guidelines|the system prompt|the rules|policy guidelines)\b"
    r"|The user (?:asks|is asking|wants|needs|wants to know)\b"
    r"|Let me (?:think|consider|analyze|break this down|work through)\b"
    r"|I (?:will|need to|should|must) (?:think|consider|analyze|respond|answer|check|ground|follow)\b"
    r"|First,?\s+I(?:'ll| will| need to| should| must)\b"
    r"|To answer this(?:\s+question)?\b"
    r"|Step \d+\s*:"
    r"|Following the instructions\b"
    r"|As per the (?:guidelines|instructions|rules|system prompt)\b"
    r"|Per the (?:guidelines|instructions|rules)\b"
    r"|Okay,?\s+(?:let me|so the user|so I)\b"
    r"|Alright,?\s+(?:let me|so the user|so I)\b"
    r"|So,?\s+the user\b"
    r"|Thinking about this\b"
    r"|My (?:thought|reasoning|plan|approach) (?:process )?(?:is|here)\b"
    r")",
    flags=re.IGNORECASE,
)

# Sentence splitter — split on ". " / "! " / "? " / "\n" but keep the
# delimiter attached to the preceding sentence so we can rejoin losslessly.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

# Labelled reasoning blocks. Match only the SAME-LINE label content; do
# not consume the next line (which is usually the real answer).
_LABELLED_REASONING_LINE = re.compile(
    r"^[ \t]*(?:\*\*)?(?:Reasoning|Thought|Plan|Internal|Scratch(?:pad)?|Chain[- ]of[- ]thought|CoT)(?:\*\*)?\s*:\s*[^\n]*$",
    flags=re.IGNORECASE | re.MULTILINE,
)
_BRACKET_INTERNAL = re.compile(
    r"\[(?:INTERNAL|REASONING|THOUGHT|PLAN|CoT)\].*?\[/(?:INTERNAL|REASONING|THOUGHT|PLAN|CoT)\]",
    flags=re.IGNORECASE | re.DOTALL,
)

# Stray, unbalanced <think> tags that persona.strip_think_tags doesn't
# already handle (it requires both open and close in the same blob).
# If we see an isolated </think> mid-reply, drop everything before it.
_STRAY_CLOSE_THINK = re.compile(r"^.*?</think>", flags=re.DOTALL | re.IGNORECASE)

# Maximum scan window for preamble. Beyond this, content is treated as
# substantive prose even if it matches a starter pattern — protects
# legitimate mid-reply phrasing like "Let me think about your three options".
_PREAMBLE_SCAN_LINES = 6
_PREAMBLE_SCAN_CHARS = 600

# Fallback when stripping removes the entire reply — better than empty.
_EMERGENCY_REPLY = (
    "Let me think about this — could you ask me again in a moment?"
)


def _drop_leading_cot_sentences(text: str) -> str:
    """Sentence-by-sentence strip of CoT preamble at the top of a reply.

    Split the first ~600 chars into sentences. Drop leading sentences that
    match a CoT starter pattern. Stop at the first substantive sentence.
    Rejoin and prepend to whatever's left of the reply.
    """
    if not text:
        return text

    # Only walk the first window — anything beyond is presumed substantive.
    head = text[:_PREAMBLE_SCAN_CHARS]
    tail = text[_PREAMBLE_SCAN_CHARS:]

    # Track delimiters so we rejoin without losing them.
    sentences: list[str] = []
    last_end = 0
    for m in _SENTENCE_SPLIT.finditer(head):
        sentence = head[last_end : m.start()]
        delim = m.group(0)
        sentences.append(sentence + delim)
        last_end = m.end()
    # Final trailing chunk (no terminating delimiter).
    if last_end < len(head):
        sentences.append(head[last_end:])

    # Walk and drop CoT starters.
    drop_index = 0
    while drop_index < len(sentences) and drop_index < _PREAMBLE_SCAN_LINES:
        s = sentences[drop_index]
        stripped = s.strip()
        if not stripped:
            drop_index += 1
            continue
        if _COT_SENTENCE_STARTERS.match(stripped):
            drop_index += 1
            continue
        break

    if drop_index == 0:
        return text

    rebuilt_head = "".join(sentences[drop_index:])
    return (rebuilt_head + tail).lstrip()


def strip_cot_preamble(text: str) -> str:
    """Strip chain-of-thought / instruction-echo leakage from a model reply.

    Conservative rules (in order):
      1. Drop labelled reasoning lines (`**Reasoning:** …`, `[INTERNAL]…[/INTERNAL]`).
         These are SAME-LINE strips — we never consume the next line, which
         is typically the real answer.
      2. If a stray `</think>` appears (no opening `<think>`), drop
         everything up to and including it.
      3. Sentence-walk the first ~600 chars; drop leading sentences that
         match a CoT starter pattern. Stop at the first substantive sentence
         — substantive content is preserved verbatim.
      4. If the whole reply gets stripped, return `_EMERGENCY_REPLY`.

    Args:
      text: Raw model output (post-<think>-strip but pre-user-display).

    Returns:
      Cleaned reply with internal reasoning removed. Never empty.
    """
    if not text or not str(text).strip():
        return _EMERGENCY_REPLY

    cleaned = text

    # Rule 1 — kill labelled reasoning blocks. Same-line only.
    cleaned = _LABELLED_REASONING_LINE.sub("", cleaned)
    cleaned = _BRACKET_INTERNAL.sub("", cleaned)

    # Rule 2 — stray close-think tag: drop everything before it.
    if "</think>" in cleaned.lower() and "<think>" not in cleaned.lower():
        cleaned = _STRAY_CLOSE_THINK.sub("", cleaned, count=1).lstrip()

    # Rule 3 — sentence-level CoT preamble strip.
    cleaned = _drop_leading_cot_sentences(cleaned)

    # Rule 4 — emergency fallback if the whole reply was CoT.
    if not cleaned or not cleaned.strip():
        return _EMERGENCY_REPLY

    return cleaned


def tts_preprocess(text: str, language: str = "en", max_words: int = 60) -> str:
    """Public entry — turn an LLM reply into spoken-language text for TTS."""
    if not text:
        return ""
    # KI-104 — defense in depth: even if the reply went through
    # persona.strip_think_tags upstream, run the preamble strip again here
    # in case it's called on a path that bypasses persona (e.g., direct
    # TTS of a cached reply).
    cleaned = strip_cot_preamble(text)
    cleaned = _strip_markdown(cleaned)
    # KI-066 (2026-05-15) — currency/range shorthand expansion before
    # acronym handling so ₹5L becomes "5 lakhs" instead of getting caught
    # by the bare-L acronym path.
    cleaned = _normalize_money(cleaned)
    cleaned = _expand_acronyms(cleaned, language=language)
    cleaned = _compress_whitespace(cleaned)
    cleaned = _truncate_for_voice(cleaned, max_words=max_words)
    return cleaned
