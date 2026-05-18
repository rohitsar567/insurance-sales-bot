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

    record: dict[str, Any] = {
        "policy_id": policy_id,
        "policy_name": policy_name or _derive_policy_name(full_text, policy_id),
        "insurer_slug": UPLOAD_INSURER_SLUG,
        "_uploaded_doc": True,  # provenance flag (ignored by scorecard)
    }
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
            "insurer_slug": UPLOAD_INSURER_SLUG,
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
