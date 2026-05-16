"""Deterministic Sum-Insured rationalisation — SINGLE SOURCE OF TRUTH.

The curated `40-data/policy_facts/<id>.json` layer stores every Sum Insured
as a wrapped fact: `{"value": [..], "source_quote": "...", ...}`. The raw
`value` list is the LLM/curation extraction; it can include numbers that the
field's own `source_quote` does NOT actually support (extraction bleed — a
co-pay table figure, a valuables-cover sub-limit, a daily-cash amount, a
premium column read as an SI tier).

This module is the deterministic, no-LLM, no-fabrication gate:

  1. corroborated_values(quote)  — the set of INR amounts the source_quote
     genuinely states, interpreted at the quote's own stated scale
     (lakh|lac|L → ×1e5, crore|cr → ×1e7, Indian digit grouping collapsed).
  2. corroborate(values, quote)  — keep ONLY the policy's listed values that
     appear (verbatim, at the quote's scale) in that quote. Nothing is
     invented: tokens can only CONFIRM a value the policy already lists.
  3. classify(kept, quote)       — decide if the corroborated set is a
     continuous BAND ("₹X – ₹Y") or discrete TIERS ("₹25 L / ₹50 L / …").

It is intentionally dependency-free (no FastAPI / Chroma import) so the
marketplace serializer in main.py can import it without an import cycle, the
same pattern as backend/policy_identity.py.

Public API:
    corroborated_values(source_quote: str) -> set[int]
    corroborate(values: list[int], source_quote: str) -> list[int]
    classify(kept: list[int], source_quote: str) -> ("band"|"tiers"|"none")
    rationalise(values, source_quote) -> SumInsuredView   (one-call helper)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "corroborated_values",
    "corroborate",
    "classify",
    "rationalise",
    "SumInsuredView",
]

# Range/band language in the field's own source_quote. Presence of any of
# these (AND a wide corroborated min→max) is the heuristic for a genuine
# continuous band rather than a discrete plan ladder.
_BAND_TERMS: tuple[str, ...] = (
    "ranging from",
    " to ",
    "range",
    "minimum",
    "maximum",
    "in multiples of",
    "in multiple of",
)

# A corroborated set is treated as a continuous band only when it also spans
# a materially wide spread — guards against e.g. "₹3 L to ₹5 L room rent"
# language on a 2-tier plan being rendered as a continuous band.
_BAND_MIN_ABS_SPREAD = 900_000   # max − min ≥ ₹9 L
_BAND_MIN_RATIO = 3              # max / min ≥ 3×

# Smallest plausible standalone full-rupee SI figure in a quote. Below this a
# bare integer is treated as noise (page numbers, counts) unless a lakh/crore
# unit is in scope.
_MIN_RUPEE_TOKEN = 10_000


def _collapse_indian_grouping(s: str) -> str:
    """Join Indian-format digit grouping (1,00,000 / 5,00,000 / ₹5, 00,000)
    into a single integer so the value matches its policy-list form.

    A list separator (`0.5, 1, 1.5`) is NOT joined: the left operand of a
    grouping comma never carries a decimal point, and a grouping tail is
    exactly 2 or 3 digits terminated by a non-digit. Comma-then-space is a
    grouping form too (real quotes contain `Rs5, 00,000`), so we accept an
    optional single space after the comma.
    """
    pat = re.compile(r"(?<![\d.])(\d{1,3})\s*,\s?(\d{2,3})(?=\D|$)")
    prev = None
    while prev != s:
        prev = s
        s = pat.sub(lambda mo: mo.group(1) + mo.group(2), s)
    # Explicit full 3-group form (handled by the iterative pass above too,
    # kept for clarity / belt-and-braces).
    s = re.sub(
        r"(?<![\d.])(\d{1,2}),\s?(\d{2}),\s?(\d{3})(?=\D|$)",
        lambda mo: mo.group(1) + mo.group(2) + mo.group(3),
        s,
    )
    return s


def corroborated_values(source_quote: str | None) -> set[int]:
    """The set of INR Sum-Insured amounts the source_quote genuinely states.

    Deterministic. Per the D3 normalisation rule: lakh|lac|L → ×1e5,
    crore|cr → ×1e7, commas/spaces stripped from digit groups. A bare number
    is interpreted at every scale the quote explicitly invokes:
      • full rupee value when it is a large standalone integer (≥ ₹10k);
      • × ₹1 L when the quote anywhere says lakh/lac/L (covers both inline
        "₹5 Lakh" AND a "(Rs. in Lakhs) 3.00 5.00 10.00" header list);
      • × ₹1 Cr when the quote anywhere says crore/cr.
    No fabrication: this only enumerates numbers that physically appear in
    the quote; corroborate() then intersects with the policy's own list.
    """
    if not source_quote:
        return set()
    raw = source_quote.lower()
    for ch in ("₹", "`"):
        raw = raw.replace(ch, " ")
    raw = re.sub(r"\brs\.?\b", " ", raw)
    raw = raw.replace("inr", " ")

    glued_lakh: set[int] = set()
    glued_crore: set[int] = set()
    # Glued unit notation: 3l / 10l / 7.5l / 1cr / 50lacs / ₹5 Lakh
    for mo in re.finditer(
        r"(?<![\d.])(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|l|crores?|cr)(?![a-z])", raw
    ):
        f = float(mo.group(1))
        unit = mo.group(2)
        if unit.startswith("cr") or unit.startswith("crore"):
            glued_crore.add(int(round(f * 1e7)))
        else:
            glued_lakh.add(int(round(f * 1e5)))

    has_lakh = bool(glued_lakh) or re.search(
        r"lakhs?|lacs?|\(in l|in lakh", raw
    ) is not None
    has_crore = bool(glued_crore) or re.search(r"crores?|\bcr\b", raw) is not None

    collapsed = _collapse_indian_grouping(raw)
    out: set[int] = set(glued_lakh) | set(glued_crore)
    for mo in re.finditer(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])", collapsed):
        f = float(mo.group(1))
        if f >= _MIN_RUPEE_TOKEN and f == int(f):
            out.add(int(f))
        if has_lakh:
            out.add(int(round(f * 1e5)))
        if has_crore:
            out.add(int(round(f * 1e7)))
    return out


def corroborate(values, source_quote: str | None) -> list[int]:
    """Keep ONLY the policy-listed `values` whose figure is genuinely stated
    in this field's `source_quote`. Sorted, de-duplicated, ints. Empty list
    when nothing corroborates (→ caller renders "As per policy schedule")."""
    if not values:
        return []
    present = corroborated_values(source_quote)
    if not present:
        return []
    uniq: set[int] = set()
    for v in values:
        try:
            uniq.add(int(v))
        except (TypeError, ValueError):
            continue
    return sorted(v for v in uniq if v in present)


def classify(kept, source_quote: str | None) -> str:
    """'band' | 'tiers' | 'none' for an already-corroborated value set.

    BAND  — the quote uses range language AND the corroborated set spans a
            materially wide min→max (continuous offering).
    TIERS — corroborated discrete plan amounts.
    NONE  — nothing corroborated.
    """
    kept = sorted(set(int(v) for v in (kept or [])))
    if not kept:
        return "none"
    q = (source_quote or "").lower()
    has_band_lang = any(t in q for t in _BAND_TERMS)
    lo, hi = kept[0], kept[-1]
    wide = (hi - lo) >= _BAND_MIN_ABS_SPREAD and (hi / max(lo, 1)) >= _BAND_MIN_RATIO
    if has_band_lang and wide and len(kept) >= 2:
        return "band"
    return "tiers"


@dataclass
class SumInsuredView:
    """The rationalised SI view for one policy field. `kind` drives display:
      band  → "₹{min} – ₹{max}"   (min == sum_insured_min, max == sum_insured_max)
      tiers → list the tiers       ("₹25 L / ₹50 L / ₹1 Cr"; ">4 → min … max · N")
      none  → "As per policy schedule"
    """
    kind: str                 # "band" | "tiers" | "none"
    tiers: list[int]          # corroborated, sorted (empty when kind == "none")
    min_inr: int | None
    max_inr: int | None

    @property
    def is_band(self) -> bool:
        return self.kind == "band"


def rationalise(values, source_quote: str | None) -> SumInsuredView:
    """One-call helper: corroborate → classify → packaged view."""
    kept = corroborate(values, source_quote)
    kind = classify(kept, source_quote)
    if kind == "none":
        return SumInsuredView(kind="none", tiers=[], min_inr=None, max_inr=None)
    return SumInsuredView(
        kind=kind,
        tiers=kept,
        min_inr=kept[0],
        max_inr=kept[-1],
    )
