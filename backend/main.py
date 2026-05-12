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
from fastapi.responses import JSONResponse
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


@app.get("/")
async def root():
    return {
        "service": "Insurance Sales Portfolio Expert API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/api/health",
    }
