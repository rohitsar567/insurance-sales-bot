"""Sarvam Saarika v2.5 — speech-to-text.

Endpoint: POST https://api.sarvam.ai/speech-to-text
Auth: header `api-subscription-key: <SARVAM_API_KEY>`
Request: multipart/form-data with `file`, `model`, optional `language_code`
Response: {"transcript": str, "language_code": str?, "language_probability": float?, ...}
"""

from __future__ import annotations

import io
from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import STTProvider, STTResult


# Public error-code vocabulary returned by /api/transcribe when Sarvam fails.
# Frontends (PTT + live voice) consume these as a closed enum so they can map
# each cause to a user-friendly reply without ever parsing httpx error text.
STT_ERROR_RATE_LIMIT = "rate_limit"
STT_ERROR_SERVICE = "service_unavailable"
STT_ERROR_NETWORK = "network"
STT_ERROR_AUTH = "auth"
STT_ERROR_UNKNOWN = "unknown"

# Human-readable reply per error_code. Kept here so the message lives next to
# the classifier — single source of truth for both backend response shaping
# and any place a tool wants to render the same string.
STT_ERROR_USER_MESSAGES = {
    STT_ERROR_RATE_LIMIT: (
        "Voice is busy right now — please try again in a moment, "
        "or type your question."
    ),
    STT_ERROR_SERVICE: (
        "Voice service is temporarily unavailable — please type your "
        "question or try voice again shortly."
    ),
    STT_ERROR_NETWORK: (
        "Network hiccup while transcribing — please try again, or "
        "type your question."
    ),
    STT_ERROR_AUTH: (
        "Voice service is unavailable right now — please type your question."
    ),
    STT_ERROR_UNKNOWN: (
        "Couldn't transcribe that — please try again or type your question."
    ),
}


def classify_stt_exception(exc: BaseException) -> str:
    """Map an httpx / network exception to a STT_ERROR_* code.

    KI-242 pattern — the frontend never reads raw httpx text. Backend
    classifies once at the boundary so PTT + live voice + any future
    caller share one closed vocabulary.
    """
    # httpx raises HTTPStatusError on resp.raise_for_status() with the
    # original Response attached. Status code is the most reliable signal.
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        if status == 429:
            return STT_ERROR_RATE_LIMIT
        if status in (401, 403):
            return STT_ERROR_AUTH
        if 500 <= status < 600:
            return STT_ERROR_SERVICE
        return STT_ERROR_UNKNOWN
    # TimeoutException covers connect/read/write/pool timeouts.
    if isinstance(exc, httpx.TimeoutException):
        return STT_ERROR_NETWORK
    # NetworkError covers ConnectError, ReadError, RemoteProtocolError, etc.
    if isinstance(exc, httpx.NetworkError):
        return STT_ERROR_NETWORK
    if isinstance(exc, httpx.HTTPError):
        return STT_ERROR_UNKNOWN
    return STT_ERROR_UNKNOWN


class SarvamSTT(STTProvider):
    name = "sarvam-saarika"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = settings.SARVAM_STT_MODEL,
        timeout: float = 30.0,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY not set in .env")

    # Sarvam STT accepts these container formats per their API. webm is NOT
    # in this list — browser MediaRecorder defaults to webm/opus, so we
    # transcode webm → wav (16kHz mono) on the fly via pydub before calling
    # Sarvam. This is the fix for the live "400 Bad Request" we hit when
    # the browser's webm bytes were uploaded as if they were wav.
    _SARVAM_NATIVE_FORMATS = {"wav", "mp3", "flac", "ogg", "m4a"}

    @staticmethod
    def _transcode_to_wav(audio_bytes: bytes, src_format: str) -> bytes:
        """Convert any pydub-readable container to 16 kHz mono WAV.

        Sarvam's recommended sampling rate is 16 kHz mono — what Saarika
        was trained on. Down-mixing + resampling at the gateway also
        prevents Sarvam from doing it server-side, which keeps latency tight.
        """
        from pydub import AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=src_format)
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        buf = io.BytesIO()
        audio.export(buf, format="wav")
        return buf.getvalue()

    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language_code: Optional[str] = None,
    ) -> STTResult:
        if not audio_bytes or len(audio_bytes) < 1024:
            # < 1 KB audio is almost certainly silence or a record-and-immediately-stop;
            # Sarvam 400s on these. Surface a clean empty result instead of a 500.
            return STTResult(text="", language_code=language_code, confidence=0.0, raw={"reason": "audio_too_short"})

        fmt = (audio_format or "wav").lower().lstrip(".")
        # Browser MediaRecorder uses webm/opus by default; Sarvam rejects it.
        # Transcode in-process when we get a non-native format.
        if fmt not in self._SARVAM_NATIVE_FORMATS:
            try:
                audio_bytes = self._transcode_to_wav(audio_bytes, fmt)
                fmt = "wav"
            except Exception as e:
                # If pydub/ffmpeg fails (e.g., truly corrupt audio), let Sarvam
                # see the original bytes and return its own error rather than
                # silently swallowing.
                pass

        url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_STT_PATH}"
        files = {
            "file": (f"audio.{fmt}", io.BytesIO(audio_bytes), f"audio/{fmt}"),
        }
        data = {"model": self.model}
        if language_code:
            data["language_code"] = language_code

        headers = {"api-subscription-key": self.api_key}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            payload = resp.json()

        return STTResult(
            text=payload.get("transcript", ""),
            language_code=payload.get("language_code"),
            confidence=payload.get("language_probability"),
            raw=payload,
        )
