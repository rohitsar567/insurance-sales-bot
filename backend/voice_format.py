"""Convert an LLM reply (markdown, citations, lists, acronyms) into clean
spoken-language text for Sarvam Bulbul TTS.

Why this exists: an unprocessed LLM reply with markdown bold, inline
[Source: ...] tags, and acronyms reads like a screenshot when spoken. Users
would otherwise hear "asterisk asterisk bold asterisk asterisk A-Y-U-S-H
pp dot 1 dash 2".

This module also exposes `strip_cot_preamble`, the chain-of-thought /
instruction-echo stripper that runs on TEXT replies (not just TTS).
Reasoning models can leak internal reasoning into `reply_text` — e.g.
"We need to respond to user question…", "We must ground every factual
claim…", "<think>...</think>The answer is X." `strip_cot_preamble`
removes that preamble.

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
  3. Expand abbreviations ("e.g." -> "for example") and slashes ("yes / no"
     -> "yes or no"; "₹5L / ₹10L" -> "5 lakh, 10 lakh") so Sarvam Bulbul
     never spells "e.g." letter-by-letter or reads "/" as "by"/"divide"
  4. Expand acronyms common in insurance to pronounceable forms
  5. Compress whitespace
  6. NO word cap — the FULL reply is spoken. Sarvam Bulbul v2's hard
     per-request character limit is handled downstream by chunked
     synthesis in providers/sarvam_tts.py (mirrors the STT 30s-chunking
     house style); we never silently drop trailing questions here.
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

# Money / range shorthand that Sarvam Bulbul would otherwise read
# letter-by-letter (e.g. "₹25L+" as "two five L plus"). Order in
# `_normalize_money` matters: handle RANGES first, then PLUS-SUFFIXES,
# then bare unit-suffixes, then standalone "+".
#
# Also covers `k` (thousand), word-forms (`1 lakh`, `1 crore`), `+`
# suffix → "above", and bare numerics like `30000` → "30 thousand",
# `1,00,000` → "1 lakh". `k` is currency-gated to avoid breaking
# "10K marathon".
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

# --- KI-148: word-form lakh/crore (e.g. "1 lakh", "5 crores") ---
# Normalize to a canonical "<n> lakh rupees" / "<n> crore rupees" so the
# downstream `L`/`Cr` regexes don't re-process them. Optional ₹ prefix.
_MONEY_WORD_LAKH = re.compile(
    r"₹?\s*(\d+(?:\.\d+)?)\s*lakhs?\b",
    re.IGNORECASE,
)
_MONEY_WORD_CRORE = re.compile(
    r"₹?\s*(\d+(?:\.\d+)?)\s*crores?\b",
    re.IGNORECASE,
)

# --- KI-148: `k` (thousand) shorthand, currency-gated ---
# Match `₹15k`, `₹15-30k`, `₹15k-30k`, `60k+`. Three contexts qualify as
# currency: (a) ₹ prefix, (b) adjacent to another currency token (handled
# by ordering — list-context like "₹15k, 30k, 60k+" gets ₹ on the first
# token then the rest cascade), (c) followed by rupee/premium/budget/
# income/sum-insured. We pre-scan and tag list-context k's by injecting
# ₹ before bare k-numbers that sit in a comma/dash list with a ₹-prefixed
# sibling. Simplest approach: run ₹-prefixed patterns first, then a
# context-aware second pass.
_MONEY_RANGE_K_BOTH = re.compile(
    r"₹\s*(\d+(?:\.\d+)?)\s*k\s*-\s*(\d+(?:\.\d+)?)\s*k\b",
    re.IGNORECASE,
)
_MONEY_RANGE_K = re.compile(
    r"₹\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*k\b",
    re.IGNORECASE,
)
_MONEY_PLUS_K = re.compile(r"₹?\s*(\d+(?:\.\d+)?)\s*k\s*\+", re.IGNORECASE)
_MONEY_K_PREFIXED = re.compile(r"₹\s*(\d+(?:\.\d+)?)\s*k\b", re.IGNORECASE)
# Bare `Nk` (no ₹) — only expand if followed by a currency-context word.
_MONEY_K_CONTEXT = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*k\b(?=[\s,]*(?:rupee|premium|budget|income|sum insured))",
    re.IGNORECASE,
)
# Comma/`or`-list cascade: once a list contains "<digits> thousand rupees",
# subsequent bare "Nk" tokens in the same list segment inherit currency.
# We approximate by running this AFTER the first pass: any bare `Nk` that
# sits within 60 chars after a "thousand rupees" or "lakh rupees" token is
# treated as currency.
_MONEY_K_LIST_CASCADE = re.compile(
    r"((?:thousand|lakh|crore) rupees[^.?!]{0,60}?)\b(\d+(?:\.\d+)?)\s*k\b",
    re.IGNORECASE,
)

# --- KI-148: bare-number expansion (Indian + standard formatting) ---
# Indian comma format: `1,00,000` → "1 lakh", `15,00,000` → "15 lakh".
# Standard format: `2,500,000` → "25 lakh". Plain 4-5 digit: `30000` →
# "30 thousand". Context-gated to currency to avoid butchering "30000 km".
_MONEY_INDIAN_LAKH_COMMA = re.compile(
    r"₹?\s*(\d{1,2}(?:,\d{2})+,\d{3})\b"
)
_MONEY_STD_COMMA = re.compile(
    r"₹?\s*(\d{1,3}(?:,\d{3})+)\b"
)
_MONEY_BARE_THOUSANDS = re.compile(
    r"₹\s*(\d{4,7})\b"
)

# Bare year ranges like "29-32" or "24/7" — leave alone; TTS handles dashes.


def _expand_indian_comma_number(s: str) -> str:
    """Convert `1,00,000` → "1 lakh", `15,00,000` → "15 lakh",
    `1,00,00,000` → "1 crore". Indian system: rightmost 3 digits, then
    pairs."""
    digits = s.replace(",", "")
    try:
        n = int(digits)
    except ValueError:
        return s
    return _humanize_int(n)


def _expand_standard_comma_number(s: str) -> str:
    """Convert `2,500,000` → "25 lakh", `30,000` → "30 thousand"."""
    digits = s.replace(",", "")
    try:
        n = int(digits)
    except ValueError:
        return s
    return _humanize_int(n)


def _humanize_int(n: int) -> str:
    """Render an integer rupee amount as a spoken Indian-English phrase."""
    if n >= 10_000_000 and n % 100_000 == 0:
        cr = n // 10_000_000
        rem_lakh = (n % 10_000_000) // 100_000
        if rem_lakh == 0:
            return f"{cr} crore rupees"
        return f"{cr} crore {rem_lakh} lakh rupees"
    if n >= 100_000 and n % 1_000 == 0:
        lakh = n // 100_000
        return f"{lakh} lakh rupees"
    if n >= 1_000 and n % 1_000 == 0:
        return f"{n // 1_000} thousand rupees"
    if n >= 100_000:
        # Not a clean lakh — fall back to "X point Y lakh"
        return f"{n / 100_000:.1f} lakh rupees".replace(".0 ", " ")
    if n >= 1_000:
        return f"{n / 1_000:.1f} thousand rupees".replace(".0 ", " ")
    return f"{n} rupees"


# ---- #56: abbreviation + slash normalization ----
#
# Sarvam Bulbul reads "e.g." as the letters "E G", and a bare "/" as
# "by"/"divide"/"slash". Both make a numbered pricing question sound like a
# robot reading a spreadsheet. We expand these to natural spoken words
# BEFORE money normalization so "₹5L / ₹10L / ₹25L / ₹1Cr" first becomes a
# comma-separated list ("5L, 10L, 25L, or 1Cr") and the existing money
# regexes then turn each token into "5 lakh", "10 lakh", etc.

# "e.g." / "i.e." / "etc." in any spacing/casing. Order: longest first.
# `e.g.,` and `e.g.` both collapse to "for example".
_ABBR_EG = re.compile(r"\be\.\s*g\.\s*,?", re.IGNORECASE)
_ABBR_IE = re.compile(r"\bi\.\s*e\.\s*,?", re.IGNORECASE)
_ABBR_ETC = re.compile(r"\b,?\s*etc\.?", re.IGNORECASE)
_ABBR_VS = re.compile(r"\bvs\.?\b", re.IGNORECASE)
_ABBR_APPROX = re.compile(r"\bapprox\.?", re.IGNORECASE)

# A "/" that joins currency/amount tokens (₹5L / ₹10L / ₹25L / ₹1Cr).
# Convert the whole run into a natural list: "A, B, C, or D". We match a
# slash-separated run of money-ish tokens (digits + optional ₹ + optional
# L/Cr/K/lakh/crore/%/word) and re-join with commas + a trailing "or".
_SLASH_MONEY_RUN = re.compile(
    r"(₹?\s*\d[\d,]*(?:\.\d+)?\s*(?:L\b|Cr\b|K\b|lakh|crore|thousand|%)?[+]?)"
    r"(?:\s*/\s*"
    r"(₹?\s*\d[\d,]*(?:\.\d+)?\s*(?:L\b|Cr\b|K\b|lakh|crore|thousand|%)?[+]?))+",
    re.IGNORECASE,
)
# Generic word/number slash like "parents/siblings", "yes / no",
# "work/otherwise" — read as "or". Keep things like "24/7" and "and/or"
# natural; "and/or" -> "and or" is acceptable spoken English.
_SLASH_GENERIC = re.compile(
    r"(?<=[\w%)\]])\s*/\s*(?=[\w(₹])"
)


def _normalize_slash_money_run(text: str) -> str:
    """Turn "₹5L / ₹10L / ₹25L / ₹1Cr" into "₹5L, ₹10L, ₹25L, or ₹1Cr".

    The downstream money regexes then expand each token to "5 lakh" etc.
    Result spoken: "5 lakh rupees, 10 lakh rupees, 25 lakh rupees, or 1
    crore rupees" — never a slash read as "by"/"divide".
    """

    def _repl(m: "re.Match[str]") -> str:
        run = m.group(0)
        parts = [p.strip() for p in run.split("/") if p.strip()]
        if len(parts) <= 1:
            return run
        if len(parts) == 2:
            return f"{parts[0]}, or {parts[1]}"
        return ", ".join(parts[:-1]) + f", or {parts[-1]}"

    return _SLASH_MONEY_RUN.sub(_repl, text)


# --- #56: numeric ranges + per-unit slashes that the money regexes miss ---
#
# Advisor messages use the EN DASH (–, U+2013) for ranges ("10–30%",
# "₹10–15K", "30–50%"). The existing _MONEY_RANGE_* regexes only match an
# ASCII hyphen, and _normalize_numbers strips –/— to a space, so an
# un-normalized en-dash range becomes "ten thirty percent" / "rupees ten
# 15K". Normalize en/em dash ranges to spoken "X to Y …" HERE (before
# money + before _normalize_numbers) so the whole range survives.
#
# Order: percent-range, then ₹K-range (with optional "/period"), then
# ₹L / ₹Cr ranges, then a bare numeric range fallback.
_RANGE_PCT = re.compile(
    r"(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*%",
)
# "₹10–15K/year" / "₹10–15K per year" / "₹10–15K". The trailing
# "/<word>" becomes "per <word>" (a rate), NOT "or".
_RANGE_K_PERIOD = re.compile(
    r"₹?\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*K\s*/\s*([A-Za-z]+)",
    re.IGNORECASE,
)
_RANGE_K = re.compile(
    r"₹\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*K\b",
    re.IGNORECASE,
)
_RANGE_L = re.compile(
    r"₹\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*L\b",
    re.IGNORECASE,
)
_RANGE_CR = re.compile(
    r"₹\s*(\d+(?:\.\d+)?)\s*[–—-]\s*(\d+(?:\.\d+)?)\s*Cr\b",
    re.IGNORECASE,
)
# A unit "/period" rate that is NOT part of a handled money token, e.g.
# "50K/year" alone, "₹500/month". Convert "/word" -> "per word".
_RATE_SLASH_PERIOD = re.compile(
    r"(?<=[\w%)\]])\s*/\s*(year|yr|month|mo|annum|day|week|claim|policy|person|member|head)\b",
    re.IGNORECASE,
)


def _normalize_ranges(text: str) -> str:
    """Expand en/em-dash numeric ranges + per-period rate slashes.

    Runs BEFORE _normalize_money and _normalize_numbers so the dash isn't
    stripped to a space mid-range and each side keeps its unit.
    """
    # Percent ranges first: "10–30%" -> "10 to 30 percent".
    text = _RANGE_PCT.sub(lambda m: f"{m.group(1)} to {m.group(2)} percent", text)
    # "₹10–15K/year" -> "10 to 15 thousand rupees per year".
    text = _RANGE_K_PERIOD.sub(
        lambda m: f"{m.group(1)} to {m.group(2)} thousand rupees per {m.group(3)}",
        text,
    )
    # "₹10–15K" -> "10 to 15 thousand rupees".
    text = _RANGE_K.sub(
        lambda m: f"{m.group(1)} to {m.group(2)} thousand rupees", text
    )
    # "₹5–10L" -> "5 to 10 lakh rupees" (en-dash variant of _MONEY_RANGE_L).
    text = _RANGE_L.sub(
        lambda m: f"{m.group(1)} to {m.group(2)} lakh rupees", text
    )
    text = _RANGE_CR.sub(
        lambda m: f"{m.group(1)} to {m.group(2)} crore rupees", text
    )
    # Lone "/period" rate slash -> "per period" (do this BEFORE the generic
    # slash->"or" pass so "K/year" doesn't become "K or year").
    text = _RATE_SLASH_PERIOD.sub(lambda m: f" per {m.group(1).lower()}", text)
    return text


def _normalize_abbreviations(text: str) -> str:
    """Expand spoken-hostile abbreviations + slashes.

    Runs BEFORE _normalize_money so currency slash-runs become comma lists
    that the money regexes can then expand token-by-token.
    """
    # Abbreviations first (so a trailing "etc." inside a slash run is gone
    # before slash handling).
    text = _ABBR_EG.sub("for example", text)
    text = _ABBR_IE.sub("that is", text)
    text = _ABBR_ETC.sub(" and so on", text)
    text = _ABBR_VS.sub("versus", text)
    text = _ABBR_APPROX.sub("approximately", text)

    # Ranges + rate-slashes BEFORE slash/money handling so en-dash ranges
    # and "K/year" survive intact.
    text = _normalize_ranges(text)

    # Currency/amount slash runs -> comma list with trailing "or".
    text = _normalize_slash_money_run(text)

    # Any remaining word/number slash -> " or " (parents/siblings, yes / no,
    # work or otherwise). Loop until stable so chained "a/b/c" all convert.
    for _ in range(6):
        new = _SLASH_GENERIC.sub(" or ", text)
        if new == text:
            break
        text = new
    return text


def _normalize_money(text: str) -> str:
    """Turn currency / range shorthand into spoken-language equivalents.

    Examples:
      "₹5L"      → "5 lakhs"
      "₹25L+"    → "25 lakhs or more"
      "₹5-10L"   → "5 to 10 lakhs"
      "₹2Cr"     → "2 crores"
      "Rs. 5000" → "rupees 5000"
    """
    # KI-148: word-form lakh/crore FIRST so "₹15 lakh" / "1 crore" become
    # canonical "<n> lakh rupees" / "<n> crore rupees" and downstream regexes
    # don't double-process them.
    text = _MONEY_WORD_CRORE.sub(lambda m: f"{m.group(1)} crore rupees", text)
    text = _MONEY_WORD_LAKH.sub(lambda m: f"{m.group(1)} lakh rupees", text)

    # KI-148: `k` handling — ranges first, plus second, then prefixed bare.
    text = _MONEY_RANGE_K_BOTH.sub(
        lambda m: f"{m.group(1)} to {m.group(2)} thousand rupees", text
    )
    text = _MONEY_RANGE_K.sub(
        lambda m: f"{m.group(1)} to {m.group(2)} thousand rupees", text
    )
    # PLUS_K must be currency-gated: ₹ prefix OR cascade context. We do a
    # split-pass: prefixed ₹...k+ first, then unprefixed Nk+ that appears
    # after a "thousand rupees"/"lakh rupees" anchor (list-cascade).
    text = re.sub(
        r"₹\s*(\d+(?:\.\d+)?)\s*k\s*\+",
        lambda m: f"above {m.group(1)} thousand rupees",
        text,
        flags=re.IGNORECASE,
    )
    text = _MONEY_K_PREFIXED.sub(
        lambda m: f"{m.group(1)} thousand rupees", text
    )
    # List-cascade pass: bare "Nk" tokens that sit in a list after an
    # already-expanded currency token. Run twice to catch chained items.
    for _ in range(3):
        new = _MONEY_K_LIST_CASCADE.sub(
            lambda m: f"{m.group(1)}{m.group(2)} thousand rupees", text
        )
        if new == text:
            break
        text = new
    # Also handle "+suffix" bare tokens that gained currency via cascade.
    text = re.sub(
        r"((?:thousand|lakh|crore) rupees[^.?!]{0,60}?)\b(\d+(?:\.\d+)?)\s*k\s*\+",
        lambda m: f"{m.group(1)}above {m.group(2)} thousand rupees",
        text,
        flags=re.IGNORECASE,
    )
    # Explicit currency-context bare `Nk` (followed by rupee/premium/etc).
    text = _MONEY_K_CONTEXT.sub(
        lambda m: f"{m.group(1)} thousand rupees", text
    )

    # Existing L / Cr handling (lakh / crore short-suffix).
    text = _MONEY_RANGE_L.sub(lambda m: f"{m.group(1)} to {m.group(2)} lakh rupees", text)
    text = _MONEY_RANGE_CR.sub(lambda m: f"{m.group(1)} to {m.group(2)} crore rupees", text)
    text = _MONEY_PLUS_L.sub(lambda m: f"above {m.group(1)} lakh rupees", text)
    text = _MONEY_PLUS_CR.sub(lambda m: f"above {m.group(1)} crore rupees", text)
    text = _MONEY_L.sub(lambda m: f"{m.group(1)} lakh rupees", text)
    text = _MONEY_CR.sub(lambda m: f"{m.group(1)} crore rupees", text)

    # KI-148: bare-number expansion (only ₹-prefixed or comma-formatted).
    # Indian format `1,00,000` first (longest match), then standard
    # `2,500,000`, then bare ₹30000.
    text = _MONEY_INDIAN_LAKH_COMMA.sub(
        lambda m: _expand_indian_comma_number(m.group(1)), text
    )
    text = _MONEY_STD_COMMA.sub(
        lambda m: _expand_standard_comma_number(m.group(1)), text
    )
    text = _MONEY_BARE_THOUSANDS.sub(
        lambda m: _humanize_int(int(m.group(1))), text
    )

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


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]


def _int_to_words(n: int) -> str:
    """Deterministic 0–99,999 → spoken English (no external dep). Covers ages,
    counts, years. Money/lakh/crore is already handled by _normalize_money."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return _ONES[n // 100] + " hundred" + (" " + _int_to_words(n % 100) if n % 100 else "")
    if n < 100000:
        return _int_to_words(n // 1000) + " thousand" + (" " + _int_to_words(n % 1000) if n % 1000 else "")
    return str(n)


def _normalize_numbers(text: str) -> str:
    """#106 — Sarvam TTS spells bare numerals digit-by-digit ("29" → "two
    nine") and chokes on en/em dashes ("29–year-old"). Run AFTER
    _normalize_money (so lakh/crore is already words): neutralise –/— to a
    space, then expand any remaining standalone integer ≤ 99,999 to words so
    the spoken payload says "twenty-nine". Display text is untouched."""
    text = re.sub(r"\s*[–—]\s*", " ", text)
    def _sub(m: "re.Match[str]") -> str:
        try:
            return _int_to_words(int(m.group(0)))
        except (ValueError, TypeError):
            return m.group(0)
    # standalone integers only — not glued to letters/decimals/version-ids
    text = re.sub(r"(?<![\w.])\d{1,5}(?![\w.])", _sub, text)
    # spoken "percent" so "ten%" isn't read as "ten" (symbol dropped)
    text = re.sub(r"\s*%", " percent", text)
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
# Chain-of-thought / instruction-echo strip
# ============================================================================
# LLM replies can leak internal reasoning into user-visible reply_text in
# three modes:
#   1. Reasoning models (Qwen3-Next 80B) emit a <think>...</think> block
#      followed by the answer, sometimes with a missing / malformed
#      <think> tag that a plain tag strip would let through.
#   2. The faithfulness JUDGE model occasionally returns its own reasoning
#      instead of a clean rescue reply.
#   3. The brain model misunderstands the system prompt and echoes the
#      instruction prose ("We need to respond to user question…").
#
# The strip below is CONSERVATIVE — it only kills CoT preamble lines that
# appear BEFORE the first natural-sounding sentence (within the first ~6
# lines / first 600 chars), so substantive mid-reply content like
# "We have three options: A, B, C" is preserved.

# ---- Sentence-level preamble patterns ----
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

# Stray, unbalanced <think> tags (a balanced-tag strip requires both open
# and close in the same blob). If we see an isolated </think> mid-reply,
# drop everything before it.
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


def tts_preprocess(
    text: str,
    language: str = "en",
    max_words: int | None = None,
) -> str:
    """Public entry — turn an LLM reply into spoken-language text for TTS.

    #55 FIX: there is NO premature word cap. The ENTIRE reply is normalized
    and returned so every numbered question is spoken. Sarvam Bulbul v2's
    per-request character limit is enforced *downstream* by chunked
    synthesis in providers/sarvam_tts.py (house style mirrors the STT
    30s-chunking) — we never silently drop trailing content here.

    `max_words` is kept ONLY for backward call-site compatibility
    (backend/main.py passes max_words=55). It is intentionally IGNORED:
    capping spoken words is exactly the bug we are removing. A caller that
    truly wants a hard cap can still pass an int and we honour it as a
    LAST-RESORT safety ceiling that is far above any real reply
    (>= 100000 effectively never trips); the default None means "speak
    everything".
    """
    if not text:
        return ""
    # Defense in depth: run the preamble strip again here in case this is
    # called on a path that didn't already strip (e.g. direct TTS of a
    # cached reply).
    cleaned = strip_cot_preamble(text)
    cleaned = _strip_markdown(cleaned)
    # #56 — abbreviations ("e.g." -> "for example") and slashes
    # ("₹5L / ₹10L" -> list; "yes / no" -> "yes or no") BEFORE money so
    # currency slash-runs become comma lists the money regexes can expand.
    cleaned = _normalize_abbreviations(cleaned)
    # Currency/range shorthand expansion before acronym handling so ₹5L
    # becomes "5 lakhs" instead of getting caught by the bare-L acronym
    # path.
    cleaned = _normalize_money(cleaned)
    # #106 — AFTER money (lakh/crore already words): expand remaining bare
    # integers ("29" → "twenty-nine") + neutralise en/em dashes so Sarvam
    # stops spelling numbers digit-by-digit ("two nine year old").
    cleaned = _normalize_numbers(cleaned)
    cleaned = _expand_acronyms(cleaned, language=language)
    cleaned = _compress_whitespace(cleaned)
    # #55 — DO NOT truncate by default. Only honour an explicit, sane
    # last-resort ceiling if a caller deliberately passes one (we ignore
    # the legacy 55/60 values — those ARE the bug).
    if max_words is not None and max_words >= 100_000:
        cleaned = _truncate_for_voice(cleaned, max_words=max_words)
    return cleaned
