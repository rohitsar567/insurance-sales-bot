"""FastAPI app — the backend API for the Insurance Sales Portfolio Expert.

Run locally:
  uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs at http://localhost:8000/docs
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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


@app.get("/api/health", response_model=HealthResponse)
async def health():
    missing = settings.validate()
    providers_ok = {
        "sarvam": bool(settings.SARVAM_API_KEY),
        "voyage": bool(settings.VOYAGE_API_KEY),
        "groq": bool(settings.GROQ_API_KEY),
        "openrouter": bool(settings.OPENROUTER_API_KEY),
    }
    return HealthResponse(
        status="ok" if not missing else "degraded",
        providers_ok=providers_ok,
        missing_keys=missing,
    )


@app.post("/api/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: UploadFile = File(...),
    language_code: Optional[str] = Form(None),
):
    """Speech-to-text. Accepts an audio file upload (WAV/MP3/etc.)."""
    t0 = time.time()
    audio_bytes = await file.read()
    ext = (file.filename or "audio.wav").rsplit(".", 1)[-1].lower()
    try:
        result = await get_stt().transcribe(
            audio_bytes=audio_bytes,
            audio_format=ext if ext in ("wav", "mp3", "flac", "ogg", "m4a") else "wav",
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
    try:
        turn = await handle_turn(
            user_text=req.user_text,
            chat_history=req.chat_history,
            user_profile=req.profile,
            policy_filter_ids=req.policy_filter_ids,
        )
    except Exception as e:
        log_turn({
            "session_id": session_id,
            "user_text": req.user_text,
            "error": f"{type(e).__name__}: {e}",
        })
        raise HTTPException(500, f"Orchestrator failed: {type(e).__name__}: {e}")

    audio_b64 = None
    if req.return_audio and turn.reply_text:
        try:
            audio = await get_tts().synthesize(turn.reply_text, language_code=req.tts_language_code)
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
async def upload_policy(file: UploadFile = File(...)):
    """Accept a user-uploaded PDF policy doc, chunk + embed it, add to Chroma.

    Note: in v1 demo this appends to the shared corpus (single-tenant).
    Production would isolate by session/user.
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

    # Slugify filename for policy_id
    raw = file.filename or "user_upload.pdf"
    stem = _PathLib(raw).stem
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stem.lower()).strip("-")[:80] or "user-upload"
    policy_id = f"user-upload__{slug}"
    policy_name = stem.replace("_", " ").replace("-", " ").title()

    # Save to disk so ingest can read with pdfplumber
    user_dir = settings.CORPUS_DIR / "user-upload"
    user_dir.mkdir(parents=True, exist_ok=True)
    out_path = user_dir / f"{slug}.pdf"
    out_path.write_bytes(contents)

    # Ingest just this one file
    try:
        from rag.ingest import chunk_pages, get_chroma_collection, read_pdf_pages
        from backend.providers.local_embeddings import LocalEmbeddings as _Emb

        pages = read_pdf_pages(out_path)
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
            }
            for c in chunks
        ]
        collection = get_chroma_collection()
        # Remove any existing chunks under this policy_id (re-upload case)
        try:
            collection.delete(where={"policy_id": policy_id})
        except Exception:
            pass
        collection.add(ids=ids, documents=texts, embeddings=vectors, metadatas=metadatas)
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
