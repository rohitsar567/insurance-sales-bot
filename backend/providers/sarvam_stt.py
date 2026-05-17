"""Sarvam Saarika v2.5 — speech-to-text.

Endpoint: POST https://api.sarvam.ai/speech-to-text
Auth: header `api-subscription-key: <SARVAM_API_KEY>`
Request: multipart/form-data with `file`, `model`, optional `language_code`
Response: {"transcript": str, "language_code": str?, "language_probability": float?, ...}
"""

from __future__ import annotations

import io
import logging
from typing import List, Optional

import httpx

from backend.config import settings
from backend.providers.base import STTProvider, STTResult

_log = logging.getLogger(__name__)

# Sarvam's saarika REST /speech-to-text endpoint has a hard ~30s audio
# limit. Critically, it does NOT 4xx on longer audio — it returns HTTP 200
# with a `transcript` containing ONLY the first ~30s and silently drops the
# rest. The live-voice hook's grace-window batching (UTTERANCE_GRACE_MS)
# deliberately merges multiple pause-separated speech bursts into ONE blob,
# so any real-world utterance with natural pauses easily exceeds 30s of
# wall-clock audio and gets truncated. To capture the COMPLETE utterance we
# split anything over the safe ceiling into <= SAFE chunks (at silence
# boundaries where possible so words aren't cut mid-syllable), transcribe
# each chunk, and concatenate the transcripts in order.
#
# Ceiling is set below the documented 30s so the boundary search + minor
# encode jitter never pushes a chunk back over Sarvam's real limit.
STT_CHUNK_MS = 25_000          # target max length per Sarvam call
STT_SILENCE_SEARCH_MS = 4_000  # widen a cut by up to this to land on silence
STT_MIN_SILENCE_MS = 350       # a gap >= this counts as a sentence pause
STT_MAX_TOTAL_MS = 15 * 60_000  # hard safety cap (15 min) — refuse absurd input


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
    def _load_segment(audio_bytes: bytes, src_format: str):
        """Decode any pydub-readable container to a 16 kHz mono AudioSegment.

        Sarvam's recommended sampling rate is 16 kHz mono — what Saarika
        was trained on. Down-mixing + resampling at the gateway also
        prevents Sarvam from doing it server-side, which keeps latency tight.
        """
        from pydub import AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=src_format)
        return audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)

    @staticmethod
    def _segment_to_wav_bytes(audio) -> bytes:
        buf = io.BytesIO()
        audio.export(buf, format="wav")
        return buf.getvalue()

    @classmethod
    def _transcode_to_wav(cls, audio_bytes: bytes, src_format: str) -> bytes:
        """Convert any pydub-readable container to 16 kHz mono WAV."""
        return cls._segment_to_wav_bytes(cls._load_segment(audio_bytes, src_format))

    @staticmethod
    def _split_on_silence(audio, chunk_ms: int) -> List:
        """Split `audio` into <= chunk_ms slices.

        We never cut a slice longer than chunk_ms. Where possible the cut is
        nudged BACKWARDS to the quietest point within the last
        STT_SILENCE_SEARCH_MS of the slice so the boundary lands in a pause
        rather than mid-word (which would drop a syllable from BOTH the tail
        of one chunk and the head of the next). If no clear pause is found we
        cut at the hard chunk_ms boundary — a worst-case half-word seam, far
        better than silently losing every word past 30s.
        """
        total = len(audio)
        if total <= chunk_ms:
            return [audio]

        # Cheap energy probe per 100ms frame so we can find the quietest
        # spot near each prospective cut without an external VAD.
        from pydub import AudioSegment  # noqa: F401  (ensures pydub import path)

        chunks: List = []
        start = 0
        while start < total:
            if total - start <= chunk_ms:
                chunks.append(audio[start:])
                break

            hard_cut = start + chunk_ms
            search_lo = max(start + 1, hard_cut - STT_SILENCE_SEARCH_MS)
            window = audio[search_lo:hard_cut]

            # Find the 100ms frame with the lowest dBFS in the search window.
            best_offset = None
            best_db = None
            step = 100
            for off in range(0, max(1, len(window)), step):
                frame = window[off:off + step]
                if len(frame) == 0:
                    continue
                db = frame.dBFS  # -inf for pure digital silence
                if best_db is None or db < best_db:
                    best_db = db
                    best_offset = off

            # Only honour the silence cut if the quietest frame is actually
            # quiet relative to the segment (a real pause), else hard-cut.
            cut = hard_cut
            if (
                best_offset is not None
                and best_db is not None
                and (best_db == float("-inf") or best_db < audio.dBFS - 12)
            ):
                cut = search_lo + best_offset + step // 2
                cut = min(max(cut, start + 1), hard_cut)

            chunks.append(audio[start:cut])
            start = cut
        return chunks

    async def _transcribe_wav_bytes(
        self,
        wav_bytes: bytes,
        language_code: Optional[str],
    ) -> dict:
        """POST one already-encoded WAV blob to Sarvam, return its JSON.

        Raises on transport / HTTP errors so the caller's classifier can map
        them to a closed error_code — we deliberately do NOT swallow here.
        """
        url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_STT_PATH}"
        files = {
            "file": ("audio.wav", io.BytesIO(wav_bytes), "audio/wav"),
        }
        data = {"model": self.model}
        if language_code:
            data["language_code"] = language_code
        headers = {"api-subscription-key": self.api_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()
            return resp.json()

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

        # Decode to a 16 kHz mono AudioSegment so we can (a) measure the true
        # duration and (b) chunk anything over Sarvam's ~30s REST limit. The
        # legacy path only transcoded webm→wav; native containers (wav/mp3/
        # m4a/...) went straight through UNMEASURED, so a 90s m4a was sent
        # whole and silently truncated to its first 30s by Sarvam. Decoding
        # every format here closes that hole for ALL callers (live voice +
        # push-to-talk).
        segment = None
        try:
            segment = self._load_segment(audio_bytes, fmt)
        except Exception:
            # pydub/ffmpeg couldn't decode (truly corrupt audio, or a codec
            # ffmpeg lacks). Fall back to the original single-shot path so
            # Sarvam returns its own error rather than us swallowing it.
            # Native formats are sent as-is; unknown ones default to wav.
            _log.warning(
                "STT decode failed (fmt=%s, %d bytes) — single-shot fallback",
                fmt,
                len(audio_bytes),
            )
            send_fmt = fmt if fmt in self._SARVAM_NATIVE_FORMATS else "wav"
            url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_STT_PATH}"
            files = {
                "file": (f"audio.{send_fmt}", io.BytesIO(audio_bytes), f"audio/{send_fmt}"),
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

        total_ms = len(segment)
        if total_ms > STT_MAX_TOTAL_MS:
            # Refuse absurd input loudly rather than burn N Sarvam calls.
            raise ValueError(
                f"audio too long for STT: {total_ms} ms > cap {STT_MAX_TOTAL_MS} ms"
            )

        chunks = self._split_on_silence(segment, STT_CHUNK_MS)

        # Common case: short utterance, one Sarvam call (identical wire
        # behaviour to the pre-fix path, just always WAV-normalised).
        if len(chunks) == 1:
            payload = await self._transcribe_wav_bytes(
                self._segment_to_wav_bytes(chunks[0]), language_code
            )
            return STTResult(
                text=payload.get("transcript", ""),
                language_code=payload.get("language_code"),
                confidence=payload.get("language_probability"),
                raw=payload,
            )

        # Long utterance: transcribe every chunk, then stitch the
        # transcripts back together in order so the COMPLETE utterance
        # survives. If any chunk fails we let the exception propagate so the
        # /api/transcribe classifier surfaces a real error_code — we never
        # return a silently-partial transcript (no silent failures).
        _log.info(
            "STT chunked transcription: %d ms split into %d chunks (<= %d ms each)",
            total_ms,
            len(chunks),
            STT_CHUNK_MS,
        )
        parts: List[str] = []
        first_lang: Optional[str] = None
        first_conf: Optional[float] = None
        raws: List[dict] = []
        for idx, ch in enumerate(chunks):
            payload = await self._transcribe_wav_bytes(
                self._segment_to_wav_bytes(ch), language_code
            )
            piece = (payload.get("transcript") or "").strip()
            if piece:
                parts.append(piece)
            if idx == 0:
                first_lang = payload.get("language_code")
                first_conf = payload.get("language_probability")
            raws.append(payload)

        stitched = " ".join(parts).strip()
        return STTResult(
            text=stitched,
            language_code=first_lang or language_code,
            confidence=first_conf,
            raw={
                "chunked": True,
                "chunk_count": len(chunks),
                "total_ms": total_ms,
                "chunks": raws,
            },
        )
