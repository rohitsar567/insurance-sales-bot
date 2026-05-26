"""FastAPI app — the backend API for the Insurance Sales Portfolio Expert.

Run locally:
  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs at http://localhost:8000/docs
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.config import settings
from backend import nim_fallback
from backend import brain_tools  # KI-271 — SLOT_UNION-driven profile_dict in 3 endpoints
from backend import sum_insured as _si  # SI rationalisation (D1/D3) — source-quote corroboration
from backend.providers.sarvam_stt import SarvamSTT
from backend.providers.sarvam_tts import SarvamTTS

# Single-LLM brain toggle. Off by default; flip via env var. When on, the
# /api/chat hot path runs single_brain.handle_turn and falls back to
# nim_fallback.handle_turn_fallback on any SingleBrainError (so users
# always get a reply). When off, /api/chat routes directly through
# nim_fallback.handle_turn_fallback.
import os as _os  # local alias to avoid stomping any later `import os`

USE_SINGLE_BRAIN = _os.environ.get("USE_SINGLE_BRAIN", "false").lower() in (
    "1", "true", "yes", "on",
)

# Safety net for RULE 7. If Gemini does not call mark_recommendation when
# the user clearly commits to a policy ("I'll go with that one", "let's do
# #2", "buy this"), the post-turn detector below auto-calls
# mark_recommendation against session.last_recommendation_ids[:1] so the
# closure event is recorded for analytics.
# Word-boundary anchored; case-insensitive at match-time.
_CLOSER_KEYWORD_RE = re.compile(
    r"\b(go with|i'?ll take|i will take|let'?s do|let me get|sign me up|"
    r"purchase|buy this|i want to purchase|i'?ll go with|i want to buy)\b",
    re.IGNORECASE,
)

# Singleton provider instances (initialized on first call)
_stt: Optional[SarvamSTT] = None
_tts: Optional[SarvamTTS] = None

def get_stt() -> SarvamSTT:
    global _stt
    if _stt is None:
        _stt = SarvamSTT()
    return _stt

def get_tts() -> SarvamTTS:
    global _tts
    if _tts is None:
        _tts = SarvamTTS()
    return _tts


# ---------- log helpers ----------

LOG_DIR = settings.CORPUS_DIR.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
TURNS_LOG = LOG_DIR / "turns.jsonl"


def log_turn(event: dict) -> None:
    event["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(TURNS_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")


# ---------- API schemas ----------

class HealthResponse(BaseModel):
    status: str
    providers_ok: dict[str, bool]
    missing_keys: list[str]


class TranscribeResponse(BaseModel):
    text: str
    language_code: Optional[str] = None
    confidence: Optional[float] = None
    latency_ms: int
    # KI-242 — When Sarvam STT fails, the endpoint returns HTTP 200 with
    # `text=""` plus these two fields set so the frontend can render a
    # friendly message instead of parsing raw httpx error strings.
    # error_code is a closed enum: rate_limit | service_unavailable |
    # network | auth | unknown. Absent on the success path.
    error_code: Optional[str] = None
    user_message: Optional[str] = None


class CitationOut(BaseModel):
    policy_id: str
    policy_name: str
    insurer_slug: str
    page_start: int
    page_end: int
    source_url: str
    score: float


class ChatRequest(BaseModel):
    user_text: str = Field(..., description="The user's question or utterance")
    session_id: Optional[str] = Field(None, description="Stable per-session ID for logging")
    chat_history: list[dict] = Field(default_factory=list, description="[{role, content}, ...]")
    profile: dict = Field(default_factory=dict, description="User profile (age, dependents, etc.)")
    policy_filter_ids: Optional[list[str]] = Field(None, description="Restrict retrieval to these policies")
    return_audio: bool = Field(False, description="If true, also return TTS audio (base64 WAV)")
    tts_language_code: str = Field("en-IN", description="Language for TTS playback")
    view_context: Optional[dict] = Field(
        None,
        description=(
            "Frontend-supplied snapshot of what the user is looking at right now: "
            "{active_view, active_policy_id, filters}. Injected into the system prompt "
            "so the bot can ground 'this policy' / 'these filters' references."
        ),
    )


class ChatResponse(BaseModel):
    reply_text: str
    citations: list[CitationOut]
    brain_used: str
    intent: str
    language: str
    latency_ms: int
    session_id: str
    audio_base64: Optional[str] = None
    audio_mime: Optional[str] = None  # X8 — "audio/wav" | "audio/mp4" | "audio/webm"
    # TTS voice-output failures are surfaced to the client (text reply is
    # unaffected): the frontend renders a small inline "voice unavailable"
    # notice under the bot bubble. tts_error_code is a closed enum mirroring
    # the STT path's contract so the client never parses raw httpx text:
    #   rate_limit          — Sarvam 429 / insufficient_quota / no credits
    #   service_unavailable  — Sarvam 5xx / 503
    #   auth                 — Sarvam 401/403 / missing SARVAM_API_KEY
    #   network              — connect/read timeout, DNS, conn reset
    #   unknown              — anything else
    tts_error_code: Optional[str] = None
    tts_user_message: Optional[str] = None
    faithfulness_passed: bool = True
    faithfulness_reasons: list[str] = Field(default_factory=list)
    blocked: bool = False
    profile_updates: dict = Field(
        default_factory=dict,
        description=(
            "Any profile fields auto-extracted from the user's free-form message "
            "this turn (age, dependents, health_conditions, etc.). Frontend can "
            "flash an acknowledgment + refresh the completeness panel."
        ),
    )
    # Whether the 7 required profile slots are captured. Surfaced in the
    # primary chat response so the UI can flip to 100% in the same render
    # cycle without a second roundtrip to /api/profile/completeness.
    # Computed via brain_tools._REQUIRED_FOR_READY (same slot list used by
    # retrieve_policies' profile-complete gate) so client + server never
    # disagree.
    profile_complete: bool = Field(
        False,
        description=(
            "True when every required profile slot (name, age, dependents, "
            "location_tier, income_band, primary_goal, health_conditions) is "
            "non-empty on the live session.profile at end-of-turn."
        ),
    )
    # KI-Z7 (2026-05-15) — Feature B. True when single_brain.handle_turn's
    # turn-1 name heuristic matched a stored profile and hydrated the
    # session. Frontend renders a "Welcome back, <name>!" banner with the
    # last predicted-premium band when this flips True on the first turn.
    returning_user_recalled: bool = Field(
        False,
        description=(
            "True iff a stored named-profile was matched + hydrated on the "
            "current turn (typically only on turn 1)."
        ),
    )


class TTSRequest(BaseModel):
    text: str
    language_code: str = "en-IN"
    speaker: Optional[str] = None


class PolicyEntry(BaseModel):
    name: str
    source_url: str = ""  # PDF URL, verified at download time


class InsurerCoverage(BaseModel):
    slug: str
    name: str
    home_url: str  # insurer's main website (manually curated, verified)
    policy_count: int
    sample_policies: list[PolicyEntry]
    # KI-141 (2026-05-15) — backward-compatible default empty. Per-product
    # alias list isn't actually surfaced on the coverage card today, but the
    # field is mirrored from MarketplacePolicy so callers that union the two
    # endpoints see a consistent schema. Total aliases collapsed into this
    # insurer's parents — useful for QA + future UI surfacing.
    alias_count: int = 0


class CoverageResponse(BaseModel):
    total_chunks: int
    total_policies: int
    # KI-130 (2026-05-15) — totals reflect REAL insurers and their products
    # ONLY. The 'regulatory' slug (18 IRDAI/NHA documents) is excluded from
    # the marketplace surface entirely; those documents are still retrieved
    # and cited inside chat answers, they just don't belong in a "policy
    # marketplace" UI.
    total_insurers: int
    insurers: list[InsurerCoverage]


class UploadResponse(BaseModel):
    policy_id: str
    policy_name: str
    chunks_added: int
    pages_indexed: int
    elapsed_ms: int
    # #47 (2026-05-21) — UIN net-new dedup. When the uploaded PDF's IRDAI
    # UIN already belongs to a catalogue policy, the upload is NOT indexed
    # as a new card; these fields point the caller at the existing policy.
    already_in_catalogue: bool = False
    existing_policy_id: Optional[str] = None
    existing_policy_name: Optional[str] = None


# ---------------------------------------------------------------------------
# #47 (2026-05-21) — UIN net-new dedup for user uploads. Before a freshly
# uploaded PDF is indexed as a brand-new marketplace card, check whether its
# IRDAI UIN already belongs to a catalogued policy; if so it is NOT net-new
# and the caller is pointed at the existing card. All imports are lazy — the
# upload route imports `re` locally, so `re` is not module-level here.
_UIN_PATTERN = r"\b[A-Z]{5,9}\d{5}V\d{6}\b"
_catalogue_uin_cache = None  # type: Optional[dict]


def _catalogue_uin_index() -> dict:
    """Map every catalogue policy's IRDAI UIN -> (policy_id, policy_name).
    Built once from 40-data/policy_facts/*.json, then cached."""
    global _catalogue_uin_cache
    if _catalogue_uin_cache is not None:
        return _catalogue_uin_cache
    import json as _json
    import pathlib as _pl
    import re as _re

    def _find_uin(o):
        if isinstance(o, dict):
            if "uin_code" in o:
                v = o["uin_code"]
                return v.get("value") if isinstance(v, dict) else v
            for x in o.values():
                r = _find_uin(x)
                if r:
                    return r
        elif isinstance(o, list):
            for x in o:
                r = _find_uin(x)
                if r:
                    return r
        return None

    idx: dict = {}
    pf_dir = _pl.Path(__file__).resolve().parent.parent / "40-data" / "policy_facts"
    for fp in sorted(pf_dir.glob("*.json")):
        try:
            uin = _find_uin(_json.loads(fp.read_text()))
        except Exception:
            continue
        if not uin or not isinstance(uin, str):
            continue
        uin = uin.strip().upper()
        # Only index modern-format IRDAI UINs — those are the only ones the
        # uploaded-text matcher (_UIN_PATTERN) can ever extract. Legacy
        # registration codes (e.g. "IRDAI/HLT/CTTK/...") are unmatchable,
        # so indexing them would be dead weight.
        if not _re.fullmatch(r"[A-Z]{5,9}\d{5}V\d{6}", uin):
            continue
        stem = fp.stem
        for suf in ("__wordings", "__cis", "__brochure", "__prospectus"):
            if stem.endswith(suf):
                stem = stem[: -len(suf)]
        nm = stem.split("__")[-1].replace("-", " ").title()
        idx.setdefault(uin, (stem, nm))
    _catalogue_uin_cache = idx
    return idx


def _match_catalogue_uin(text: str):
    """Return (policy_id, policy_name) if `text` carries the IRDAI UIN of an
    already-catalogued policy; else None."""
    import re as _re

    idx = _catalogue_uin_index()
    # Case-insensitive extraction — a UIN may appear in any case in the
    # uploaded text / after PDF extraction; normalise to upper for lookup.
    for u in {m.upper() for m in _re.findall(_UIN_PATTERN, text or "", _re.IGNORECASE)}:
        if u in idx:
            return idx[u]
    return None


# ---------------------------------------------------------------------------
# Quarantine TTL auto-purge (2026-05-16)
#
# User-uploaded PDFs land in the SEPARATE `user_uploads_quarantine` Chroma
# collection, scoped per session_id. They are intentionally ephemeral — NOT
# durable corpus. Two risks if they linger forever:
#   1. The quarantine HNSW index grows unbounded across thousands of one-off
#      uploads (a soft version of the 2026-05-14 link_lists.bin bloat).
#   2. A user's private policy document stays queryable long after their
#      session is over.
#
# Mechanism (mirrors the existing in-memory ledgers in security.py /
# session_state.py — process-local, resets on restart, v2 → Redis):
#   - `_quarantine_last_seen`: {session_id: epoch_seconds} updated on every
#     successful upload via `_quarantine_touch`.
#   - A periodic asyncio task (`_quarantine_purge_loop`) sweeps every
#     settings.QUARANTINE_PURGE_INTERVAL_SEC and deletes all quarantine
#     chunks whose session_id has had no upload for
#     settings.QUARANTINE_TTL_SECONDS (default 24h).
# Deletion is `where={"session_id": sid}` — strictly scoped, can never touch
# the curated `policies` collection (different collection entirely).
# ---------------------------------------------------------------------------

_quarantine_last_seen: dict[str, float] = {}
_quarantine_lock = asyncio.Lock()


def _quarantine_touch(session_id: str, policy_id: str = "") -> None:
    """Record that `session_id` just wrote to the quarantine collection.

    Synchronous + best-effort: bookkeeping must never break an upload.
    """
    try:
        if session_id:
            _quarantine_last_seen[session_id] = time.time()
    except Exception:  # noqa: BLE001 — bookkeeping never breaks the upload
        pass


def _purge_expired_quarantine(now: Optional[float] = None) -> int:
    """Delete quarantine chunks for every session idle longer than the TTL.

    Returns the number of sessions purged. Pure/synchronous so it can be
    unit-tested directly and run via asyncio.to_thread (Chroma client is
    blocking). Never raises — a Chroma hiccup must not crash the loop.
    """
    now = now if now is not None else time.time()
    ttl = settings.QUARANTINE_TTL_SECONDS
    expired = [
        sid for sid, ts in list(_quarantine_last_seen.items())
        if now - ts >= ttl
    ]
    if not expired:
        return 0
    purged = 0
    try:
        from rag.ingest import get_quarantine_collection
        coll = get_quarantine_collection()
    except Exception as e:  # noqa: BLE001
        logging.warning(
            "quarantine TTL: could not open quarantine collection (%s: %s)",
            type(e).__name__, e,
        )
        return 0
    for sid in expired:
        try:
            coll.delete(where={"session_id": sid})
            _quarantine_last_seen.pop(sid, None)
            purged += 1
            logging.info(
                "quarantine TTL: purged session %s (idle > %ds)",
                sid[:12], ttl,
            )
        except Exception as e:  # noqa: BLE001 — one bad delete must not abort the sweep
            logging.warning(
                "quarantine TTL: delete(where session=%s) failed (%s: %s)",
                sid[:12], type(e).__name__, e,
            )
    return purged


async def _quarantine_purge_loop() -> None:
    """Periodic background sweep — registered at startup. Mirrors the
    llm_health.background_probe_loop pattern (sleep → work → repeat,
    swallow all errors so the loop never dies)."""
    interval = max(60, settings.QUARANTINE_PURGE_INTERVAL_SEC)
    while True:
        try:
            await asyncio.sleep(interval)
            await asyncio.to_thread(_purge_expired_quarantine)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — loop must survive any error
            logging.warning(
                "quarantine TTL purge loop iteration failed (%s: %s)",
                type(e).__name__, e,
            )


# Single source of truth for "is this profile ready to recommend against".
# brain_tools._profile_complete uses the same _REQUIRED_FOR_READY tuple; we
# _FEATURE_B_SLOT_LIST + _every_filled_slot_was_set_this_turn were the
# heuristic that distinguished "first-time capture on turn 1" from
# "stored profile recalled on turn 1" for the returning-user banner.
# Removed in ADR-043 (2026-05-27) — no cross-session recall, so there is
# no banner to flip.


def _compute_profile_complete(session_id: str) -> bool:
    """Read the live session profile and return True iff every required slot
    is populated. Tolerant of every failure mode (no session yet, session
    state import explodes, profile missing attrs) — returns False on any
    error so the frontend NEVER sees a stale `true` from a partial profile.
    """
    try:
        from backend.session_state import get_session
        from backend.brain_tools import _profile_complete

        sess = get_session(session_id)
        return bool(_profile_complete(sess.profile))
    except Exception:  # noqa: BLE001 — never block a chat reply for this
        return False


# ---------- app ----------

app = FastAPI(
    title="Insurance Sales Portfolio Expert API",
    description="Backend for the Sarvam AI take-home assignment.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production deploy
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Bug B (2026-05-15) — /api/chat raw 422 leak. Live smoke saw the frontend
# render `{"detail":[{"type":"missing","loc":["body","user_text"]...}]}` as
# the bot reply because a malformed POST (missing user_text) hit FastAPI's
# default RequestValidationError handler — that body bypasses our
# ChatResponse envelope and the frontend has no shape-mapping for it.
# We intercept the chat endpoint specifically and return a clean
# ChatResponse-shaped JSON so frontend parsing never errors out. Other
# endpoints keep FastAPI's default 422 behaviour (which their callers
# already handle).
@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/chat":
        logging.warning(
            "chat endpoint received malformed body — returning graceful "
            "ChatResponse-shaped 200 instead of raw 422. errors=%r",
            exc.errors()[:3],
        )
        return JSONResponse(
            status_code=200,
            content={
                "reply_text": (
                    "Sorry, something went wrong — try again."
                ),
                "citations": [],
                "brain_used": "error_fallback",
                "intent": "qa",
                "language": "en",
                "latency_ms": 0,
                "session_id": "",
                "audio_base64": None,
                "audio_mime": None,
                "faithfulness_passed": True,
                "faithfulness_reasons": [],
                "blocked": False,
                "profile_updates": {},
                "profile_complete": False,
            },
        )
    # Default behaviour for every other endpoint.
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


# ---------- Admin panel + LLM health background loop ----------
# Mount the password-gated admin endpoints. Unauthorized callers get
# 401 Unauthorized. Access is gated by a strong password only (no IP
# allowlist, which would lock the operator out when switching networks
# without adding real security).
from backend import admin as _admin_router_module
app.include_router(_admin_router_module.router)


@app.on_event("startup")
async def _startup_load_admin_overrides():
    """Re-apply any persisted chain reorderings from the previous process."""
    import asyncio
    from pathlib import Path
    override_path = settings.DATA_DIR / "admin_overrides.json"
    if override_path.exists():
        try:
            overrides = json.loads(override_path.read_text())
            from backend.providers import nvidia_nim_llm as nim
            name_map = {"brain": "BRAIN_CHAIN", "fast_brain": "FAST_BRAIN_CHAIN", "judge": "JUDGE_CHAIN"}
            for role, attr in name_map.items():
                if role in overrides and isinstance(overrides[role], list):
                    setattr(nim, attr, list(overrides[role]))
        except Exception:
            pass  # bad override file shouldn't crash boot — fall back to defaults


@app.on_event("startup")
async def _startup_llm_health_probe():
    """Launch the background probe loop — pings every NIM model every 5 min,
    auto-marks 'down' models, NimChainLLM uses filter_chain() to skip them."""
    import asyncio
    from backend import llm_health
    asyncio.create_task(llm_health.background_probe_loop())


@app.on_event("startup")
async def _startup_quarantine_ttl_purge():
    """Launch the periodic quarantine TTL sweep (2026-05-16).

    Evicts user-uploaded PDF chunks whose session has been idle longer than
    settings.QUARANTINE_TTL_SECONDS so the quarantine index can't grow
    unbounded and stale private docs don't linger. Mirrors the
    _startup_llm_health_probe fire-and-forget create_task pattern.
    """
    asyncio.create_task(_quarantine_purge_loop())


@app.on_event("startup")
async def _startup_single_brain_warmup():
    """Pre-warm the Gemini single-brain connection so the FIRST /api/chat turn
    doesn't eat 4-5s of cold-start latency (TLS + auth + cache init).

    Wrapped in try/except — warmup is an optimization, not a boot
    requirement. A failed warmup must NEVER crash the server.
    """
    try:
        from backend import single_brain
        latency = await single_brain.warmup()
        if latency is not None:
            logging.info(
                "single_brain warmup completed at boot (%.2fs)", latency,
            )
    except Exception as e:  # noqa: BLE001
        logging.warning(
            "single_brain warmup raised at top level (%s: %s) — boot continues",
            type(e).__name__, e,
        )


@app.on_event("startup")
async def _startup_reingest_uploaded_docs():
    """#52 — re-materialise persisted uploaded-policy docs after a restart.

    On the HF Space, rag/vectors is the EPHEMERAL container FS (KI-119):
    every rebuild pulls a fresh Chroma snapshot, so an uploaded doc's
    chunks indexed last boot are GONE. The PDF + curated-facts JSON + chunk
    payload were persisted to the /data disk (settings.UPLOADED_DOCS_DIR),
    so here we re-embed those chunks back into the fresh `policies`
    collection. The cards themselves reappear automatically because
    _load_curated_facts merges the persisted JSON records.

    Wrapped so a re-ingest hiccup never crashes boot — but it logs LOUDLY
    (no silent failure): an uploaded card with no retrievable chunks is a
    real degradation operators must see.
    """
    try:
        from backend import uploaded_docs as _udocs

        summary = await _udocs.reingest_persisted_into_policies()
        if summary.get("docs") or summary.get("skipped"):
            logging.info(
                "#52 startup re-ingest: %d uploaded docs / %d chunks "
                "re-indexed into `policies` (%d skipped)",
                summary.get("docs", 0), summary.get("chunks", 0),
                summary.get("skipped", 0),
            )
        # Bust the #40 grade cache so the restored cards grade on first hit.
        try:
            with _MG_LOCK:
                _MG_CACHE["sig"] = None
                _MG_CACHE["index"] = None
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001 — re-ingest failure must not block boot
        logging.warning(
            "#52 startup re-ingest FAILED (%s: %s) — uploaded-doc cards "
            "will show but their chunks are NOT retrievable until next "
            "successful re-ingest",
            type(e).__name__, e,
        )


async def _startup_purge_dangling_profile_chunks():
    """KI-117 — boot-time self-heal of dangling `doc_type='profile'` chunks.

    Background: KI-102's earliest deploy wrote a `profile_anonymous` chunk
    WITHOUT a `session_id` metadata field. That legacy row poisoned every
    subsequent retrieval whose `where` clause referenced session_id, because
    Chroma raises when a filtered row is missing the filtered key. KI-112
    added input guards so no new bad rows can be written, and the local DB
    was cleaned manually. But the HF Space carries its OWN copy of the
    Chroma DB and still contains the dangling row.

    This handler scans the collection for any `doc_type='profile'` chunks
    whose metadata lacks a non-empty `session_id` and deletes them. Runs
    idempotently — if there are no bad rows, it's a no-op. After HF rebuilds
    with this code, the boot task self-heals HF's DB on first request.

    Wrapped in try/except so a Chroma hiccup never crashes boot.
    """
    def _do_purge() -> None:
        from rag.retrieve import get_collection

        coll = get_collection()
        try:
            res = coll.get(
                where={"doc_type": "profile"},
                limit=10000,
                include=["metadatas"],
            )
        except Exception as e:
            logging.warning(
                "KI-117: profile-chunk scan failed (%s: %s) — skipping cleanup",
                type(e).__name__, e,
            )
            return

        ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        bad_ids: list[str] = []
        for cid, meta in zip(ids, metas):
            # KI-118 (2026-05-15) — profile chunks are now keyed by name_slug;
            # accept EITHER a non-empty name_slug (new) OR a non-empty
            # session_id (legacy KI-102 row) as proof-of-ownership. A profile
            # chunk with neither key is the dangling-row corruption case and
            # must be purged.
            slug = (meta or {}).get("name_slug")
            sid = (meta or {}).get("session_id")
            slug_ok = isinstance(slug, str) and slug.strip()
            sid_ok = isinstance(sid, str) and sid.strip()
            if not (slug_ok or sid_ok):
                bad_ids.append(cid)

        if bad_ids:
            try:
                coll.delete(ids=bad_ids)
                logging.info(
                    "KI-117: purged %d dangling profile chunks at boot (ids=%s)",
                    len(bad_ids),
                    bad_ids[:10] + (["..."] if len(bad_ids) > 10 else []),
                )
            except Exception as e:
                logging.warning(
                    "KI-117: delete(ids=...) failed (%s: %s) — bad rows remain",
                    type(e).__name__, e,
                )
                return
        else:
            logging.info("KI-117: no dangling profile chunks found (DB clean)")

        try:
            total = coll.count()
            logging.info("KI-117: total chunks after cleanup: %d", total)
        except Exception as e:
            logging.warning(
                "KI-117: post-cleanup count() failed (%s: %s)",
                type(e).__name__, e,
            )

    try:
        await asyncio.to_thread(_do_purge)
    except Exception as e:
        # Belt + suspenders — boot must never crash.
        logging.warning(
            "KI-117: boot cleanup raised at top level (%s: %s) — continuing boot",
            type(e).__name__, e,
        )


@app.on_event("startup")
async def _startup_purge_dangling_profile_chunks_handler():
    """KI-117 — register the boot-time cleanup as a FastAPI startup hook."""
    await _startup_purge_dangling_profile_chunks()


@app.get("/api/health", response_model=HealthResponse)
async def health():
    missing = settings.validate()
    # Post-D-019 the stack is Sarvam (voice + Indic) + NVIDIA NIM (brain +
    # judge). GROQ + OpenRouter were retired; don't reference them here or
    # this endpoint AttributeError's on every call.
    providers_ok = {
        "sarvam":     bool(settings.SARVAM_API_KEY),
        "nvidia_nim": bool(settings.NVIDIA_NIM_API_KEY),
    }
    return HealthResponse(
        status="ok" if not missing else "degraded",
        providers_ok=providers_ok,
        missing_keys=missing,
    )


# KI-096 — public deploy-verification endpoint. No auth (deliberate) so any
# caller can confirm which commit the HF Space is actually serving without
# needing the admin password. Cached at module import so we don't spawn
# `git` per request.
def _compute_build_sha() -> str:
    import os
    import subprocess
    env_sha = os.environ.get("BUILD_SHA") or os.environ.get("HF_SPACE_GIT_REV")
    if env_sha:
        return env_sha[:12]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).decode().strip()
        return out[:12] if out else "unknown"
    except Exception:
        return "unknown"


_BUILD_SHA = _compute_build_sha()
_BUILD_STARTED_AT = time.time()


@app.get("/api/version")
async def version():
    """Public deploy-verification endpoint — no auth required.

    Returns the git SHA the running app was built from + the process start
    timestamp. Used by deploy probes (and humans) to confirm which commit
    HF Space is actually serving. The admin /api/admin/* endpoints are
    password-gated (KI-097) and return 401; this endpoint is the auth-free
    escape hatch for deploy verification.
    """
    return {
        "sha": _BUILD_SHA,
        "started_at": _BUILD_STARTED_AT,
        "uptime_s": round(time.time() - _BUILD_STARTED_AT, 1),
    }


@app.post("/api/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: UploadFile = File(...),
    language_code: Optional[str] = Form(None),
):
    """Speech-to-text. Accepts an audio file upload (WAV/MP3/etc.).

    KI-242 — Sarvam errors are classified into a closed `error_code` enum
    and the endpoint always returns HTTP 200 with a friendly `user_message`
    on failure. The frontend never parses raw httpx text. 429 (rate limit)
    is retried ONCE with a 2 s backoff before being surfaced as
    `error_code: "rate_limit"`.
    """
    import httpx as _httpx
    from backend.providers.sarvam_stt import (
        classify_stt_exception,
        STT_ERROR_USER_MESSAGES,
        STT_ERROR_RATE_LIMIT,
    )

    t0 = time.time()
    audio_bytes = await file.read()
    ext = (file.filename or "audio.wav").rsplit(".", 1)[-1].lower()
    audio_format = (
        ext if ext in ("wav", "mp3", "flac", "ogg", "m4a", "webm", "opus", "mp4")
        else "wav"
    )

    async def _try_once():
        return await get_stt().transcribe(
            audio_bytes=audio_bytes,
            audio_format=audio_format,
            language_code=language_code,
        )

    last_exc: Optional[BaseException] = None
    try:
        result = await _try_once()
    except Exception as e:  # noqa: BLE001 — classifier narrows
        last_exc = e
        # 429-only single retry with 2s backoff, mirroring KI-242. Only retry
        # on a positively identified rate-limit; other failures surface fast.
        is_rate_limited = (
            isinstance(e, _httpx.HTTPStatusError)
            and e.response is not None
            and e.response.status_code == 429
        )
        if is_rate_limited:
            await asyncio.sleep(2.0)
            try:
                result = await _try_once()
                last_exc = None
            except Exception as e2:  # noqa: BLE001
                last_exc = e2

    latency = int((time.time() - t0) * 1000)

    if last_exc is not None:
        code = classify_stt_exception(last_exc)
        # Log the underlying error server-side so we keep diagnostics, but
        # never leak the raw httpx string to the user-facing response.
        logging.warning(
            "STT failed: error_code=%s exc=%s: %s",
            code,
            type(last_exc).__name__,
            last_exc,
        )
        # Force rate_limit code when the retry-arm exhausted on 429 too.
        if (
            isinstance(last_exc, _httpx.HTTPStatusError)
            and last_exc.response is not None
            and last_exc.response.status_code == 429
        ):
            code = STT_ERROR_RATE_LIMIT
        return TranscribeResponse(
            text="",
            language_code=language_code,
            confidence=0.0,
            latency_ms=latency,
            error_code=code,
            user_message=STT_ERROR_USER_MESSAGES.get(
                code, STT_ERROR_USER_MESSAGES["unknown"]
            ),
        )

    return TranscribeResponse(
        text=result.text,
        language_code=result.language_code,
        confidence=result.confidence,
        latency_ms=latency,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    session_id = req.session_id or str(uuid.uuid4())
    # X8 (2026-05-15) — frontend sends X-Preferred-Codec so the browser can
    # decode the TTS payload natively (WebM/Opus on Chrome+Firefox, MP4/AAC on
    # Safari, WAV as universal fallback). Default to WAV if the header is
    # missing or invalid.
    _allowed_codecs = {"audio/wav", "audio/mp4", "audio/webm"}
    # KI-278 (2026-05-16) — the frontend sends a full MediaSource codec
    # string, e.g. "audio/webm; codecs=opus". The previous exact-match
    # against _allowed_codecs NEVER matched that (the "; codecs=opus"
    # suffix), so webm/opus-capable browsers were ALWAYS silently
    # downgraded to wav. Strip the codec parameter + whitespace before
    # the membership test so the negotiated container is honoured.
    preferred_codec = (
        request.headers.get("X-Preferred-Codec", "audio/wav") or "audio/wav"
    ).split(";")[0].strip().lower()
    if preferred_codec not in _allowed_codecs:
        preferred_codec = "audio/wav"
    t_chat0 = time.time()
    # Never let an inner TimeoutError / unhandled exception bubble out of
    # handle_turn as a 500. The whole call is wrapped in an outer 45s
    # budget so even a pathological hang inside handle_turn surfaces as a
    # graceful reply, not a connection-reset to the user. 45s is generous
    # but tighter than HF Space's gateway timeout, so the user always gets
    # a response.
    try:
        if USE_SINGLE_BRAIN:
            # One Gemini call per turn with native function-calling.
            # Falls back to nim_fallback on SingleBrainError so a missing
            # GOOGLE_API_KEY / model outage never breaks the chat.
            from backend import single_brain
            from backend.session_state import get_session

            _sb_session = get_session(session_id)
            # Once a session has had ANY successful single_brain turn, it
            # must stay on single_brain for the rest of its lifetime.
            # Switching brains mid-stream would discard everything
            # single_brain captured in last_recommendation_ids /
            # last_retrieved_chunks / slug_to_insurer. Sticky check below.
            _sb_was_sticky = getattr(_sb_session, "single_brain_sticky", False)
            try:
                turn = await asyncio.wait_for(
                    single_brain.handle_turn(
                        session=_sb_session,
                        user_text=req.user_text,
                        chat_history=req.chat_history,
                    ),
                    timeout=45.0,
                )
                # First successful single_brain turn stamps the flag so
                # every subsequent turn on this session is locked in.
                try:
                    _sb_session.single_brain_sticky = True
                except Exception:  # noqa: BLE001
                    pass
            except single_brain.SingleBrainError as _sb_err:
                if _sb_was_sticky:
                    # Session already had a clean single_brain turn. Do NOT
                    # cross-fade to the fallback brain (loses turn state +
                    # frontend sees the brain hop). Emit a graceful retry
                    # prompt instead.
                    logging.warning(
                        "single_brain failed on STICKY session (session=%s); "
                        "emitting graceful retry, NOT falling back: %s",
                        session_id, _sb_err,
                    )
                    turn = single_brain.TurnResult(
                        reply_text=(
                            # 2026-05-27 — honest copy. Previous text falsely
                            # blamed comprehension ("could you say that
                            # again?") when the actual cause was an upstream
                            # Gemini transient (HTTP 503 / timeout / etc.)
                            # that survived the internal retry schedule.
                            # Tells the user exactly what to do (resend the
                            # same message) and locates blame correctly.
                            "My model service had a brief blip on that turn "
                            "— please send the same message again, it should "
                            "go through now."
                        ),
                        citations=[],
                        retrieved_chunk_ids=[],
                        brain_used="single_brain::sticky_graceful_retry",
                        intent="qa",
                        language="en",
                        latency_ms=int((time.time() - t_chat0) * 1000),
                        raw_reply=f"SingleBrainError: {_sb_err}",
                        faithfulness_passed=True,
                        faithfulness_reasons=[],
                        blocked=False,
                        profile_updates={},
                    )
                else:
                    logging.warning(
                        "single_brain failed, falling back to nim_fallback "
                        "(session=%s): %s",
                        session_id, _sb_err,
                    )
                    turn = await asyncio.wait_for(
                        nim_fallback.handle_turn_fallback(
                            session=_sb_session,
                            user_text=req.user_text,
                            chat_history=req.chat_history,
                        ),
                        timeout=20.0,
                    )
        else:
            # When USE_SINGLE_BRAIN is off, route directly through the
            # minimal NIM fallback so the bot still serves a reply.
            from backend.session_state import get_session as _get_session
            turn = await asyncio.wait_for(
                nim_fallback.handle_turn_fallback(
                    session=_get_session(session_id),
                    user_text=req.user_text,
                    chat_history=req.chat_history,
                ),
                timeout=20.0,
            )
    except asyncio.TimeoutError:
        logging.warning(
            "handle_turn outer TimeoutError; returning graceful reply (session=%s)",
            session_id,
        )
        log_turn({
            "session_id": session_id,
            "user_text": req.user_text,
            "error": "asyncio.TimeoutError (outer 45s budget or inner wait_for)",
            "graceful": True,
        })
        return ChatResponse(
            reply_text=(
                "That took longer than expected — let me try a smaller answer. "
                "Could you ask me again, maybe more specifically?"
            ),
            citations=[],
            brain_used="timeout_fallback",
            intent="qa",
            language="en",
            latency_ms=int((time.time() - t_chat0) * 1000),
            session_id=session_id,
            audio_base64=None,
            faithfulness_passed=True,
            faithfulness_reasons=[],
            blocked=False,
            profile_updates={},
            profile_complete=_compute_profile_complete(session_id),
        )
    except Exception as e:
        logging.exception(
            "handle_turn unhandled exception (session=%s)", session_id
        )
        log_turn({
            "session_id": session_id,
            "user_text": req.user_text,
            "error": f"{type(e).__name__}: {e}",
            "graceful": True,
        })
        return ChatResponse(
            reply_text=(
                "Hmm, something went wrong on my end. Could you try once more?"
            ),
            citations=[],
            brain_used="error_fallback",
            intent="qa",
            language="en",
            latency_ms=int((time.time() - t_chat0) * 1000),
            session_id=session_id,
            audio_base64=None,
            faithfulness_passed=True,
            faithfulness_reasons=[],
            blocked=False,
            profile_updates={},
            profile_complete=_compute_profile_complete(session_id),
        )

    # Server-side closer-keyword safety net for RULE 7.
    # If the user clearly committed to a policy this turn but Gemini did
    # NOT call mark_recommendation (single_brain stamps "mark_recommendation"
    # into turn.brain_used when the tool fires — see single_brain.py:1052),
    # auto-call mark_recommendation against session.last_recommendation_ids[:1]
    # so the closure event is recorded for analytics regardless of whether
    # the LLM remembered to pull the tool. Best-effort; never blocks the
    # reply if anything goes wrong.
    try:
        if (
            USE_SINGLE_BRAIN
            and turn is not None
            and getattr(turn, "reply_text", None)
            and _CLOSER_KEYWORD_RE.search(req.user_text or "")
            and "mark_recommendation" not in (turn.brain_used or "")
        ):
            from backend.session_state import get_session as _get_session
            from backend import brain_tools as _brain_tools

            _closer_session = _get_session(session_id)
            _last_recs = list(
                getattr(_closer_session, "last_recommendation_ids", []) or []
            )
            if _last_recs:
                _result = _brain_tools.mark_recommendation(
                    session=_closer_session,
                    policy_ids=_last_recs[:1],
                    is_final=True,
                )
                logging.info(
                    "U1-T9 closer auto-mark (session=%s) user_text=%r "
                    "policy_ids=%s result=%s",
                    session_id, req.user_text, _last_recs[:1], _result,
                )
    except Exception as _closer_err:  # noqa: BLE001
        # Safety-net must never break the reply.
        logging.warning(
            "U1-T9 closer auto-mark failed (session=%s): %s: %s",
            session_id, type(_closer_err).__name__, _closer_err,
        )

    # KI-254 — auto-mark_recommendation when single_brain emits a
    # recommendation turn (retrieve_policies fired + citations non-empty)
    # but Gemini skipped calling mark_recommendation. This populates
    # session.last_recommendation_ids so the NEXT turn's ordinal follow-up
    # ("tell me about #2", "the second one", "first option") can resolve.
    # Without this, RULE 3 ("call mark_recommendation alongside retrieve")
    # depends on Gemini remembering; smoke-3-personas showed it forgets
    # on recommendation turns ~70% of the time, breaking T4 ordinal routing.
    # Safety net mirrors the U1-T9 closer pattern: best-effort, never blocks.
    try:
        if (
            USE_SINGLE_BRAIN
            and turn is not None
            and getattr(turn, "citations", None)
            and "retrieve_policies" in (turn.brain_used or "")
            and "mark_recommendation" not in (turn.brain_used or "")
        ):
            from backend.session_state import get_session as _get_session_r
            from backend import brain_tools as _brain_tools_r

            _rec_session = _get_session_r(session_id)
            _cited_ids: list[str] = []
            _seen: set[str] = set()
            for _c in (turn.citations or []):
                pid = _c.get("policy_id") if isinstance(_c, dict) else getattr(_c, "policy_id", None)
                pid = (pid or "").strip()
                if pid and pid not in _seen:
                    _seen.add(pid)
                    _cited_ids.append(pid)
            if _cited_ids:
                # KI-278 — turn.citations is now the prose-aligned
                # recommendation set (single source of truth), so it equals
                # exactly the cards the user sees. Back-fill the FULL set so
                # ordinal follow-ups ("the 4th one") resolve against the same
                # list. The old [:4] cap existed only because citations used
                # to be the raw recall dump; it would now wrongly truncate a
                # legitimate 5-policy shortlist.
                _result_r = _brain_tools_r.mark_recommendation(
                    session=_rec_session,
                    policy_ids=_cited_ids,
                    is_final=False,
                )
                logging.info(
                    "KI-254 auto-mark on rec turn (session=%s) "
                    "policy_ids=%s result=%s",
                    session_id, _cited_ids, _result_r,
                )
    except Exception as _rec_err:  # noqa: BLE001
        logging.warning(
            "KI-254 auto-mark on rec turn failed (session=%s): %s: %s",
            session_id, type(_rec_err).__name__, _rec_err,
        )

    # F3 — confirmation auto-extract safety net.
    # Symptom: Gemini emits a recap bullet list ("**Primary Goal:** first
    # family policy ...") from conversation context but skips one or more
    # save_profile_field calls. User says "yes this is correct"; the
    # _profile_complete gate refuses retrieval and the bot embarrassingly
    # re-asks. This block parses the bot's prior recap turn, maps slot
    # labels -> _REQUIRED_FOR_READY field names, and backfills any slot
    # that's STILL missing on the live profile. Best-effort; never blocks.
    # Mirror of KI-253 closer regex + KI-254 auto-mark pattern.
    try:
        _CONFIRM_RE = re.compile(
            r"\b(yes|correct|that'?s right|all correct|looks good)\b",
            re.IGNORECASE,
        )
        _RECAP_BULLET_RE = re.compile(r"^\s*\*\s*\*\*[^:]+:\*\*", re.MULTILINE)
        _RECAP_LINE_RE = re.compile(
            r"^\s*\*\s*\*\*([^:]+):\*\*\s*(.+?)\s*$",
            re.MULTILINE,
        )
        # Slot label aliases -> canonical _REQUIRED_FOR_READY field names.
        # Keys are lowercased + whitespace-collapsed.
        _SLOT_ALIASES = {
            "name": "name",
            "full name": "name",
            "age": "age",
            "dependents": "dependents",
            "family": "dependents",
            "family members": "dependents",
            "location": "location_tier",
            "city": "location_tier",
            "location tier": "location_tier",
            "city tier": "location_tier",
            "income": "income_band",
            "income band": "income_band",
            "annual income": "income_band",
            "primary goal": "primary_goal",
            "goal": "primary_goal",
            "objective": "primary_goal",
            "health conditions": "health_conditions",
            "health": "health_conditions",
            "medical conditions": "health_conditions",
            "pre-existing conditions": "health_conditions",
            "medical history": "health_conditions",
        }

        if (
            USE_SINGLE_BRAIN
            and turn is not None
            and _CONFIRM_RE.search(req.user_text or "")
            and not getattr(turn, "profile_complete", False)
        ):
            # Locate the most recent bot/assistant message in chat_history.
            _prior_bot_text = ""
            for _msg in reversed(req.chat_history or []):
                if not isinstance(_msg, dict):
                    continue
                _role = (_msg.get("role") or "").lower()
                if _role in ("assistant", "bot", "model"):
                    _prior_bot_text = _msg.get("content") or ""
                    break

            if _prior_bot_text and _RECAP_BULLET_RE.search(_prior_bot_text):
                from backend.session_state import get_session as _get_session_f3
                from backend import brain_tools as _brain_tools_f3

                _f3_session = _get_session_f3(session_id)
                _profile_f3 = _f3_session.profile

                _parsed: dict[str, str] = {}
                for _label, _value in _RECAP_LINE_RE.findall(_prior_bot_text):
                    _key = " ".join(_label.strip().lower().split())
                    _slot = _SLOT_ALIASES.get(_key)
                    if not _slot:
                        continue
                    _val = (_value or "").strip().rstrip(".")
                    if not _val:
                        continue
                    # Don't override slots that already have values.
                    _existing = getattr(_profile_f3, _slot, None)
                    if _existing not in (None, "", []):
                        continue
                    # First mapping wins (in case a label appears twice).
                    _parsed.setdefault(_slot, _val)

                _backfilled: list[str] = []
                for _slot in _brain_tools_f3._REQUIRED_FOR_READY:
                    if _slot not in _parsed:
                        continue
                    _existing = getattr(_profile_f3, _slot, None)
                    if _existing not in (None, "", []):
                        continue
                    try:
                        _r = _brain_tools_f3.save_profile_field(
                            session=_f3_session,
                            field=_slot,
                            value=_parsed[_slot],
                        )
                        if isinstance(_r, dict) and _r.get("saved"):
                            _backfilled.append(_slot)
                    except Exception:  # noqa: BLE001 — best-effort
                        continue

                if _backfilled:
                    logging.info(
                        "F3 confirmation auto-extract (session=%s): backfilled %s",
                        session_id, _backfilled,
                    )
    except Exception as _f3_err:  # noqa: BLE001
        # Safety-net must never break the reply.
        logging.warning(
            "F3 confirmation auto-extract failed (session=%s): %s: %s",
            session_id, type(_f3_err).__name__, _f3_err,
        )

    audio_b64 = None
    audio_mime: Optional[str] = None
    # When TTS fails we propagate a STRUCTURED, user-facing notice instead
    # of silently dropping the audio. The text reply is still returned in
    # full; the frontend renders a small inline "voice unavailable" line
    # under the bot bubble so the user understands why there's no voice
    # (e.g. Sarvam 429 / no credits).
    tts_error_code: Optional[str] = None
    tts_user_message: Optional[str] = None
    if req.return_audio and turn.reply_text:
        try:
            from backend.voice_format import tts_preprocess
            # Send a CLEANED version of the reply to TTS — strip markdown,
            # citations, expand acronyms, truncate. The text in the chat
            # bubble remains the full structured reply.
            spoken = tts_preprocess(
                turn.reply_text,
                language="indic" if req.tts_language_code.startswith("hi") else "en",
                max_words=55,
            )
            # X8 — honor X-Preferred-Codec; on transcoding failure the provider
            # falls back to raw WAV and reports audio_mime="audio/wav".
            audio, audio_mime = await get_tts().synthesize_with_mime(
                spoken,
                language_code=req.tts_language_code,
                preferred_codec=preferred_codec,
            )
            audio_b64 = base64.b64encode(audio).decode("utf-8")
        except Exception as e:
            # Don't fail the whole turn if TTS hiccups — but make the failure
            # LOUD: classify it once at the boundary (closed enum, same
            # contract as STT) so the client can render a friendly notice
            # instead of showing a voice-less reply with no explanation.
            from backend.providers.sarvam_tts import (
                classify_tts_exception,
                TTS_ERROR_USER_MESSAGES,
                TTS_ERROR_UNKNOWN,
            )
            tts_error_code = classify_tts_exception(e)
            tts_user_message = TTS_ERROR_USER_MESSAGES.get(
                tts_error_code, TTS_ERROR_USER_MESSAGES[TTS_ERROR_UNKNOWN]
            )
            audio_b64 = None
            audio_mime = None
            # Server-side diagnostics keep the raw error; the client never
            # sees the raw httpx string.
            logging.warning(
                "TTS failed (session=%s): error_code=%s exc=%s: %s",
                session_id, tts_error_code, type(e).__name__, e,
            )
            log_turn({
                "session_id": session_id,
                "tts_error": f"{type(e).__name__}: {e}",
                "tts_error_code": tts_error_code,
            })

    try:
        log_turn({
            "session_id": session_id,
            "user_text": req.user_text,
            "reply_text": turn.reply_text,
            "brain_used": turn.brain_used,
            "intent": turn.intent,
            "language": turn.language,
            "latency_ms": turn.latency_ms,
            "retrieved_chunk_ids": turn.retrieved_chunk_ids,
            "citation_count": len(turn.citations),
            "faithfulness_passed": turn.faithfulness_passed,
            "faithfulness_reasons": turn.faithfulness_reasons,
            "blocked": turn.blocked,
        })
    except Exception:  # noqa: BLE001 — log IO must never block a reply
        pass

    # Cross-session profile persistence + returning-user detection removed
    # in ADR-043 (2026-05-27). Sessions are in-memory only.
    _returning_user_recalled = False

    # Bug B defense — CitationOut requires page_start/page_end as ints, but
    # single_brain.TurnResult.citations dicts don't carry those fields (its
    # citation shape is {chunk_id, policy_id, policy_name, insurer_slug,
    # doc_type, source_url, score}). Without this normalisation the
    # Pydantic constructor below would raise ValidationError, the
    # exception would escape /api/chat, and FastAPI would return a raw
    # 500 (or its default JSON error envelope) that the frontend can't
    # parse as a ChatResponse. We patch every citation dict to satisfy
    # CitationOut's required fields and wrap the whole response build in
    # an explicit try/except so a malformed citation can never silently
    # bypass our envelope.
    try:
        from backend.policy_identity import clean_display_policy_name
        safe_citations: list[CitationOut] = []
        for c in turn.citations or []:
            if not isinstance(c, dict):
                continue
            try:
                safe_citations.append(
                    CitationOut(
                        policy_id=str(c.get("policy_id", "") or ""),
                        policy_name=clean_display_policy_name(
                            str(c.get("policy_name", "") or "")
                        ),
                        insurer_slug=str(c.get("insurer_slug", "") or ""),
                        page_start=int(c.get("page_start", 0) or 0),
                        page_end=int(c.get("page_end", 0) or 0),
                        source_url=str(c.get("source_url", "") or ""),
                        score=float(c.get("score", 0.0) or 0.0),
                    )
                )
            except Exception as _cite_err:  # noqa: BLE001
                logging.warning(
                    "drop malformed citation (session=%s): %s — payload=%r",
                    session_id, _cite_err, c,
                )

        return ChatResponse(
            reply_text=turn.reply_text,
            citations=safe_citations,
            brain_used=turn.brain_used,
            intent=turn.intent,
            language=turn.language,
            latency_ms=turn.latency_ms,
            session_id=session_id,
            audio_base64=audio_b64,
            audio_mime=audio_mime,
            tts_error_code=tts_error_code,
            tts_user_message=tts_user_message,
            faithfulness_passed=turn.faithfulness_passed,
            faithfulness_reasons=turn.faithfulness_reasons,
            blocked=turn.blocked,
            profile_updates=turn.profile_updates,
            profile_complete=_compute_profile_complete(session_id),
            returning_user_recalled=_returning_user_recalled,
        )
    except Exception as _resp_err:  # noqa: BLE001
        # Anything else (TypeError/AttributeError/ValidationError) on the
        # response-build path — return the standard error_fallback shape
        # so the frontend always parses cleanly. Bug B catch-all.
        logging.exception(
            "chat response-build failed (session=%s): %s",
            session_id, _resp_err,
        )
        return ChatResponse(
            reply_text=(
                "Sorry, something went wrong — try again"
            ),
            citations=[],
            brain_used="error_fallback",
            intent="qa",
            language="en",
            latency_ms=int((time.time() - t_chat0) * 1000),
            session_id=session_id,
            audio_base64=None,
            audio_mime=None,
            faithfulness_passed=True,
            faithfulness_reasons=[],
            blocked=False,
            profile_updates={},
            profile_complete=_compute_profile_complete(session_id),
        )


@app.get("/api/coverage", response_model=CoverageResponse)
async def coverage():
    """What policies/insurers are indexed in the corpus.

    Drives the UI's "what's covered" panel — sets user expectations + reduces
    over-refusals from off-corpus queries.
    """
    try:
        from rag.retrieve import get_collection
        coll = get_collection()
        total = coll.count()
    except Exception:
        total = 0

    # Insurer metadata — names + home URLs are curated + verified
    # (see eval/verified_urls.json + tools/verify_urls.py).
    # KI-132 (2026-05-15) — expanded to all 19 real insurers so /api/coverage
    # also returns proper display names (was 10 of 19).
    insurer_meta = {
        "acko":               ("Acko Health Insurance", "https://www.acko.com/health-insurance/"),
        "aditya-birla":       ("Aditya Birla Health Insurance", "https://www.adityabirlacapital.com/healthinsurance"),
        "bajaj-allianz":      ("Bajaj Allianz General Insurance", "https://www.bajajallianz.com/"),
        "care-health":        ("Care Health Insurance", "https://www.careinsurance.com/"),
        "cholamandalam":      ("Cholamandalam MS General Insurance", "https://www.cholainsurance.com/"),
        "go-digit":           ("Go Digit General Insurance", "https://www.godigit.com/"),
        "hdfc-ergo":          ("HDFC ERGO General Insurance", "https://www.hdfcergo.com/"),
        "icici-lombard":      ("ICICI Lombard General Insurance", "https://www.icicilombard.com/"),
        "iffco-tokio":        ("IFFCO Tokio General Insurance", "https://www.iffcotokio.co.in/"),
        "manipalcigna":       ("ManipalCigna Health Insurance", "https://www.manipalcigna.com/"),
        "national-insurance": ("National Insurance Company", "https://nationalinsurance.nic.co.in/"),
        "new-india":          ("New India Assurance", "https://www.newindia.co.in/"),
        "niva-bupa":          ("Niva Bupa Health Insurance", "https://www.nivabupa.com/"),
        "indusind-general":   ("IndusInd General Insurance (formerly Reliance General)", "https://www.indusind.com/general-insurance/"),
        "oriental-insurance": ("Oriental Insurance Company", "https://orientalinsurance.org.in/"),
        "reliance-general":   ("Reliance General Insurance", "https://www.reliancegeneral.co.in/"),
        "royal-sundaram":     ("Royal Sundaram General Insurance", "https://www.royalsundaram.in/"),
        "sbi-general":        ("SBI General Insurance", "https://www.sbigeneral.in/"),
        "star-health":        ("Star Health & Allied Insurance", "https://www.starhealth.in/"),
        "tata-aig":           ("Tata AIG General Insurance", "https://www.tataaig.com/"),
        "user-upload":        ("Your uploaded policies", ""),
    }

    # policy -> source_url (verified at download time)
    policy_urls: dict[tuple[str, str], str] = {}
    by_insurer: dict[str, dict] = {}

    # KI-135 (2026-05-15) — count policies the SAME way /api/policies/all
    # does (extracted/*.json + curated-facts pass-2) so the marketplace badge
    # ALWAYS matches the marketplace card count. Previously this loop read
    # Chroma metadata, which under-counted by ~20 because ~15 curated-facts
    # policies (Activ One, Optima Secure, Reassure 2/3, Health Guard Gold,
    # etc.) are legitimate distinct products that have no Chroma chunks yet,
    # plus ~5 display-name mismatches collapsed two policies into one. After
    # this refactor: badge = cards = 158 / 19.
    # KI-129 + KI-130 invariants still hold (profile + regulatory excluded).
    import json as _json
    from backend.policy_identity import clean_display_policy_name
    _DOCTYPE_RANK_COV = {"wordings": 0, "prospectus": 1, "cis": 2, "brochure": 3}
    _doctype_of_cov = lambda stem: stem.rsplit("__", 1)[1] if "__" in stem else ""
    _product_key_of_cov = lambda pid: pid.rsplit("__", 1)[0] if "__" in pid else pid

    curated_facts = _load_curated_facts()
    sorted_files = sorted(
        settings.EXTRACTED_DIR.glob("*.json"),
        key=lambda fp: (_DOCTYPE_RANK_COV.get(_doctype_of_cov(fp.stem), 99), fp.stem),
    )
    seen_product_keys: set[str] = set()
    seen_policy_ids: set[str] = set()

    # KI-141 (2026-05-15) — pre-compute the alias mapping (curated marketing
    # renames whose source PDF maps to an extracted parent). These curated
    # entries collapse onto the parent card; they DO NOT count separately.
    # Same algorithm as /api/policies/all so the totals stay in sync.
    #
    # KI-142 (2026-05-15, REFACTORED) — UIN-primary invariant: 1 unique UIN
    # = 1 unique marketplace card. Mirrors the /api/policies/all algorithm
    # so the coverage policy_count stays in lockstep with the marketplace
    # card count. See the long-form comment block in that endpoint for the
    # full algorithm rationale.
    extracted_stems_cov = {fp.stem for fp in sorted_files}

    # Phase A — extracted parents claim their UINs first. We also retain the
    # parsed extracted JSON so Phase B can run the KI-145 material-diff check
    # without re-reading from disk.
    uin_to_parent_cov: dict[str, str] = {}
    extracted_uin_cov: dict[str, str] = {}
    extracted_data_cov: dict[str, dict] = {}
    for fp in sorted_files:
        try:
            _d = _json.loads(fp.read_text())
        except Exception:
            continue
        extracted_data_cov[fp.stem] = _d
        _u = _d.get("uin_code")
        if isinstance(_u, dict):
            _u = _u.get("value")
        _u = (_u or "").strip() if isinstance(_u, str) else ""
        if _u:
            extracted_uin_cov[fp.stem] = _u
            uin_to_parent_cov.setdefault(_u, fp.stem)

    direct_parent_cov: dict[str, str] = {}
    curated_canonical_ids_cov: list[str] = []
    # KI-145 — curated entries that failed the material-diffs gate (same UIN
    # or source-PDF as a pass-1 card but >= 2 decision-critical fields
    # disagree). These must emit as standalone pass-2 cards so the coverage
    # policy_count stays in lockstep with /api/policies/all.
    ki145_variant_curated_ids_cov: set[str] = set()

    # Phase B — walk curated entries deterministically (sorted by policy_id).
    for curated_pid, cdata in sorted(curated_facts.items()):
        if curated_pid != cdata.get("policy_id", curated_pid):
            continue
        if any(curated_pid.endswith(f"__{dt}")
               for dt in ("wordings", "brochure", "cis", "prospectus")):
            continue
        curated_canonical_ids_cov.append(curated_pid)

        curated_uin = cdata.get("uin_code")
        if isinstance(curated_uin, dict):
            curated_uin = curated_uin.get("value")
        curated_uin = (curated_uin or "").strip() if isinstance(curated_uin, str) else ""

        parent_id: str | None = None
        if curated_uin and curated_uin in uin_to_parent_cov \
                and uin_to_parent_cov[curated_uin] != curated_pid:
            # KI-145 (2026-05-15) — same UIN ≠ same product. Compare
            # decision-critical fields against the candidate extracted
            # parent; if 2+ disagree (non-null on both sides) this is a
            # VARIANT and stays as its own card. Pure RENAME (< 2 diffs)
            # falls through to the alias-merge as before.
            candidate = uin_to_parent_cov[curated_uin]
            # Candidate may be extracted OR curated — fall back to curated
            # facts when no extracted JSON exists, so the diff has real data.
            cand_data = extracted_data_cov.get(candidate) or curated_facts.get(candidate, {})
            if _ki145_material_diffs(cdata, cand_data) < 2:
                parent_id = candidate
            else:
                ki145_variant_curated_ids_cov.add(curated_pid)
        elif curated_uin:
            # New UIN — claim it. KI-145 spec: UIN unmatched against any
            # extracted parent = standalone. Flag so pass-2 emits even if
            # policy_id is a prefix of a seen extracted id.
            uin_to_parent_cov[curated_uin] = curated_pid
            ki145_variant_curated_ids_cov.add(curated_pid)

        if parent_id is None and not curated_uin:
            # KI-142 (preserved): source-PDF fallback only when curated entry
            # has NO UIN. When UIN is present but unmatched, KI-145 spec
            # mandates standalone — PDF coincidence cannot override.
            fb_parent = _source_pdf_to_policy_id(cdata.get("_primary_source_pdf"))
            if fb_parent and fb_parent in extracted_stems_cov and fb_parent != curated_pid:
                ext_data = extracted_data_cov.get(fb_parent, {})
                if _ki145_material_diffs(cdata, ext_data) < 2:
                    parent_id = fb_parent
                else:
                    ki145_variant_curated_ids_cov.add(curated_pid)

        if parent_id:
            direct_parent_cov[curated_pid] = parent_id

    # Phase C — chain-compress (see /api/policies/all for rationale).
    aliased_curated_ids_cov: set[str] = set()
    parent_pkey_alias_count: dict[str, int] = {}

    def _terminal_parent_cov(start: str) -> str | None:
        seen_chain: set[str] = set()
        cur = start
        while True:
            nxt = direct_parent_cov.get(cur)
            if not nxt:
                return cur if cur != start else None
            if nxt in seen_chain or nxt == start:
                return None
            seen_chain.add(nxt)
            cur = nxt

    for curated_pid in curated_canonical_ids_cov:
        if curated_pid not in direct_parent_cov:
            continue
        terminal = _terminal_parent_cov(curated_pid)
        if not terminal:
            continue
        if terminal in extracted_stems_cov:
            terminal_pkey = _product_key_of_cov(terminal)
        else:
            terminal_pkey = terminal
        aliased_curated_ids_cov.add(curated_pid)
        parent_pkey_alias_count[terminal_pkey] = parent_pkey_alias_count.get(terminal_pkey, 0) + 1

    # by_insurer entries:
    #   products: set of product_keys (matches /api/policies/all card count)
    #   names:    ordered dict of policy_NAME -> first product_key (for sample display)
    #   aliases:  KI-141 — count of curated marketing-rename entries merged
    #             into this insurer's parent cards (for the alias_count field).
    # KI-135 (2026-05-15) — track product_keys (not names) for counting so the
    # ~1 within-insurer policy_name collision (e.g. new-india Floater listed
    # as both extracted + curated_facts) doesn't collapse the count below the
    # marketplace card count. Both representations are still distinct products.

    # Pass 1: extracted JSONs (KI-133 dedup by product_key — wordings wins)
    for fp in sorted_files:
        try:
            data = _json.loads(fp.read_text())
        except Exception:
            continue
        pid = data.get("policy_id", fp.stem)
        seen_policy_ids.add(pid)
        slug = data.get("insurer_slug", "")
        if slug == "regulatory":
            continue
        pkey = _product_key_of_cov(pid)
        if pkey in seen_product_keys:
            continue
        seen_product_keys.add(pkey)
        name = clean_display_policy_name(data.get("policy_name", "") or pid)
        url = data.get("source_pdf_url", "")
        if slug not in by_insurer:
            by_insurer[slug] = {"products": set(), "names": [], "chunks": 0, "aliases": 0}
        by_insurer[slug]["products"].add(pkey)
        # KI-141 — accumulate alias count from the pre-pass
        by_insurer[slug]["aliases"] += parent_pkey_alias_count.get(pkey, 0)
        if name not in by_insurer[slug]["names"]:
            by_insurer[slug]["names"].append(name)
        by_insurer[slug]["chunks"] += 1
        if url and (slug, name) not in policy_urls:
            policy_urls[(slug, name)] = url

    # Pass 2: curated-facts policies that have no extracted counterpart
    for curated_pid, data in curated_facts.items():
        if curated_pid != data.get("policy_id", curated_pid):
            continue  # permutation alias
        if curated_pid in seen_policy_ids:
            continue
        # KI-145 — bypass the startswith dedup for genuine variants (same
        # UIN/source-PDF as a pass-1 card but materially different fields).
        # Otherwise variant cards would be silently dropped here.
        if curated_pid not in ki145_variant_curated_ids_cov \
                and any(eid.startswith(curated_pid + "__") for eid in seen_policy_ids):
            continue
        # KI-141 — skip curated entries that have already been collapsed into
        # a pass-1 parent's alias list.
        if curated_pid in aliased_curated_ids_cov:
            continue
        seen_policy_ids.add(curated_pid)
        slug = data.get("insurer_slug", "")
        if slug == "regulatory":
            continue
        # Curated entries don't have a __doctype suffix, so use the full
        # policy_id as the product_key.
        pkey = curated_pid
        # KI-145 — variants share product_key with their pass-1 sibling
        # (different doctype-stripped stems are identical). Allow them past
        # this dedup so coverage policy_count = marketplace card count.
        if pkey in seen_product_keys and curated_pid not in ki145_variant_curated_ids_cov:
            continue
        seen_product_keys.add(pkey)
        name = clean_display_policy_name(
            data.get("policy_name", "") or curated_pid
        )
        url = data.get("source_pdf_url", "")
        if slug not in by_insurer:
            by_insurer[slug] = {"products": set(), "names": [], "chunks": 0, "aliases": 0}
        # KI-145 — variants share pkey with a pass-1 sibling, so adding the
        # bare pkey to the set would be a no-op (set semantics). Tag variant
        # pkeys with a suffix in the counting set so the per-insurer count
        # increments by 1, matching the marketplace card count.
        if curated_pid in ki145_variant_curated_ids_cov:
            by_insurer[slug]["products"].add(f"{pkey}__ki145variant")
        else:
            by_insurer[slug]["products"].add(pkey)
        # KI-142 — accumulate alias count for curated parents (curated entries
        # that themselves became the claimant of a new UIN, with later curated
        # siblings aliasing onto them).
        by_insurer[slug]["aliases"] += parent_pkey_alias_count.get(pkey, 0)
        if name not in by_insurer[slug]["names"]:
            by_insurer[slug]["names"].append(name)
        by_insurer[slug]["chunks"] += 1
        if url and (slug, name) not in policy_urls:
            policy_urls[(slug, name)] = url

    # #80 — SINGLE SOURCE OF TRUTH. Derive the catalogue counts from the SAME
    # de-duplicated marketplace the cards render from, so the header count can
    # never drift from what the user actually sees (1 product = 1 card
    # everywhere). The old parallel product_key tally double-counted the
    # doctype-sibling permutations the marketplace collapses.
    from collections import Counter as _Counter
    _mp = await policies_all()
    _pc = _Counter(p.insurer_slug for p in _mp.policies)

    insurers_out = []
    for slug, info in sorted(by_insurer.items()):
        # KI-130 — regulatory is not an insurer; never a marketplace card.
        if slug == "regulatory":
            continue
        product_count = _pc.get(slug, 0)
        if product_count == 0:
            continue
        sample_names = sorted(info["names"])[:8]
        name, home_url = insurer_meta.get(slug, (slug, ""))
        sample_entries = [
            PolicyEntry(name=p, source_url=policy_urls.get((slug, p), ""))
            for p in sample_names
        ]
        insurers_out.append(
            InsurerCoverage(
                slug=slug,
                name=name,
                home_url=home_url,
                policy_count=product_count,
                sample_policies=sample_entries,
                alias_count=info.get("aliases", 0),
            )
        )

    return CoverageResponse(
        total_chunks=total,
        total_policies=_mp.total,
        total_insurers=_mp.insurers_indexed,
        insurers=insurers_out,
    )


@app.post("/api/upload-policy", response_model=UploadResponse)
async def upload_policy(
    request: Request,
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
):
    """Accept a user-uploaded PDF policy doc, chunk + embed it, add to the
    quarantine collection (NOT the shared `policies` corpus).

    Each upload is tagged with the caller's session_id so retrieval can scope
    quarantine queries to the uploader only. If no session_id is supplied,
    falls back to "anonymous" for backwards compatibility.
    """
    import re
    import tempfile
    import time as _time
    from pathlib import Path as _PathLib

    t0 = _time.time()
    contents = await file.read()
    if not contents.startswith(b"%PDF"):
        raise HTTPException(400, "File does not look like a PDF (magic bytes wrong).")
    if len(contents) > 25 * 1024 * 1024:
        raise HTTPException(413, "PDF too large (>25 MB). Use a smaller file.")

    sid = session_id or "anonymous"

    # Slugify filename for policy_id
    raw = file.filename or "user_upload.pdf"
    stem = _PathLib(raw).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stem.lower()).strip("-")[:80] or "user-upload"
    policy_id = f"user-upload__{sid[:12]}__{slug}"
    policy_name = stem.replace("_", " ").replace("-", " ").title()

    # Save to disk so ingest can read with pdfplumber
    user_dir = settings.CORPUS_DIR / "user-upload"
    user_dir.mkdir(parents=True, exist_ok=True)
    out_path = user_dir / f"{slug}.pdf"
    out_path.write_bytes(contents)

    # Orphan-file guard (2026-05-16) — the PDF is written to disk BEFORE the
    # 8 security gates run (pdfplumber needs a path). On ANY non-success exit
    # — security reject, empty-text reject, embed failure, bloat trip, the
    # broad 500 catch — the file must NOT be left lying in rag/corpus/
    # user-upload/. `indexed_ok` is flipped True only after a successful
    # quarantine collection.add(); the finally block deletes the file unless
    # it was actually indexed (or short-circuited via the dedupe accept
    # cache, where the bytes are already represented by cached chunks).
    indexed_ok = False

    # Ingest just this one file
    try:
        from rag.ingest import (
            _abort_if_hnsw_bloated,
            chunk_pages,
            get_quarantine_collection,
            read_pdf_pages,
        )
        from backend.providers.local_embeddings import LocalEmbeddings as _Emb
        from backend.security import check_upload, rate_limiter

        pages = read_pdf_pages(out_path)
        # Run 8-gate security check (dedupe + mechanics + encrypted + content +
        # page ceiling + injection + per-session + per-IP rate limit + LLM judge)
        full_text = "\n".join(t for _, t in pages)
        # #47 — UIN net-new dedup: if the uploaded PDF's IRDAI UIN already
        # belongs to a catalogue policy it is NOT net-new — return the
        # existing card instead of indexing a duplicate. `indexed_ok` stays
        # False so the finally block deletes the freshly-written temp file.
        _uin_hit = _match_catalogue_uin(full_text)
        if _uin_hit:
            return UploadResponse(
                policy_id=_uin_hit[0],
                policy_name=_uin_hit[1],
                chunks_added=0,
                pages_indexed=len(pages),
                elapsed_ms=int((_time.time() - t0) * 1000),
                already_in_catalogue=True,
                existing_policy_id=_uin_hit[0],
                existing_policy_name=_uin_hit[1],
            )
        client_ip = (request.client.host if request and request.client else "") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        verdict = await check_upload(
            content=contents,
            extracted_text=full_text,
            page_count=len(pages),
            session_id=sid,
            ip=client_ip,
        )
        if not verdict.accepted:
            # File cleanup handled uniformly in the finally block (orphan
            # guard) — no explicit unlink needed here.
            raise HTTPException(
                400,
                f"Upload rejected by security gates: {', '.join(verdict.reasons[:3])}",
            )
        # If the dedupe gate found this exact (hash, session) already indexed,
        # skip chunking + embedding entirely and return the cached chunk count.
        # The bytes are already represented by the previously-indexed chunks,
        # so this freshly-written duplicate file is redundant — let the
        # finally block delete it (indexed_ok stays False).
        if verdict.cached_chunks is not None:
            return UploadResponse(
                policy_id=policy_id,
                policy_name=policy_name,
                chunks_added=verdict.cached_chunks,
                pages_indexed=len(pages),
                elapsed_ms=int((_time.time() - t0) * 1000),
            )
        # Successful pass — record IP-level upload for rate-limit ledger
        from backend.security import record_ip_upload, record_accept
        import hashlib as _hashlib
        record_ip_upload(client_ip)

        chunks = list(chunk_pages(pages))
        if not chunks:
            raise HTTPException(400, "Could not extract any text from the PDF (scanned image-only?).")

        # Quarantine HNSW bloat guard (2026-05-16) — fail fast BEFORE we
        # spend an embed call if a prior ingest/upload already bloated the
        # on-disk index. The guard scans ALL link_lists.bin under
        # VECTORS_DIR, so it covers both `policies` and the
        # `user_uploads_quarantine` collection. Raises RuntimeError on
        # trip; the broad except below converts it to a clean HTTP 500
        # rather than letting the index grow into a disk-fill incident.
        _abort_if_hnsw_bloated()

        embedder = _Emb()
        texts = [c["text"] for c in chunks]
        vectors = await embedder.embed(texts, input_type="document")

        ids = [f"{policy_id}::chunk{c['chunk_idx']}" for c in chunks]
        metadatas = [
            {
                "policy_id": policy_id,
                "insurer_slug": "user-upload",
                "policy_name": policy_name,
                "doc_type": "user_upload",
                "source_url": "",
                "page_start": c["page_start"],
                "page_end": c["page_end"],
                "chunk_idx": c["chunk_idx"],
                "local_path": str(out_path),
                "session_id": sid,
            }
            for c in chunks
        ]
        collection = get_quarantine_collection()
        # Remove any existing chunks under this policy_id (re-upload case)
        try:
            collection.delete(where={"policy_id": policy_id})
        except Exception:
            pass
        collection.add(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)
        # Index write succeeded — the on-disk file is now legitimately
        # referenced by chunk metadata.local_path; the finally block must
        # NOT delete it.
        indexed_ok = True
        # Post-add bloat guard — catch a bloat THIS upload caused (e.g. a
        # ChromaDB version / batch-size pathology). Mirrors ingest.py's
        # _abort_if_hnsw_bloated() after collection.add().
        _abort_if_hnsw_bloated()
        # TTL bookkeeping — remember when this session last touched the
        # quarantine collection so the periodic purge task can evict its
        # chunks after the configured idle window (default 24h).
        _quarantine_touch(sid, policy_id)
        # Update rate-limit ledger after successful index
        rate_limiter.record_upload(sid, len(chunks))
        # Cache this content hash → chunk count so an identical re-upload in
        # the same session short-circuits via gate_hash_dedupe.
        try:
            sha = _hashlib.sha256(contents).hexdigest()
            record_accept(sha, sid, len(chunks))
        except Exception:
            pass

        # ---- #52: PERSIST + add to THE (global) marketplace ----------------
        # The session-scoped quarantine add above is the immediate, private
        # path. #52 additionally requires the uploaded doc to become a REAL,
        # GRADED, PERSISTENT marketplace card that survives an HF Space
        # restart. So we:
        #   (1) persist the raw PDF + a curated-facts-shaped JSON record +
        #       the chunk payload under the PERSISTENT UPLOADED_DOCS_DIR,
        #   (2) add the SAME chunks to the GLOBAL `policies` Chroma
        #       collection (doc_type='user_upload') so they're retrievable
        #       for everyone — per spec the doc is added to THE marketplace,
        #       so global visibility is intentional; only the uploaded
        #       document itself is exposed, never any session profile,
        #   (3) invalidate the #40 marketplace-grade cache so the new card
        #       grades immediately (the curated record flows through the
        #       EXISTING _marketplace_catalogue Pass-2 + build_scorecard).
        # ANY failure here MUST surface (no silent failure): a 200 that
        # didn't persist would violate the #52 contract.
        from backend import uploaded_docs as _udocs

        _record = _udocs.persist_upload(
            policy_id=policy_id,
            policy_name=policy_name,
            pdf_bytes=contents,
            full_text=full_text,
            chunks=chunks,
            session_id=sid,
        )
        # Global-collection ingest (idempotent — keyed by policy_id).
        from rag.ingest import get_chroma_collection as _get_pol_coll
        _pol = _get_pol_coll()
        _g_ids = [f"{policy_id}::chunk{c['chunk_idx']}" for c in chunks]
        # Use whatever insurer_slug build_record resolved (detected from
        # PDF text via detect_insurer_slug, or UPLOAD_INSURER_SLUG on no
        # match) so chunk metadata + scorecard reviews lookup agree.
        _resolved_insurer_slug = _record.get("insurer_slug", _udocs.UPLOAD_INSURER_SLUG)
        _g_meta = [
            {
                "policy_id": policy_id,
                "insurer_slug": _resolved_insurer_slug,
                "policy_name": policy_name,
                "doc_type": _udocs.UPLOAD_DOC_TYPE,
                "source_url": "",
                "page_start": c["page_start"],
                "page_end": c["page_end"],
                "chunk_idx": c["chunk_idx"],
                # GLOBAL by design — NO session_id on these chunks.
            }
            for c in chunks
        ]
        try:
            _pol.delete(where={"policy_id": policy_id})
        except Exception:  # noqa: BLE001 — nothing to delete on first upload
            pass
        _pol.add(ids=_g_ids, documents=texts, embeddings=vectors, metadatas=_g_meta)
        _abort_if_hnsw_bloated()
        # Bust the #40 grade cache + the corpus-pdf index so the new card
        # appears immediately with a real grade.
        try:
            global _CORPUS_PDF_IDX
            _CORPUS_PDF_IDX = None
            with _MG_LOCK:
                _MG_CACHE["sig"] = None
                _MG_CACHE["index"] = None
        except Exception:  # noqa: BLE001 — cache bust is best-effort
            pass

        # ── Fire LLM-assisted extraction in background (ADR-044) ─────────
        # Same extractor as the catalogued 148. Runs ~30-60s; the upload
        # HTTP response returns now and the frontend polls
        # /api/upload/extraction-status/{policy_id} (see below) to know
        # when the card-bearing chat message should be pushed.
        # Fail-silent: a failed LLM pass leaves the heuristic record
        # intact, so the card still has SOMETHING to show — never blocks
        # the user. NEVER blocks this request.
        try:
            from pathlib import Path as _PathLib2
            _persisted_pdf = _udocs.uploaded_docs_dir() / policy_id / "source.pdf"
            _detected_insurer_name = _record.get(
                "insurer_name",
                _udocs.detected_insurer_name(_resolved_insurer_slug)
                if _resolved_insurer_slug != _udocs.UPLOAD_INSURER_SLUG
                else _udocs.UPLOAD_INSURER_NAME,
            )
            # Pre-stamp "pending" so a frontend poll that arrives BEFORE
            # extract_one_for_upload's first await still sees a known
            # state instead of HTTP 404.
            await _udocs._set_extraction_status(
                policy_id,
                status="pending",
                policy_name=policy_name,
                insurer_slug=_resolved_insurer_slug,
                started_at=None,
                completed_at=None,
                completeness_pct=None,
                overall_grade=None,
                error=None,
            )
            asyncio.create_task(
                _udocs.extract_one_for_upload(
                    policy_id=policy_id,
                    pdf_path=_persisted_pdf,
                    policy_name=policy_name,
                    insurer_slug=_resolved_insurer_slug,
                    insurer_name=_detected_insurer_name,
                )
            )
        except Exception:  # noqa: BLE001 — extraction is async + optional
            pass
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Indexing failed: {type(e).__name__}: {e}")
    finally:
        # Orphan-file guard — delete the on-disk PDF unless it was actually
        # indexed into the quarantine collection. Covers EVERY non-success
        # exit (security reject, empty-text, dedupe short-circuit, embed
        # failure, bloat trip, 500 catch). Best-effort: a cleanup failure
        # must never mask the real response/exception.
        if not indexed_ok:
            try:
                out_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    return UploadResponse(
        policy_id=policy_id,
        policy_name=policy_name,
        chunks_added=len(chunks),
        pages_indexed=len(pages),
        elapsed_ms=int((_time.time() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# GET /api/upload/extraction-status/{policy_id} — frontend poll target
# (ADR-044, 2026-05-27).
#
# After the upload endpoint returns, the chat flow needs to know when
# the background LLM extraction completes so it can push the card-bearing
# assistant message into chat with the FULL data (not the heuristic
# stub). This endpoint exposes _UPLOAD_EXTRACTION_STATUS so the
# frontend can poll every ~3s for up to ~120s.
# ---------------------------------------------------------------------------


class ExtractionStatusResponse(BaseModel):
    policy_id: str
    status: str  # "pending" | "running" | "complete" | "failed" | "unknown"
    policy_name: Optional[str] = None
    insurer_slug: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    completeness_pct: Optional[float] = None
    overall_grade: Optional[str] = None
    error: Optional[str] = None


@app.get(
    "/api/upload/extraction-status/{policy_id}",
    response_model=ExtractionStatusResponse,
)
async def upload_extraction_status(policy_id: str):
    """Return the live status of a per-upload LLM-assisted extraction.

    Returns `status="unknown"` for an unrecognised policy_id (e.g. the
    frontend polled a stale id or a policy that was uploaded on a prior
    container) so the client can stop polling without ambiguity.
    """
    from backend import uploaded_docs as _udocs
    state = _udocs.get_extraction_status(policy_id)
    if not state:
        return ExtractionStatusResponse(policy_id=policy_id, status="unknown")
    return ExtractionStatusResponse(
        policy_id=policy_id,
        status=state.get("status", "unknown"),
        policy_name=state.get("policy_name"),
        insurer_slug=state.get("insurer_slug"),
        started_at=state.get("started_at"),
        completed_at=state.get("completed_at"),
        completeness_pct=state.get("completeness_pct"),
        overall_grade=state.get("overall_grade"),
        error=state.get("error"),
    )


class ScorecardSubScore(BaseModel):
    name: str
    score: int
    summary: str
    signals: list[str]


class ProfileSummaryModel(BaseModel):
    """Deterministic, profile-aware {strengths, caveat} (see
    backend.scorecard.build_profile_summary). Rendered at the TOP of every
    scorecard surface; the frontend falls back to one_liner when strengths
    is empty / insufficient. Optional with a default so every existing
    construction path (compare, insufficient-data) stays valid."""
    strengths: list[str] = Field(default_factory=list)
    caveat: Optional[str] = None


class ProfileCompletenessResponse(BaseModel):
    completeness: float                  # 0.0 - 1.0
    completeness_pct: int                # 0 - 100
    fields_collected: list[str]
    fields_missing: list[str]
    is_personalized: bool                # True if completeness >= threshold
    gate_threshold: float = 0.6
    next_question_hint: Optional[str] = None
    profile: dict = Field(default_factory=dict)  # current profile state for UI to render
    session_id: Optional[str] = None


class ProfileUpdateRequest(BaseModel):
    session_id: str
    name: Optional[str] = None  # KI-077
    age: Optional[int] = None
    dependents: Optional[str] = None
    income_band: Optional[str] = None
    existing_cover_inr: Optional[int] = None
    primary_goal: Optional[str] = None
    location_tier: Optional[str] = None
    parents_to_insure: Optional[bool] = None
    parents_age_max: Optional[int] = None
    parents_has_ped: Optional[bool] = None
    health_conditions: Optional[list[str]] = None
    budget_band: Optional[str] = None
    budget_inr: Optional[int] = None  # #64 — exact ₹/yr from the slider
    # Collected by the profile-builder UI; also present on the Profile
    # dataclass + chat-path save_profile_field. Whitelisted here so
    # POST /api/profile accepts them.
    desired_sum_insured_inr: Optional[int] = None
    copay_pct: Optional[int] = None
    family_medical_history: Optional[list[str]] = None
    smoker: Optional[bool] = None


class SessionResetRequest(BaseModel):
    session_id: str
    drop_profile: bool = False  # True = nuke session entirely; False = clear chat only
    confirm: bool = False  # KI-095 — must be True when drop_profile=True; guards accidental wipes


class SessionResetResponse(BaseModel):
    ok: bool
    session_id: Optional[str] = None  # new session_id when drop_profile=True
    cleared_state: bool


class SessionClearRequest(BaseModel):
    session_id: str


class SessionClearResponse(BaseModel):
    cleared: bool
    new_session_id: str


@app.post("/api/session/clear", response_model=SessionClearResponse)
async def session_clear(req: SessionClearRequest):
    """Clean Clear-chat semantic. Wipes the in-memory session state for the
    supplied session_id and ALWAYS returns a freshly minted UUID the
    frontend must adopt as its new session_id going forward.

    Post-ADR-043 (2026-05-27) there is nothing to preserve across sessions
    — there is no on-disk profile to "leave intact". A clear is a complete
    forget.

    Body : {session_id: str}
    Reply: {cleared: bool, new_session_id: str}
    """
    from backend.session_state import clear_session
    cleared = clear_session(req.session_id) if req.session_id else False
    return SessionClearResponse(
        cleared=cleared,
        new_session_id=uuid.uuid4().hex[:12],
    )


@app.post("/api/session/reset", response_model=SessionResetResponse)
async def session_reset(req: SessionResetRequest):
    """KI-020 — User-facing chat clear / fresh-start toggle.

    Two modes:
      - drop_profile=False: caller (frontend) wipes its own message history; the
        server-side profile is preserved so the next message resumes with what
        the bot already knows. Light-touch "clear visible chat".
      - drop_profile=True: server-side session state (profile + awaiting_question
        + free_form_session flag + on-disk JSON) is deleted entirely. The response
        returns a fresh session_id the frontend should adopt as its new id.
    """
    from backend.session_state import reset_session
    cleared = False
    new_sid: Optional[str] = None
    if req.drop_profile:
        # KI-095 — require explicit confirm=True so a misclick or replayed
        # request cannot wipe a populated session by accident.
        if not req.confirm:
            raise HTTPException(status_code=400, detail="confirm=true required to drop session")
        cleared = reset_session(req.session_id)
        new_sid = uuid.uuid4().hex[:12]
    return SessionResetResponse(ok=True, session_id=new_sid, cleared_state=cleared)


@app.post("/api/profile", response_model=ProfileCompletenessResponse)
async def profile_update(req: ProfileUpdateRequest):
    """Write user-provided profile fields into session_state. Returns the new
    completeness so the frontend can immediately reveal personalized scores.

    ALSO ingests the profile as a chunk into Chroma (doc_type='profile',
    policy_id='profile_<session_id>') so the brain sees user context
    alongside policy + regulatory chunks at retrieval time. This is the
    "profile RAG" architecture — every recommendation grounds in (policy
    text + IRDAI mandate + user's own situation) jointly.
    """
    from backend.scorecard import profile_completeness as _completeness
    from backend.session_state import get_session

    sess = get_session(req.session_id)
    # Update only fields the client explicitly sent (non-None) — keeps partial
    # save flows clean
    for field_name in (
        "name",  # KI-077 — accept name updates from the profile-builder UI
        "age", "dependents", "income_band", "existing_cover_inr", "primary_goal",
        "location_tier", "parents_to_insure", "parents_age_max", "parents_has_ped",
        "health_conditions", "budget_band", "budget_inr",
        "desired_sum_insured_inr", "copay_pct", "family_medical_history", "smoker",
    ):
        v = getattr(req, field_name, None)
        if v in (None, "", []):
            # KI-095 — never clobber a filled field with empty input from the client
            continue
        setattr(sess.profile, field_name, v)
        # KI-196 (ADR-041) — mark the slot as explicitly answered so the
        # completeness scorer recognises it. Without this, builder-form
        # captures land on the profile but the badge still reads 0% because
        # profile_completeness_view now gates on `Profile.asked`.
        if field_name not in sess.profile.asked:
            sess.profile.asked.append(field_name)

    # ADR-043 (2026-05-27) — cross-session persistence + profile_rag
    # upsert removed. The captured fields live only in the in-memory
    # SessionState for this session's lifetime (1 h idle TTL).

    p = sess.profile
    # KI-271 — SLOT_UNION-driven profile_dict (15 fields) so copay_pct +
    # family_medical_history + desired_sum_insured_inr propagate to the
    # save endpoint's response + RAG chunk.
    profile_dict = {
        slot: getattr(p, slot, None) for slot in brain_tools.SLOT_UNION
    }
    # KI-196 (ADR-041) — same answered-only gate as profile_completeness_view.
    answered = set(getattr(p, "asked", []) or [])
    completeness_input = {
        k: (v if k in answered else None) for k, v in profile_dict.items()
    }
    c = _completeness(completeness_input)
    collected = [k for k, v in profile_dict.items() if k in answered and v not in (None, "", [], False)]
    missing = [k for k, v in profile_dict.items() if k not in answered or v in (None, "", [])]

    # profile_rag upsert removed in ADR-043 (2026-05-27). Captured fields
    # remain in the in-memory SessionState only.

    return ProfileCompletenessResponse(
        completeness=c,
        completeness_pct=int(c * 100),
        fields_collected=collected,
        fields_missing=missing,
        is_personalized=c >= 0.6,
        profile=profile_dict,
        session_id=req.session_id,
    )


@app.get("/api/profile/completeness", response_model=ProfileCompletenessResponse)
async def profile_completeness_view(session_id: Optional[str] = None):
    """Returns how much we know about the user. Frontend uses this to gate the
    personalized scorecard render — until completeness >= 0.6 we show the
    insurer-level metrics only, NOT the per-user grade.
    """
    from backend.scorecard import profile_completeness as _completeness
    from backend.session_state import get_session
    from backend.needs_finder import next_question

    if not session_id:
        return ProfileCompletenessResponse(
            completeness=0.0, completeness_pct=0,
            fields_collected=[], fields_missing=[],
            is_personalized=False,
            next_question_hint="Start the chat and tell me about your situation",
        )
    sess = get_session(session_id)
    p = sess.profile
    # KI-271 — profile_dict now built from brain_tools.SLOT_UNION (15 fields)
    # so every captured slot (including B5 desired_sum_insured_inr, D2 copay_pct,
    # D2 family_medical_history) propagates through to /api/profile/completeness,
    # /api/profile/predicted-premium-band, /api/profile/recall-by-name. Prior
    # 12-key hand-roll caused E3 to discover the band endpoint ignoring copay
    # + family entirely.
    profile_dict = {
        slot: getattr(p, slot, None) for slot in brain_tools.SLOT_UNION
    }
    # KI-196 (ADR-041) — Profile completeness gates on Profile.asked.
    answered = set(getattr(p, "asked", []) or [])
    completeness_input = {
        k: (v if k in answered else None) for k, v in profile_dict.items()
    }
    c = _completeness(completeness_input)
    collected = [k for k, v in profile_dict.items() if k in answered and v not in (None, "", [], False)]
    missing = [k for k, v in profile_dict.items() if k not in answered or v in (None, "", [])]
    hint = None
    try:
        # next_question returns the field name (str) of the next missing
        # slot. The frontend uses it as a slot-hint; the actual phrasing
        # is generated by the single-brain LLM.
        hint = next_question(p)
    except Exception:
        pass
    return ProfileCompletenessResponse(
        completeness=c,
        completeness_pct=int(c * 100),
        fields_collected=collected,
        fields_missing=missing,
        is_personalized=c >= 0.6,
        next_question_hint=hint,
        profile=profile_dict,
        session_id=session_id,
    )


class ScorecardResponse(BaseModel):
    policy_id: str
    policy_name: str
    insurer_slug: str
    overall_score: int
    grade: str
    one_liner: str
    sub_scores: list[ScorecardSubScore]
    data_completeness_pct: float
    methodology_link: str
    # True ⇒ this policy had too little structured data to grade honestly.
    # The response is still a valid HTTP-200 ScorecardResponse (grade "—",
    # overall_score 0, empty sub_scores, an honest one_liner) so the frontend
    # renders a truthful "not enough data yet" state instead of the generic
    # Retry fallback or a fabricated grade. Optional w/ default so the
    # existing /api/policies/compare construction (no flag) stays valid.
    insufficient_data: bool = False
    # Deterministic, profile-aware {strengths, caveat} computed on the same
    # pass as the grade. Optional w/ default so every construction path
    # (compare with no summary, insufficient-data) stays valid.
    profile_summary: Optional[ProfileSummaryModel] = None


class CompareEntry(BaseModel):
    policy_id: str
    policy_name: str
    insurer_slug: str
    fields: dict
    scorecard: Optional[ScorecardResponse] = None


class CompareResponse(BaseModel):
    policies: list[CompareEntry]
    field_order: list[str]


class MarketplacePolicy(BaseModel):
    policy_id: str
    policy_name: str
    insurer_slug: str
    insurer_name: str
    insurer_home_url: str
    source_pdf_url: Optional[str] = None
    grade: str
    overall_score: int
    one_liner: str
    data_completeness_pct: float
    # Deterministic, profile-aware {strengths, caveat}. Populated from the
    # SAME build_scorecard pass that produced `grade`. None when the catalogue
    # was built profile-neutrally and no facts qualified.
    profile_summary: Optional[ProfileSummaryModel] = None
    # Headline filterable fields
    min_entry_age: Optional[int] = None
    max_entry_age: Optional[int] = None
    sum_insured_options: list[int] = Field(default_factory=list)
    # #81 — cover presented as a RANGE (min – max), never a discrete ladder
    # or a single deterministic number.
    # SI RATIONALISATION (D1/D3) — sum_insured_options / _min / _max are now
    # the SOURCE-QUOTE-CORROBORATED set only (backend/sum_insured.py). Values
    # the field's own source_quote does not genuinely state are dropped, so
    # the marketplace never shows an SI the policy document doesn't back.
    #   • sum_insured_is_band — True only when the corroborated set is a
    #     genuine continuous band (range language + wide min→max). The
    #     frontend renders "₹X – ₹Y"; otherwise it lists the discrete tiers.
    #   • sum_insured_tiers   — the corroborated discrete plan amounts
    #     (== sum_insured_options; kept as an explicit, named field so the
    #     display contract is unambiguous on the frontend).
    sum_insured_min: Optional[int] = None
    sum_insured_max: Optional[int] = None
    sum_insured_is_band: bool = False
    sum_insured_tiers: list[int] = Field(default_factory=list)
    pre_existing_disease_waiting_months: Optional[int] = None
    initial_waiting_period_days: Optional[int] = None
    maternity_waiting_months: Optional[int] = None
    copayment_pct: Optional[float] = None
    network_hospital_count: Optional[int] = None
    no_claim_bonus_pct: Optional[int] = None
    ayush_coverage: Optional[bool] = None
    maternity_coverage: Optional[bool] = None
    cashless_treatment_supported: Optional[bool] = None
    room_rent_capping: Optional[str] = None
    # #86 — sourced insurer-level network: the official list URL + the
    # official stated count (when the insurer publishes one). Replaces the
    # web-backfilled per-policy network_hospital_count for display.
    network_list_url: Optional[str] = None
    network_count_official: Optional[int] = None
    network_list_is_pdf: bool = False

    # #73/#76 — the curated re-extraction legitimately writes non-numeric
    # honest values (e.g. max_entry_age "No maximum age (Lifelong)", a
    # fractional no_claim_bonus_pct). Previously these raised ValidationError
    # and the ENTIRE policy was dropped from the marketplace. Coerce at the
    # model so any construction path self-heals: keep a parseable number,
    # else degrade that ONE field to None — never drop the policy.
    @field_validator(
        "min_entry_age", "max_entry_age", "pre_existing_disease_waiting_months",
        "initial_waiting_period_days", "maternity_waiting_months",
        "network_hospital_count", "no_claim_bonus_pct",
        "network_count_official", mode="before",
    )
    @classmethod
    def _coerce_optional_int(cls, v):
        if v is None or isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        try:
            return int(round(float(str(v).replace(",", "").strip())))
        except (ValueError, TypeError):
            return None

    @field_validator("copayment_pct", mode="before")
    @classmethod
    def _coerce_optional_float(cls, v):
        if v is None or isinstance(v, bool):
            return None
        try:
            return float(str(v).replace("%", "").replace(",", "").strip())
        except (ValueError, TypeError):
            return None

    @field_validator("sum_insured_options", "sum_insured_tiers", mode="before")
    @classmethod
    def _coerce_int_list(cls, v):
        if not isinstance(v, list):
            return []
        out = []
        for x in v:
            try:
                out.append(int(round(float(str(x).replace(",", "").strip()))))
            except (ValueError, TypeError):
                continue
        return out

    @field_validator(
        "ayush_coverage", "maternity_coverage", "cashless_treatment_supported",
        mode="before",
    )
    @classmethod
    def _coerce_optional_bool(cls, v):
        if v is None or isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "yes", "covered", "y", "1"):
            return True
        if s in ("false", "no", "not covered", "excluded", "n", "0"):
            return False
        return None

    # KI-141 (2026-05-15) — marketing-rename aliases that share the same
    # source PDF (e.g. "Activ One" and "Activ Health" both point to the
    # activ-health-individual__wordings.pdf parent). Default empty list so
    # the field is backward-compatible. Frontend renders these as small
    # "Also known as: X, Y" sub-labels under the parent card title.
    aliases: list[str] = Field(default_factory=list)


class MarketplaceResponse(BaseModel):
    policies: list[MarketplacePolicy]
    total: int
    insurers_indexed: int


@app.get("/api/scorecard/methodology")
async def scorecard_methodology():
    """Transparency endpoint — returns the 6-criterion blueprint with weights,
    consumer rationale, fields driving each sub-score, and regulatory anchors.

    Frontend renders this inside PolicyDetailModal so the user can see exactly
    how the headline number is computed and which of the HealthPolicy schema
    fields feed into which criterion. Both counts below are DERIVED (single
    source of truth) — never hardcode them on the frontend; consume these.
    """
    from backend.scorecard import (
        METHODOLOGY_BLUEPRINT, WEIGHTS, SCORED_FIELDS, grade_for,
    )
    from rag.schema import HealthPolicy
    # grade_thresholds DERIVED from grade_for() — the single source of truth
    # for the frozen cutoffs (2026-05-16). Never restate the numbers here:
    # the old hardcoded "≥85/70–84/…" had drifted out of sync with the
    # recalibrated A≥76/B≥69/C≥61/D≥54/F<54 scoring, so the disclosed bands
    # did not match how grades were actually assigned. Introspecting grade_for
    # makes a future recalibration propagate automatically.
    _band_lo: dict[str, int] = {}
    _band_desc: dict[str, str] = {}
    for _s in range(0, 101):
        _g, _d = grade_for(_s)
        if _g not in _band_lo:
            _band_lo[_g] = _s
            _band_desc[_g] = _d
    _f_cut = min(v for k, v in _band_lo.items() if k != "F")
    _grade_thresholds = {
        g: (f"<{_f_cut} — {_band_desc[g]}" if g == "F"
            else f"≥{_band_lo[g]} — {_band_desc[g]}")
        for g in ("A", "B", "C", "D", "F")
    }
    return {
        "weights": WEIGHTS,
        "scored_fields_count": len(SCORED_FIELDS),
        "total_schema_fields": len(HealthPolicy.model_fields),
        "criteria": METHODOLOGY_BLUEPRINT,
        "grade_thresholds": _grade_thresholds,
        "scoring_approach": (
            "Rules-based (deterministic), no LLM-in-the-loop. Each criterion produces a "
            "0–100 sub-score from concrete schema fields; the overall score is the weighted "
            "average. Weights adapt to user profile when age/parents/budget are known."
        ),
    }


def _build_corpus_url_index() -> dict[str, str]:
    """Parse 40-data/corpus_urls.md and return {policy_id: source_url}. Used to
    backfill source_pdf_url when the LLM extraction didn't capture it."""
    import re as _re
    out: dict[str, str] = {}
    md_path = settings.DATA_DIR / "corpus_urls.md"
    if not md_path.exists():
        return out
    for line in md_path.read_text().splitlines():
        if not line.startswith("|") or "insurer_slug" in line or "---" in line:
            continue
        parts = [p.strip() for p in line.strip("|").split("|")]
        if len(parts) < 5:
            continue
        insurer_slug = parts[0]
        policy_name = parts[2]
        doc_type = parts[3]
        m = _re.search(r"https?://\S+", parts[4])
        if not (insurer_slug and m):
            continue
        url = m.group(0)
        # Primary key — match rag.ingest.policy_id_for: <insurer>__<filename-stem>
        # where filename-stem is the URL's PDF filename without extension.
        url_stem = url.rsplit("/", 1)[-1].rsplit("?", 1)[0].rsplit(".", 1)[0]
        url_slug = _re.sub(r"[^a-z0-9]+", "-", url_stem.lower()).strip("-")
        out[f"{insurer_slug}__{url_slug}"] = url
        # Secondary key — derived from policy_name + doc_type (some extracted
        # JSONs use a name-based slug when the original URL filename differs)
        if policy_name and doc_type:
            name_slug = _re.sub(r"[^a-z0-9]+", "-", policy_name.lower()).strip("-")
            out.setdefault(f"{insurer_slug}__{name_slug}__{doc_type.lower()}", url)
            out.setdefault(f"{insurer_slug}__{name_slug}", url)
    return out


def _load_curated_facts() -> dict[str, dict]:
    """Load the 40-data/policy_facts/*.json curated layer. Each file has a
    `{field: {value, source_pdf_path, source_quote}}` shape. We unwrap to a
    flat `{field: value}` dict for the marketplace endpoint, preserving the
    provenance in a `_facts_provenance` field for transparency.

    KI-141 (2026-05-15) — also computes `_primary_source_pdf`, the most-common
    `source_pdf_path` across this curated entry's fields. Used by both
    /api/policies/all and /api/coverage to alias-merge marketing-rename
    curated entries into their extracted-JSON parent card.

    KI-219 (2026-05-15) — CANONICAL PRECEDENCE. When the curated dir has BOTH
    a canonical (`<insurer>__<product>.json`) and one or more doctype-suffixed
    siblings (`<insurer>__<product>__wordings.json`, `__brochure.json`,
    `__cis.json`, `__prospectus.json`) for the same product, the canonical
    file's content is the source of truth. The suffixed-sibling keys point
    AT the canonical entry so any downstream lookup by either form resolves
    to the richer canonical data. Previously the order of `glob('*.json')`
    + `setdefault` made the loser non-deterministic; the more complete
    canonical entry (e.g. `hdfc-ergo__optima-secure.json` says "No room rent
    cap") was getting shadowed by the suffixed sibling (`...__wordings.json`
    says "Room rent capped at 1%"), collapsing scorecards to 72/100.
    """
    import json as _json
    from collections import Counter
    facts: dict[str, dict] = {}
    facts_dir = settings.DATA_DIR / "policy_facts"
    if not facts_dir.exists():
        return facts

    _DOCTYPE_SUFFIXES = ("__wordings", "__brochure", "__cis", "__prospectus")

    def _flatten(d: dict, fallback_id: str) -> dict:
        policy_id = d.get("policy_id") or fallback_id
        flat: dict = {}
        provenance: dict = {}
        all_source_pdfs: list[str] = []
        for k, v in d.items():
            if k.startswith("_") or k in ("policy_id", "policy_name", "insurer_slug"):
                flat[k] = v
                continue
            if isinstance(v, dict) and "value" in v:
                flat[k] = v["value"]
                if v.get("source_pdf_path") or v.get("source_quote") or v.get("source_url"):
                    provenance[k] = {
                        "source_pdf_path": v.get("source_pdf_path"),
                        "source_quote": v.get("source_quote"),
                        "source_url": v.get("source_url"),
                    }
                if v.get("source_pdf_path"):
                    all_source_pdfs.append(v["source_pdf_path"])
            else:
                flat[k] = v
        flat.setdefault("policy_id", policy_id)
        flat["_facts_provenance"] = provenance
        flat["_primary_source_pdf"] = (
            Counter(all_source_pdfs).most_common(1)[0][0]
            if all_source_pdfs else None
        )
        return flat

    # Pass 1 — load every curated JSON, indexed by its on-disk stem, AND
    # group siblings by their canonical product_key (stem with any trailing
    # __doctype suffix stripped).
    by_stem: dict[str, dict] = {}
    siblings: dict[str, list[tuple[str, bool]]] = {}  # product_key → [(stem, is_canonical), ...]
    for f in sorted(facts_dir.glob("*.json")):
        try:
            d = _json.loads(f.read_text())
        except Exception:
            continue
        stem = f.stem
        flat = _flatten(d, stem)
        by_stem[stem] = flat
        # Determine canonical-ness by FILE STEM (not by policy_id field).
        # A stem ending in one of the four doctype tokens is a non-canonical
        # sibling; everything else is canonical.
        is_canonical = not any(stem.endswith(suf) for suf in _DOCTYPE_SUFFIXES)
        if is_canonical:
            product_key = stem
        else:
            # Strip the matching suffix to find the canonical sibling.
            for suf in _DOCTYPE_SUFFIXES:
                if stem.endswith(suf):
                    product_key = stem[: -len(suf)]
                    break
            else:
                product_key = stem
        siblings.setdefault(product_key, []).append((stem, is_canonical))

    # Pass 2 — for each product_key, pick the canonical entry's flat dict if
    # present; otherwise fall back to the first suffixed sibling (sorted to
    # be deterministic). Then make every sibling key (canonical stem + each
    # __doctype variant + each sibling's own stem + each sibling's
    # policy_id) point at the chosen flat dict so the source-of-truth wins
    # regardless of which key the caller looked up by.
    #
    # KI-251 (2026-05-16) — FIELD-LEVEL canonical precedence. The original
    # KI-219 logic chose ONE entry (canonical) wholesale. That silently
    # dropped real curated data whenever the canonical file had a field
    # extracted as null (`{"value": null, ... "source_quote": "not stated
    # in <pdf>"}`) while a doctype sibling had the genuine value. Concrete
    # incident: `icici-lombard__health-elite-plus.json` (canonical, every
    # field null) shadowed `icici-lombard__health-elite-plus__wordings.json`
    # whose `sum_insured_options` is a real list — so the marketplace card
    # rendered "COVER UP TO —" despite the value existing in the curated
    # layer. Affected 8 products on sum_insured_options + 3 on entry-age.
    #
    # Fix: keep canonical precedence for every field the canonical populates
    # (KI-219 preserved exactly — `hdfc-ergo__optima-secure` "No room rent
    # cap" still wins over its sibling's "1%"), but for any field the
    # canonical leaves null/empty, backfill from the highest-ranked sibling
    # that has a genuine value. Doctype rank: wordings > prospectus > cis >
    # brochure (most authoritative source first). This ONLY surfaces data
    # that already exists verbatim in 40-data/policy_facts — nothing is
    # fabricated; the per-field provenance pointer is backfilled too so the
    # UI still shows the correct source quote for the borrowed field.
    _SIB_FILL_RANK = {"__wordings": 0, "__prospectus": 1, "__cis": 2, "__brochure": 3}

    def _is_empty(val) -> bool:
        return val is None or val == "" or val == [] or val == {}

    # Fields that are pure metadata / structural and must NOT be borrowed
    # across siblings (they describe the chosen entry itself, not a fact).
    _NON_FACT_KEYS = {
        "policy_id", "policy_name", "insurer_slug",
        "_facts_provenance", "_primary_source_pdf",
    }

    for product_key, entries in siblings.items():
        canonical_entries = [s for s, c in entries if c]
        if canonical_entries:
            chosen_stem = canonical_entries[0]
        else:
            chosen_stem = sorted(s for s, _ in entries)[0]
        chosen = dict(by_stem[chosen_stem])

        # Deterministic sibling order for field-level backfill: by doctype
        # authority rank, then stem (stable tiebreak). The chosen stem is
        # excluded — it is already the base.
        def _rank(stem: str) -> tuple:
            for suf, r in _SIB_FILL_RANK.items():
                if stem.endswith(suf):
                    return (r, stem)
            return (99, stem)

        fill_order = sorted(
            (s for s, _ in entries if s != chosen_stem),
            key=_rank,
        )
        if fill_order:
            chosen_prov = dict(chosen.get("_facts_provenance") or {})
            for sib_stem in fill_order:
                sib = by_stem[sib_stem]
                sib_prov = sib.get("_facts_provenance") or {}
                for k, v in sib.items():
                    if k in _NON_FACT_KEYS:
                        continue
                    # Canonical/base value wins whenever it is populated.
                    if not _is_empty(chosen.get(k)):
                        continue
                    if _is_empty(v):
                        continue
                    chosen[k] = v
                    # Carry the borrowed field's provenance so the UI still
                    # shows the correct verbatim source quote for it.
                    if k in sib_prov:
                        chosen_prov[k] = sib_prov[k]
            chosen["_facts_provenance"] = chosen_prov

        # Register the canonical product_key.
        facts[product_key] = chosen
        # Register every doctype-suffix permutation pointing at the chosen
        # flat (back-compat with code that looks up by the suffixed name).
        for suf in _DOCTYPE_SUFFIXES:
            facts.setdefault(f"{product_key}{suf}", chosen)
        # Register every sibling's actual on-disk stem AND policy_id field
        # so callers that already hold a stem-like ID still resolve to the
        # canonical content.
        for sib_stem, _is_can in entries:
            facts[sib_stem] = chosen
            sib_pid = by_stem[sib_stem].get("policy_id")
            if isinstance(sib_pid, str) and sib_pid:
                facts[sib_pid] = chosen

    # #52 — merge PERSISTED user-uploaded docs into the curated layer so each
    # surfaces as a marketplace card via the EXISTING _marketplace_catalogue
    # Pass-2 + build_scorecard path (NO grading re-implementation). Records
    # are already in the curated `{field:{value,source_*}}` shape; run them
    # through the same _flatten so per-field provenance is preserved. They
    # have unique `user-upload__*` policy_ids so they can never collide with
    # a real curated product key. A failure here must NOT break the curated
    # layer for the 200+ real policies — log + continue.
    try:
        from backend import uploaded_docs as _udocs

        for _pid, _rec in _udocs.load_persisted_records().items():
            if not isinstance(_rec, dict):
                continue
            facts[_pid] = _flatten(_rec, _pid)
    except Exception as e:  # noqa: BLE001 — uploaded layer is additive
        logging.warning(
            "uploaded-docs curated merge failed (%s: %s) — "
            "marketplace falls back to corpus-only cards",
            type(e).__name__, e,
        )

    return facts


def _source_pdf_to_policy_id(pdf_path: str | None) -> str | None:
    """KI-141 — map a curated `source_pdf_path` like
    'rag/corpus/aditya-birla/activ-health-individual__wordings.pdf' to the
    extracted-JSON policy_id 'aditya-birla__activ-health-individual__wordings'.

    Returns None if the input is empty/None.
    """
    if not pdf_path:
        return None
    s = pdf_path
    if s.startswith("rag/corpus/"):
        s = s[len("rag/corpus/"):]
    if s.endswith(".pdf"):
        s = s[: -len(".pdf")]
    return s.replace("/", "__")


_INSURER_NET: dict | None = None


def _insurer_network(slug: str) -> dict:
    """#86 — official insurer-level network source (40-data/insurer_network
    .json): the official list URL + the official stated count where the
    insurer publishes one. Sourced, not web-backfilled. Cached."""
    global _INSURER_NET
    if _INSURER_NET is None:
        p = settings.DATA_DIR / "insurer_network.json"
        try:
            _INSURER_NET = (
                json.loads(p.read_text()).get("insurers", {}) if p.exists() else {}
            )
        except Exception:
            _INSURER_NET = {}
    return _INSURER_NET.get(slug, {}) or {}


def _recover_scorecard_facts(sc) -> dict:
    """#48 — port of the frontend parseScorecardFacts. The detail-modal
    snapshot recovers facts (co-pay, PED wait, network, cashless, …) from
    the scorecard's signal strings when the flat policy field is null. The
    marketplace CARD only had the flat fields, so it showed "—" where the
    modal showed a real value. Recover the SAME facts server-side and
    backfill `data` so card == modal everywhere, with no extra client call."""
    import re as _re

    f: dict = {}
    sub = getattr(sc, "sub_scores", None) or []
    for s in sub:
        for raw in getattr(s, "signals", None) or []:
            sig = str(raw).strip()
            low = sig.lower()
            m = _re.search(r"(\d+(?:\.\d+)?)%\s*copay", sig, _re.I)
            if m:
                f["copayment_pct"] = float(m.group(1))
            elif _re.search(r"0% copayment", sig, _re.I):
                f["copayment_pct"] = 0
            m = _re.search(r"(\d+)\s*mo\s*PED\s*waiting", sig, _re.I)
            if m:
                f["pre_existing_disease_waiting_months"] = int(m.group(1))
            m = _re.search(r"([\d,]+)\+?\s*network hospitals", sig, _re.I)
            if m:
                f["network_hospital_count"] = int(m.group(1).replace(",", ""))
            if _re.search(r"cashless supported", low, _re.I):
                f["cashless_treatment_supported"] = True
            elif _re.search(r"no cashless", low, _re.I):
                f["cashless_treatment_supported"] = False
            if _re.search(r"ayush covered", low, _re.I):
                f["ayush_coverage"] = True
            elif _re.search(r"no ayush", low, _re.I):
                f["ayush_coverage"] = False
            if _re.search(r"maternity covered", low, _re.I):
                f["maternity_coverage"] = True
            if _re.search(r"no room rent cap", low, _re.I):
                f["_room_no_cap"] = True
            else:
                rr = _re.search(r"room rent capped:\s*(.+)$", sig, _re.I)
                if rr:
                    f["_room_cap_text"] = rr.group(1).strip()
            m = _re.search(r"entry up to\s*(\d+)", sig, _re.I)
            if m:
                f["max_entry_age"] = int(m.group(1))
    return f


_CORPUS_PDF_IDX: dict[str, str] | None = None


def _corpus_pdf_index() -> dict[str, str]:
    """Every policy in the catalogue exists ONLY because its source PDF was
    downloaded into rag/corpus to build the vectors + policy_facts. This maps
    each policy_id (full id, file stem, AND the #80 dedup-stripped id) to the
    absolute corpus PDF that physically exists on disk — so the marketplace
    can always link the real document even when no public origin URL was ever
    recorded. Wordings/policy docs win over CIS/brochure/prospectus. Built
    once and cached for the process lifetime."""
    global _CORPUS_PDF_IDX
    if _CORPUS_PDF_IDX is not None:
        return _CORPUS_PDF_IDX
    from collections import Counter

    prio = {"wordings": 0, "policy": 1, "cis": 2, "prospectus": 3, "brochure": 4}

    def _rank(path: str) -> int:
        low = path.lower()
        for k, v in prio.items():
            if k in low:
                return v
        return 9

    idx: dict[str, str] = {}
    best: dict[str, int] = {}
    root = settings.CORPUS_DIR.parent.parent
    corpus_root = str(settings.CORPUS_DIR.resolve())
    facts_dir = settings.DATA_DIR / "policy_facts"
    if facts_dir.exists():
        for fp in sorted(facts_dir.glob("*.json")):
            try:
                d = json.loads(fp.read_text())
            except Exception:
                continue
            pid = d.get("policy_id") or fp.stem
            paths = [
                v.get("source_pdf_path")
                for v in d.values()
                if isinstance(v, dict) and v.get("source_pdf_path")
            ]
            if not paths:
                continue
            cand = Counter(paths).most_common(1)[0][0]
            ap = (root / cand).resolve()
            try:
                ok = ap.is_file() and str(ap).startswith(corpus_root)
            except Exception:
                ok = False
            if not ok:
                continue
            rank = _rank(cand)
            keys = {pid, fp.stem}
            for suff in ("__wordings", "__brochure", "__cis", "__prospectus", "__policy"):
                if pid.endswith(suff):
                    keys.add(pid[: -len(suff)])
                    break
            for k in keys:
                if k not in idx or rank < best.get(k, 9):
                    idx[k] = str(ap)
                    best[k] = rank

    # #52 — persisted uploaded docs keep their real PDF in the persistent
    # UPLOADED_DOCS_DIR (NOT rag/corpus). Map their policy_id → that file so
    # the marketplace card's /api/policy-pdf link resolves to the exact
    # document the user uploaded and that the card was graded from.
    try:
        for d in sorted(settings.UPLOADED_DOCS_DIR.glob("*/source.pdf")):
            meta_p = d.parent / "meta.json"
            try:
                pid = json.loads(meta_p.read_text()).get("policy_id") or d.parent.name
            except Exception:  # noqa: BLE001
                pid = d.parent.name
            idx[pid] = str(d.resolve())
    except Exception:  # noqa: BLE001 — uploaded-pdf index is additive
        pass

    _CORPUS_PDF_IDX = idx
    return idx


def _is_credible_pdf_url(u: str | None) -> bool:
    """#87 — a recorded source_pdf_url is only trustworthy as the policy-PDF
    link if it unambiguously points at a document, not an insurer homepage
    or a generic section page (e.g. https://www.sbigeneral.in,
    https://nationalinsurance.nic.co.in/en/health-insurance). When it isn't,
    we prefer the local corpus PDF we definitively have for every policy."""
    if not u:
        return False
    from urllib.parse import urlparse

    try:
        path = (urlparse(u).path or "").lower()
    except Exception:
        return False
    if ".pdf" in path:
        return True
    return any(
        m in path
        for m in ("/documents/", "/dam/", "/download", "/sites/default/files/")
    )


@app.get("/api/policy-pdf/{policy_id}")
def policy_pdf(policy_id: str):
    """Serve the local corpus PDF for a policy — the exact document the
    catalogue, vectors and facts were all built from. Guarantees every one
    of the 148 cards has a working real-PDF link even when no public origin
    URL was ever captured. Path is constrained to rag/corpus."""
    idx = _corpus_pdf_index()
    ap = idx.get(policy_id) or idx.get(policy_id.replace("/", "__"))
    if not ap:
        raise HTTPException(status_code=404, detail="No source PDF for this policy")
    p = Path(ap).resolve()
    # #52 — also allow the persistent uploaded-docs store (the uploaded PDF
    # lives there, not in rag/corpus). Both roots are server-controlled
    # directories; the index only ever maps to files inside one of them, so
    # this stays a strict allowlist (no traversal surface).
    _allowed_roots = (
        str(settings.CORPUS_DIR.resolve()),
        str(settings.UPLOADED_DOCS_DIR.resolve()),
    )
    if not (p.is_file() and any(str(p).startswith(r) for r in _allowed_roots)):
        raise HTTPException(status_code=404, detail="Source PDF not found")
    return FileResponse(
        str(p),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{p.name}"'},
    )


def _merge_curated(extracted: dict, curated: dict | None) -> dict:
    """Curated facts override LLM extraction for every field they populate.
    LLM extraction fills the long tail. Provenance pointers survive in the
    merged dict so the UI can show source quotes per field."""
    if not curated:
        return extracted
    merged = dict(extracted)
    for k, v in curated.items():
        if v is not None and v != "" and v != []:
            merged[k] = v
    return merged


def _si_source_quote(data: dict) -> str:
    """The verbatim source_quote backing this policy's sum_insured_options.

    Every real SI value in the catalogue comes from the curated
    40-data/policy_facts layer (extracted SI is null across the board); the
    flatten step in _load_curated_facts() stores its provenance at
    data["_facts_provenance"]["sum_insured_options"]["source_quote"], which
    _merge_curated() carries through onto the merged dict. We also accept the
    wrapped `{value, source_quote}` shape defensively in case a future
    extraction path leaves the field unflattened.
    """
    prov = (data.get("_facts_provenance") or {}).get("sum_insured_options")
    if isinstance(prov, dict) and prov.get("source_quote"):
        return str(prov["source_quote"])
    raw = data.get("sum_insured_options")
    if isinstance(raw, dict) and raw.get("source_quote"):
        return str(raw["source_quote"])
    return ""


def _rationalise_si(data: dict, si_values: list[int]) -> "_si.SumInsuredView":
    """Apply the deterministic D3 source-quote corroboration filter + D1
    band-vs-tier classification to this policy's SI list. Returns a
    SumInsuredView the marketplace serializer maps onto sum_insured_*.
    """
    return _si.rationalise(si_values, _si_source_quote(data))


def _policy_corroborated_si(policy_id: str | None) -> "_si.SumInsuredView":
    """The corroborated SI view for a single policy_id (D2/D3). Resolves the
    same merged extracted+curated `data` the marketplace serializer sees,
    then runs the source-quote corroboration filter. `kind == "none"` ⇒ the
    policy publishes NO corroborated Sum Insured (drives the D2 disclosure).
    """
    if not policy_id:
        return _si.SumInsuredView(kind="none", tiers=[], min_inr=None, max_inr=None)
    import json as _json
    try:
        curated = _load_curated_facts()
    except Exception:
        curated = {}
    data: dict = {}
    ep = settings.EXTRACTED_DIR / f"{policy_id}.json"
    if ep.exists():
        try:
            data = _json.loads(ep.read_text())
        except Exception:
            data = {}
    cur = curated.get((data.get("policy_id") if data else None) or policy_id) \
        or curated.get(policy_id)
    data = _merge_curated(data, cur) if (data or cur) else {}
    si = data.get("sum_insured_options") or []
    if isinstance(si, list):
        si = [int(x) for x in si
              if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
    else:
        si = []
    return _rationalise_si(data, si)


# Decision-critical fields that distinguish a RENAME (curated entry folds
# onto extracted parent) from a VARIANT (same UIN but materially different
# product — must stay as its own card). Same UIN ≠ same product:
# regulators file one "wordings" PDF that covers multiple marketed variants
# (e.g. ProHealth Prime vs ProHealth Protect both filed under
# MCIHLIP24011V072324; copay/PED/maternity/NCB differ).
_KI145_DIFF_FIELDS: tuple[str, ...] = (
    "copayment_pct",
    "pre_existing_disease_waiting_months",
    "maternity_coverage",
    "maternity_waiting_months",
    "room_rent_capping",
    "restoration_benefit",
    "no_claim_bonus_pct",
    "post_hospitalization_days",
)


def _ki145_extract_value(raw, field: str):
    """Unwrap the value from either scalar OR nested `{value, ...}` shapes.
    For two fields the extracted-side shape is `{covered, ...}` instead of
    `{value, ...}`: maternity_coverage and restoration_benefit. We project
    those onto the boolean `covered` so a curated bool/str compares cleanly
    against the extracted dict's truthiness.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        if "value" in raw:
            return raw.get("value")
        if field in ("maternity_coverage", "restoration_benefit") and "covered" in raw:
            return raw.get("covered")
        # Unknown dict shape — treat as opaque non-null marker so a real
        # diff isn't accidentally suppressed.
        return raw
    return raw


def _ki145_normalize(field: str, val):
    """Coerce field value into a comparable form (numbers as floats, bools as
    bools, strings stripped lower-case). Returns None on null/empty/"" so it
    is consistently skipped in the diff count."""
    if val is None:
        return None
    # Numeric fields
    if field in (
        "copayment_pct",
        "pre_existing_disease_waiting_months",
        "maternity_waiting_months",
        "no_claim_bonus_pct",
        "post_hospitalization_days",
    ):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None
    # Boolean fields
    if field == "maternity_coverage":
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        if isinstance(val, str):
            s = val.strip().lower()
            if s in ("true", "yes", "covered"):
                return True
            if s in ("false", "no", "not covered", "excluded"):
                return False
            return None
        return None
    # Restoration may arrive as bool (extracted .covered), str (curated prose)
    # or dict (already unwrapped above). Treat presence/absence as the signal:
    # a free-text limit phrase = True, explicit False/None = False.
    if field == "restoration_benefit":
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            s = val.strip().lower()
            if not s:
                return None
            if s in ("false", "no", "none", "n/a", "not available"):
                return False
            return True
        return None
    # String fields (room_rent_capping)
    if isinstance(val, str):
        s = val.strip().lower()
        return s or None
    return val


def _ki145_material_diffs(curated: dict, extracted: dict) -> int:
    """Count fields where BOTH sides have non-null values that disagree.
    Null on either side = SKIP (extraction incompleteness, not a real diff).
    >= 2 diffs → VARIANT (keep separate). < 2 → RENAME (alias-merge)."""
    diffs = 0
    for f in _KI145_DIFF_FIELDS:
        cur_v = _ki145_normalize(f, _ki145_extract_value(curated.get(f), f))
        ext_v = _ki145_normalize(f, _ki145_extract_value(extracted.get(f), f))
        if cur_v is None or ext_v is None:
            continue  # extraction incompleteness, not a real diff
        if cur_v != ext_v:
            diffs += 1
    return diffs


def _profile_summary_model(sc) -> Optional[ProfileSummaryModel]:
    """Adapt the scorecard's ProfileSummary dataclass → the API model.

    None-safe (older Scorecard objects / defensive paths may not carry one).
    """
    ps = getattr(sc, "profile_summary", None)
    if ps is None:
        return None
    return ProfileSummaryModel(
        strengths=list(getattr(ps, "strengths", []) or []),
        caveat=getattr(ps, "caveat", None),
    )


def _marketplace_catalogue(user_profile_dict, _collect_scorecards=None):
    """SINGLE SOURCE OF TRUTH for the marketplace card set (#40).

    The recommendation path (brain_tools._scorecard_signal via
    marketplace_grade) and /api/policies/all BOTH derive a policy's
    grade from THIS one computation, so the cited-card grade can never
    diverge from the marketplace card grade — including marketing-rename
    alias / KI-145 variant cards. Body is the former inline endpoint
    logic, moved verbatim; fully synchronous.

    Task #31 (single-source-of-truth, option (a)): when `_collect_scorecards`
    is a dict, it is populated `{surviving_card.policy_id: Scorecard}` with
    the EXACT `Scorecard` object (full 6 sub_scores + profile_summary +
    grade) computed here for each card that survives the final dedup. The
    single /api/policies/{id}/scorecard endpoint serves that object verbatim
    so its profile_summary / grade / sub_scores are byte-identical to the
    /api/policies/all card for the same canonical id BY CONSTRUCTION — both
    flow through this one `build_scorecard` pass on the same chosen sibling's
    `(data, insurer_reviews, profile)`. (The endpoint's old doctype-rank
    sibling-reconstruction picked a DIFFERENT sibling than this catalogue's
    completeness-based `_best` dedup, emitting a different strength set.)
    """
    import json as _json
    # Task #31 — {id(MarketplacePolicy): Scorecard}. Keyed by the card
    # OBJECT's identity (NOT its policy_id string — several pre-dedup `out`
    # entries can share the same curated-canonical policy_id, so a string
    # key would let a lower-completeness sibling's Scorecard clobber the
    # survivor's). After `_best` picks the surviving object we map each
    # survivor's policy_id / canonical `_ident` to ITS OWN Scorecard.
    _sc_by_obj: dict = {} if _collect_scorecards is not None else None
    from backend.scorecard import build_scorecard
    from backend.policy_identity import clean_display_policy_name
    corpus_url_index = _build_corpus_url_index()
    curated_facts = _load_curated_facts()

    # KI-132 (2026-05-15) — expanded insurer_meta to cover all 19 real
    # insurers. Previously only 10 had curated display-names + home URLs,
    # so 9 insurers rendered as raw slugs in the marketplace dropdown
    # (acko, cholamandalam, go-digit, iffco-tokio, national-insurance,
    # oriental-insurance, reliance-general, royal-sundaram, sbi-general).
    insurer_meta = {
        "acko":               ("Acko Health Insurance", "https://www.acko.com/health-insurance/"),
        "aditya-birla":       ("Aditya Birla Health Insurance", "https://www.adityabirlacapital.com/healthinsurance"),
        "bajaj-allianz":      ("Bajaj Allianz General Insurance", "https://www.bajajallianz.com/"),
        "care-health":        ("Care Health Insurance", "https://www.careinsurance.com/"),
        "cholamandalam":      ("Cholamandalam MS General Insurance", "https://www.cholainsurance.com/"),
        "go-digit":           ("Go Digit General Insurance", "https://www.godigit.com/"),
        "hdfc-ergo":          ("HDFC ERGO General Insurance", "https://www.hdfcergo.com/"),
        "icici-lombard":      ("ICICI Lombard General Insurance", "https://www.icicilombard.com/"),
        "iffco-tokio":        ("IFFCO Tokio General Insurance", "https://www.iffcotokio.co.in/"),
        "manipalcigna":       ("ManipalCigna Health Insurance", "https://www.manipalcigna.com/"),
        "national-insurance": ("National Insurance Company", "https://nationalinsurance.nic.co.in/"),
        "new-india":          ("New India Assurance", "https://www.newindia.co.in/"),
        "niva-bupa":          ("Niva Bupa Health Insurance", "https://www.nivabupa.com/"),
        "indusind-general":   ("IndusInd General Insurance (formerly Reliance General)", "https://www.indusind.com/general-insurance/"),
        "oriental-insurance": ("Oriental Insurance Company", "https://orientalinsurance.org.in/"),
        "reliance-general":   ("Reliance General Insurance", "https://www.reliancegeneral.co.in/"),
        "royal-sundaram":     ("Royal Sundaram General Insurance", "https://www.royalsundaram.in/"),
        "sbi-general":        ("SBI General Insurance", "https://www.sbigeneral.in/"),
        "star-health":        ("Star Health & Allied Insurance", "https://www.starhealth.in/"),
        "tata-aig":           ("Tata AIG General Insurance", "https://www.tataaig.com/"),
    }

    def _coerce_bool(v):
        if isinstance(v, dict) and "covered" in v: return v.get("covered")
        if isinstance(v, bool): return v
        return None

    # Build a unified policy set: every extracted JSON + every curated facts
    # JSON that doesn't have an extracted counterpart yet. This way, even
    # policies whose LLM extraction failed still surface in the marketplace
    # with their human-curated data.
    #
    # KI-133 (2026-05-15) — dedup marketplace cards to ONE per policy product
    # (not one per PDF document). Previously wordings + brochure + cis of
    # the same product created three separate cards, ballooning the
    # marketplace from 138 products to ~209 cards and confusing users who
    # see "138 POLICIES" in the badge but 209 cards below.
    #
    # Algorithm: sort source filenames so the canonical "wordings" variant
    # is encountered first for each product, then dedup by stripped
    # policy_id (everything before the trailing __<doctype>).
    # Doctype preference: wordings > prospectus > cis > brochure > anything
    # else (alphabetical fallback).
    _DOCTYPE_RANK = {"wordings": 0, "prospectus": 1, "cis": 2, "brochure": 3}
    def _doctype_of(stem: str) -> str:
        return stem.rsplit("__", 1)[1] if "__" in stem else ""
    def _product_key_of(policy_id: str) -> str:
        # Strip trailing __<doctype> so wordings/brochure/cis of the same
        # product collapse to a single key.
        return policy_id.rsplit("__", 1)[0] if "__" in policy_id else policy_id

    sorted_files = sorted(
        settings.EXTRACTED_DIR.glob("*.json"),
        key=lambda fp: (_DOCTYPE_RANK.get(_doctype_of(fp.stem), 99), fp.stem),
    )

    # KI-141 (2026-05-15) — alias-dedup pre-pass. Curated "marketing rename"
    # entries that re-describe the SAME IRDAI-filed product collapse onto a
    # single marketplace card; the marketing names surface as `aliases`.
    #
    # KI-142 (2026-05-15, REFACTORED) — UIN-primary invariant: 1 unique UIN
    # = 1 unique marketplace card. The PDF-based gate is now a fallback for
    # entries that lack a UIN.
    #
    # Algorithm (two phases so PDF-backed extracted entries always claim
    # their UIN before any curated rename does):
    #   Phase A: walk extracted/*.json (sorted by doctype rank, then stem).
    #     Each extracted parent claims its uin_code into `uin_to_parent`.
    #   Phase B: walk curated_facts (sorted by policy_id for determinism).
    #     For each canonical curated entry (skip lookup-permutation aliases
    #     and entries that ARE __wordings/__brochure/__cis themselves):
    #       1. Read curated UIN (scalar OR nested .value form).
    #       2. If UIN non-empty AND already in `uin_to_parent` (claimant !=
    #          self) → alias of that parent.
    #       3. Else if UIN non-empty → claim it (so subsequent curated
    #          siblings with the same UIN alias onto THIS entry in pass 2).
    #       4. Else (UIN empty) OR (UIN had no prior claimant) → fall back
    #          to the source_pdf gate: if `_primary_source_pdf` maps to an
    #          extracted parent stem, alias under that parent.
    #       5. Otherwise the curated entry stays as a standalone card.
    #
    # Multi-variant wordings PDFs with a single filed UIN (e.g.
    # manipalcigna prohealth-insurance-all-variants.pdf — the PDF text
    # confirms only ONE UIN `MCIHLIP24011V072324` is filed for that
    # product) correctly collapse all sub-product curated entries onto one
    # card. Distinct-UIN siblings under a shared PDF would surface as
    # separate cards because their UINs claim independent parents.
    extracted_stems = {fp.stem for fp in sorted_files}

    # Phase A — extracted parents claim their UINs first. We also retain the
    # parsed extracted JSON so Phase B can run the KI-145 material-diff check
    # without re-reading from disk.
    uin_to_parent: dict[str, str] = {}
    extracted_uin: dict[str, str] = {}  # kept for downstream introspection
    extracted_data: dict[str, dict] = {}
    for fp in sorted_files:
        try:
            _d = _json.loads(fp.read_text())
        except Exception:
            continue
        extracted_data[fp.stem] = _d
        _u = _d.get("uin_code")
        if isinstance(_u, dict):
            _u = _u.get("value")
        _u = (_u or "").strip() if isinstance(_u, str) else ""
        if _u:
            extracted_uin[fp.stem] = _u
            uin_to_parent.setdefault(_u, fp.stem)

    # Direct-parent map for each curated entry (built in Phase B), then
    # chain-compressed in Phase C so transitive aliases (e.g. activ-one →
    # activ-health → activ-health-individual__wordings) flatten onto the
    # ultimate extracted parent.
    direct_parent: dict[str, str] = {}
    curated_canonical_ids: list[str] = []
    # KI-145 — curated entries whose UIN matched a candidate parent but
    # failed the material-diffs gate (>= 2 decision-critical fields disagree
    # with the parent's extracted JSON). These are genuine variants that
    # must emit as standalone cards in pass 2 even when their policy_id is
    # a prefix of a seen extracted policy_id (the old startswith-skip would
    # otherwise drop them silently).
    ki145_variant_curated_ids: set[str] = set()

    # Phase B — walk curated entries deterministically (sorted by policy_id).
    for curated_policy_id, cdata in sorted(curated_facts.items()):
        # Skip the __wordings/__brochure/__cis lookup-permutation aliases
        # that _load_curated_facts adds for hit-rate (canonical policy_id is
        # stored in the JSON's "policy_id" field).
        if curated_policy_id != cdata.get("policy_id", curated_policy_id):
            continue
        # Skip curated entries that ARE their own __wordings/__brochure/__cis
        # (doctype-permutation curated files, not marketing renames; pass-2
        # dedup handles them via the seen_policy_ids prefix check).
        if any(curated_policy_id.endswith(f"__{dt}")
               for dt in ("wordings", "brochure", "cis", "prospectus")):
            continue
        curated_canonical_ids.append(curated_policy_id)

        # Read curated UIN (scalar OR nested {value, source_pdf_path, ...}).
        curated_uin = cdata.get("uin_code")
        if isinstance(curated_uin, dict):
            curated_uin = curated_uin.get("value")
        curated_uin = (curated_uin or "").strip() if isinstance(curated_uin, str) else ""

        parent_id: str | None = None
        if curated_uin and curated_uin in uin_to_parent \
                and uin_to_parent[curated_uin] != curated_policy_id:
            # KI-145 (2026-05-15) — UIN-primary path with smart variant
            # detection. Same UIN does NOT guarantee same product: a single
            # regulator-filed PDF often covers multiple marketed variants
            # (e.g. ProHealth Prime vs ProHealth Protect both filed under
            # MCIHLIP24011V072324; activ-assure-diamond curated vs extracted
            # disagree on PED/NCB). Compare 8 decision-critical fields; if
            # 2+ disagree on non-null values, treat as a VARIANT and keep
            # this curated entry as its own card. < 2 = pure rename → merge.
            candidate = uin_to_parent[curated_uin]
            # Candidate may be an extracted stem OR a previously-claimed
            # curated entry. Look up extracted JSON first; fall back to the
            # candidate's curated facts so the diff has real data to compare.
            cand_data = extracted_data.get(candidate) or curated_facts.get(candidate, {})
            if _ki145_material_diffs(cdata, cand_data) < 2:
                parent_id = candidate
            else:
                ki145_variant_curated_ids.add(curated_policy_id)
        elif curated_uin:
            # New UIN — this curated entry becomes the claimant so any
            # later curated sibling with the same UIN aliases onto it. Per
            # KI-145 spec ("if UIN doesn't match any extracted parent →
            # treat as standalone"), also flag this entry so pass-2 emits
            # it even when its policy_id is a prefix of a seen extracted id.
            uin_to_parent[curated_uin] = curated_policy_id
            ki145_variant_curated_ids.add(curated_policy_id)

        if parent_id is None and not curated_uin:
            # KI-142 (preserved): source-PDF fallback only fires for curated
            # entries with NO UIN. When UIN is present but unmatched, the
            # KI-145 spec mandates standalone — source-PDF coincidence MUST
            # NOT override the UIN-mismatch signal.
            fb_parent = _source_pdf_to_policy_id(cdata.get("_primary_source_pdf"))
            if fb_parent and fb_parent in extracted_stems and fb_parent != curated_policy_id:
                ext_data = extracted_data.get(fb_parent, {})
                if _ki145_material_diffs(cdata, ext_data) < 2:
                    parent_id = fb_parent
                else:
                    ki145_variant_curated_ids.add(curated_policy_id)

        if parent_id:
            direct_parent[curated_policy_id] = parent_id

    # Phase C — chain-compress direct_parent so every curated alias points
    # at its terminal parent (an extracted stem, or a curated parent that
    # itself has no parent). Detect cycles defensively. After compression
    # we emit one alias entry per curated descendant onto the terminal
    # parent's product_key.
    parent_pkey_aliases: dict[str, list[str]] = {}
    aliased_curated_ids: set[str] = set()

    def _terminal_parent(start: str) -> str | None:
        """Walk direct_parent until we hit an extracted stem or a curated id
        with no further parent. Returns None on cycle (defensive)."""
        seen_chain: set[str] = set()
        cur = start
        while True:
            nxt = direct_parent.get(cur)
            if not nxt:
                return cur if cur != start else None
            if nxt in seen_chain or nxt == start:
                return None  # cycle — drop the alias attempt
            seen_chain.add(nxt)
            cur = nxt

    for curated_policy_id in curated_canonical_ids:
        if curated_policy_id not in direct_parent:
            continue
        terminal = _terminal_parent(curated_policy_id)
        if not terminal:
            continue
        # Alias-target product_key: extracted stems use _product_key_of()
        # (strips __doctype). Curated terminals use the policy_id directly.
        if terminal in extracted_stems:
            terminal_pkey = _product_key_of(terminal)
        else:
            terminal_pkey = terminal
        alias_name = clean_display_policy_name(
            curated_facts.get(curated_policy_id, {}).get("policy_name")
            or curated_policy_id
        )
        parent_pkey_aliases.setdefault(terminal_pkey, []).append(alias_name)
        aliased_curated_ids.add(curated_policy_id)

    seen_product_keys: set[str] = set()
    seen_policy_ids: set[str] = set()
    out = []

    # Pass 1: existing extracted policies (merged with curated overrides)
    for fp in sorted_files:
        try:
            data = _json.loads(fp.read_text())
        except Exception:
            continue
        policy_id_local = data.get("policy_id", fp.stem)
        curated_for_this = curated_facts.get(policy_id_local) or curated_facts.get(fp.stem)
        data = _merge_curated(data, curated_for_this)
        seen_policy_ids.add(policy_id_local)
        slug = data.get("insurer_slug", "")
        # Regulatory is not an insurer; drop entirely from the marketplace.
        # IRDAI/NHA docs are still retrieved and cited in chat answers, they
        # just don't appear as marketplace cards.
        if slug == "regulatory":
            continue
        # Dedup by product (insurer__product), so the wordings PDF wins and
        # the brochure/cis variants don't generate duplicate cards. Pass-1
        # sort order guarantees wordings comes first.
        product_key = _product_key_of(policy_id_local)
        if product_key in seen_product_keys:
            continue
        seen_product_keys.add(product_key)
        name, home = insurer_meta.get(slug, (slug, ""))
        # Get insurer reviews if available for the scorecard
        ir = None
        if slug:
            rp = settings.DATA_DIR / "reviews" / f"{slug}.json"
            if rp.exists():
                try: ir = _json.loads(rp.read_text())
                except Exception: pass
        sc = build_scorecard(data, insurer_reviews=ir, profile=user_profile_dict)
        # #48 — recover facts from the scorecard so the flat marketplace
        # fields (hence the CARD) match the detail-modal snapshot. Only
        # fill nulls; never overwrite a real extracted value.
        _rf = _recover_scorecard_facts(sc)
        for _dk in (
            "pre_existing_disease_waiting_months", "copayment_pct",
            "network_hospital_count", "cashless_treatment_supported",
            "ayush_coverage", "maternity_coverage", "max_entry_age",
        ):
            if data.get(_dk) is None and _rf.get(_dk) is not None:
                data[_dk] = _rf[_dk]
        if not data.get("room_rent_capping"):
            if _rf.get("_room_no_cap"):
                data["room_rent_capping"] = "No room rent cap"
            elif _rf.get("_room_cap_text"):
                data["room_rent_capping"] = _rf["_room_cap_text"]

        si = data.get("sum_insured_options") or []
        if isinstance(si, list):
            si = [int(x) for x in si if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
        else:
            si = []
        # D3 — drop every SI value the field's own source_quote does not
        # genuinely state, then D1-classify the corroborated set as a
        # continuous band or discrete tiers. sum_insured_options/_min/_max
        # are now the CORROBORATED set (no fabrication), so the slider
        # filter + range display stay honest by construction.
        _siv = _rationalise_si(data, si)
        si = _siv.tiers

        try:
            policy_id = data.get("policy_id", fp.stem)
            # Backfill source_pdf_url from corpus_urls.md when extraction didn't
            # populate it. Try exact policy_id match first, then key permutations.
            # #87 — prefer a CREDIBLE public document URL; otherwise use the
            # local corpus PDF we definitively have for every policy (served
            # via /api/policy-pdf). A homepage/section URL is never trusted
            # over the real document. Never an empty link.
            _pidx = _corpus_pdf_index()
            _cand = (
                data.get("source_pdf_url")
                or corpus_url_index.get(policy_id)
                or corpus_url_index.get(fp.stem)
                or ""
            )
            _local = (
                f"/api/policy-pdf/{policy_id}"
                if (_pidx.get(policy_id) or _pidx.get(fp.stem))
                else ""
            )
            source_pdf_url = (
                _cand if _is_credible_pdf_url(_cand) else (_local or _cand)
            )
            _mp = MarketplacePolicy(
                policy_id=policy_id,
                policy_name=clean_display_policy_name(
                    data.get("policy_name", fp.stem)
                ),
                insurer_slug=slug,
                insurer_name=name,
                insurer_home_url=home,
                source_pdf_url=source_pdf_url,
                grade=sc.grade,
                overall_score=sc.overall_score,
                one_liner=sc.one_liner,
                data_completeness_pct=sc.data_completeness_pct,
                profile_summary=_profile_summary_model(sc),
                min_entry_age=data.get("min_entry_age"),
                max_entry_age=data.get("max_entry_age"),
                sum_insured_options=si,
                sum_insured_min=_siv.min_inr,
                sum_insured_max=_siv.max_inr,
                sum_insured_is_band=_siv.is_band,
                sum_insured_tiers=si,
                pre_existing_disease_waiting_months=data.get("pre_existing_disease_waiting_months"),
                initial_waiting_period_days=data.get("initial_waiting_period_days"),
                maternity_waiting_months=data.get("maternity_waiting_months"),
                copayment_pct=data.get("copayment_pct") if isinstance(data.get("copayment_pct"), (int, float)) else None,
                network_hospital_count=data.get("network_hospital_count"),
                no_claim_bonus_pct=data.get("no_claim_bonus_pct"),
                ayush_coverage=_coerce_bool(data.get("ayush_coverage")),
                maternity_coverage=_coerce_bool(data.get("maternity_coverage")),
                cashless_treatment_supported=_coerce_bool(data.get("cashless_treatment_supported")),
                room_rent_capping=data.get("room_rent_capping") if isinstance(data.get("room_rent_capping"), str) else None,
                network_list_url=_insurer_network(slug).get("network_list_url"),
                network_count_official=_insurer_network(slug).get("stated_count"),
                network_list_is_pdf=bool(_insurer_network(slug).get("is_pdf")),
                # KI-141 — merge marketing-rename curated entries onto this
                # parent card. Sorted for deterministic output.
                aliases=sorted(parent_pkey_aliases.get(product_key, [])),
            )
            out.append(_mp)
            if _sc_by_obj is not None:
                # Task #31 — bind THIS card object to the exact Scorecard
                # built above on the catalogue's chosen sibling
                # `data`/`ir`/profile. Object-keyed so it survives the
                # post-dedup mapping unambiguously.
                _sc_by_obj[id(_mp)] = sc
        except Exception as e:
            # One malformed extraction should not kill the whole feed
            print(f"[marketplace] skipping {fp.name}: {type(e).__name__}: {str(e)[:120]}")
            continue

    # Pass 2: curated policies that don't yet have an LLM extraction.
    # These come straight from 40-data/policy_facts/*.json — fully human-curated
    # with verbatim source quotes per field.
    for curated_policy_id, data in curated_facts.items():
        # Skip permutation keys (we set __wordings / __brochure / __cis aliases
        # in _load_curated_facts to maximise the lookup hit-rate in pass 1)
        if curated_policy_id != data.get("policy_id", curated_policy_id):
            continue
        if curated_policy_id in seen_policy_ids:
            continue
        # Also skip if any extracted ID matches with a suffix — UNLESS this
        # curated entry was classified as a KI-145 variant (same UIN/source-PDF
        # as a pass-1 card but materially different decision-critical fields).
        # Variants MUST surface as their own marketplace card; the legacy
        # startswith dedup would otherwise drop them silently.
        if curated_policy_id not in ki145_variant_curated_ids \
                and any(eid.startswith(curated_policy_id + "__") for eid in seen_policy_ids):
            continue
        # KI-141 — skip curated entries that have already been collapsed onto
        # a pass-1 parent card via the aliases mechanism (e.g. Activ One →
        # Activ Health Individual Wordings).
        if curated_policy_id in aliased_curated_ids:
            continue
        seen_policy_ids.add(curated_policy_id)
        slug = data.get("insurer_slug", "")
        # KI-208 (2026-05-15) — defensive symmetry with pass-1 (line 1842): any
        # curated_facts entry with insurer_slug=='regulatory' must NOT surface
        # as a marketplace card. Today no curated regulatory docs exist, but
        # adding the filter here closes a future-leak vector if an operator
        # accidentally curates an IRDAI/NHA fact-sheet under 40-data/policy_facts.
        if slug == "regulatory":
            continue
        name, home = insurer_meta.get(slug, (slug, ""))
        # Insurer reviews for scorecard
        ir = None
        if slug:
            rp = settings.DATA_DIR / "reviews" / f"{slug}.json"
            if rp.exists():
                try:
                    ir = _json.loads(rp.read_text())
                except Exception:
                    pass
        sc = build_scorecard(data, insurer_reviews=ir, profile=user_profile_dict)
        # #48 — recover facts from the scorecard so the flat marketplace
        # fields (hence the CARD) match the detail-modal snapshot. Only
        # fill nulls; never overwrite a real extracted value.
        _rf = _recover_scorecard_facts(sc)
        for _dk in (
            "pre_existing_disease_waiting_months", "copayment_pct",
            "network_hospital_count", "cashless_treatment_supported",
            "ayush_coverage", "maternity_coverage", "max_entry_age",
        ):
            if data.get(_dk) is None and _rf.get(_dk) is not None:
                data[_dk] = _rf[_dk]
        if not data.get("room_rent_capping"):
            if _rf.get("_room_no_cap"):
                data["room_rent_capping"] = "No room rent cap"
            elif _rf.get("_room_cap_text"):
                data["room_rent_capping"] = _rf["_room_cap_text"]
        si = data.get("sum_insured_options") or []
        if isinstance(si, list):
            si = [int(x) for x in si if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
        else:
            si = []
        # D3/D1 — same source-quote corroboration + band/tier classification
        # as pass 1 (curated-only products take this branch).
        _siv = _rationalise_si(data, si)
        si = _siv.tiers
        try:
            # #87 — credible doc URL preferred, else the guaranteed-real
            # local corpus PDF; a homepage/section URL is never trusted.
            _pidx = _corpus_pdf_index()
            _cand = (
                data.get("source_pdf_url")
                or corpus_url_index.get(curated_policy_id)
                or corpus_url_index.get(f"{curated_policy_id}__wordings")
                or ""
            )
            _local = (
                f"/api/policy-pdf/{curated_policy_id}"
                if _pidx.get(curated_policy_id)
                else ""
            )
            source_pdf_url = (
                _cand if _is_credible_pdf_url(_cand) else (_local or _cand)
            )
            _mp = MarketplacePolicy(
                policy_id=curated_policy_id,
                policy_name=clean_display_policy_name(
                    data.get("policy_name", curated_policy_id)
                ),
                insurer_slug=slug,
                insurer_name=name,
                insurer_home_url=home,
                source_pdf_url=source_pdf_url,
                grade=sc.grade,
                overall_score=sc.overall_score,
                one_liner=sc.one_liner,
                data_completeness_pct=sc.data_completeness_pct,
                profile_summary=_profile_summary_model(sc),
                min_entry_age=data.get("min_entry_age"),
                max_entry_age=data.get("max_entry_age"),
                sum_insured_options=si,
                sum_insured_min=_siv.min_inr,
                sum_insured_max=_siv.max_inr,
                sum_insured_is_band=_siv.is_band,
                sum_insured_tiers=si,
                pre_existing_disease_waiting_months=data.get("pre_existing_disease_waiting_months"),
                initial_waiting_period_days=data.get("initial_waiting_period_days"),
                maternity_waiting_months=data.get("maternity_waiting_months"),
                copayment_pct=data.get("copayment_pct") if isinstance(data.get("copayment_pct"), (int, float)) else None,
                network_hospital_count=data.get("network_hospital_count"),
                no_claim_bonus_pct=data.get("no_claim_bonus_pct"),
                ayush_coverage=_coerce_bool(data.get("ayush_coverage")),
                maternity_coverage=_coerce_bool(data.get("maternity_coverage")),
                cashless_treatment_supported=_coerce_bool(data.get("cashless_treatment_supported")),
                room_rent_capping=data.get("room_rent_capping") if isinstance(data.get("room_rent_capping"), str) else None,
                network_list_url=_insurer_network(slug).get("network_list_url"),
                network_count_official=_insurer_network(slug).get("stated_count"),
                network_list_is_pdf=bool(_insurer_network(slug).get("is_pdf")),
                # KI-142 — curated entries can ALSO be UIN-claimants when no
                # extracted parent owns their UIN. In that case their later
                # curated siblings alias onto them and surface here.
                aliases=sorted(parent_pkey_aliases.get(curated_policy_id, [])),
            )
            out.append(_mp)
            if _sc_by_obj is not None:
                # Task #31 — exact Scorecard for this curated-only card,
                # bound to the card object (see Pass-1 rationale).
                _sc_by_obj[id(_mp)] = sc
        except Exception as e:
            print(f"[marketplace] skipping curated {curated_policy_id}: {type(e).__name__}: {str(e)[:120]}")
            continue

    # #80 — final safety dedup. The UIN/PDF gate above can still leak the
    # SAME logical product as both `insurer__product` and a doctype sibling
    # (`insurer__product__wordings|brochure|cis|prospectus|policy`). Collapse
    # to ONE card per product identity (richer entry wins; aliases merged) so
    # the marketplace never shows a plan twice — 1 product = 1 card.
    _DOCT = ("wordings", "brochure", "cis", "prospectus", "policy")

    def _ident(pid: str) -> str:
        for dt in _DOCT:
            if pid.endswith(f"__{dt}"):
                return pid[: -(len(dt) + 2)]
        return pid

    _best: dict[str, MarketplacePolicy] = {}
    for p in out:
        k = _ident(p.policy_id)
        prev = _best.get(k)
        if prev is None:
            _best[k] = p
            continue
        s = (p.data_completeness_pct, len(p.sum_insured_options),
             1 if p.policy_id == k else 0)
        ps = (prev.data_completeness_pct, len(prev.sum_insured_options),
              1 if prev.policy_id == k else 0)
        if s > ps:
            p.aliases = sorted(set(p.aliases) | set(prev.aliases))
            _best[k] = p
        else:
            prev.aliases = sorted(set(prev.aliases) | set(p.aliases))
    deduped = list(_best.values())

    if _collect_scorecards is not None:
        # Task #31 — publish ONLY the post-dedup survivors' Scorecards,
        # each survivor mapped (by OBJECT identity) to ITS OWN Scorecard,
        # keyed by the survivor's policy_id AND its canonical
        # `_ident(policy_id)` so /api/policies/{id}/scorecard resolves a
        # doctype-suffixed / curated-canonical id onto the SAME card the
        # catalogue serves — the exact `Scorecard` object built here on the
        # catalogue's chosen sibling. Parity holds by construction (one
        # build_scorecard pass feeds both surfaces). Exact policy_id wins
        # over the canonical-ident fallback (setdefault) so a precise id is
        # never shadowed by a sibling sharing its _ident.
        for p in deduped:
            sc = _sc_by_obj.get(id(p))
            if sc is None:
                continue
            _collect_scorecards[p.policy_id] = sc
            _collect_scorecards.setdefault(_ident(p.policy_id), sc)
    return deduped

@app.get("/api/policies/all", response_model=MarketplaceResponse)
async def policies_all(session_id: Optional[str] = None):
    """The marketplace data feed — every extracted policy + scorecard + filterable fields.

    When session_id is provided AND the session has a profile populated to
    ≥0.6 completeness, every policy is scored against THAT profile (dynamic
    per-user grade). Otherwise we score with the generic baseline weights.
    """
    import json as _json
    from backend.scorecard import build_scorecard, profile_completeness as _completeness
    from backend.session_state import get_session as _get_sess

    # Pull user profile if we have one. KI-271 — drive the profile dict off
    # brain_tools.SLOT_UNION (via union_snapshot) so every captured slot —
    # including copay_pct, desired_sum_insured_inr, family_medical_history,
    # health_conditions, age — flows into build_scorecard's profile-aware
    # {strengths, caveat} generator (task #31). union_snapshot already drops
    # empty/None/[] slots, so presence == captured.
    user_profile_dict: Optional[dict] = None
    if session_id:
        sess = _get_sess(session_id)
        p = sess.profile
        profile_dict = brain_tools.union_snapshot(p)
        # parents_* are NOT in SLOT_UNION's snapshot if False/None but the
        # weight-tuner reads parents_to_insure/parents_age_max/parents_has_ped
        # explicitly — carry them through (None-safe) without overwriting a
        # snapshot value.
        for _pf in ("parents_to_insure", "parents_age_max", "parents_has_ped"):
            if _pf not in profile_dict:
                _v = getattr(p, _pf, None)
                if _v is not None:
                    profile_dict[_pf] = _v
        if _completeness(profile_dict) >= 0.6:
            user_profile_dict = profile_dict

    deduped = _marketplace_catalogue(user_profile_dict)
    return MarketplaceResponse(
        policies=deduped,
        total=len(deduped),
        insurers_indexed=len({p.insurer_slug for p in deduped}),
    )


@app.get("/api/policies/compare", response_model=CompareResponse)
async def compare_policies(policy_ids: list[str] = None):
    """Side-by-side comparison of 2-4 policies with their scorecards + field diffs."""
    import json as _json
    from backend.scorecard import build_scorecard
    from backend.policy_identity import clean_display_policy_name

    if not policy_ids:
        from fastapi import Query
        raise HTTPException(400, "Provide policy_ids as repeated query params")
    if len(policy_ids) < 2 or len(policy_ids) > 4:
        raise HTTPException(400, "compare requires 2 to 4 policy_ids")

    entries = []
    # KI: apply the SAME curated-override as /api/policies/all so COMPARE ALL
    # reflects the corrected/verbatim 40-data/policy_facts, not stale extract.
    _curated = _load_curated_facts()
    for pid in policy_ids:
        ep = settings.EXTRACTED_DIR / f"{pid}.json"
        data: Optional[dict] = None
        if ep.exists():
            try:
                data = _json.loads(ep.read_text())
            except Exception:
                data = None
            if data is not None:
                data = _merge_curated(
                    data,
                    _curated.get(data.get("policy_id", pid)) or _curated.get(pid),
                )
        if data is None:
            # #75 (2026-05-18) — curated-only catalogued products (e.g.
            # star-health__star-comprehensive, UIN SHAHLIP26044V092526) have
            # NO rag/extracted/<pid>.json. The marketplace, single
            # /api/scorecard, and bulk scorecard endpoints all resolve these
            # from the curated layer; compare_policies alone still 404'd,
            # breaking "Compare all" for those policies. Mirror the same
            # curated fallback (curated dict also carries doctype-suffixed
            # alias keys) instead of raising.
            data = (
                _curated.get(pid)
                or _curated.get(f"{pid}__wordings")
                or _curated.get(f"{pid}__cis")
                or _curated.get(f"{pid}__brochure")
                or _curated.get(f"{pid}__prospectus")
            )
        if not data:
            raise HTTPException(404, f"No data for {pid}")
        # Insurer reviews for scorecard
        slug = data.get("insurer_slug")
        ir = None
        if slug:
            rp = settings.DATA_DIR / "reviews" / f"{slug}.json"
            if rp.exists():
                try: ir = _json.loads(rp.read_text())
                except Exception: pass
        sc = build_scorecard(data, insurer_reviews=ir)
        entries.append(CompareEntry(
            policy_id=pid,
            policy_name=clean_display_policy_name(
                data.get("policy_name", pid)
            ),
            insurer_slug=slug or "?",
            fields=data,
            scorecard=ScorecardResponse(
                policy_id=sc.policy_id, policy_name=sc.policy_name, insurer_slug=sc.insurer_slug,
                overall_score=sc.overall_score, grade=sc.grade, one_liner=sc.one_liner,
                sub_scores=[ScorecardSubScore(**s.__dict__) for s in sc.sub_scores],
                data_completeness_pct=sc.data_completeness_pct,
                methodology_link=sc.methodology_link,
                profile_summary=_profile_summary_model(sc),
            ),
        ))

    # Comparison-critical fields, in order
    field_order = [
        "policy_type", "uin_code",
        "min_entry_age", "max_entry_age",
        "sum_insured_options",
        "initial_waiting_period_days", "pre_existing_disease_waiting_months",
        "maternity_waiting_months",
        "pre_hospitalization_days", "post_hospitalization_days",
        "day_care_treatments_count", "ayush_coverage", "maternity_coverage",
        "newborn_coverage", "organ_donor_expenses",
        "no_claim_bonus_pct", "restoration_benefit",
        "room_rent_capping", "copayment_pct", "deductible_amount",
        "network_hospital_count", "cashless_treatment_supported",
        "claim_settlement_ratio", "tat_cashless_authorization_hours",
    ]
    return CompareResponse(policies=entries, field_order=field_order)


@app.get("/api/policies/{policy_id}/scorecard", response_model=ScorecardResponse)
async def policy_scorecard(
    policy_id: str,
    age: Optional[int] = None,
    parents_to_insure: Optional[bool] = None,
    budget_band: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """Compute the 6-sub-score A-F scorecard for an extracted policy.

    Now also pulls insurer-level reviews (IRDAI claim ratio + complaints) into
    the Claim Experience sub-score. See 70-docs/scorecard-methodology.md.

    §4c (task #31) — when `session_id` is supplied AND that session's profile
    is populated to ≥0.6 completeness, the policy is scored against THAT full
    profile (resolved the SAME way /api/policies/all does, via
    brain_tools.union_snapshot) so this endpoint's grade + profile_summary
    are byte-identical to the marketplace card for the same canonical id. The
    standalone `age` / `parents_to_insure` / `budget_band` query params remain
    a back-compat fallback when no session profile is available.
    """
    import json as _json

    from backend.scorecard import build_scorecard
    from backend.scorecard import profile_completeness as _completeness
    from backend.session_state import get_session as _get_sess

    # ROOT-CAUSE FIX (scorecard 404 for catalogued curated-only products):
    # /api/policies/all catalogues a card for every extracted JSON AND every
    # curated-facts product (40-data/policy_facts/<insurer>__<product>.json).
    # Curated-only products (e.g. Tata AIG MediCare Lite → policy_id
    # `tata-aig__medicare-lite`) have NO `rag/extracted/<policy_id>.json` —
    # only doctype-suffixed extractions like `...__cis.json` — so the old
    # `extracted_path.exists() → 404` made the scorecard hard-fail for ~77 of
    # 170 catalogued policies, surfacing as the frontend's generic Retry
    # fallback. The marketplace builds those cards' grades straight from the
    # curated dict (policies_all Pass-2 `build_scorecard(data, ...)`); the
    # scorecard endpoint must resolve the SAME way. A catalogued policy_id
    # therefore resolves from extracted-with-curated-override OR, when no
    # extracted file exists, from the curated layer alone — never a 404 for a
    # catalogued product, never a fabricated grade.
    _curated = _load_curated_facts()
    extracted_path = settings.EXTRACTED_DIR / f"{policy_id}.json"

    if extracted_path.exists():
        try:
            policy = _json.loads(extracted_path.read_text())
        except Exception as e:
            raise HTTPException(500, f"Could not load extracted policy: {e}")
        # KI: same curated-override as /api/policies/all so the standalone
        # scorecard reflects the corrected/verbatim 40-data/policy_facts.
        policy = _merge_curated(
            policy,
            _curated.get(policy.get("policy_id", policy_id)) or _curated.get(policy_id),
        )
    else:
        # No bare `<policy_id>.json` extraction. Task #31 PARITY FIX: the
        # marketplace card for a doctype-suffixed extracted-only product
        # (e.g. star-health__star-cardiac-care, whose only extraction is
        # `...__wordings.json`) is built by /api/policies/all Pass-1 from
        # that doctype-suffixed EXTRACTED file (preferred over curated via
        # _DOCTYPE_RANK). The standalone endpoint previously skipped straight
        # to the curated layer, so its grade + profile_summary diverged from
        # the card for the SAME canonical id. Mirror the catalogue's doctype
        # preference (wordings > prospectus > cis > brochure) on the
        # EXTRACTED layer first, with the same curated-override, before
        # falling back to a curated-only product.
        policy = None
        for _dt in ("wordings", "prospectus", "cis", "brochure"):
            _ep = settings.EXTRACTED_DIR / f"{policy_id}__{_dt}.json"
            if _ep.exists():
                try:
                    policy = _json.loads(_ep.read_text())
                except Exception:
                    policy = None
                    continue
                policy = _merge_curated(
                    policy,
                    _curated.get(policy.get("policy_id", policy_id))
                    or _curated.get(f"{policy_id}__{_dt}")
                    or _curated.get(policy_id),
                )
                break
        if policy is None:
            # No extraction in ANY doctype — fall back to the human-curated
            # facts layer (mirrors /api/policies/all Pass 2). The curated
            # dict also carries doctype-suffixed alias keys, so try the
            # canonical id and the raw lookup keys.
            policy = _curated.get(policy_id) or _curated.get(f"{policy_id}__cis") \
                or _curated.get(f"{policy_id}__wordings") \
                or _curated.get(f"{policy_id}__brochure") \
                or _curated.get(f"{policy_id}__prospectus")
            if not policy:
                # Genuinely not in EITHER layer ⇒ this id is not a catalogued
                # product at all (bad/typo id). 404 is the correct, honest
                # response here — it is NOT a catalogued policy.
                raise HTTPException(404, f"No data for policy_id={policy_id}")
            policy = dict(policy)
            policy.setdefault("policy_id", policy_id)

    # Load insurer reviews if present so the Claim Experience sub-score
    # uses authoritative IRDAI data, not just the (mostly-null) per-policy fields.
    insurer_reviews = None
    slug = policy.get("insurer_slug")
    if slug:
        rp = settings.DATA_DIR / "reviews" / f"{slug}.json"
        if rp.exists():
            try:
                insurer_reviews = _json.loads(rp.read_text())
            except Exception:
                pass

    # §4c — resolve the session profile the SAME way /api/policies/all does
    # (brain_tools.union_snapshot full dict + parents_* carry-through) so this
    # endpoint's grade + profile_summary match the marketplace card for the
    # same canonical id by construction. Only when ≥0.6 complete.
    #
    # `catalogue_profile` is EXACTLY what /api/policies/all would pass to
    # _marketplace_catalogue for this session (the ≥0.6 SLOT_UNION snapshot,
    # else None) — used below for the catalogue-card parity override. The
    # back-compat query-param path is a separate, profile-NEUTRAL-vs-catalogue
    # fallback (the catalogue is never built from loose query params).
    catalogue_profile: Optional[dict] = None
    profile: dict = {}
    if session_id:
        try:
            _p = _get_sess(session_id).profile
            _pd = brain_tools.union_snapshot(_p)
            for _pf in ("parents_to_insure", "parents_age_max", "parents_has_ped"):
                if _pf not in _pd:
                    _v = getattr(_p, _pf, None)
                    if _v is not None:
                        _pd[_pf] = _v
            if _completeness(_pd) >= 0.6:
                profile = _pd
                catalogue_profile = _pd
        except Exception:  # noqa: BLE001 — bad/expired session ⇒ back-compat path
            profile = {}
            catalogue_profile = None
    if not profile:
        # Back-compat: standalone query params when no usable session profile.
        if age is not None: profile["age"] = age
        if parents_to_insure is not None: profile["parents_to_insure"] = parents_to_insure
        if budget_band is not None: profile["budget_band"] = budget_band

    # TASK #31 — SINGLE SOURCE OF TRUTH (option (a)). When this id IS a
    # marketplace card, serve the EXACT `Scorecard` object the
    # /api/policies/all catalogue built for that canonical card under THIS
    # session's profile — full sub_scores + profile_summary + grade +
    # data_completeness + one_liner, all from the catalogue's ONE
    # build_scorecard pass on the catalogue's chosen sibling
    # `(data, insurer_reviews, profile)`. Parity is byte-identical BY
    # CONSTRUCTION: the same object feeds both surfaces, so the endpoint can
    # no longer pick a different doctype sibling than the catalogue's
    # completeness-based `_best` dedup (the prior bug — the old endpoint
    # reconstructed `policy` via its own doctype-rank loop and emitted a
    # different strength set / caveat for the same canonical id).
    #
    # `_catalogue_scorecard` returns None ONLY when the id is not a
    # catalogued product at all — then we fall through to the locally-built
    # scorecard so the curated-only / back-compat query-param / never-404
    # behaviour the resolution block above guarantees is fully preserved.
    cat_sc = None
    try:
        cat_sc = _catalogue_scorecard(policy_id, catalogue_profile)
    except Exception:  # noqa: BLE001 — never let the SSOT resolver 500 a card
        cat_sc = None

    sc = cat_sc if cat_sc is not None else build_scorecard(
        policy, insurer_reviews=insurer_reviews, profile=profile or None
    )

    return ScorecardResponse(
        policy_id=sc.policy_id,
        policy_name=sc.policy_name,
        insurer_slug=sc.insurer_slug,
        overall_score=sc.overall_score,
        grade=sc.grade,
        one_liner=sc.one_liner,
        sub_scores=[ScorecardSubScore(**s.__dict__) for s in sc.sub_scores],
        data_completeness_pct=sc.data_completeness_pct,
        methodology_link=sc.methodology_link,
        insufficient_data=sc.insufficient_data,
        profile_summary=_profile_summary_model(sc),
    )


# ----------------------------------------------------------------------------
# Bulk scorecard endpoint — powers the PolicyCompareModal scorecard widget.
# ----------------------------------------------------------------------------
# Why bulk: the compare modal renders 2-4 scorecards in parallel and each is
# profile-tuned. Doing N sequential GETs from the client wastes the per-policy
# JSON I/O cost (we re-load every reviews file even for the same insurer) and
# fans out N renders. One POST with the full profile + id list lets us:
#   - load each reviews file once per slug (memoized in the loop)
#   - return missing policies as N/A so the client renders a clean placeholder
#   - share one profile dict — no copy-paste of every field in N query strings
class BulkScorecardRequest(BaseModel):
    policy_ids: list[str]
    profile: Optional[dict] = None


class BulkScorecardEntry(BaseModel):
    policy_id: str
    policy_name: str
    insurer_slug: str
    overall_grade: str               # "A" / "B+" / etc — letter only for missing
    overall_score: int               # 0-100
    sub_scores: dict[str, int]       # {coverage_breadth: 82, cost_predictability: 64, ...}
    profile_rationale: list[str]     # bullets explaining WHY this score for this user
    data_completeness_pct: float
    one_liner: str = ""
    # raw signals per sub-score so the widget can pop-out a tooltip with detail
    signals: dict[str, list[str]] = Field(default_factory=dict)
    # Deterministic, profile-aware {strengths, caveat} — the structured
    # replacement for the generic one_liner the widget now renders at top.
    profile_summary: Optional[ProfileSummaryModel] = None


class BulkScorecardResponse(BaseModel):
    per_policy: dict[str, BulkScorecardEntry]


def _slugify_subscore(name: str) -> str:
    """'Coverage Breadth' -> 'coverage_breadth' (stable key for the widget)."""
    return name.lower().replace("-", "_").replace("&", "and").replace(" ", "_").replace("__", "_")


def _profile_rationale_for(policy: dict, profile: Optional[dict], sub_scores) -> list[str]:
    """Turn raw signals + profile facts into 2-5 plain-English bullets.

    Each bullet is shaped as 'Strong fit:' or 'Weak fit:' so the buyer can scan
    pros and cons at a glance. We anchor each bullet to a concrete profile
    attribute (you mentioned X) so the user trusts the personalization is real.
    """
    if not profile:
        return []
    bullets: list[str] = []
    conditions = profile.get("health_conditions") or []
    cond_str = " ".join(str(c).lower() for c in conditions) if isinstance(conditions, list) else ""
    age = profile.get("age") if isinstance(profile.get("age"), int) else None
    deps = (profile.get("dependents") or "").lower()
    loc = profile.get("location_tier")
    goal = (profile.get("primary_goal") or "").lower()
    existing = profile.get("existing_cover_inr")

    # Pre-existing disease handling
    if cond_str and any(c in cond_str for c in ("diab", "bp", "hyper", "thyroid", "heart")):
        ped = policy.get("pre_existing_disease_waiting_months")
        try:
            ped_n = int(ped) if ped is not None else None
        except (TypeError, ValueError):
            ped_n = None
        if ped_n is not None:
            if ped_n <= 24:
                bullets.append(f"Strong fit: PED waiting is only {ped_n} months — short for your {cond_str.strip()}.")
            elif ped_n >= 48:
                bullets.append(f"Weak fit: {ped_n}-month PED waiting is long for your {cond_str.strip()} — alternatives offer 24-36 months.")
            else:
                bullets.append(f"Fair fit: {ped_n}-month PED waiting is standard for your {cond_str.strip()}.")

    # Senior + claim reliability
    if age and age >= 60:
        nh = policy.get("network_hospital_count")
        try:
            nh_n = int(nh) if nh is not None else None
        except (TypeError, ValueError):
            nh_n = None
        if nh_n is not None and nh_n >= 7000:
            bullets.append(f"Strong fit: {nh_n:,}+ cashless hospitals matters at age {age} when access speed counts.")
        elif nh_n is not None and nh_n < 3000:
            bullets.append(f"Weak fit: only {nh_n} cashless hospitals — thin network for age {age}.")
        # max_renewal_age removed: lifelong renewability is the IRDAI norm for
        # every health-indemnity policy (universal → not a differentiator, and
        # the old `>= 99` check fired on the fabricated 999 sentinel).

    # Family + room-rent / maternity
    if any(k in deps for k in ("spouse", "wife", "husband", "partner", "kid", "child", "family")):
        rrc = policy.get("room_rent_capping")
        rrc_text = rrc if isinstance(rrc, str) else (rrc.get("limit_text") if isinstance(rrc, dict) else None)
        if rrc_text and "no cap" in rrc_text.lower():
            bullets.append("Strong fit: no room-rent cap — works for any hospital your family chooses.")
        elif rrc_text and ("1%" in rrc_text or "%" in rrc_text):
            metro_qual = " in a metro" if loc == "metro" else ""
            bullets.append(f"Weak fit: room rent capped ({rrc_text[:40].strip()}) may be tight for hospitals{metro_qual}.")
        if any(k in deps for k in ("spouse", "wife", "husband", "partner")):
            mc = policy.get("maternity_coverage")
            covered = mc.get("covered") if isinstance(mc, dict) else mc
            if covered is True:
                mw = policy.get("maternity_waiting_months")
                bullets.append(
                    f"Strong fit: maternity covered with {mw}-month wait — relevant to your spouse."
                    if mw else
                    "Strong fit: maternity covered — relevant to your spouse."
                )
            elif covered is False:
                bullets.append("Weak fit: no maternity coverage — you'd need a separate rider.")

    # First-time buyer — simplicity / premium predictability
    if existing == 0:
        copay = policy.get("copayment_pct")
        try:
            copay_n = float(copay) if copay is not None else None
        except (TypeError, ValueError):
            copay_n = None
        if copay_n is not None and copay_n == 0:
            bullets.append("Strong fit: zero co-pay — simpler to budget for as a first-time buyer.")
        elif copay_n is not None and copay_n >= 20:
            bullets.append(f"Weak fit: {copay_n:.0f}% co-pay adds a surprise out-of-pocket — hard to plan as a first-time buyer.")

    # Tax-saving goal anchor
    if "tax" in goal:
        bullets.append("Note: premium qualifies for Section 80D deduction — aligned with your tax-saving goal.")

    # If we still have <2 bullets, fall back to top sub-score deltas vs neutral
    if len(bullets) < 2:
        ranked = sorted(sub_scores, key=lambda s: s.score, reverse=True)
        if ranked:
            top = ranked[0]
            bullets.append(f"Strongest area: {top.name} ({top.score}/100) — {top.summary.lower()}.")
        if len(ranked) > 1:
            bot = ranked[-1]
            if bot.score < 60:
                bullets.append(f"Watch out: {bot.name} ({bot.score}/100) — {bot.summary.lower()}.")

    return bullets[:5]


def _letter_grade_with_plus(score: int) -> str:
    """Convert 0-100 to A / A- / B+ / B / B- / C+ / C / C- / D / F.

    The base grade_for() returns flat letters (A/B/C/D/F). For the compare
    widget the buyer wants finer distinction between e.g. an 84 (top of B) and
    a 71 (bottom of B). Thresholds:
        90+ A, 85-89 A-, 80-84 B+, 75-79 B, 70-74 B-,
        65-69 C+, 60-64 C, 55-59 C-, 40-54 D, <40 F.
    """
    if score >= 90: return "A"
    if score >= 85: return "A-"
    if score >= 80: return "B+"
    if score >= 75: return "B"
    if score >= 70: return "B-"
    if score >= 65: return "C+"
    if score >= 60: return "C"
    if score >= 55: return "C-"
    if score >= 40: return "D"
    return "F"


@app.post("/api/scorecard/bulk", response_model=BulkScorecardResponse)
async def scorecard_bulk(req: BulkScorecardRequest):
    """Compute profile-tuned scorecards for N policies in one round-trip.

    Body: { policy_ids: [...], profile: {...} }
    Returns: { per_policy: { <policy_id>: { overall_grade, overall_score,
                                            sub_scores, profile_rationale,
                                            data_completeness_pct } } }

    Missing policy_ids get overall_grade="N/A" + rationale=["Data not indexed"].
    """
    import json as _json
    from backend.scorecard import build_scorecard

    if not req.policy_ids:
        raise HTTPException(400, "policy_ids must be a non-empty list")
    if len(req.policy_ids) > 8:
        raise HTTPException(400, "bulk scorecard caps at 8 policies per call")

    profile = req.profile or None
    insurer_cache: dict[str, Optional[dict]] = {}
    out: dict[str, BulkScorecardEntry] = {}
    # KI: same curated-override as /api/policies/all so the bulk scorecard
    # badges reflect the corrected/verbatim 40-data/policy_facts.
    _curated = _load_curated_facts()

    for pid in req.policy_ids:
        extracted_path = settings.EXTRACTED_DIR / f"{pid}.json"
        policy = None
        if extracted_path.exists():
            try:
                policy = _json.loads(extracted_path.read_text())
            except Exception as e:
                out[pid] = BulkScorecardEntry(
                    policy_id=pid, policy_name=pid, insurer_slug="?",
                    overall_grade="N/A", overall_score=0, sub_scores={},
                    profile_rationale=[f"Data unreadable: {e}"],
                    data_completeness_pct=0.0,
                    one_liner="Extraction file is corrupted.",
                    signals={},
                )
                continue
            policy = _merge_curated(
                policy, _curated.get(policy.get("policy_id", pid)) or _curated.get(pid)
            )
        else:
            # ROOT-CAUSE FIX #60 (2026-05-18): curated-only catalogued
            # products (e.g. star-health__star-comprehensive, UIN
            # SHAHLIP26044V092526) have NO rag/extracted/<pid>.json — only
            # doctype-suffixed extractions. The marketplace + the single
            # /api/scorecard endpoint already resolve these from the curated
            # layer (policies_all Pass-2 / lines ~3617). The BULK endpoint
            # did not, so it emitted the N/A "No extraction available" /
            # "Data not indexed" sentinel — the broken-card defect the user
            # saw for Star Comprehensive. Mirror that curated fallback here;
            # the curated dict also carries doctype-suffixed alias keys.
            policy = (
                _curated.get(pid)
                or _curated.get(f"{pid}__wordings")
                or _curated.get(f"{pid}__cis")
                or _curated.get(f"{pid}__brochure")
                or _curated.get(f"{pid}__prospectus")
            )
        if not policy:
            # Genuinely absent from BOTH layers ⇒ not a catalogued product.
            out[pid] = BulkScorecardEntry(
                policy_id=pid,
                policy_name=pid,
                insurer_slug="?",
                overall_grade="N/A",
                overall_score=0,
                sub_scores={},
                profile_rationale=["Data not indexed"],
                data_completeness_pct=0.0,
                one_liner="No extraction available for this policy.",
                signals={},
            )
            continue

        slug = policy.get("insurer_slug") or "?"
        if slug not in insurer_cache:
            insurer_cache[slug] = None
            rp = settings.DATA_DIR / "reviews" / f"{slug}.json"
            if rp.exists():
                try:
                    insurer_cache[slug] = _json.loads(rp.read_text())
                except Exception:
                    insurer_cache[slug] = None

        sc = build_scorecard(policy, insurer_reviews=insurer_cache[slug], profile=profile)

        sub_map = {_slugify_subscore(s.name): s.score for s in sc.sub_scores}
        signal_map = {_slugify_subscore(s.name): s.signals for s in sc.sub_scores}
        psm = _profile_summary_model(sc)

        # Bridge the legacy profile_rationale list off the deterministic
        # profile_summary so the old field stays populated AND consistent
        # with the new structured data (strengths + [caveat]). Only fall
        # back to the heuristic _profile_rationale_for when the deterministic
        # summary produced too little to be useful (insufficient-data /
        # profile-neutral with <3 facts) so no surface goes blank.
        if psm and psm.strengths:
            rationale = list(psm.strengths)
            if psm.caveat:
                rationale.append(psm.caveat)
        else:
            rationale = _profile_rationale_for(policy, profile, sc.sub_scores)

        out[pid] = BulkScorecardEntry(
            policy_id=sc.policy_id or pid,
            policy_name=sc.policy_name or pid,
            insurer_slug=sc.insurer_slug or slug,
            overall_grade=_letter_grade_with_plus(sc.overall_score),
            overall_score=sc.overall_score,
            sub_scores=sub_map,
            profile_rationale=rationale,
            data_completeness_pct=sc.data_completeness_pct,
            one_liner=sc.one_liner,
            signals=signal_map,
            profile_summary=psm,
        )

    return BulkScorecardResponse(per_policy=out)


class ReviewsResponse(BaseModel):
    insurer_slug: str
    insurer_name: str
    # #76 — these structured sub-objects are NOT present in every review
    # file (e.g. acko.json has none); requiring them 500'd the endpoint and
    # blanked the whole reputation panel even though real data existed.
    # Default-empty so the endpoint always returns 200 with whatever real
    # data the file does have (InsurerReviewsBlock already renders each
    # sub-object conditionally).
    aggregate_score: dict = Field(default_factory=dict)
    claim_metrics: dict = Field(default_factory=dict)
    aggregator_ratings: dict = Field(default_factory=dict)
    reddit_sentiment: dict = Field(default_factory=dict)
    youtube_coverage: dict = Field(default_factory=dict)
    in_news: list = Field(default_factory=list)
    last_updated: str = ""


@app.get("/api/insurers/{insurer_slug}/reviews", response_model=ReviewsResponse)
async def get_reviews(insurer_slug: str):
    """Aggregated reviews + claim metrics for an insurer.

    Data sourced from IRDAI annual report + PolicyBazaar/InsuranceDekho +
    Reddit r/IndianFinance + YouTube finance creators (Ditto et al) +
    news mentions. Per-insurer JSON at 40-data/reviews/<slug>.json — see
    40-data/reviews/INDEX.md for leaderboard.
    """
    import json
    p = settings.DATA_DIR / "reviews" / f"{insurer_slug}.json"
    if not p.exists():
        raise HTTPException(404, f"No reviews for insurer={insurer_slug}")
    try:
        d = json.loads(p.read_text())
        return ReviewsResponse(**d)
    except Exception as e:
        raise HTTPException(500, f"Failed to load reviews: {e}")


class PremiumEstimateRequest(BaseModel):
    age: int = Field(..., ge=0, le=120)
    sum_insured_inr: int = Field(..., ge=100000, le=100000000)
    city_tier: str = Field("metro", pattern="^(metro|tier1|tier2)$")
    smoker: bool = False
    # family_size: 0 is the slider "self-only" sentinel (treated identical to 1)
    family_size: int = Field(1, ge=0, le=8)
    policy_id: Optional[str] = None
    # Pre-existing condition flag — controls PED premium load. Allowed values
    # mirror the FALLBACK_PED keys in backend/premium_calculator.py
    pre_existing_conditions: str = Field(
        "none",
        pattern="^(none|diabetes_or_hypertension|heart_disease|multiple)$",
    )
    # Voluntary co-payment % — reduces premium ~7% per 10pp of co-pay
    copayment_pct: float = Field(0.0, ge=0, le=40)
    # Family medical history tokens (cancer / diabetes / heart_disease / …).
    # Applies the same family_history_loading (1.0×–1.10×) the header band and
    # bulk path use, so the per-policy panel reflects family history too (#52).
    family_medical_history: Optional[list[str]] = None
    # B2 widget parity (KI-bugfix, 2026-05-15) — optional slider overrides so
    # PolicyPremiumWidget (compare modal) can use the same curated-anchored
    # estimate() pipeline as PremiumCalculatorPanel instead of the divergent
    # bulk_estimate() flat-base path. Applied as straight multipliers on top
    # of the estimate() result using premium_calculator.BULK_TENURE_MULT /
    # BULK_DEDUCTIBLE_DISCOUNT constants — leaves estimate() math untouched.
    tenure_years: Optional[int] = Field(None, ge=1, le=3)
    deductible_inr: Optional[int] = Field(None, ge=0, le=200_000)


class PremiumEstimateResponse(BaseModel):
    policy_id: str
    point_estimate_inr: int
    low_inr: int
    high_inr: int
    methodology: str
    sources: list[str]
    is_illustrative: bool = True
    disclaimer: str = (
        "Illustrative range only — actual premium depends on underwriting + "
        "medical history + risk factors. Confirm with the insurer before purchase."
    )
    # Echo back the effective tenure / deductible so the widget can render a
    # consistent breakdown line without re-deriving them. Optional for legacy
    # callers (PremiumCalculatorPanel ignores both).
    tenure_years: Optional[int] = None
    deductible_inr: Optional[int] = None
    # BUG #29 — whether THIS policy genuinely offers a user-selectable
    # voluntary deductible (curated deductible_amount > 0 AND not a
    # top-up). Only ~2 of 148 do. The widget hides the deductible selector
    # entirely when False; allowed_deductibles is the exact pill set.
    supports_voluntary_deductible: bool = False
    allowed_deductibles: list[int] = [0]
    # True when the underlying estimate() anchored to a curated quote sample.
    # PolicyPremiumWidget uses this (instead of bulk_estimate's `assumed` flag)
    # to decide whether to show its "Estimate" badge.
    base_sample_used: bool = False
    # D2 (2026-05-16) — non-null ONLY when the policy publishes no
    # corroborated Sum Insured, so this estimate was priced against a
    # fallback cover. The frontend renders it verbatim under the estimate:
    # "Estimate shown for ₹X cover — this policy's sum insured isn't published."
    sum_insured_disclosure: Optional[str] = None


@app.post("/api/premium/estimate", response_model=PremiumEstimateResponse)
async def premium_estimate(req: PremiumEstimateRequest):
    """Illustrative premium calculator — rules-based estimate from curated public data."""
    from backend.premium_calculator import (
        estimate as _estimate,
        BULK_TENURE_MULT,
        BULK_DEDUCTIBLE_DISCOUNT,
    )
    e = _estimate(
        age=req.age,
        sum_insured_inr=req.sum_insured_inr,
        city_tier=req.city_tier,
        smoker=req.smoker,
        family_size=req.family_size,
        policy_id=req.policy_id,
        pre_existing_conditions=req.pre_existing_conditions,
        copayment_pct=req.copayment_pct,
        family_medical_history=req.family_medical_history,
    )

    # Snap incoming tenure / deductible to the nearest supported bucket so the
    # widget can pass raw slider values without precomputing.
    point = e.point_estimate_inr
    low = e.low_inr
    high = e.high_inr
    effective_tenure: Optional[int] = None
    effective_ded: Optional[int] = None
    if req.tenure_years is not None:
        effective_tenure = req.tenure_years if req.tenure_years in BULK_TENURE_MULT else 1
        tenure_mult = BULK_TENURE_MULT.get(effective_tenure, 1.0)
        point = int(round(point * tenure_mult))
        low = int(round(low * tenure_mult))
        high = int(round(high * tenure_mult))
    # BUG #29 — resolve whether this policy genuinely supports a voluntary
    # deductible. Only ~2 of 148 do; for every other policy a caller-supplied
    # deductible must NOT discount the premium.
    from backend.premium_calculator import policy_deductible_support
    _supports, _allowed = policy_deductible_support(req.policy_id)
    if req.deductible_inr is not None:
        if not _supports or req.deductible_inr not in _allowed:
            # Unsupported policy (or a value outside this policy's allowed
            # set) — no phantom discount, honest echo.
            effective_ded = 0
        elif req.deductible_inr in BULK_DEDUCTIBLE_DISCOUNT:
            effective_ded = req.deductible_inr
        else:
            effective_ded = min(
                BULK_DEDUCTIBLE_DISCOUNT.keys(),
                key=lambda d: abs(d - req.deductible_inr),
            )
        ded_mult = BULK_DEDUCTIBLE_DISCOUNT.get(effective_ded, 1.0)
        point = int(round(point * ded_mult))
        low = int(round(low * ded_mult))
        high = int(round(high * ded_mult))

    # D2 — when this policy publishes NO corroborated Sum Insured, the
    # estimate was necessarily priced against a fallback cover (the SI the
    # caller sent, which the per-policy estimator seeds from
    # desired_sum_insured_inr ?? ₹10 L). Surface the verbatim disclosure so
    # the user knows the SI is assumed, not the policy's own.
    si_disclosure: Optional[str] = None
    if req.policy_id:
        try:
            _siv = _policy_corroborated_si(req.policy_id)
            if _siv.kind == "none":
                from backend.premium_calculator import unpublished_si_disclosure
                si_disclosure = unpublished_si_disclosure(req.sum_insured_inr)
        except Exception:
            si_disclosure = None

    return PremiumEstimateResponse(
        policy_id=e.policy_id,
        point_estimate_inr=point,
        low_inr=low,
        high_inr=high,
        methodology=e.methodology,
        sources=e.sources or [],
        tenure_years=effective_tenure,
        deductible_inr=effective_ded,
        supports_voluntary_deductible=_supports,
        allowed_deductibles=_allowed,
        base_sample_used=e.base_sample_used is not None,
        sum_insured_disclosure=si_disclosure,
    )


# ---------------------------------------------------------------------------
# /api/premium/bulk — multi-policy slider-driven premium calculator
# Powers the PolicyPremiumWidget inside PolicyCompareModal.
# ---------------------------------------------------------------------------

class PremiumBulkProfile(BaseModel):
    age: Optional[int] = Field(None, ge=0, le=120)
    dependents: Optional[str] = None
    location_tier: Optional[str] = None
    family_size: Optional[int] = Field(None, ge=0, le=10)
    smoker: Optional[bool] = False
    pre_existing_conditions: Optional[str] = "none"


class PremiumBulkOverride(BaseModel):
    sum_insured_inr: Optional[int] = Field(None, ge=100_000, le=100_000_000)
    tenure_years: Optional[int] = Field(None, ge=1, le=3)
    deductible_inr: Optional[int] = Field(None, ge=0, le=200_000)


class PremiumBulkRequest(BaseModel):
    policy_ids: list[str] = Field(..., min_length=1, max_length=20)
    profile: PremiumBulkProfile = Field(default_factory=PremiumBulkProfile)
    overrides: Optional[dict[str, PremiumBulkOverride]] = None


class PremiumBulkRow(BaseModel):
    policy_id: str
    premium_inr_annual: int
    breakdown: dict
    sum_insured_inr: int
    tenure_years: int
    deductible_inr: int
    assumed: bool
    notes: list[str] = []


class PremiumBulkResponse(BaseModel):
    per_policy: dict[str, PremiumBulkRow]
    profile_used: PremiumBulkProfile
    disclaimer: str = (
        "Illustrative estimates only — actual premiums depend on underwriting, "
        "medical history, and quote-time risk factors. Confirm with the insurer."
    )


@app.post("/api/premium/bulk", response_model=PremiumBulkResponse)
async def premium_bulk(req: PremiumBulkRequest):
    """Bulk slider-driven premium estimator for the PolicyCompareModal widget."""
    from backend.premium_calculator import bulk_estimate as _bulk

    overrides = {
        pid: (ov.model_dump(exclude_none=True) if ov else {})
        for pid, ov in (req.overrides or {}).items()
    }
    rows = _bulk(
        policy_ids=req.policy_ids,
        profile=req.profile.model_dump(exclude_none=True),
        overrides=overrides,
    )
    return PremiumBulkResponse(
        per_policy={
            pid: PremiumBulkRow(
                policy_id=r.policy_id,
                premium_inr_annual=r.premium_inr_annual,
                breakdown=r.breakdown,
                sum_insured_inr=r.sum_insured_inr,
                tenure_years=r.tenure_years,
                deductible_inr=r.deductible_inr,
                assumed=r.assumed,
                notes=r.notes,
            )
            for pid, r in rows.items()
        },
        profile_used=req.profile,
    )


@app.post("/api/tts")
async def tts(req: TTSRequest):
    """Standalone TTS endpoint — returns base64 WAV."""
    try:
        audio = await get_tts().synthesize(
            text=req.text,
            language_code=req.language_code,
            speaker=req.speaker,
        )
    except Exception as e:
        raise HTTPException(500, f"TTS failed: {type(e).__name__}: {e}")
    return JSONResponse({"audio_base64": base64.b64encode(audio).decode("utf-8")})


@app.get("/api")
async def api_root():
    return {
        "service": "Insurance Sales Portfolio Expert API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/health",
    }


# ---------------------------------------------------------------------------
# Profile-level predicted-premium BAND — feeds the chat-UI chip that sits
# next to the "X% DONE" profile-completeness pill. Updates reactively as the
# profile fills in (frontend refetches whenever completeness_pct changes).
# ---------------------------------------------------------------------------
class PredictedPremiumBandResponse(BaseModel):
    min_inr: int
    median_inr: int
    max_inr: int
    sample_size: int
    assumed: bool
    # #63 — the SI the band was priced at. estimate_premium_band's KI-278
    # contract already returns this; the model dropped it, so the pill
    # couldn't tell the user the band is the TYPICAL cohort range at this
    # cover (vs the per-plan LIVE PREMIUM, which is one specific plan and
    # may sit outside the typical band — expected, not a contradiction).
    sum_insured_used: int = 0


@app.get(
    "/api/profile/predicted-premium-band",
    response_model=PredictedPremiumBandResponse,
)
async def predicted_premium_band(session_id: Optional[str] = None):
    """Return the user's estimated premium band aggregated across a
    representative basket of marketplace policies. Mirrors the slot-shape
    used by /api/profile/completeness so the chip and the bar share triggers.
    """
    from backend.premium_calculator import estimate_premium_band
    from backend.session_state import get_session

    if not session_id:
        return PredictedPremiumBandResponse(
            min_inr=0, median_inr=0, max_inr=0, sample_size=0, assumed=True,
            sum_insured_used=0,
        )

    sess = get_session(session_id)
    p = sess.profile
    # KI-271 — band endpoint now drives off SLOT_UNION so copay_pct +
    # family_medical_history (D2/KI-269) actually shift the band. Prior
    # 12-key hand-roll silently omitted both → E3 smoke caught identical
    # bands with/without copay+family input.
    profile_dict = {
        slot: getattr(p, slot, None) for slot in brain_tools.SLOT_UNION
    }
    # Same answered-only gate as profile_completeness_view (KI-196 / ADR-041) —
    # only feed slots the user has actually answered, not pre-populated
    # defaults. Keeps the band stable until the user has actually said
    # something meaningful.
    answered = set(getattr(p, "asked", []) or [])
    filtered_profile = {
        k: (v if k in answered else None) for k, v in profile_dict.items()
    }
    band = estimate_premium_band(filtered_profile)
    return PredictedPremiumBandResponse(**band)


# /api/profile/recall-by-name was REMOVED in ADR-043 (2026-05-27).
# Cross-session profile recall is gone — sessions are in-memory only, so
# there is nothing to "recall" off a bare name. The frontend api.ts caller
# that wrapped this endpoint has also been removed. Old clients still
# pinging the path get a 404, which is the correct degraded behaviour.


# ---- Static frontend (served alongside /api on the same port for HF Spaces) ----
# The Next.js frontend is statically exported during the Docker build to
# /app/frontend/out. In local dev, this directory may not exist — we still
# want the backend to start cleanly.
import os
from pathlib import Path as _Path

_FRONTEND_DIR = _Path(__file__).resolve().parent.parent / "frontend" / "out"
if _FRONTEND_DIR.exists():
    # Serve the built site as the catch-all. /api/* routes registered above
    # take precedence because they are matched first.
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="static")
else:
    @app.get("/")
    async def root():
        return {
            "service": "Insurance Sales Portfolio Expert API",
            "version": "0.1.0",
            "frontend": "not built — run `cd frontend && npm run build`",
            "docs": "/docs",
            "health": "/api/health",
        }


# ---- #40 SSOT grade resolver ------------------------------------------------
# marketplace_grade(policy_id) returns the SAME (grade, overall) the
# marketplace card for that policy's canonical identity shows. The
# recommendation path calls this instead of re-deriving a scorecard, so
# rec-card grade == marketplace grade for ALL 148 by construction.

import threading as _mg_threading

_MG_LOCK = _mg_threading.Lock()
_MG_CACHE: dict = {"sig": None, "index": None}


def _mg_data_signature() -> tuple:
    """Cheap fingerprint that changes when any grading input changes
    (so an uploaded-PDF card or a curated edit invalidates the cache)."""
    sig = []
    for d in (settings.EXTRACTED_DIR, settings.DATA_DIR / "policy_facts",
              settings.DATA_DIR / "reviews"):
        try:
            for fp in sorted(d.glob("*.json")):
                st = fp.stat()
                sig.append((fp.name, int(st.st_mtime), st.st_size))
        except Exception:  # noqa: BLE001 — missing dir → empty contribution
            continue
    # #52 — PERSISTED uploaded-doc records are ALSO grading inputs
    # (_load_curated_facts merges them). Walk the persistent UPLOADED_DOCS_DIR
    # so a brand-new upload — or a restart that re-materialised the dir —
    # invalidates the #40 grade cache and the new card grades immediately.
    try:
        for fp in sorted(settings.UPLOADED_DOCS_DIR.glob("*/record.json")):
            st = fp.stat()
            sig.append((str(fp.relative_to(settings.UPLOADED_DOCS_DIR)),
                        int(st.st_mtime), st.st_size))
    except Exception:  # noqa: BLE001 — missing dir → empty contribution
        pass
    return tuple(sig)


def _mg_norm_uin(raw) -> str:
    try:
        from backend.policy_identity import normalize_uin
        return normalize_uin(raw)
    except Exception:  # noqa: BLE001
        return ""


def _mg_build_index() -> dict:
    """{lookup_key -> (grade, overall_score)} for every marketplace card,
    keyed by policy_id, product_key, and normalised UIN so a variant /
    alias id resolves to its canonical card's grade."""
    cards = _marketplace_catalogue(None)
    cur = _load_curated_facts()
    idx: dict = {}

    def _put(k, val):
        if k:
            idx.setdefault(k, val)

    for c in cards:
        val = (c.grade, c.overall_score)
        pid = c.policy_id or ""
        _put(f"id:{pid}", val)
        try:
            from backend.policy_identity import product_key as _pk
            _put(f"pk:{_pk(pid)}", val)
        except Exception:  # noqa: BLE001
            pass
        # UIN of the card's underlying data (curated wins, like the catalogue)
        cdata = cur.get(pid) or {}
        uin = _mg_norm_uin(cdata.get("uin_code") or cdata.get("uin"))
        if not uin:
            try:
                ep = settings.EXTRACTED_DIR / f"{pid}.json"
                if ep.exists():
                    import json as _j
                    uin = _mg_norm_uin(_j.loads(ep.read_text()).get("uin_code"))
            except Exception:  # noqa: BLE001
                uin = ""
        _put(f"uin:{uin}" if uin else "", val)
    return idx


def _mg_index() -> dict:
    sig = _mg_data_signature()
    with _MG_LOCK:
        if _MG_CACHE["sig"] != sig or _MG_CACHE["index"] is None:
            _MG_CACHE["index"] = _mg_build_index()
            _MG_CACHE["sig"] = sig
        return _MG_CACHE["index"]


# Task #31 — profile-keyed {policy_id -> MarketplacePolicy} index so the
# single /api/policies/{id}/scorecard endpoint can serve the EXACT card the
# /api/policies/all catalogue produced for that id. This is the only way to
# guarantee byte-identical profile_summary (and grade / overall_score) for
# every card id — including doctype-suffixed stems the catalogue's pre-
# existing #133/#145 dedup picks as the canonical card-id while computing
# the scorecard from a different-doctype sibling. We do NOT re-architect
# that dedup (out of scope, protected by test_full_id_universe_parity);
# instead the endpoint defers to the catalogue, the single source of truth.
_CAT_CARD_LOCK = _mg_threading.Lock()
_CAT_CARD_CACHE: dict = {}  # profile_key -> (sig, card_idx, sc_idx)


def _profile_cache_key(profile: Optional[dict]) -> str:
    if not profile:
        return "∅"
    import json as _j

    return _j.dumps(
        {k: profile[k] for k in sorted(profile)},
        sort_keys=True, default=str,
    )


def _catalogue_indices(profile: Optional[dict]) -> tuple[dict, dict]:
    """`({policy_id -> MarketplacePolicy}, {policy_id|_ident -> Scorecard})`
    for `profile`, cached on the data signature + a stable profile key so
    repeated single-scorecard calls in one render don't rebuild the catalogue
    per request.

    The Scorecard index is the Task #31 single-source-of-truth: it holds the
    EXACT `Scorecard` object `_marketplace_catalogue` built for each surviving
    card (full sub_scores + profile_summary + grade), keyed by the card's
    policy_id AND its canonical `_ident`. `/api/policies/{id}/scorecard`
    serves it verbatim, so its scorecard is byte-identical to the
    /api/policies/all card for the same canonical id by construction."""
    sig = _mg_data_signature()
    pkey = _profile_cache_key(profile)
    with _CAT_CARD_LOCK:
        entry = _CAT_CARD_CACHE.get(pkey)
        if entry is None or entry[0] != sig:
            sc_idx: dict = {}
            cards = _marketplace_catalogue(profile, _collect_scorecards=sc_idx)
            card_idx = {c.policy_id: c for c in cards}
            _CAT_CARD_CACHE[pkey] = (sig, card_idx, sc_idx)
            # Bound the cache so distinct profiles don't grow it unbounded.
            if len(_CAT_CARD_CACHE) > 16:
                for k in list(_CAT_CARD_CACHE.keys())[:-16]:
                    _CAT_CARD_CACHE.pop(k, None)
            entry = _CAT_CARD_CACHE[pkey]
        return entry[1], entry[2]


def _catalogue_card_index(profile: Optional[dict]) -> dict:
    """{policy_id -> MarketplacePolicy} for `profile` (back-compat shim)."""
    return _catalogue_indices(profile)[0]


# Doctype suffixes used to canonicalise a requested policy_id onto the
# catalogue's surviving card id (mirrors _marketplace_catalogue._ident).
_SCORECARD_DOCT = ("wordings", "brochure", "cis", "prospectus", "policy")


def _canonical_ident(pid: str) -> str:
    for dt in _SCORECARD_DOCT:
        if pid.endswith(f"__{dt}"):
            return pid[: -(len(dt) + 2)]
    return pid


def _catalogue_scorecard(policy_id: str, profile: Optional[dict]):
    """The EXACT `Scorecard` the /api/policies/all catalogue produced for
    `policy_id`'s canonical card under `profile`, or None when `policy_id`
    is not a catalogued product.

    Resolution order (single source of truth — same dedup the catalogue
    uses): exact policy_id  ->  canonical `_ident(policy_id)`  ->  the
    canonical id of any catalogue card whose `aliases` contains this id's
    display name. Returns None (NOT a 404) so the caller keeps its existing
    curated-only / back-compat / never-404 behaviour for non-card ids."""
    if not policy_id:
        return None
    card_idx, sc_idx = _catalogue_indices(profile)
    pid = policy_id.strip()
    sc = sc_idx.get(pid) or sc_idx.get(_canonical_ident(pid))
    if sc is not None:
        return sc
    # Alias path: a marketing-rename id maps onto its canonical card.
    try:
        from backend.policy_identity import clean_display_policy_name
        want = clean_display_policy_name(pid)
    except Exception:  # noqa: BLE001
        want = pid
    for c in card_idx.values():
        if pid in (c.aliases or []) or want in (c.aliases or []):
            cand = sc_idx.get(c.policy_id) or sc_idx.get(
                _canonical_ident(c.policy_id)
            )
            if cand is not None:
                return cand
    return None


def marketplace_grade(policy_id: str) -> dict:
    """{"_grade", "_overall_score"} for policy_id, identical to its
    marketplace card. Resolution order: exact id -> product_key -> UIN
    (so a marketing-rename / variant id maps onto its canonical card).
    Returns {} only when the policy is unknown to the marketplace."""
    if not policy_id:
        return {}
    idx = _mg_index()
    from backend.policy_identity import product_key as _pk
    cur = _load_curated_facts()
    pid = policy_id.strip()
    keys = [f"id:{pid}", f"pk:{_pk(pid)}"]
    cdata = cur.get(pid) or cur.get(_pk(pid)) or {}
    uin = _mg_norm_uin(cdata.get("uin_code") or cdata.get("uin"))
    if not uin:
        try:
            import json as _j
            for cand in (pid, _pk(pid)):
                ep = settings.EXTRACTED_DIR / f"{cand}.json"
                if ep.exists():
                    uin = _mg_norm_uin(_j.loads(ep.read_text()).get("uin_code"))
                    if uin:
                        break
        except Exception:  # noqa: BLE001
            uin = ""
    if uin:
        keys.append(f"uin:{uin}")
    for k in keys:
        if k in idx:
            g, o = idx[k]
            return {"_grade": g, "_overall_score": o}
    return {}
