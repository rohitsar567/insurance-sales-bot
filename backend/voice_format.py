"""Convert an LLM reply (markdown, citations, lists, acronyms) into clean
spoken-language text for Sarvam Bulbul TTS.

Why this exists: an unprocessed LLM reply with markdown bold, inline
[Source: ...] tags, and acronyms reads like a screenshot when spoken. Users
hear "asterisk asterisk bold asterisk asterisk A-Y-U-S-H pp dot 1 dash 2".
That's a UX-killing bug — not a Sarvam limitation, a *us* bug.

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


def tts_preprocess(text: str, language: str = "en", max_words: int = 60) -> str:
    """Public entry — turn an LLM reply into spoken-language text for TTS."""
    if not text:
        return ""
    cleaned = _strip_markdown(text)
    cleaned = _expand_acronyms(cleaned, language=language)
    cleaned = _compress_whitespace(cleaned)
    cleaned = _truncate_for_voice(cleaned, max_words=max_words)
    return cleaned
