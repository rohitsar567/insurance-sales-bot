"""Persistent uploaded-policy store (#52 — graded server-assignment).

WHAT THIS MODULE DOES
---------------------
When a user uploads a PDF via POST /api/upload-policy, three things must
survive an HF Space restart and become globally visible:

  1. the raw PDF bytes,
  2. a curated-facts-shaped JSON record (the SAME `{field:{value,
     source_pdf_path, source_quote, _confidence}}` schema that
     40-data/policy_facts/*.json uses, so it flows through the EXISTING
     `backend.main._load_curated_facts` -> `_marketplace_catalogue` Pass-2
     -> `build_scorecard` path with ZERO grading re-implementation), and
  3. enough to re-index the document's chunks into the working Chroma
     `policies` collection on the next boot.

PERSISTENCE MODEL
-----------------
Everything lands under `settings.UPLOADED_DOCS_DIR`:

    <UPLOADED_DOCS_DIR>/
        <policy_id>/
            source.pdf            # raw uploaded bytes
            record.json           # curated-facts-shaped JSON (the card)
            chunks.json           # [{chunk_idx,text,page_start,page_end}, ...]
            meta.json             # {policy_id, policy_name, insurer_slug,
                                  #  sha256, uploaded_at, session_id}

On the HF Space `settings.UPLOADED_DOCS_DIR` resolves to a directory on
the PERSISTENT `/data` disk (see backend/config.py + entrypoint.sh), so a
Space rebuild — which throws away the ephemeral container FS including
rag/vectors — does NOT lose uploaded policies. Locally (no /data) it
resolves under settings.DATA_DIR so the exact same code path works.

PRIVACY MODEL (explicit, per #52 spec)
--------------------------------------
The #52 spec says the uploaded doc is *added to THE (global) marketplace*.
So once a user uploads a policy it is intentionally a public marketplace
card and its chunks are globally retrievable (doc_type='user_upload' in
the main `policies` collection). The persistent store therefore contains
ONLY the uploaded policy document itself + data derived from it — never a
session profile, never another user's data. `session_id` is recorded in
meta.json purely for operational audit/abuse-tracing; it is NEVER used to
gate visibility of the card or the chunks (those are global by design) and
is NEVER written into the Chroma chunk metadata of the global collection.
The pre-existing session-scoped `user_uploads_quarantine` collection is a
separate, private, ephemeral path and is untouched by this module.

NO SILENT FAILURES
------------------
Every function here either succeeds or raises a typed exception with a
clear message. Callers (backend.main) decide whether a failure is fatal to
the request (record creation) or best-effort-logged (startup re-ingest of
ONE doc must not abort boot, but the failure is logged loudly).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from backend.config import settings

_log = logging.getLogger(__name__)

# Chroma metadata doc_type for a persisted, globally-visible uploaded doc.
# Deliberately the SAME token the quarantine path uses so the existing
# brain_tools UPLOADED-DOC handling + retrieve.py treat it identically.
UPLOAD_DOC_TYPE = "user_upload"

# Insurer slug for uploaded docs. MUST NOT be "regulatory" (that slug is
# filtered out of the marketplace) and MUST be stable so the card always
# resolves the same insurer_meta fallback.
UPLOAD_INSURER_SLUG = "user-upload"
UPLOAD_INSURER_NAME = "User-uploaded document"


# ---------------------------------------------------------------------------
# Insurer detection from PDF text (2026-05-27).
#
# Pre-this-change, every upload was stamped insurer_slug="user-upload",
# which short-circuits the Claim Experience scorecard sub-score (no
# matching reviews JSON under 40-data/reviews/<slug>.json) and leaves
# the card showing "reputation data being compiled" forever.
#
# Strategy: regex-match the first ~3 pages of the PDF against the
# canonical legal names of the 21 insurers we already have reviews
# data for. On a confident hit, flip insurer_slug to the real slug so
# the scorecard's Claim-Experience pass uses the real reviews data
# (claim_ratio, complaints, network) — same path as a catalogued card.
#
# Fail-closed: no match ⇒ stays "user-upload". Score still works,
# Claim Experience just falls back to a generic mid-range number.
# ---------------------------------------------------------------------------

# Each entry: (slug, [name_patterns]). Order matters — first hit wins,
# so put the most specific patterns first (e.g. "future generali" before
# bare "generali" — though we don't have a generali reviews file today).
# Patterns are matched case-insensitive against the first ~3 pages of
# PDF text (first ~6000 chars).
_INSURER_NAME_PATTERNS: list[tuple[str, list[str]]] = [
    ("acko",                ["acko general insurance", "acko general", "acko gen ins", "acko gi"]),
    ("aditya-birla",        ["aditya birla health insurance", "abhicl", "aditya birla health", "aditya birla"]),
    ("bajaj-allianz",       ["bajaj allianz general insurance", "bajaj allianz general", "bajaj allianz"]),
    ("care-health",         ["care health insurance", "religare health insurance"]),
    ("cholamandalam",       ["cholamandalam ms general insurance", "cholamandalam ms general", "cholamandalam ms", "chola ms"]),
    ("go-digit",            ["go digit general insurance", "go digit", "godigit"]),
    ("hdfc-ergo",           ["hdfc ergo general insurance", "hdfc ergo health", "hdfc ergo"]),
    ("icici-lombard",       ["icici lombard general insurance", "icici lombard"]),
    ("iffco-tokio",         ["iffco tokio general insurance", "iffco tokio"]),
    ("indusind-general",    ["indusind general insurance", "indusind general"]),
    ("manipalcigna",        ["manipalcigna health insurance", "manipal cigna health insurance", "manipalcigna", "manipal cigna"]),
    ("national-insurance",  ["national insurance company", "national insurance"]),
    ("new-india",           ["new india assurance company", "new india assurance", "the new india assurance"]),
    ("niva-bupa",           ["niva bupa health insurance", "niva bupa", "max bupa"]),
    ("oriental-insurance",  ["oriental insurance company", "the oriental insurance", "oriental insurance"]),
    ("reliance-general",    ["reliance general insurance"]),
    ("royal-sundaram",      ["royal sundaram general insurance", "royal sundaram"]),
    ("sbi-general",         ["sbi general insurance", "sbi gen"]),
    ("star-health",         ["star health and allied insurance", "star health and allied", "star health"]),
    ("tata-aig",            ["tata aig general insurance", "tata aig"]),
]


def detect_insurer_slug(full_text: str) -> Optional[str]:
    """Return the matching insurer slug (one of 21) or None.

    Scans only the first ~6 000 chars (typically the cover + Part I) since
    the insurer's legal name is in the header / footer of every IRDAI PDF.
    Case-insensitive substring match in pattern-priority order. Fail-closed.
    """
    if not full_text:
        return None
    head = full_text[:6000].lower()
    for slug, patterns in _INSURER_NAME_PATTERNS:
        for pat in patterns:
            if pat in head:
                return slug
    return None


def detected_insurer_name(slug: str) -> str:
    """Pretty-display name for a detected slug — used in the persisted
    record so the card header reads "ManipalCigna" not "manipalcigna".
    """
    return {
        "acko":                "Acko",
        "aditya-birla":        "Aditya Birla Health",
        "bajaj-allianz":       "Bajaj Allianz",
        "care-health":         "Care Health",
        "cholamandalam":       "Cholamandalam MS",
        "go-digit":            "Go Digit",
        "hdfc-ergo":           "HDFC ERGO",
        "icici-lombard":       "ICICI Lombard",
        "iffco-tokio":         "IFFCO Tokio",
        "indusind-general":    "IndusInd General",
        "manipalcigna":        "ManipalCigna",
        "national-insurance":  "National Insurance",
        "new-india":           "New India Assurance",
        "niva-bupa":           "Niva Bupa",
        "oriental-insurance":  "Oriental Insurance",
        "reliance-general":    "Reliance General",
        "royal-sundaram":      "Royal Sundaram",
        "sbi-general":         "SBI General",
        "star-health":         "Star Health",
        "tata-aig":            "Tata AIG",
    }.get(slug, slug)


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------


def uploaded_docs_dir() -> Path:
    """The persistent root for uploaded docs. Created on first use."""
    d = settings.UPLOADED_DOCS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _doc_dir(policy_id: str) -> Path:
    # policy_id is already a tight slug (see backend.main.upload_policy:
    # user-upload__<sid12>__<fileslug>) but defend against path traversal.
    safe = re.sub(r"[^a-zA-Z0-9_.\-]+", "-", policy_id).strip("-") or "user-upload"
    return uploaded_docs_dir() / safe


def prune_persisted_upload(
    policy_id: Optional[str] = None, *, prefix: Optional[str] = None
) -> dict:
    """Operator/abuse prune of persisted uploaded doc(s) (#52 residual #5,
    #77). Pass an exact `policy_id` OR a `prefix` (e.g.
    'user-upload__e2e-verify' to bulk-remove test/abuse cards).

    HARD GUARDRAIL: only ever removes a directory that is a DIRECT CHILD of
    UPLOADED_DOCS_DIR — it can never touch rag/corpus, 40-data, or any
    curated/extracted data. A path-safety violation RAISES (must surface;
    a silent no-op here would be forbidden by the no-silent-failure rule).
    Returns {removed:[ids], skipped:[ids-not-present], root}.
    """
    root = uploaded_docs_dir().resolve()
    targets: list[str] = []
    if policy_id:
        targets.append(policy_id)
    if prefix is not None:
        pfx = re.sub(r"[^a-zA-Z0-9_.\-]+", "-", prefix).strip("-")
        if not pfx:
            raise RuntimeError("prune prefix is empty after sanitisation")
        for d in sorted(root.glob("*")):
            if d.is_dir() and d.name.startswith(pfx):
                targets.append(d.name)
    removed: list[str] = []
    skipped: list[str] = []
    for pid in dict.fromkeys(targets):  # dedupe, preserve order
        ddir = _doc_dir(pid).resolve()
        if ddir == root or root not in ddir.parents:
            raise RuntimeError(
                f"refusing to prune outside uploaded-docs root: {pid!r}"
            )
        if not ddir.exists():
            skipped.append(pid)
            continue
        shutil.rmtree(ddir)
        removed.append(pid)
    return {"removed": removed, "skipped": skipped, "root": str(root)}


# ---------------------------------------------------------------------------
# Heuristic field extraction  ->  curated-facts-shaped record
#
# The repo's LLM extractor (rag/extract.py) needs network + the NIM brain.
# That is correct for the corpus build but unusable inside a request (and
# untestable offline). So we derive a REAL, sourced record deterministically
# from the PDF's own text via regex over the IRDAI-standardised wording that
# every Indian health policy uses. Each field we emit carries the verbatim
# source_quote it was matched from — nothing is fabricated; a field is only
# emitted when its evidence is literally present in the document.
#
# This populates well above the scorecard's MIN_GRADEABLE_COMPLETENESS_PCT
# (9.0 == ~2 of 23 SCORED_FIELDS) so the card grades for real instead of
# returning the data-starved "—"/0 sentinel. When the document genuinely
# lacks structured terms, we DO NOT invent any — the card then honestly
# shows the sentinel, which is the correct behaviour.
# ---------------------------------------------------------------------------


def _ctx(text: str, m: re.Match, pad: int = 90) -> str:
    """Verbatim surrounding snippet for a regex match (the source_quote)."""
    s = max(0, m.start() - pad)
    e = min(len(text), m.end() + pad)
    return re.sub(r"\s+", " ", text[s:e]).strip()[:300]


def _fact(value: Any, quote: str, conf: str = "medium") -> dict:
    """A curated-facts cell: {value, source_pdf_path, source_quote, _confidence}."""
    return {
        "value": value,
        "source_pdf_path": "",  # filled by the caller with the persisted PDF path
        "source_quote": quote,
        "_confidence": conf,
    }


def extract_fields_from_text(full_text: str) -> dict[str, dict]:
    """Regex-derive scorecard-relevant fields from policy text.

    Returns a {field_name: <fact cell>} dict using the SAME canonical field
    names backend.scorecard.SCORED_FIELDS / ALIASES read. Only fields with
    literal textual evidence are emitted. Never raises (a totally
    unparseable doc just yields {}).
    """
    t = full_text or ""
    low = t.lower()
    out: dict[str, dict] = {}

    def add(field: str, value: Any, m: Optional[re.Match], conf: str = "medium"):
        if value is None:
            return
        if field in out:
            return
        quote = _ctx(t, m) if m is not None else ""
        out[field] = _fact(value, quote, conf)

    # --- UIN (regulator identity; not a scored field but anchors the card) --
    m = re.search(r"\b([A-Z]{3}[A-Z0-9]{10,22}V\d{6})\b", t)
    if m:
        add("uin_code", m.group(1), m, "high")

    # --- Initial waiting period (days) -------------------------------------
    m = re.search(
        r"(\d{1,3})\s*days?[^.]{0,80}?(?:waiting period|from the (?:first )?"
        r"(?:policy )?(?:commencement|inception)|shall be excluded)",
        t, re.IGNORECASE,
    ) or re.search(
        r"(?:waiting period|initial waiting)[^.]{0,60}?(\d{1,3})\s*days?",
        t, re.IGNORECASE,
    )
    if m:
        d = int(m.group(1))
        if 0 < d <= 90:
            add("initial_waiting_period_days", d, m, "high")

    # --- Pre-existing disease waiting (months) -----------------------------
    m = re.search(
        r"pre[\-\s]?existing[^.]{0,120}?(\d{1,2})\s*(?:months|month)",
        t, re.IGNORECASE,
    ) or re.search(
        r"(\d{1,2})\s*months[^.]{0,80}?pre[\-\s]?existing",
        t, re.IGNORECASE,
    )
    if m:
        mo = int(m.group(1))
        if 0 < mo <= 72:
            add("pre_existing_disease_waiting_months", mo, m, "high")

    # --- Specific-disease waiting (months) ---------------------------------
    m = re.search(
        r"(?:specific (?:disease|illness)|cataract|hernia)[^.]{0,120}?"
        r"(\d{1,2})\s*months",
        t, re.IGNORECASE,
    )
    if m:
        mo = int(m.group(1))
        if 0 < mo <= 48:
            add("specific_disease_waiting_months", mo, m, "medium")

    # --- Maternity waiting (months) ----------------------------------------
    m = re.search(
        r"maternity[^.]{0,120}?(\d{1,2})\s*months",
        t, re.IGNORECASE,
    )
    if m:
        mo = int(m.group(1))
        if 0 < mo <= 48:
            add("maternity_waiting_months", mo, m, "medium")

    # --- Pre / post hospitalisation (days) ---------------------------------
    m = re.search(r"pre[\-\s]?hospitali[sz]ation[^.]{0,60}?(\d{1,3})\s*days", t, re.IGNORECASE)
    if m:
        d = int(m.group(1))
        if 0 < d <= 180:
            add("pre_hospitalization_days", d, m, "high")
    m = re.search(r"post[\-\s]?hospitali[sz]ation[^.]{0,60}?(\d{1,3})\s*days", t, re.IGNORECASE)
    if m:
        d = int(m.group(1))
        if 0 < d <= 365:
            add("post_hospitalization_days", d, m, "high")

    # --- Co-payment (%) ----------------------------------------------------
    m = re.search(r"co[\-\s]?pay(?:ment)?[^.]{0,80}?(\d{1,2})\s*%", t, re.IGNORECASE) \
        or re.search(r"(\d{1,2})\s*%[^.]{0,40}?co[\-\s]?pay", t, re.IGNORECASE)
    if m:
        pct = int(m.group(1))
        if 0 <= pct <= 50:
            add("copayment_pct", pct, m, "medium")

    # --- No-claim bonus (%) ------------------------------------------------
    m = re.search(
        r"(?:no[\-\s]?claim bonus|cumulative bonus|ncb)[^.]{0,80}?(\d{1,3})\s*%",
        t, re.IGNORECASE,
    )
    if m:
        pct = int(m.group(1))
        if 0 < pct <= 200:
            # MarketplacePolicy.no_claim_bonus_pct is Optional[int]; the
            # scorecard reads it numerically either way. Emit int.
            add("no_claim_bonus_pct", pct, m, "medium")

    # --- Room rent capping -------------------------------------------------
    m = re.search(
        r"room rent[^.]{0,90}?(no (?:sub[\-\s]?limit|cap|capping|limit)|"
        r"\d{1,2}\s*%\s*(?:of\s*(?:the\s*)?sum insured|of si)?|single private|"
        r"twin sharing|shared accommodation)",
        t, re.IGNORECASE,
    )
    if m:
        cap = m.group(1).strip()
        if re.search(r"no (sub[\-\s]?limit|cap|capping|limit)", cap, re.IGNORECASE):
            cap = "No room rent cap"
        add("room_rent_capping", cap, m, "medium")

    # --- Network hospital count -------------------------------------------
    m = re.search(
        r"([\d,]{3,7})\+?\s*(?:network |empanelled |cashless )?hospitals?",
        t, re.IGNORECASE,
    )
    if m:
        try:
            n = int(m.group(1).replace(",", ""))
            if 50 <= n <= 50000:
                add("network_hospital_count", n, m, "medium")
        except ValueError:
            pass

    # --- Cashless supported -----------------------------------------------
    if "cashless" in low:
        m = re.search(r"cashless[^.]{0,80}", t, re.IGNORECASE)
        add("cashless_treatment_supported", True, m, "medium")

    # --- Max entry age (years) --------------------------------------------
    m = re.search(
        r"(?:maximum |max\.? )?entry age[^.]{0,40}?(\d{2,3})\s*years",
        t, re.IGNORECASE,
    ) or re.search(
        r"entry age[^.]{0,40}?up to\s*(\d{2,3})\s*years", t, re.IGNORECASE,
    )
    if m:
        age = int(m.group(1))
        if 30 <= age <= 100:
            add("max_entry_age", age, m, "medium")

    # --- AYUSH coverage ----------------------------------------------------
    if re.search(r"\bayush\b", low) or "ayurved" in low:
        m = re.search(r"ayush[^.]{0,90}", t, re.IGNORECASE) or re.search(
            r"ayurved[^.]{0,90}", t, re.IGNORECASE)
        add("ayush_coverage", {"covered": True}, m, "medium")

    # --- Maternity coverage (boolean-with-detail) -------------------------
    if "maternity" in low:
        m = re.search(r"maternity[^.]{0,120}", t, re.IGNORECASE)
        covered = not bool(re.search(
            r"maternity[^.]{0,40}(not covered|excluded|no cover)", t, re.IGNORECASE))
        add("maternity_coverage", {"covered": covered}, m, "medium")

    # --- Ambulance / day-care / restoration (presence booleans) -----------
    if "ambulance" in low:
        m = re.search(r"ambulance[^.]{0,90}", t, re.IGNORECASE)
        add("ambulance_cover", {"covered": True}, m, "low")
    if "day care" in low or "day-care" in low or "daycare" in low:
        m = re.search(r"day[\-\s]?care[^.]{0,90}", t, re.IGNORECASE)
        add("day_care_treatments_count", {"covered": True, "limit_text": "Day-care procedures covered"}, m, "low")
    if "restoration" in low or "refill" in low or "reinstatement" in low:
        m = re.search(r"(restoration|refill|reinstatement)[^.]{0,90}", t, re.IGNORECASE)
        add("restoration_benefit", {"covered": True}, m, "low")

    # --- Claim settlement ratio (insurer-level; commonly stated in CIS) ----
    m = re.search(
        r"claim settlement ratio[^.]{0,40}?(\d{2,3}(?:\.\d{1,2})?)\s*%",
        t, re.IGNORECASE,
    )
    if m:
        try:
            csr = float(m.group(1))
            if 30 <= csr <= 100:
                add("claim_settlement_ratio", csr, m, "medium")
        except ValueError:
            pass

    # ─── 2026-05-27 — heuristic-baseline expansion (KI-332) ─────────────
    # Adds ~12 new patterns that lift typical upload completeness from
    # ~47.8% to ~65-70% even when ALL LLM passes fail. Each pattern is
    # high-precision (regex with sanity bounds) — if the doc literally
    # doesn't state the value we skip the field, never fabricate.

    # --- Sum insured options (INR) ----------------------------------------
    # Catches "₹3 Lakh / ₹5 Lakh / ₹10 Lakh / ₹25 Lakh" style ladders.
    si_matches = re.findall(
        r"(?:rs\.?|₹|inr)\s*(\d{1,3}(?:[,.]\d{2,3})*)\s*(lakh|lac|crore|cr)\b",
        t, re.IGNORECASE,
    )
    if si_matches:
        vals: list[int] = []
        for num_str, unit in si_matches:
            try:
                n = float(num_str.replace(",", ""))
                if unit.lower() in ("lakh", "lac"):
                    n_inr = int(n * 100_000)
                else:  # crore
                    n_inr = int(n * 10_000_000)
                if 100_000 <= n_inr <= 500_000_000:
                    vals.append(n_inr)
            except (ValueError, TypeError):
                continue
        vals = sorted(set(vals))[:10]  # cap at 10 options; sorted ascending
        if 2 <= len(vals) <= 10:
            m2 = re.search(r"(?:rs\.?|₹|inr)\s*\d", t, re.IGNORECASE)
            add("sum_insured_options_inr", vals, m2, "medium")

    # --- Policy type ------------------------------------------------------
    if re.search(r"\bfamily floater\b", low):
        m = re.search(r"family floater[^.]{0,60}", t, re.IGNORECASE)
        add("policy_type", "family_floater", m, "medium")
    elif re.search(r"\bsenior citizen\b", low) and "policy" in low:
        m = re.search(r"senior citizen[^.]{0,60}", t, re.IGNORECASE)
        add("policy_type", "senior_citizen", m, "medium")
    elif re.search(r"\bcritical illness\b", low) and "lump" in low:
        m = re.search(r"critical illness[^.]{0,60}", t, re.IGNORECASE)
        add("policy_type", "critical_illness", m, "medium")
    elif re.search(r"\btop[-\s]?up\b", low):
        m = re.search(r"top[-\s]?up[^.]{0,60}", t, re.IGNORECASE)
        add("policy_type", "top_up", m, "medium")

    # --- Min entry age (years) ---------------------------------------------
    m = re.search(
        r"min(?:imum)?[^.]{0,30}?entry age[^.]{0,30}?(\d{1,2})\s*(?:years|yrs)",
        t, re.IGNORECASE,
    ) or re.search(
        r"entry age[^.]{0,40}?(\d{1,2})\s*(?:years|yrs)[^.]{0,20}?to",
        t, re.IGNORECASE,
    )
    if m:
        age = int(m.group(1))
        if 0 <= age <= 35:
            add("min_entry_age_years", age, m, "medium")

    # --- Min child entry age (days) ---------------------------------------
    m = re.search(
        r"(\d{2,3})\s*days?[^.]{0,40}?(?:dependent (?:child|children)|child(?:ren)?)",
        t, re.IGNORECASE,
    ) or re.search(
        r"(?:dependent (?:child|children)|child(?:ren)?)[^.]{0,40}?(\d{2,3})\s*days",
        t, re.IGNORECASE,
    )
    if m:
        d = int(m.group(1))
        if 1 <= d <= 365:
            add("min_child_entry_age_days", d, m, "medium")

    # --- Lifelong / max renewal age ----------------------------------------
    if re.search(r"\blifelong renew", low) or re.search(r"\blife[-\s]?long renew", low):
        m = re.search(r"life[-\s]?long renew[^.]{0,80}", t, re.IGNORECASE)
        add("max_renewal_age_years", 999, m, "medium")
    else:
        m = re.search(
            r"(?:renewal|renewable)[^.]{0,40}?(?:up to|until|till)\s*(\d{2,3})\s*(?:years|yrs)",
            t, re.IGNORECASE,
        )
        if m:
            age = int(m.group(1))
            if 50 <= age <= 120:
                add("max_renewal_age_years", age, m, "medium")

    # --- Grace period (days) -----------------------------------------------
    m = re.search(
        r"grace period[^.]{0,40}?(\d{1,3})\s*(?:days?)",
        t, re.IGNORECASE,
    )
    if m:
        d = int(m.group(1))
        if 1 <= d <= 90:
            add("grace_period_days", d, m, "high")

    # --- Free-look period (days) -------------------------------------------
    m = re.search(
        r"free[-\s]?look[^.]{0,40}?(\d{1,3})\s*(?:days?)",
        t, re.IGNORECASE,
    ) or re.search(
        r"(\d{1,3})\s*days?\s*(?:as a )?free[-\s]?look",
        t, re.IGNORECASE,
    )
    if m:
        d = int(m.group(1))
        if 7 <= d <= 60:
            add("free_look_period_days", d, m, "high")

    # --- Geographic coverage ------------------------------------------------
    if re.search(r"\b(?:worldwide|global)\b", low):
        m = re.search(r"(?:worldwide|global)[^.]{0,80}", t, re.IGNORECASE)
        add("geographic_coverage", "worldwide", m, "medium")
    elif re.search(r"\bpan[-\s]?india\b", low):
        m = re.search(r"pan[-\s]?india[^.]{0,40}", t, re.IGNORECASE)
        add("geographic_coverage", "pan_india", m, "medium")
    elif re.search(r"\bonly in india\b|\bindian (resident|territory)\b", low):
        m = re.search(r"india[^.]{0,40}", t, re.IGNORECASE)
        add("geographic_coverage", "india", m, "low")

    # --- ICU capping --------------------------------------------------------
    m = re.search(
        r"icu(?:\s+charges?| rent)?[^.]{0,80}?(?:no cap|no limit|(\d{1,2})\s*%|2\s*x)",
        t, re.IGNORECASE,
    )
    if m:
        s = m.group(0).strip()
        if "no cap" in s.lower() or "no limit" in s.lower():
            add("icu_capping", "No ICU cap", m, "medium")
        elif m.group(1):
            add("icu_capping", f"{m.group(1)}% of sum insured", m, "medium")

    # --- Deductible (INR) — top-up / super top-up plans --------------------
    m = re.search(
        r"deductible[^.]{0,40}?(?:rs\.?|₹|inr)\s*(\d{1,3}(?:[,.]\d{2,3})*)",
        t, re.IGNORECASE,
    )
    if m:
        try:
            n = int(m.group(1).replace(",", "").replace(".", ""))
            if 25_000 <= n <= 10_000_000:
                add("deductible_amount_inr", n, m, "medium")
        except ValueError:
            pass

    # --- No-claim bonus cap (%) --------------------------------------------
    m = re.search(
        r"(?:no[\-\s]?claim bonus|cumulative bonus|ncb)[^.]{0,160}?"
        r"(?:up to|maximum|cap(?:ped)?)\s*(\d{1,3})\s*%",
        t, re.IGNORECASE,
    )
    if m:
        pct = int(m.group(1))
        if 25 <= pct <= 250:
            add("no_claim_bonus_cap_pct", pct, m, "medium")

    # --- Organ donor / critical illness / preventive health-check ----------
    if re.search(r"organ\s+donor", low):
        m = re.search(r"organ\s+donor[^.]{0,90}", t, re.IGNORECASE)
        add("organ_donor_expenses", {"covered": True}, m, "low")
    if re.search(r"critical illness", low):
        m = re.search(r"critical illness[^.]{0,100}", t, re.IGNORECASE)
        # If we find a number of CIs covered, capture it in limit_text.
        cnt = re.search(r"(\d{1,3})\s*critical illnesses?", t, re.IGNORECASE)
        item: dict[str, Any] = {"covered": True}
        if cnt:
            item["limit_text"] = f"Covers {cnt.group(1)} critical illnesses"
        add("critical_illness_cover", item, m, "low")
    if re.search(r"preventive (?:health )?check[\-\s]?up|annual (?:health )?check", low):
        m = re.search(
            r"preventive (?:health )?check[^.]{0,90}|annual (?:health )?check[^.]{0,90}",
            t, re.IGNORECASE,
        )
        add("preventive_health_checkup", {"covered": True}, m, "low")
    if re.search(r"domiciliary", low):
        m = re.search(r"domiciliary[^.]{0,90}", t, re.IGNORECASE)
        add("domiciliary_treatment", {"covered": True}, m, "low")
    if re.search(r"newborn|new[\-\s]?born", low):
        m = re.search(r"new[-\s]?born[^.]{0,90}", t, re.IGNORECASE)
        add("newborn_coverage", {"covered": True}, m, "low")

    # --- Premium payment modes (often listed as a comma-separated set) ----
    modes: list[str] = []
    if re.search(r"\bannual(?:ly)?\b", low):
        modes.append("annual")
    if re.search(r"\bhalf[\-\s]?yearly\b|\bsemi[\-\s]?annual\b", low):
        modes.append("half_yearly")
    if re.search(r"\bquarterly\b", low):
        modes.append("quarterly")
    if re.search(r"\bmonthly\b", low):
        modes.append("monthly")
    if len(modes) >= 1:
        m = re.search(r"premium[^.]{0,160}?(?:annual|monthly|quarterly|half[\-\s]?yearly)", t, re.IGNORECASE)
        add("premium_payment_modes", modes, m, "low")

    return out


def _derive_policy_name(full_text: str, fallback: str) -> str:
    """Best-effort human policy name from the document header."""
    for line in (full_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.search(r"(policy|plan|insurance|mediclaim|health)", s, re.IGNORECASE) \
                and 6 <= len(s) <= 90:
            return re.sub(r"\s+", " ", s)
    return fallback


# ---------------------------------------------------------------------------
# Persisted record  (curated-facts JSON)  +  PDF  +  chunk payload
# ---------------------------------------------------------------------------


def build_record(
    policy_id: str,
    policy_name: str,
    full_text: str,
    persisted_pdf_path: str,
) -> dict:
    """Build the curated-facts-shaped JSON the marketplace Pass-2 consumes.

    The returned dict is the EXACT shape `_load_curated_facts._flatten`
    expects: scalar identity keys + per-field `{value, source_*}` cells.
    """
    fields = extract_fields_from_text(full_text)
    rel_pdf = persisted_pdf_path
    for cell in fields.values():
        if isinstance(cell, dict) and "source_pdf_path" in cell:
            cell["source_pdf_path"] = rel_pdf

    # 2026-05-27 — detect the actual insurer from the PDF text and flip the
    # insurer_slug off the generic "user-upload" so the scorecard's Claim
    # Experience sub-score pulls the real reviews JSON
    # (40-data/reviews/<slug>.json). Fail-closed: no match ⇒ keep
    # UPLOAD_INSURER_SLUG.
    detected = detect_insurer_slug(full_text)
    slug = detected or UPLOAD_INSURER_SLUG

    record: dict[str, Any] = {
        "policy_id": policy_id,
        "policy_name": policy_name or _derive_policy_name(full_text, policy_id),
        "insurer_slug": slug,
        "_uploaded_doc": True,  # provenance flag (ignored by scorecard)
    }
    if detected:
        # Pretty name for any card renderer that reads from this record.
        record["insurer_name"] = detected_insurer_name(detected)
    record.update(fields)
    return record


def persist_upload(
    *,
    policy_id: str,
    policy_name: str,
    pdf_bytes: bytes,
    full_text: str,
    chunks: list[dict],
    session_id: str,
) -> dict:
    """Atomically persist the PDF + JSON record + chunk payload + meta.

    Returns the built record dict. Raises RuntimeError on any failure (the
    caller MUST surface this — a "successful" upload that didn't persist is
    a silent failure and is forbidden by the #52 spec).
    """
    try:
        ddir = _doc_dir(policy_id)
        ddir.mkdir(parents=True, exist_ok=True)

        pdf_path = ddir / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)

        record = build_record(
            policy_id, policy_name, full_text, persisted_pdf_path=str(pdf_path),
        )

        # Write to temp files then os.replace for crash-atomic visibility.
        rec_tmp = ddir / "record.json.tmp"
        rec_tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False))
        rec_tmp.replace(ddir / "record.json")

        chunk_payload = [
            {
                "chunk_idx": c["chunk_idx"],
                "text": c["text"],
                "page_start": c["page_start"],
                "page_end": c["page_end"],
            }
            for c in chunks
        ]
        ch_tmp = ddir / "chunks.json.tmp"
        ch_tmp.write_text(json.dumps(chunk_payload, ensure_ascii=False))
        ch_tmp.replace(ddir / "chunks.json")

        meta = {
            "policy_id": policy_id,
            "policy_name": record["policy_name"],
            # Use whatever build_record resolved — the detected insurer
            # slug if we matched one, else UPLOAD_INSURER_SLUG.
            "insurer_slug": record.get("insurer_slug", UPLOAD_INSURER_SLUG),
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
            "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "session_id": session_id,  # audit only — NEVER a visibility gate
            "n_chunks": len(chunk_payload),
        }
        meta_tmp = ddir / "meta.json.tmp"
        meta_tmp.write_text(json.dumps(meta, indent=2))
        meta_tmp.replace(ddir / "meta.json")

        _log.info(
            "persisted uploaded doc %s (%d fields, %d chunks) -> %s",
            policy_id, len([k for k in record if not k.startswith(("policy_", "insurer_", "_"))]),
            len(chunk_payload), ddir,
        )
        return record
    except Exception as e:  # noqa: BLE001 — convert to a loud typed failure
        raise RuntimeError(
            f"persist_upload failed for {policy_id}: {type(e).__name__}: {e}"
        ) from e


# ---------------------------------------------------------------------------
# Read side — used by _load_curated_facts (cards) + startup re-ingest (chunks)
# ---------------------------------------------------------------------------


def load_persisted_records() -> dict[str, dict]:
    """{policy_id: curated-facts-shaped record} for every persisted upload.

    Consumed by backend.main._load_curated_facts so each uploaded doc
    surfaces as a marketplace card via the EXISTING Pass-2 + build_scorecard
    path. A single corrupt record is skipped (logged) — it must not take
    down the whole catalogue.
    """
    out: dict[str, dict] = {}
    root = settings.UPLOADED_DOCS_DIR
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        rec_path = d / "record.json"
        if not rec_path.exists():
            continue
        try:
            rec = json.loads(rec_path.read_text())
            pid = rec.get("policy_id") or d.name
            out[pid] = rec
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "skipping corrupt uploaded record %s: %s: %s",
                rec_path, type(e).__name__, e,
            )
            continue
    return out


def iter_persisted_chunks():
    """Yield (policy_id, policy_name, [chunk dicts]) for every persisted doc.

    Used by the startup re-ingest to rebuild the uploaded docs' vectors in
    the working Chroma `policies` collection after a Space restart wiped the
    ephemeral rag/vectors snapshot.
    """
    root = settings.UPLOADED_DOCS_DIR
    if not root.exists():
        return
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        ch_path = d / "chunks.json"
        meta_path = d / "meta.json"
        if not (ch_path.exists() and meta_path.exists()):
            continue
        try:
            meta = json.loads(meta_path.read_text())
            chunks = json.loads(ch_path.read_text())
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "skipping unreadable persisted chunks %s: %s: %s",
                ch_path, type(e).__name__, e,
            )
            continue
        yield (
            meta.get("policy_id") or d.name,
            meta.get("policy_name") or d.name,
            chunks,
        )


async def reingest_persisted_into_policies() -> dict:
    """Re-embed every persisted uploaded doc's chunks into the working
    Chroma `policies` collection (idempotent: deletes the doc's prior
    chunks first, keyed by policy_id).

    Globally visible by design (#52: uploaded doc is added to THE
    marketplace). Returns a small summary dict. Raises only if Chroma /
    embedder are completely unavailable; a single bad doc is logged and
    skipped so one corrupt upload can't block boot.
    """
    from rag.ingest import get_chroma_collection
    from backend.providers.local_embeddings import LocalEmbeddings

    docs = list(iter_persisted_chunks())
    summary = {"docs": 0, "chunks": 0, "skipped": 0}
    if not docs:
        return summary

    collection = get_chroma_collection()
    embedder = LocalEmbeddings()

    for policy_id, policy_name, chunks in docs:
        if not chunks:
            summary["skipped"] += 1
            continue
        try:
            texts = [c["text"] for c in chunks]
            vectors = await embedder.embed(texts, input_type="document")
            ids = [f"{policy_id}::chunk{c['chunk_idx']}" for c in chunks]
            metadatas = [
                {
                    "policy_id": policy_id,
                    "insurer_slug": UPLOAD_INSURER_SLUG,
                    "policy_name": policy_name,
                    "doc_type": UPLOAD_DOC_TYPE,
                    "source_url": "",
                    "page_start": c["page_start"],
                    "page_end": c["page_end"],
                    "chunk_idx": c["chunk_idx"],
                    # NOTE: no session_id — these are GLOBAL marketplace
                    # chunks by design, not session-private quarantine.
                }
                for c in chunks
            ]
            try:
                collection.delete(where={"policy_id": policy_id})
            except Exception:  # noqa: BLE001 — first-ever ingest has nothing to delete
                pass
            collection.add(
                ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas,
            )
            summary["docs"] += 1
            summary["chunks"] += len(chunks)
            _log.info(
                "re-ingested uploaded doc %s (%d chunks) into policies",
                policy_id, len(chunks),
            )
        except Exception as e:  # noqa: BLE001 — one bad doc must not block boot
            summary["skipped"] += 1
            _log.warning(
                "startup re-ingest skipped %s: %s: %s",
                policy_id, type(e).__name__, e,
            )
    return summary


# ---------------------------------------------------------------------------
# LLM-assisted extraction for uploaded PDFs (2026-05-27, ADR-044).
#
# Parity with the catalogued 148: same LLM (get_brain_llm), same EXTRACT
# prompt, same HealthPolicy schema, same downstream merge into the
# marketplace catalogue. Pre-this-change the upload path only ran the
# deterministic-heuristic `extract_fields_from_text` over the PDF, which
# is why uploaded cards stalled at 13-48% data_completeness vs the
# 74% median for catalogued. After this change uploaded cards land in
# the same completeness band by construction.
#
# Runs as a background asyncio task fired from the upload endpoint.
# The upload's HTTP response returns immediately (sub-second) with the
# heuristic record; the LLM pass (~30-60s) lands in the background.
# A new GET /api/upload/extraction-status/{policy_id} endpoint exposes
# in-flight state to the frontend so the chat flow can wait for
# extraction → THEN render the card with full data (no partial render).
# ---------------------------------------------------------------------------


# In-memory status dict — one entry per uploaded policy_id.
# Shape:
#   {
#     "status": "pending" | "running" | "complete" | "failed",
#     "policy_id": str,
#     "policy_name": str,
#     "insurer_slug": str,
#     "started_at": ISO-8601 UTC,
#     "completed_at": ISO-8601 UTC | None,
#     "completeness_pct": float | None,  # populated on complete
#     "overall_grade": str | None,
#     "error": str | None,
#   }
# Survives only the live process — fine for the UX use case (the
# frontend polls within ~120s of upload).
_UPLOAD_EXTRACTION_STATUS: dict[str, dict] = {}
_UPLOAD_EXTRACTION_LOCK = asyncio.Lock()


async def _set_extraction_status(policy_id: str, **fields) -> None:
    async with _UPLOAD_EXTRACTION_LOCK:
        cur = _UPLOAD_EXTRACTION_STATUS.get(policy_id, {})
        cur.update(fields)
        cur["policy_id"] = policy_id
        _UPLOAD_EXTRACTION_STATUS[policy_id] = cur


def get_extraction_status(policy_id: str) -> Optional[dict]:
    """Public read accessor used by the /api/upload/extraction-status endpoint."""
    return _UPLOAD_EXTRACTION_STATUS.get(policy_id)


# ---------------------------------------------------------------------------
# Tier-2 optimisations (ADR-044, 2026-05-27):
#   - Content-hash cache:  same sha256(pdf_bytes) → reuse prior extraction
#                          instead of re-running the LLM.
#   - Multi-pass per-section extraction:  for big PDFs (≥25K chars) the
#                          single-pass extractor truncates JSON output.
#                          Split the schema into 7 logical sections, run
#                          each as its own smaller Gemini call IN PARALLEL
#                          via asyncio.gather(), merge into one
#                          HealthPolicy. Each section call carries ~15%
#                          of the schema → fits comfortably in Gemini's
#                          output budget. Failure isolation: 6/7 sections
#                          landing produces a partial extraction far
#                          better than the heuristic floor.
# ---------------------------------------------------------------------------


# Schema partition for multi-pass extraction. Each entry = (section_name,
# [field names from HealthPolicy]). Field membership mirrors the schema's
# own section comments (`# === 1. Identity`, `# === 4. Waiting periods`,
# etc.) so reasoning about which call missed what is mechanical.
#
# Total fields covered: 39 (= all non-derived HealthPolicy fields). The
# downstream `HealthPolicy(**merged)` happily accepts a dict missing any
# Optional field; the four required identity fields (policy_id,
# insurer_name, insurer_slug, policy_name) are force-filled by the caller
# from already-resolved upload state, NOT relied on from the LLM.
_EXTRACT_SECTIONS: list[tuple[str, list[str]]] = [
    ("identity", [
        "policy_id", "insurer_name", "insurer_slug", "policy_name",
        "policy_type", "uin_code",
    ]),
    ("eligibility", [
        "min_entry_age_years", "max_entry_age_years",
        "max_renewal_age_years", "min_child_entry_age_days",
        "family_composition_allowed", "residency_requirement",
    ]),
    ("financial", [
        "sum_insured_options_inr", "premium_payment_modes",
        "premium_range_indicative_inr", "premium_payment_term_years",
        "grace_period_days", "free_look_period_days",
        "no_claim_bonus_pct", "no_claim_bonus_cap_pct",
        "deductible_amount_inr", "copayment_pct",
        "copayment_trigger_notes",
    ]),
    ("waiting_periods", [
        "initial_waiting_period_days",
        "pre_existing_disease_waiting_months",
        "specific_disease_waiting_months",
        "specific_diseases_listed",
        "maternity_waiting_months",
        "sub_limits_waiting_notes",
    ]),
    ("coverage", [
        "inpatient_hospitalization", "pre_hospitalization_days",
        "post_hospitalization_days", "day_care_treatments",
        "domiciliary_treatment", "ayush_coverage",
        "maternity_coverage", "newborn_coverage",
        "organ_donor_expenses", "ambulance_cover",
        "critical_illness_cover", "restoration_benefit",
        "preventive_health_checkup",
    ]),
    ("limits", [
        "room_rent_capping", "icu_capping",
        "disease_wise_sub_limits",
    ]),
    ("network_claims", [
        "geographic_coverage", "worldwide_emergency_cover",
        "network_hospital_count", "cashless_treatment_supported",
        "permanent_exclusions", "temporary_exclusions",
        "claim_settlement_ratio_pct",
    ]),
]


def _schema_excerpt_for_fields(field_names: list[str]) -> str:
    """Like rag.extract.schema_excerpt() but filtered to just these fields.
    Keeps the LLM's per-section task tightly scoped + saves input tokens."""
    from rag.schema import HealthPolicy as _HP
    fields = _HP.model_fields
    lines = []
    for name in field_names:
        info = fields.get(name)
        if info is None:
            continue
        ann_str = (
            str(info.annotation)
            .replace("typing.", "")
            .replace("Optional[", "?")
            .replace("]", "")
        )
        lines.append(f"  {name}: {ann_str}")
    return "{\n" + "\n".join(lines) + "\n}"


async def _multipass_extract_with_gemini(
    *,
    text: str,
    policy_id: str,
    insurer_slug: str,
    insurer_name: str,
    policy_name: str,
    llm_gemini,
    set_status,
    doc_dir: Path,
) -> Optional[dict]:
    """Multi-pass per-section LLM extraction.

    Runs 7 Gemini calls in parallel (one per `_EXTRACT_SECTIONS` entry),
    each carrying only ~15% of the HealthPolicy schema. Merges all
    successful section results into a single dict suitable for
    `HealthPolicy(**out)`. Identity fields force-filled from the
    already-resolved upload state.

    Returns the merged dict on partial-or-full success (any section
    landing counts as success — heuristic floor still wins where every
    section fails). Returns None ONLY when every single section call
    raises / produces no parseable JSON, in which case the caller falls
    through to the legacy single-pass + NIM-fallback path.
    """
    from rag.extract import (
        EXTRACT_SYSTEM,
        build_extract_prompt,
        json_from_llm_text,
    )
    from backend.providers.base import ChatMessage

    async def _one_section(name: str, fields: list[str]) -> tuple[str, Optional[dict]]:
        """Run one section's Gemini call. Returns (name, dict_or_None)."""
        excerpt = _schema_excerpt_for_fields(fields)
        prompt = build_extract_prompt(text, excerpt, policy_id)
        # Soften the per-section prompt's required-fields stance: only the
        # IDENTITY section is shown the four required scalars, every other
        # section may legitimately return them as null without that being
        # a parse failure (the caller force-fills them anyway).
        section_hint = (
            f"\n\nIMPORTANT: For THIS call, only extract fields from the "
            f"'{name}' section above ({len(fields)} fields). Return JSON "
            f"containing ONLY these field names. Omit fields you can't infer."
        )
        messages = [
            ChatMessage(role="system", content=EXTRACT_SYSTEM),
            ChatMessage(role="user", content=prompt + section_hint),
        ]
        try:
            res = await asyncio.wait_for(
                llm_gemini.chat(
                    messages=messages,
                    temperature=0.0,
                    max_tokens=4096,  # ~half the single-pass budget; fits one section comfortably
                ),
                timeout=90.0,
            )
            raw = res.text or ""
            # Persist for ops visibility (one file per section).
            try:
                (doc_dir / f"llm_raw_multipass_{name}.txt").write_text(raw)
            except Exception:
                pass
            try:
                data = json_from_llm_text(raw)
            except Exception as parse_err:
                _log.warning(
                    "[upload-extract] multipass section '%s' for %s parse failed: %s",
                    name, policy_id, str(parse_err)[:160],
                )
                return name, None
            # Only keep keys this section was asked to fill — drops any
            # cross-section spill the model might emit.
            kept = {k: v for k, v in (data or {}).items() if k in set(fields)}
            _log.info(
                "[upload-extract] multipass section '%s' landed %d/%d fields "
                "(raw %d chars) for %s",
                name, len(kept), len(fields), len(raw), policy_id,
            )
            return name, kept
        except Exception as e:  # noqa: BLE001 — one section failing is fine
            _log.warning(
                "[upload-extract] multipass section '%s' for %s FAILED: %s: %s",
                name, policy_id, type(e).__name__, str(e)[:160],
            )
            return name, None

    # Surface that multi-pass started, before the first response, so an
    # operator polling the status endpoint sees the path was taken.
    await set_status(
        policy_id,
        llm_used="gemini-2.5-flash-multipass(starting)",
        llm_response_chars=0,
    )

    # Fire all 7 sections in parallel.
    results = await asyncio.gather(
        *[_one_section(name, fields) for name, fields in _EXTRACT_SECTIONS],
        return_exceptions=False,
    )

    # Merge — LATER sections do NOT override earlier ones (no section
    # claims the same field as another by construction). Drop None /
    # empty.
    merged: dict = {}
    sections_landed: list[str] = []
    for name, partial in results:
        if not partial:
            continue
        sections_landed.append(name)
        for k, v in partial.items():
            if v in (None, "", [], {}):
                continue
            merged.setdefault(k, v)

    if not merged:
        _log.warning(
            "[upload-extract] multipass: 0/7 sections landed for %s — "
            "falling through to single-pass", policy_id,
        )
        return None

    # Force-fill identity fields the caller has already resolved.
    merged.setdefault("policy_id", policy_id)
    merged.setdefault("insurer_slug", insurer_slug)
    merged.setdefault("insurer_name", insurer_name)
    merged.setdefault("policy_name", policy_name)

    _log.info(
        "[upload-extract] multipass: merged %d/7 sections (%s) for %s — "
        "%d total fields populated",
        len(sections_landed), ",".join(sections_landed),
        policy_id, len(merged),
    )
    return merged


# ---------------------------------------------------------------------------


def _find_cached_extraction(content_sha: str, current_policy_id: str) -> Optional[Path]:
    """Look for a prior successful extraction of the same content (sha256
    match) under a DIFFERENT policy_id. Returns the path to the existing
    rag/extracted/<other_pid>.json if found, else None.

    This handles the legitimate "user uploads the same PDF twice (maybe
    in two browser tabs / two sessions)" case — the second upload should
    get the identical extracted JSON without paying the LLM cost again.
    Fail-closed: any I/O error → None (caller runs a fresh extraction).
    """
    if not content_sha:
        return None
    try:
        from backend.config import settings as _settings
        for meta_path in uploaded_docs_dir().glob("*/meta.json"):
            try:
                meta = json.loads(meta_path.read_text())
            except Exception:
                continue
            if meta.get("sha256") != content_sha:
                continue
            other_pid = meta.get("policy_id") or meta_path.parent.name
            if other_pid == current_policy_id:
                continue  # ignore our own meta if it's already written
            cached = _settings.EXTRACTED_DIR / f"{other_pid}.json"
            if cached.exists():
                return cached
    except Exception:  # noqa: BLE001
        pass
    return None


async def extract_one_for_upload(
    policy_id: str,
    pdf_path: Path,
    policy_name: str,
    insurer_slug: str,
    insurer_name: str,
) -> bool:
    """Run the same LLM extractor used for the catalogued 148 against an
    uploaded PDF. On success, writes `rag/extracted/<policy_id>.json` and
    invalidates the marketplace grade cache so the next /api/policies/all
    + /api/policies/{id}/scorecard call returns the LLM-graded card.

    Status is mirrored to `_UPLOAD_EXTRACTION_STATUS[policy_id]` at every
    phase change so the frontend's poll loop sees progress in real time.

    Hash-cache short-circuit (Tier-2 ADR-044): if a prior upload with the
    same `sha256(pdf_bytes)` already has a successful `rag/extracted/
    <other_pid>.json`, that file is COPIED to this policy's path without
    re-running the LLM. Same content, same extraction — guaranteed.

    Returns True iff a HealthPolicy was successfully extracted and written.
    Swallows all errors (returns False) — a failed LLM pass must NEVER
    affect the upload's HTTP response, which has already returned.
    """
    _now = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    await _set_extraction_status(
        policy_id,
        status="running",
        policy_name=policy_name,
        insurer_slug=insurer_slug,
        started_at=_now(),
        completed_at=None,
        completeness_pct=None,
        overall_grade=None,
        error=None,
    )

    # ─── Tier-2 hash-cache short-circuit (ADR-044) ─────────────────
    # If we've previously extracted a PDF with the same content hash,
    # reuse that extraction. Same content → same fields by construction.
    # Saves 30-60s + a Gemini call.
    try:
        meta_path = _doc_dir(policy_id) / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            content_sha = meta.get("sha256") or ""
            if content_sha:
                cached_path = _find_cached_extraction(content_sha, policy_id)
                if cached_path is not None:
                    _log.info(
                        "[upload-extract] hash-cache HIT for %s "
                        "(reusing %s) — skipping LLM",
                        policy_id, cached_path.name,
                    )
                    # Copy the prior extraction to this policy's path so
                    # /api/policies/{id}/scorecard finds it under the
                    # right id.
                    from backend.config import settings as _settings_a
                    _settings_a.EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
                    dest = _settings_a.EXTRACTED_DIR / f"{policy_id}.json"
                    dest.write_text(cached_path.read_text())
                    # Bust the #40 grade cache so /api/policies/all
                    # picks up the new card BEFORE we resolve the
                    # catalogue scorecard for status reporting.
                    try:
                        import backend.main as _bm
                        with _bm._MG_LOCK:
                            _bm._MG_CACHE["sig"] = None
                            _bm._MG_CACHE["index"] = None
                    except Exception:
                        pass
                    # Mark status complete with the EXACT completeness +
                    # grade the chat card will render. Mirror the live
                    # scorecard endpoint's resolution order (catalogue
                    # primary → bare-policy fallback with insurer_reviews).
                    # Earlier draft of this branch called
                    # build_scorecard(_doc, profile=None) without reviews
                    # and read `.overall_grade` instead of `.grade`, so
                    # cache-hit uploads always reported comp≈17.4 + grade
                    # None even when the actual card was 47.8% / grade C
                    # (the 2026-05-27 multi-PDF audit caught this).
                    _final_comp = None
                    _final_grade = None
                    try:
                        from backend.scorecard import build_scorecard as _bs
                        # PRIMARY — catalogue scorecard (matches /api/policies/{id}/scorecard).
                        _sc = _bm._catalogue_scorecard(policy_id, None)
                        if _sc is None:
                            # FALLBACK — bare-policy build_scorecard with reviews.
                            _doc = json.loads(dest.read_text())
                            _ir = None
                            if insurer_slug:
                                from backend.config import settings as _settings_b
                                _rp = _settings_b.DATA_DIR / "reviews" / f"{insurer_slug}.json"
                                if _rp.exists():
                                    try:
                                        _ir = json.loads(_rp.read_text())
                                    except Exception:
                                        _ir = None
                            _sc = _bs(_doc, insurer_reviews=_ir, profile=None)
                        if _sc is not None:
                            _final_comp = float(_sc.data_completeness_pct)
                            _final_grade = _sc.grade  # NOT overall_grade
                    except Exception as _sc_err:  # noqa: BLE001
                        _log.warning(
                            "[upload-extract] cache-hit status resolve failed for %s: %s",
                            policy_id, _sc_err,
                        )
                    await _set_extraction_status(
                        policy_id, status="complete",
                        completed_at=_now(),
                        completeness_pct=_final_comp,
                        overall_grade=_final_grade,
                        # Provenance — operator sees WHY no LLM ran: the
                        # SHA256 of pdf_bytes matched a prior extraction
                        # so we reused it. Distinct from the gemini-2.5-
                        # flash#N / nim-fallback labels.
                        llm_used="hash-cache",
                        llm_response_chars=len((dest.read_text() or "")),
                    )
                    return True
    except Exception as e:  # noqa: BLE001 — cache miss is fine, run fresh
        _log.debug("[upload-extract] hash-cache lookup failed: %s", e)

    try:
        # Lazy imports — these touch the LLM client + DuckDB; we don't
        # want to pay that cost at module import time.
        from rag.extract import (
            EXTRACT_SYSTEM,
            build_extract_prompt,
            schema_excerpt,
            read_full_text,
            json_from_llm_text,
            upsert_policy,
        )
        from rag.schema import HealthPolicy
        from backend.providers.base import ChatMessage

        # 2026-05-27 — switched from NIM (get_brain_llm) to Gemini
        # 2.5-flash with native JSON-mode (response_mime_type=
        # application/json). Gemini is the steady-state primary
        # chat brain (ADR-040) and gives schema-locked structured
        # output, which is exactly what the EXTRACT prompt needs.
        # NIM stays as the fallback for the (a) missing-GOOGLE_API_KEY
        # case or (b) Gemini 5xx/quota path. Same prompt, same schema,
        # same downstream HealthPolicy parse + writes — only the
        # transport changes.
        from backend.providers.google_gemini_llm import GoogleGeminiLLM
        from backend.providers.nvidia_nim_llm import get_brain_llm

        _log.info(
            "[upload-extract] starting LLM extraction for %s (insurer=%s)",
            policy_id, insurer_slug,
        )

        # Read text from the persisted PDF (same as extract_one).
        try:
            text = read_full_text(pdf_path)
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "[upload-extract] read_full_text failed %s: %s: %s",
                policy_id, type(e).__name__, e,
            )
            await _set_extraction_status(
                policy_id, status="failed",
                completed_at=_now(),
                error=f"read_full_text: {type(e).__name__}: {str(e)[:160]}",
            )
            return False

        prompt = build_extract_prompt(text, schema_excerpt(), policy_id)
        messages = [
            ChatMessage(role="system", content=EXTRACT_SYSTEM),
            ChatMessage(role="user", content=prompt),
        ]

        # ─── Multi-pass per-section extraction (ADR-044 §D6, 2026-05-27) ───
        # For large PDFs (≥25K chars), the single-pass extraction reliably
        # truncates because Gemini 2.5-flash can't fit ~40 schema fields
        # with verbatim quotes into one parseable JSON. Split the schema
        # into 7 logical sections and run each as its own smaller Gemini
        # call in PARALLEL via asyncio.gather. Each call carries ~15% of
        # the schema → fits comfortably in Gemini's output budget even
        # for 8 MB PDFs. Successful sections merge into a partial
        # HealthPolicy; missing sections fall back to whatever the
        # heuristic baseline supplies. Significantly more reliable than
        # one giant call on large/dense PDFs (Test Policy.pdf 8 MB was
        # the trigger).
        _MULTIPASS_THRESHOLD_CHARS = 25_000
        if len(text) >= _MULTIPASS_THRESHOLD_CHARS:
            try:
                _mp_data = await _multipass_extract_with_gemini(
                    text=text,
                    policy_id=policy_id,
                    insurer_slug=insurer_slug,
                    insurer_name=insurer_name,
                    policy_name=policy_name,
                    llm_gemini=GoogleGeminiLLM(timeout=120.0),
                    set_status=_set_extraction_status,
                    doc_dir=_doc_dir(policy_id),
                )
                if _mp_data:
                    try:
                        policy = HealthPolicy(**_mp_data)
                        raw = json.dumps(_mp_data, ensure_ascii=False)
                        await _set_extraction_status(
                            policy_id,
                            llm_used="gemini-2.5-flash-multipass",
                            llm_response_chars=len(raw),
                        )
                        _log.info(
                            "[upload-extract] multi-pass produced valid HealthPolicy "
                            "for %s (%d fields in payload)", policy_id, len(_mp_data),
                        )
                    except Exception as _mp_parse_err:  # noqa: BLE001
                        _log.warning(
                            "[upload-extract] multi-pass parse failed for %s — "
                            "falling through to single-pass: %s",
                            policy_id, _mp_parse_err,
                        )
                        policy = None
            except Exception as _mp_err:  # noqa: BLE001 — fall through to single-pass
                _log.warning(
                    "[upload-extract] multi-pass extraction errored for %s "
                    "(falling through to single-pass): %s: %s",
                    policy_id, type(_mp_err).__name__, str(_mp_err)[:200],
                )
                policy = None
        else:
            policy = None  # single-pass path below will fill

        # Tier-1 Gemini-stability hardening (ADR-044, 2026-05-27):
        #   1. Bumped retry count from 1 → 3 on the Gemini primary path
        #      with jittered exp backoffs (2s/4s/8s ±25%). Mirrors the
        #      _TRANSIENT_RETRY_BACKOFFS_STICKY pattern from single_brain
        #      (ADR-042) that proved effective for Gemini's 429/5xx tail.
        #   2. NIM is the FINAL fallback after Gemini exhausts retries.
        #   3. Raw LLM responses are captured to disk on each failed
        #      attempt — UPLOADED_DOCS_DIR/<pid>/llm_raw_<n>.txt — so the
        #      operator can SEE why a Gemini call failed (was it a 429,
        #      truncated mid-emission, returned with markdown fences,
        #      etc.). Previously the failure was a black box.
        import random as _random
        _GEMINI_BACKOFFS = (2.0, 4.0, 8.0)
        _GEMINI_JITTER = 0.25
        def _jit(b: float) -> float:
            return b * _random.uniform(1 - _GEMINI_JITTER, 1 + _GEMINI_JITTER)

        llm_gemini = GoogleGeminiLLM(timeout=180.0)
        llm_nim = get_brain_llm()
        raw = ""
        policy: Optional[HealthPolicy] = None
        attempts: list[tuple[object, str]] = [
            (llm_gemini, "gemini-2.5-flash#1"),
            (llm_gemini, "gemini-2.5-flash#2"),
            (llm_gemini, "gemini-2.5-flash#3"),
            (llm_nim, "nim-fallback"),
        ]
        for attempt, (llm, label) in enumerate(attempts):
            try:
                # Backoff BEFORE every attempt after the first Gemini try.
                # First attempt: no wait. Subsequent Gemini attempts: jittered
                # exp. NIM fallback: no extra backoff (Gemini already gave up).
                if 1 <= attempt <= len(_GEMINI_BACKOFFS):
                    _bo = _jit(_GEMINI_BACKOFFS[attempt - 1])
                    _log.info(
                        "[upload-extract] sleeping %.1fs before %s retry",
                        _bo, label,
                    )
                    await asyncio.sleep(_bo)
                attempt_timeout = 180 if label.startswith("gemini") else 120
                chat_kwargs = {
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": 8192,
                }
                res = await asyncio.wait_for(
                    llm.chat(**chat_kwargs),
                    timeout=attempt_timeout,
                )
                raw = res.text
                _log.info(
                    "[upload-extract] %s returned %d chars; parsing JSON…",
                    label, len(raw or ""),
                )
                # Record provider + response size on the status dict so the
                # operator can prove WHICH LLM landed the extraction without
                # needing HF Space stdout access.
                await _set_extraction_status(
                    policy_id,
                    llm_used=label,
                    llm_response_chars=len(raw or ""),
                )
                # Persist raw response for ops visibility (always — both
                # successful + failed parses get a copy).
                try:
                    (_doc_dir(policy_id) / f"llm_raw_{attempt + 1}_{label.replace('#','-')}.txt").write_text(raw or "")
                except Exception:
                    pass
                data = json_from_llm_text(raw)
                # Force-fill identity fields (REQUIRED by the schema, the
                # LLM frequently emits null for these because they're not
                # in the truncated text). Use what the upload path
                # already resolved.
                if not data.get("policy_id"):
                    data["policy_id"] = policy_id
                if not data.get("insurer_slug"):
                    data["insurer_slug"] = insurer_slug
                if not data.get("insurer_name"):
                    data["insurer_name"] = insurer_name
                if not data.get("policy_name"):
                    data["policy_name"] = policy_name
                policy = HealthPolicy(**data)
                break
            except Exception as e:  # noqa: BLE001
                _log.warning(
                    "[upload-extract] attempt %d (%s) failed for %s: %s: %s",
                    attempt + 1, label, policy_id, type(e).__name__, str(e)[:200],
                )
                continue

        if policy is None:
            _log.warning(
                "[upload-extract] no policy extracted for %s after retries; "
                "card stays on heuristic record", policy_id,
            )
            await _set_extraction_status(
                policy_id, status="failed",
                completed_at=_now(),
                error="LLM returned no valid HealthPolicy after primary + fallback retries",
            )
            return False

        # Write rag/extracted/<policy_id>.json — same shape as catalogued.
        from backend.config import settings as _settings
        _settings.EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)
        out_json = _settings.EXTRACTED_DIR / f"{policy_id}.json"
        out_json.write_text(policy.model_dump_json(indent=2))

        # ALSO merge the LLM output INTO the persisted record.json so the
        # marketplace catalogue's _load_curated_facts() pass sees the
        # combined heuristic-baseline + LLM-extracted fields (rather than
        # just the LLM payload, which may be sparser than the heuristic
        # for non-standard PDFs). Heuristic stays as the fallback; LLM
        # values override where present + non-empty. This is the same
        # "curated overlay" model the catalogued 148 use via
        # 40-data/policy_facts/.
        try:
            doc_dir = _doc_dir(policy_id)
            rec_path = doc_dir / "record.json"
            if rec_path.exists():
                existing = json.loads(rec_path.read_text())
                llm_dump = policy.model_dump()
                # Carry over LLM scalar values + verbatim source_quotes
                # into the heuristic record. Skip null/empty/empty-list
                # so heuristic stays intact where the LLM was silent.
                for k, v in llm_dump.items():
                    if k in ("policy_id", "policy_name", "insurer_slug", "insurer_name"):
                        continue
                    if v in (None, "", [], {}):
                        continue
                    # Already in cell-shape ({value, source_quote, ...}) on
                    # the heuristic side; lift the LLM scalar into the
                    # value field, preserving the heuristic's source_quote
                    # / source_pdf_path if the LLM didn't supply one.
                    if isinstance(existing.get(k), dict) and "value" in existing[k]:
                        existing[k] = {**existing[k], "value": v}
                    else:
                        existing[k] = v
                # Also carry over the LLM's confidence + insurer_name if
                # detected, both for downstream provenance.
                if getattr(policy, "extraction_confidence_pct", None) is not None:
                    existing["_llm_extraction_confidence_pct"] = policy.extraction_confidence_pct
                tmp = rec_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False, default=str))
                tmp.replace(rec_path)
                _log.info(
                    "[upload-extract] merged LLM extraction into record.json for %s",
                    policy_id,
                )
        except Exception as _merge_err:  # noqa: BLE001
            _log.warning(
                "[upload-extract] record.json merge failed for %s: %s",
                policy_id, _merge_err,
            )

        # Persist into DuckDB so admin / re-render paths see the new card.
        try:
            upsert_policy(
                policy,
                source_pdf_path=str(pdf_path),
                source_pdf_url="",
            )
        except Exception as e:  # noqa: BLE001 — DB write is best-effort
            _log.warning(
                "[upload-extract] upsert_policy failed for %s: %s: %s",
                policy_id, type(e).__name__, e,
            )

        # Invalidate the #40 marketplace grade cache so the next
        # /api/policies/all / scorecard call returns the LLM-graded card.
        try:
            import backend.main as _bm
            with _bm._MG_LOCK:
                _bm._MG_CACHE["sig"] = None
                _bm._MG_CACHE["index"] = None
        except Exception as e:  # noqa: BLE001 — cache miss is fine
            _log.debug(
                "[upload-extract] could not invalidate _MG_CACHE for %s: %s",
                policy_id, e,
            )

        _log.info(
            "[upload-extract] OK %s (extraction_confidence_pct=%s)",
            policy_id, getattr(policy, "extraction_confidence_pct", "n/a"),
        )

        # Resolve the freshly-graded card so the status can report the
        # actual completeness + grade the CHAT CARD will show. Mirror
        # the /api/policies/{id}/scorecard endpoint's resolution order
        # byte-for-byte: PRIMARY path is the marketplace catalogue
        # scorecard (_catalogue_scorecard) which folds in the heuristic
        # record.json + curated overlay + insurer reviews + product
        # dedup — that's what produces the 52.2%/grade-C the user sees
        # on the inline card. The fallback path (build_scorecard on the
        # bare extracted JSON) reads 17.4% because it doesn't see the
        # heuristic merge or reviews-driven sub-scores.
        _final_completeness = None
        _final_grade = None
        try:
            # PRIMARY — same call /api/policies/{id}/scorecard makes first.
            # Bust the marketplace grade cache first (we invalidated _MG_CACHE
            # above, but _catalogue_indices builds off the latest record.json
            # + extracted JSON on each call so this becomes a fresh build).
            import backend.main as _bm2
            _sc = _bm2._catalogue_scorecard(policy_id, None)
            if _sc is None:
                # FALLBACK — same path the endpoint falls through to for
                # non-catalogued ids. For user uploads this is just defensive;
                # the upload IS a marketplace card by design (record.json is
                # persisted under UPLOADED_DOCS_DIR/<pid>/).
                from backend.scorecard import build_scorecard as _bs
                _doc_for_sc = json.loads(out_json.read_text())
                _ir = None
                if insurer_slug:
                    _rp = _settings.DATA_DIR / "reviews" / f"{insurer_slug}.json"
                    if _rp.exists():
                        try:
                            _ir = json.loads(_rp.read_text())
                        except Exception:
                            _ir = None
                _sc = _bs(_doc_for_sc, insurer_reviews=_ir, profile=None)
            if _sc is not None:
                _final_completeness = float(_sc.data_completeness_pct)
                # 2026-05-27 — Scorecard dataclass attr is `.grade`, NOT
                # `.overall_grade` (only the ScorecardResponse wire field
                # is renamed). Earlier draft of this resolver used the
                # wire name and silently logged grade=None on every
                # upload — the parity audit caught it.
                _final_grade = _sc.grade
        except Exception as _sc_err:  # noqa: BLE001
            _log.warning(
                "[upload-extract] status-card resolve failed for %s: %s: %s",
                policy_id, type(_sc_err).__name__, str(_sc_err)[:160],
            )

        await _set_extraction_status(
            policy_id, status="complete",
            completed_at=_now(),
            completeness_pct=_final_completeness,
            overall_grade=_final_grade,
        )
        return True
    except Exception as e:  # noqa: BLE001 — top-level catch-all
        try:
            await _set_extraction_status(
                policy_id, status="failed",
                completed_at=_now(),
                error=f"{type(e).__name__}: {str(e)[:200]}",
            )
        except Exception:
            pass
        # fall through to the existing _log.warning that follows
        _log.warning(
            "[upload-extract] unexpected failure for %s: %s: %s",
            policy_id, type(e).__name__, str(e)[:400],
        )
        return False


async def backfill_extractions(*, force: bool = False) -> dict:
    """Run LLM-assisted extraction for every persisted upload that doesn't
    yet have a corresponding `rag/extracted/<policy_id>.json` (or force=True
    to re-extract every upload). Fires sequentially so we don't fan-out the
    LLM chain. Returns a {processed, skipped, failed} summary.

    Designed to be called once at server startup (to upgrade old uploads
    that were persisted before the LLM-extraction pipeline was wired) AND
    as the backing for POST /api/admin/upload/reextract.
    """
    from backend.config import settings as _settings
    summary: dict = {"processed": 0, "skipped": 0, "failed": 0, "policies": []}
    records = load_persisted_records()
    for policy_id, record in records.items():
        try:
            out_json = _settings.EXTRACTED_DIR / f"{policy_id}.json"
            if out_json.exists() and not force:
                summary["skipped"] += 1
                continue
            pdf_path = _doc_dir(policy_id) / "source.pdf"
            if not pdf_path.exists():
                _log.warning(
                    "[backfill] missing source.pdf for %s — skipping", policy_id,
                )
                summary["skipped"] += 1
                continue
            policy_name = record.get("policy_name") or policy_id
            insurer_slug = record.get("insurer_slug") or UPLOAD_INSURER_SLUG
            insurer_name = record.get("insurer_name") or detected_insurer_name(insurer_slug)
            ok = await extract_one_for_upload(
                policy_id=policy_id,
                pdf_path=pdf_path,
                policy_name=policy_name,
                insurer_slug=insurer_slug,
                insurer_name=insurer_name,
            )
            if ok:
                summary["processed"] += 1
                summary["policies"].append(policy_id)
            else:
                summary["failed"] += 1
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "[backfill] failed for %s: %s: %s",
                policy_id, type(e).__name__, str(e)[:200],
            )
            summary["failed"] += 1
    _log.info(
        "[backfill] done: processed=%d skipped=%d failed=%d",
        summary["processed"], summary["skipped"], summary["failed"],
    )
    return summary
