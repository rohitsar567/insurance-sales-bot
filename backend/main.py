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
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.config import settings
from backend.orchestrator import handle_turn
from backend.providers.sarvam_stt import SarvamSTT
from backend.providers.sarvam_tts import SarvamTTS

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


class CoverageResponse(BaseModel):
    total_chunks: int
    total_policies: int
    total_insurers: int
    insurers: list[InsurerCoverage]


class UploadResponse(BaseModel):
    policy_id: str
    policy_name: str
    chunks_added: int
    pages_indexed: int
    elapsed_ms: int


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


# ---------- Admin panel + LLM health background loop ----------
# Mount the password-gated admin endpoints (KI-097). Unauthorized callers
# get 401 Unauthorized. The earlier IP allowlist gate (ADMIN_IP_ALLOWLIST +
# 404-to-hide-existence) was removed in KI-097 — operationally it locked
# the operator out when switching networks without adding real security
# beyond a strong password.
from backend import admin as _admin_router_module
app.include_router(_admin_router_module.router)


@app.on_event("startup")
async def _startup_load_admin_overrides():
    """Re-apply any persisted chain reorderings from the previous process."""
    import asyncio
    from pathlib import Path
    override_path = Path(__file__).resolve().parent.parent / "40-data" / "admin_overrides.json"
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
    """Speech-to-text. Accepts an audio file upload (WAV/MP3/etc.)."""
    t0 = time.time()
    audio_bytes = await file.read()
    ext = (file.filename or "audio.wav").rsplit(".", 1)[-1].lower()
    # Pass the real extension through; sarvam_stt.py transcodes non-native
    # containers (webm/opus from browser MediaRecorder) to WAV before upload.
    try:
        result = await get_stt().transcribe(
            audio_bytes=audio_bytes,
            audio_format=ext if ext in ("wav", "mp3", "flac", "ogg", "m4a", "webm", "opus", "mp4") else "wav",
            language_code=language_code,
        )
    except Exception as e:
        raise HTTPException(500, f"STT failed: {type(e).__name__}: {e}")
    latency = int((time.time() - t0) * 1000)
    return TranscribeResponse(
        text=result.text,
        language_code=result.language_code,
        confidence=result.confidence,
        latency_ms=latency,
    )


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session_id = req.session_id or str(uuid.uuid4())
    t_chat0 = time.time()
    # KI-106 — never let an inner TimeoutError / unhandled exception bubble out
    # of handle_turn as a 500. C4 NRI persona saw 5× HTTP 500s with
    # "Orchestrator failed: TimeoutError" because the outer non-fact-find
    # brain call propagated asyncio.TimeoutError from KI-099/100 wait_for
    # wrappers. We also wrap the whole call in an outer 45s budget so even a
    # pathological hang inside handle_turn surfaces as a graceful reply,
    # not a connection-reset to the user. 45s is generous but tighter than
    # HF Space's gateway timeout, so the user always gets a response.
    try:
        turn = await asyncio.wait_for(
            handle_turn(
                user_text=req.user_text,
                chat_history=req.chat_history,
                user_profile=req.profile,
                policy_filter_ids=req.policy_filter_ids,
                session_id=session_id,
                view_context=req.view_context,
            ),
            timeout=45.0,
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
        )

    audio_b64 = None
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
            audio = await get_tts().synthesize(spoken, language_code=req.tts_language_code)
            audio_b64 = base64.b64encode(audio).decode("utf-8")
        except Exception as e:
            # Don't fail the whole turn if TTS hiccups — log + return text only
            log_turn({"session_id": session_id, "tts_error": f"{type(e).__name__}: {e}"})

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

    return ChatResponse(
        reply_text=turn.reply_text,
        citations=[CitationOut(**c) for c in turn.citations],
        brain_used=turn.brain_used,
        intent=turn.intent,
        language=turn.language,
        latency_ms=turn.latency_ms,
        session_id=session_id,
        audio_base64=audio_b64,
        faithfulness_passed=turn.faithfulness_passed,
        faithfulness_reasons=turn.faithfulness_reasons,
        blocked=turn.blocked,
        profile_updates=turn.profile_updates,
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
    # (see eval/verified_urls.json + tools/verify_urls.py)
    insurer_meta = {
        "aditya-birla":  ("Aditya Birla Health Insurance", "https://www.adityabirlacapital.com/healthinsurance"),
        "bajaj-allianz": ("Bajaj Allianz General Insurance", "https://www.bajajallianz.com/"),
        "care-health":   ("Care Health Insurance", "https://www.careinsurance.com/"),
        "hdfc-ergo":     ("HDFC ERGO General Insurance", "https://www.hdfcergo.com/"),
        "icici-lombard": ("ICICI Lombard General Insurance", "https://www.icicilombard.com/"),
        "manipalcigna":  ("ManipalCigna Health Insurance", "https://www.manipalcigna.com/"),
        "new-india":     ("New India Assurance", "https://www.newindia.co.in/"),
        "niva-bupa":     ("Niva Bupa Health Insurance", "https://www.nivabupa.com/"),
        "star-health":   ("Star Health & Allied Insurance", "https://www.starhealth.in/"),
        "tata-aig":      ("Tata AIG General Insurance", "https://www.tataaig.com/"),
        "user-upload":   ("Your uploaded policies", ""),
    }

    # policy -> source_url (verified at download time)
    policy_urls: dict[tuple[str, str], str] = {}
    by_insurer: dict[str, dict] = {}
    if total > 0:
        try:
            res = coll.get(limit=10000, include=["metadatas"])
            for m in res.get("metadatas", []):
                slug = m.get("insurer_slug", "unknown")
                # KI-129 (2026-05-15) — profile chunks live in the same
                # collection as policies (KI-118 design — they get retrieval-
                # boosted as USER CONTEXT inline with policy text), but they
                # must NEVER count as a user-facing insurer or policy. Skip.
                if slug == "profile" or m.get("doc_type") == "profile":
                    continue
                name = m.get("policy_name", "")
                url = m.get("source_url", "")
                if slug not in by_insurer:
                    by_insurer[slug] = {"policies": set(), "chunks": 0}
                by_insurer[slug]["policies"].add(name)
                by_insurer[slug]["chunks"] += 1
                if url and (slug, name) not in policy_urls:
                    policy_urls[(slug, name)] = url
        except Exception:
            pass

    insurers_out = []
    total_policies = 0
    for slug, info in sorted(by_insurer.items()):
        policy_names = sorted(info["policies"])
        total_policies += len(policy_names)
        name, home_url = insurer_meta.get(slug, (slug, ""))
        sample_entries = [
            PolicyEntry(name=p, source_url=policy_urls.get((slug, p), ""))
            for p in policy_names[:8]
        ]
        insurers_out.append(
            InsurerCoverage(
                slug=slug,
                name=name,
                home_url=home_url,
                policy_count=len(policy_names),
                sample_policies=sample_entries,
            )
        )

    return CoverageResponse(
        total_chunks=total,
        total_policies=total_policies,
        total_insurers=len(insurers_out),
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

    # Ingest just this one file
    try:
        from rag.ingest import chunk_pages, get_quarantine_collection, read_pdf_pages
        from backend.providers.local_embeddings import LocalEmbeddings as _Emb
        from backend.security import check_upload, rate_limiter

        pages = read_pdf_pages(out_path)
        # Run 8-gate security check (dedupe + mechanics + encrypted + content +
        # page ceiling + injection + per-session + per-IP rate limit + LLM judge)
        full_text = "\n".join(t for _, t in pages)
        client_ip = (request.client.host if request and request.client else "") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        verdict = await check_upload(
            content=contents,
            extracted_text=full_text,
            page_count=len(pages),
            session_id=sid,
            ip=client_ip,
        )
        if not verdict.accepted:
            out_path.unlink(missing_ok=True)
            raise HTTPException(
                400,
                f"Upload rejected by security gates: {', '.join(verdict.reasons[:3])}",
            )
        # If the dedupe gate found this exact (hash, session) already indexed,
        # skip chunking + embedding entirely and return the cached chunk count.
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
        # Update rate-limit ledger after successful index
        rate_limiter.record_upload(sid, len(chunks))
        # Cache this content hash → chunk count so an identical re-upload in
        # the same session short-circuits via gate_hash_dedupe.
        try:
            sha = _hashlib.sha256(contents).hexdigest()
            record_accept(sha, sid, len(chunks))
        except Exception:
            pass
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Indexing failed: {type(e).__name__}: {e}")

    return UploadResponse(
        policy_id=policy_id,
        policy_name=policy_name,
        chunks_added=len(chunks),
        pages_indexed=len(pages),
        elapsed_ms=int((_time.time() - t0) * 1000),
    )


class ScorecardSubScore(BaseModel):
    name: str
    score: int
    summary: str
    signals: list[str]


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


class SessionResetRequest(BaseModel):
    session_id: str
    drop_profile: bool = False  # True = nuke session entirely; False = clear chat only
    confirm: bool = False  # KI-095 — must be True when drop_profile=True; guards accidental wipes


class SessionResetResponse(BaseModel):
    ok: bool
    session_id: Optional[str] = None  # new session_id when drop_profile=True
    cleared_state: bool


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
    from backend.profile_rag import upsert_profile_chunk

    sess = get_session(req.session_id)
    # Update only fields the client explicitly sent (non-None) — keeps partial
    # save flows clean
    for field_name in (
        "name",  # KI-077 — accept name updates from the profile-builder UI
        "age", "dependents", "income_band", "existing_cover_inr", "primary_goal",
        "location_tier", "parents_to_insure", "parents_age_max", "parents_has_ped",
        "health_conditions", "budget_band",
    ):
        v = getattr(req, field_name, None)
        if v in (None, "", []):
            # KI-095 — never clobber a filled field with empty input from the client
            continue
        setattr(sess.profile, field_name, v)

    # KI-077 — if name is set, also persist to the named-profile store so a
    # returning visitor's profile is recoverable across sessions.
    if req.name:
        try:
            from backend.profile_store import save_profile
            save_profile(req.name, sess.profile, session_id=req.session_id)
        except Exception as e:
            print(f"[profile_store] save failed for {req.name}: {type(e).__name__}: {e}")

    p = sess.profile
    profile_dict = {
        "name": p.name,  # KI-077
        "age": p.age, "dependents": p.dependents, "income_band": p.income_band,
        "existing_cover_inr": p.existing_cover_inr, "primary_goal": p.primary_goal,
        "location_tier": p.location_tier, "parents_to_insure": p.parents_to_insure,
        "parents_age_max": p.parents_age_max, "parents_has_ped": p.parents_has_ped,
        "health_conditions": p.health_conditions, "budget_band": p.budget_band,
    }
    c = _completeness(profile_dict)
    collected = [k for k, v in profile_dict.items() if v not in (None, "", [], False)]
    missing = [k for k, v in profile_dict.items() if v in (None, "", [])]

    # Ingest the profile into the RAG store so the brain sees user context
    # at retrieval time alongside policy + regulatory chunks. Fire-and-forget
    # — a profile upsert failure shouldn't block the API response.
    # KI-118 (2026-05-15) — gated on a known name; anonymous saves don't
    # write to Chroma. The chunk is keyed by canonical name slug, not the
    # session_id which is now opaque/in-memory.
    try:
        if p.name:
            from backend.profile_store import _normalise_name
            name_slug = _normalise_name(p.name)
            if name_slug:
                await upsert_profile_chunk(name_slug, profile_dict)
    except Exception as e:
        print(f"[profile_rag] upsert failed for {req.session_id}: {type(e).__name__}: {e}")

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
    profile_dict = {
        "age": p.age, "dependents": p.dependents, "income_band": p.income_band,
        "existing_cover_inr": p.existing_cover_inr, "primary_goal": p.primary_goal,
        "location_tier": p.location_tier, "parents_to_insure": p.parents_to_insure,
        "parents_age_max": p.parents_age_max, "parents_has_ped": p.parents_has_ped,
        "health_conditions": p.health_conditions, "budget_band": p.budget_band,
    }
    c = _completeness(profile_dict)
    collected = [k for k, v in profile_dict.items() if v not in (None, "", [], False)]
    missing = [k for k, v in profile_dict.items() if v in (None, "", [])]
    hint = None
    try:
        nq = next_question(p)
        if nq:
            hint = nq.prompt_en
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
    # Headline filterable fields
    min_entry_age: Optional[int] = None
    max_entry_age: Optional[int] = None
    max_renewal_age: Optional[int] = None
    sum_insured_options: list[int] = Field(default_factory=list)
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


class MarketplaceResponse(BaseModel):
    policies: list[MarketplacePolicy]
    total: int
    insurers_indexed: int


@app.get("/api/scorecard/methodology")
async def scorecard_methodology():
    """Transparency endpoint — returns the 6-criterion blueprint with weights,
    consumer rationale, fields driving each sub-score, and regulatory anchors.

    Frontend renders this inside PolicyDetailModal so the user can see exactly
    how the headline number is computed and which of the 48 HealthPolicy fields
    feed into which criterion.
    """
    from backend.scorecard import METHODOLOGY_BLUEPRINT, WEIGHTS, SCORED_FIELDS
    return {
        "weights": WEIGHTS,
        "scored_fields_count": len(SCORED_FIELDS),
        "total_schema_fields": 48,
        "criteria": METHODOLOGY_BLUEPRINT,
        "grade_thresholds": {
            "A": "≥85 — strong all-rounder",
            "B": "70–84 — good with a few gaps",
            "C": "55–69 — check trade-offs",
            "D": "40–54 — material concerns",
            "F": "<40 — significant gaps",
        },
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
    md_path = settings.CORPUS_DIR.parent.parent / "40-data" / "corpus_urls.md"
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
    """
    import json as _json
    facts: dict[str, dict] = {}
    facts_dir = settings.CORPUS_DIR.parent.parent / "40-data" / "policy_facts"
    if not facts_dir.exists():
        return facts
    for f in facts_dir.glob("*.json"):
        try:
            d = _json.loads(f.read_text())
        except Exception:
            continue
        policy_id = d.get("policy_id") or f.stem
        flat: dict = {}
        provenance: dict = {}
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
            else:
                flat[k] = v
        flat["_facts_provenance"] = provenance
        # Try a couple of policy_id permutations to maximise lookup hit rate
        facts[policy_id] = flat
        # Some extracted JSONs use `_wordings` suffix; the curated files don't
        facts.setdefault(f"{policy_id}__wordings", flat)
        facts.setdefault(f"{policy_id}__brochure", flat)
        facts.setdefault(f"{policy_id}__cis", flat)
    return facts


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

    # Pull user profile if we have one
    user_profile_dict: Optional[dict] = None
    if session_id:
        sess = _get_sess(session_id)
        p = sess.profile
        profile_dict = {
            "age": p.age, "dependents": p.dependents, "income_band": p.income_band,
            "existing_cover_inr": p.existing_cover_inr, "primary_goal": p.primary_goal,
            "location_tier": p.location_tier, "parents_to_insure": p.parents_to_insure,
            "parents_age_max": p.parents_age_max, "parents_has_ped": p.parents_has_ped,
            "health_conditions": p.health_conditions, "budget_band": p.budget_band,
        }
        if _completeness(profile_dict) >= 0.6:
            user_profile_dict = profile_dict

    corpus_url_index = _build_corpus_url_index()
    curated_facts = _load_curated_facts()

    insurer_meta = {
        "aditya-birla":  ("Aditya Birla Health Insurance", "https://www.adityabirlacapital.com/healthinsurance"),
        "bajaj-allianz": ("Bajaj Allianz General Insurance", "https://www.bajajallianz.com/"),
        "care-health":   ("Care Health Insurance", "https://www.careinsurance.com/"),
        "hdfc-ergo":     ("HDFC ERGO General Insurance", "https://www.hdfcergo.com/"),
        "icici-lombard": ("ICICI Lombard General Insurance", "https://www.icicilombard.com/"),
        "manipalcigna":  ("ManipalCigna Health Insurance", "https://www.manipalcigna.com/"),
        "new-india":     ("New India Assurance", "https://www.newindia.co.in/"),
        "niva-bupa":     ("Niva Bupa Health Insurance", "https://www.nivabupa.com/"),
        "star-health":   ("Star Health & Allied Insurance", "https://www.starhealth.in/"),
        "tata-aig":      ("Tata AIG General Insurance", "https://www.tataaig.com/"),
    }

    def _coerce_bool(v):
        if isinstance(v, dict) and "covered" in v: return v.get("covered")
        if isinstance(v, bool): return v
        return None

    # Build a unified policy set: every extracted JSON + every curated facts
    # JSON that doesn't have an extracted counterpart yet. This way, even
    # policies whose LLM extraction failed still surface in the marketplace
    # with their human-curated data.
    seen_policy_ids: set[str] = set()
    out = []

    # Pass 1: existing extracted policies (merged with curated overrides)
    for fp in sorted(settings.EXTRACTED_DIR.glob("*.json")):
        try:
            data = _json.loads(fp.read_text())
        except Exception:
            continue
        policy_id_local = data.get("policy_id", fp.stem)
        curated_for_this = curated_facts.get(policy_id_local) or curated_facts.get(fp.stem)
        data = _merge_curated(data, curated_for_this)
        seen_policy_ids.add(policy_id_local)
        slug = data.get("insurer_slug", "")
        name, home = insurer_meta.get(slug, (slug, ""))
        # Get insurer reviews if available for the scorecard
        ir = None
        if slug:
            rp = settings.CORPUS_DIR.parent.parent / "40-data" / "reviews" / f"{slug}.json"
            if rp.exists():
                try: ir = _json.loads(rp.read_text())
                except Exception: pass
        sc = build_scorecard(data, insurer_reviews=ir, profile=user_profile_dict)

        si = data.get("sum_insured_options") or []
        if isinstance(si, list):
            si = [int(x) for x in si if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
        else:
            si = []

        try:
            policy_id = data.get("policy_id", fp.stem)
            # Backfill source_pdf_url from corpus_urls.md when extraction didn't
            # populate it. Try exact policy_id match first, then key permutations.
            source_pdf_url = (
                data.get("source_pdf_url")
                or corpus_url_index.get(policy_id)
                or corpus_url_index.get(fp.stem)
                or ""
            )
            out.append(MarketplacePolicy(
                policy_id=policy_id,
                policy_name=data.get("policy_name", fp.stem),
                insurer_slug=slug,
                insurer_name=name,
                insurer_home_url=home,
                source_pdf_url=source_pdf_url,
                grade=sc.grade,
                overall_score=sc.overall_score,
                one_liner=sc.one_liner,
                data_completeness_pct=sc.data_completeness_pct,
                min_entry_age=data.get("min_entry_age"),
                max_entry_age=data.get("max_entry_age"),
                max_renewal_age=data.get("max_renewal_age"),
                sum_insured_options=si,
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
            ))
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
        # Also skip if any extracted ID matches with a suffix
        if any(eid.startswith(curated_policy_id + "__") for eid in seen_policy_ids):
            continue
        seen_policy_ids.add(curated_policy_id)
        slug = data.get("insurer_slug", "")
        name, home = insurer_meta.get(slug, (slug, ""))
        # Insurer reviews for scorecard
        ir = None
        if slug:
            rp = settings.CORPUS_DIR.parent.parent / "40-data" / "reviews" / f"{slug}.json"
            if rp.exists():
                try:
                    ir = _json.loads(rp.read_text())
                except Exception:
                    pass
        sc = build_scorecard(data, insurer_reviews=ir, profile=user_profile_dict)
        si = data.get("sum_insured_options") or []
        if isinstance(si, list):
            si = [int(x) for x in si if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
        else:
            si = []
        try:
            source_pdf_url = (
                data.get("source_pdf_url")
                or corpus_url_index.get(curated_policy_id)
                or corpus_url_index.get(f"{curated_policy_id}__wordings")
                or ""
            )
            out.append(MarketplacePolicy(
                policy_id=curated_policy_id,
                policy_name=data.get("policy_name", curated_policy_id),
                insurer_slug=slug,
                insurer_name=name,
                insurer_home_url=home,
                source_pdf_url=source_pdf_url,
                grade=sc.grade,
                overall_score=sc.overall_score,
                one_liner=sc.one_liner,
                data_completeness_pct=sc.data_completeness_pct,
                min_entry_age=data.get("min_entry_age"),
                max_entry_age=data.get("max_entry_age"),
                max_renewal_age=data.get("max_renewal_age"),
                sum_insured_options=si,
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
            ))
        except Exception as e:
            print(f"[marketplace] skipping curated {curated_policy_id}: {type(e).__name__}: {str(e)[:120]}")
            continue

    return MarketplaceResponse(
        policies=out,
        total=len(out),
        insurers_indexed=len({p.insurer_slug for p in out}),
    )


@app.get("/api/policies/compare", response_model=CompareResponse)
async def compare_policies(policy_ids: list[str] = None):
    """Side-by-side comparison of 2-4 policies with their scorecards + field diffs."""
    import json as _json
    from backend.scorecard import build_scorecard

    if not policy_ids:
        from fastapi import Query
        raise HTTPException(400, "Provide policy_ids as repeated query params")
    if len(policy_ids) < 2 or len(policy_ids) > 4:
        raise HTTPException(400, "compare requires 2 to 4 policy_ids")

    entries = []
    for pid in policy_ids:
        p = settings.EXTRACTED_DIR / f"{pid}.json"
        if not p.exists():
            raise HTTPException(404, f"No extraction for {pid}")
        data = _json.loads(p.read_text())
        # Insurer reviews for scorecard
        slug = data.get("insurer_slug")
        ir = None
        if slug:
            rp = settings.CORPUS_DIR.parent.parent / "40-data" / "reviews" / f"{slug}.json"
            if rp.exists():
                try: ir = _json.loads(rp.read_text())
                except Exception: pass
        sc = build_scorecard(data, insurer_reviews=ir)
        entries.append(CompareEntry(
            policy_id=pid,
            policy_name=data.get("policy_name", pid),
            insurer_slug=slug or "?",
            fields=data,
            scorecard=ScorecardResponse(
                policy_id=sc.policy_id, policy_name=sc.policy_name, insurer_slug=sc.insurer_slug,
                overall_score=sc.overall_score, grade=sc.grade, one_liner=sc.one_liner,
                sub_scores=[ScorecardSubScore(**s.__dict__) for s in sc.sub_scores],
                data_completeness_pct=sc.data_completeness_pct,
                methodology_link=sc.methodology_link,
            ),
        ))

    # Comparison-critical fields, in order
    field_order = [
        "policy_type", "uin_code",
        "min_entry_age", "max_entry_age", "max_renewal_age",
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
):
    """Compute the 6-sub-score A-F scorecard for an extracted policy.

    Now also pulls insurer-level reviews (IRDAI claim ratio + complaints) into
    the Claim Experience sub-score. See 70-docs/scorecard-methodology.md.
    """
    import json as _json

    from backend.scorecard import build_scorecard

    extracted_path = settings.EXTRACTED_DIR / f"{policy_id}.json"
    if not extracted_path.exists():
        raise HTTPException(404, f"No extracted data for policy_id={policy_id}")

    try:
        policy = _json.loads(extracted_path.read_text())
    except Exception as e:
        raise HTTPException(500, f"Could not load extracted policy: {e}")

    # Load insurer reviews if present so the Claim Experience sub-score
    # uses authoritative IRDAI data, not just the (mostly-null) per-policy fields.
    insurer_reviews = None
    slug = policy.get("insurer_slug")
    if slug:
        rp = settings.CORPUS_DIR.parent.parent / "40-data" / "reviews" / f"{slug}.json"
        if rp.exists():
            try:
                insurer_reviews = _json.loads(rp.read_text())
            except Exception:
                pass

    profile: dict = {}
    if age is not None: profile["age"] = age
    if parents_to_insure is not None: profile["parents_to_insure"] = parents_to_insure
    if budget_band is not None: profile["budget_band"] = budget_band

    sc = build_scorecard(policy, insurer_reviews=insurer_reviews, profile=profile or None)
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
    )


class ReviewsResponse(BaseModel):
    insurer_slug: str
    insurer_name: str
    aggregate_score: dict
    claim_metrics: dict
    aggregator_ratings: dict
    reddit_sentiment: dict
    youtube_coverage: dict
    in_news: list
    trustpilot: dict
    last_updated: str


@app.get("/api/insurers/{insurer_slug}/reviews", response_model=ReviewsResponse)
async def get_reviews(insurer_slug: str):
    """Aggregated reviews + claim metrics for an insurer.

    Data sourced from IRDAI annual report + PolicyBazaar/InsuranceDekho +
    Reddit r/IndianFinance + YouTube finance creators (Ditto et al) +
    news mentions. Per-insurer JSON at 40-data/reviews/<slug>.json — see
    40-data/reviews/INDEX.md for leaderboard.
    """
    import json
    p = settings.CORPUS_DIR.parent.parent / "40-data" / "reviews" / f"{insurer_slug}.json"
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


@app.post("/api/premium/estimate", response_model=PremiumEstimateResponse)
async def premium_estimate(req: PremiumEstimateRequest):
    """Illustrative premium calculator — rules-based estimate from curated public data."""
    from backend.premium_calculator import estimate as _estimate
    e = _estimate(
        age=req.age,
        sum_insured_inr=req.sum_insured_inr,
        city_tier=req.city_tier,
        smoker=req.smoker,
        family_size=req.family_size,
        policy_id=req.policy_id,
        pre_existing_conditions=req.pre_existing_conditions,
        copayment_pct=req.copayment_pct,
    )
    return PremiumEstimateResponse(
        policy_id=e.policy_id,
        point_estimate_inr=e.point_estimate_inr,
        low_inr=e.low_inr,
        high_inr=e.high_inr,
        methodology=e.methodology,
        sources=e.sources or [],
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
