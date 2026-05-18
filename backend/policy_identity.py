"""Canonical policy identity + dedup — SINGLE SOURCE OF TRUTH.

The canonical-identity rule:

    1 unique IRDAI UIN  =  1 unique product (UIN-primary invariant).
    When no UIN is present, the product_key = the policy_id with any
    trailing doctype suffix (`__wordings` / `__brochure` / `__cis` /
    `__prospectus`) stripped.

Both the marketplace endpoint (`main.py /api/policies/all`) and the
recommendation-fit gate (retrieval_filters.dedup_by_policy) key on this
rule, so two chunks for the SAME product with different ids — a marketing
rename ("my:Optima Secure" vs "my:Optima Secure (older variant)") or two
doctype siblings (`...__wordings` vs `...__brochure`) — collapse to one
identity and the two surfaces agree on what "the same policy" means.

This module factors that one rule so BOTH surfaces share it. It is
deliberately dependency-free (no FastAPI / Chroma import) so
retrieval_filters can import it without an import cycle.

Public API:
    canonical_key(chunk_or_meta) -> str
        The dedup key for a retrieved chunk: prefer a normalised UIN, else
        the product_key (policy_id with the doctype suffix stripped).
    product_key(policy_id) -> str
        The marketplace product_key (KI-133): policy_id minus any trailing
        `__<doctype>` segment.
    normalize_uin(raw) -> str
        Upper-cased, whitespace/punctuation-collapsed UIN, or "" when the
        value is absent / not a plausible UIN.
    clean_display_policy_name(name) -> str
        Display-only cleanup of the user-facing product name (BUG #24).
        Strips the typo-looking lowercase `my:` prefix from HDFC ERGO's
        Optima family ("my:Optima Secure (older variant)" ->
        "Optima Secure (older variant)") and a duplicated leading
        "HDFC ERGO " insurer label (the card shows the insurer
        separately). TARGETED — `my:health Suraksha` etc. are legitimate
        HDFC ERGO brand names and are left untouched. Never alters the
        policy_id / UIN / dedup key (name is display-only).
"""

from __future__ import annotations

import re
from typing import Any

# Trailing doctype segments the curator appends to a policy_id stem. Mirrors
# brain_tools._DOCTYPE_SUFFIXES and main.py _doctype_of_cov. A product_key is
# the stem with ANY of these removed (KI-133 — "wordings wins" dedup).
_DOCTYPE_SUFFIXES = ("__wordings", "__brochure", "__cis", "__prospectus")

# A plausible IRDAI UIN is mostly alphanumerics with a few separators and is
# reasonably long. We normalise aggressively so "IRDA/HLT/.../188/14-15" and
# "irdahlt18814 15" compare equal. Anything shorter than 8 normalised chars
# is treated as "no UIN" (a stray code, not a real filing id).
_UIN_STRIP_RE = re.compile(r"[^A-Z0-9]")
_MIN_UIN_LEN = 8


def product_key(policy_id: str) -> str:
    """KI-133 product_key: the policy_id with any trailing `__<doctype>`
    segment removed. `acko__health-iii__wordings` -> `acko__health-iii`.
    Mirrors main.py `_product_key_of_cov` exactly so the recommender and
    the marketplace agree on product identity."""
    pid = (policy_id or "").strip()
    if not pid:
        return ""
    for suf in _DOCTYPE_SUFFIXES:
        if pid.endswith(suf):
            return pid[: -len(suf)]
    # main.py uses rsplit on the LAST "__"; mirror it so an un-suffixed
    # multi-segment id collapses identically (insurer__product__variant ->
    # insurer__product only when the tail is a known doctype; otherwise the
    # rsplit form is what the marketplace card count used).
    if "__" in pid:
        head, tail = pid.rsplit("__", 1)
        # Only strip the tail if it looks like a doctype token (the
        # marketplace's _doctype_of_cov returns "" for non-doctype tails
        # and product_key then == pid, so guard against over-collapsing
        # genuine product segments like "...__platinum").
        if tail in ("wordings", "brochure", "cis", "prospectus"):
            return head
    return pid


def normalize_uin(raw: Any) -> str:
    """Upper-case + strip every non-alphanumeric char. Accepts the
    policy_facts `{value: ...}` wrapper or a bare string. Returns "" when
    the value is missing or too short to be a real IRDAI UIN."""
    if isinstance(raw, dict) and "value" in raw:
        raw = raw.get("value")
    if not isinstance(raw, str):
        return ""
    norm = _UIN_STRIP_RE.sub("", raw.upper())
    if len(norm) < _MIN_UIN_LEN:
        return ""
    return norm


def _meta(chunk_or_meta: Any) -> dict:
    """Accept a dict (brain_tools chunk) or a duck-typed object."""
    if isinstance(chunk_or_meta, dict):
        return chunk_or_meta
    out: dict = {}
    for k in ("policy_id", "uin_code", "uin"):
        if hasattr(chunk_or_meta, k):
            out[k] = getattr(chunk_or_meta, k)
    return out


def canonical_key(chunk_or_meta: Any) -> str:
    """The dedup key for a retrieved chunk.

    UIN-primary (matches the marketplace KI-142 invariant): when the chunk
    carries a plausible UIN, that normalised UIN IS the identity — a
    marketing rename keeps the same UIN so its variant chunk collapses onto
    the parent card. Otherwise fall back to the product_key (doctype-stripped
    policy_id) so two doctype siblings of the same product still collapse.

    Always returns a non-empty string (falls back to the raw policy_id, then
    to the policy_name) so a chunk is never silently keyed to "".
    """
    m = _meta(chunk_or_meta)
    uin = normalize_uin(m.get("uin_code") or m.get("uin"))
    if uin:
        return f"uin:{uin}"
    pid = (m.get("policy_id") or "").strip()
    pk = product_key(pid)
    if pk:
        return f"pk:{pk}"
    name = (m.get("policy_name") or "").strip().lower()
    return f"nm:{name}" if name else "pk:"


# BUG #24 — display-name cleanup. The user-facing name for HDFC ERGO's
# Optima Secure carries a typo-looking lowercase `my:` prefix
# ("my:Optima Secure (older variant)") that reads as a formatting error.
# It originates in the corpus manifest / extracted JSON / curated facts
# and flows to every surface (marketplace card, chat citations,
# get_policy_facts, scorecard, retrieve_policies chunks). We normalise it
# once, centrally, at every user-facing surface.
#
# TARGETED — NOT a blanket `my:` strip:
#   * `my:Optima ...`  -> `Optima ...`            (the BUG — fixed)
#   * `my:health Suraksha` / `my:health Medisure Prime` / ...
#                       -> UNCHANGED              (legitimate HDFC ERGO
#                                                  brand names — KEEP)
# We also drop a duplicated leading "HDFC ERGO " insurer label because
# the card/citation shows the insurer separately
# ("HDFC ERGO my:Optima Secure" -> "Optima Secure").
#
# The "(older variant)" suffix is an intentional KI-145 dedup
# disambiguator (1 IRDAI UIN = 1 card) and is preserved verbatim.
#
# This is display-only: policy_id / UIN / canonical_key are never derived
# from the cleaned name, so dedup + policy_id resolution are unchanged.

# Only the leading "<duplicate insurer> " prefix on THIS product family is
# stripped — scoped so we never touch an insurer token inside another
# insurer's product name.
_DUP_INSURER_PREFIX_RE = re.compile(
    r"^\s*HDFC\s*ERGO\s+(?=my:\s*Optima\b|Optima\s+Secure\b)",
    re.IGNORECASE,
)
# `my:Optima` (any case, optional space after the colon) -> `Optima`.
# Bounded to the literal word "Optima" so `my:health ...` never matches.
_MY_OPTIMA_PREFIX_RE = re.compile(r"\bmy:\s*(?=Optima\b)", re.IGNORECASE)


def clean_display_policy_name(name: Any) -> str:
    """Return the user-facing product name with the BUG #24 typo-looking
    `my:` prefix removed for the HDFC ERGO Optima family ONLY, plus any
    duplicated leading "HDFC ERGO " insurer label stripped.

    Conservative and idempotent: a name with no `my:Optima` / leading
    "HDFC ERGO " is returned unchanged (so `my:health Suraksha`,
    `Optima Secure (older variant)`, every non-HDFC name pass through
    untouched). Never mutates identity — callers pass the display string
    only; policy_id / UIN / dedup keys are computed elsewhere from the
    raw id, not from this value.
    """
    if not isinstance(name, str):
        return name if name is not None else ""
    out = _DUP_INSURER_PREFIX_RE.sub("", name)
    out = _MY_OPTIMA_PREFIX_RE.sub("", out)
    # Collapse any double space the prefix removals may have left and trim.
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out or name


__all__ = [
    "canonical_key",
    "product_key",
    "normalize_uin",
    "clean_display_policy_name",
]
