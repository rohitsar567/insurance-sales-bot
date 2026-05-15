"""Sarvam Bulbul — text-to-speech.

Endpoint: POST https://api.sarvam.ai/text-to-speech
Auth: header `api-subscription-key: <SARVAM_API_KEY>`
Request body (JSON):
  {
    "text": "...",
    "target_language_code": "en-IN" | "hi-IN" | ...,
    "speaker": "anushka" | "abhilash" | "manisha" | ...,
    "pitch": float, "pace": float, "loudness": float,
    "model": "bulbul:v2",
    "enable_preprocessing": true,
    "speech_sample_rate": 22050
  }
Response: {"audios": ["<base64 WAV>"]} — decode to bytes.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Optional, Tuple

import httpx

from backend.config import settings
from backend.providers.base import TTSProvider

logger = logging.getLogger(__name__)

# X8 (2026-05-15) — frontend now sends `X-Preferred-Codec: audio/{wav,mp4,webm}`
# header so the chat endpoint can return audio in the codec the user's browser
# decodes natively. Sarvam Bulbul's text-to-speech API itself does NOT support
# codec negotiation in the request body (response is always base64 WAV), so we
# transcode locally with pydub (already in requirements.txt; the Dockerfile
# installs the ffmpeg apt package). If either pydub or ffmpeg is missing in the
# current runtime, we gracefully fall back to raw WAV — the frontend already
# handles audio_mime="audio/wav" as the default playback path.
try:
    from pydub import AudioSegment  # type: ignore
    _PYDUB_AVAILABLE = True
except Exception:  # pragma: no cover — pydub missing in dev shell
    AudioSegment = None  # type: ignore
    _PYDUB_AVAILABLE = False


_SUPPORTED_CODECS = {"audio/wav", "audio/mp4", "audio/webm"}


def _transcode_wav(wav_bytes: bytes, preferred_codec: str) -> Tuple[bytes, str]:
    """Convert raw WAV bytes to the preferred codec.

    Returns (audio_bytes, actual_mime). Falls back to ("audio/wav", wav_bytes)
    on any error so the chat turn never breaks on a transcoding hiccup.
    """
    if preferred_codec == "audio/wav" or preferred_codec not in _SUPPORTED_CODECS:
        return wav_bytes, "audio/wav"

    if not _PYDUB_AVAILABLE:
        logger.warning(
            "X-Preferred-Codec=%s requested but pydub not available — "
            "returning raw WAV (frontend should fall back).",
            preferred_codec,
        )
        return wav_bytes, "audio/wav"

    try:
        audio = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
        out_buf = io.BytesIO()
        if preferred_codec == "audio/mp4":
            # AAC-in-MP4 — universal on Safari + iOS; ~70% smaller than WAV
            audio.export(out_buf, format="mp4", codec="aac", bitrate="64k")
            return out_buf.getvalue(), "audio/mp4"
        elif preferred_codec == "audio/webm":
            # Opus-in-WebM — Chrome/Firefox preferred; ~80% smaller than WAV
            audio.export(out_buf, format="webm", codec="libopus", bitrate="48k")
            return out_buf.getvalue(), "audio/webm"
    except Exception as e:
        logger.warning(
            "TTS transcode WAV -> %s failed (%s: %s) — returning raw WAV.",
            preferred_codec, type(e).__name__, e,
        )

    return wav_bytes, "audio/wav"


class SarvamTTS(TTSProvider):
    name = "sarvam-bulbul"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = settings.SARVAM_TTS_MODEL,
        default_speaker: str = settings.SARVAM_TTS_SPEAKER,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or settings.SARVAM_API_KEY
        self.model = model
        self.default_speaker = default_speaker
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("SARVAM_API_KEY not set in .env")

    async def synthesize(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: Optional[str] = None,
        preferred_codec: Optional[str] = None,
    ) -> bytes:
        """Backwards-compatible synthesize.

        Returns raw audio bytes (WAV by default). If callers want to know the
        actual MIME of the returned bytes (because they asked for transcoding),
        use `synthesize_with_mime` instead.
        """
        audio_bytes, _ = await self.synthesize_with_mime(
            text=text,
            language_code=language_code,
            speaker=speaker,
            preferred_codec=preferred_codec,
        )
        return audio_bytes

    async def synthesize_with_mime(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: Optional[str] = None,
        preferred_codec: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        """Like synthesize() but also returns the actual MIME type.

        `preferred_codec` is one of "audio/wav" | "audio/mp4" | "audio/webm".
        If None or "audio/wav", returns Sarvam's raw WAV unchanged. Any other
        value triggers in-process transcoding via pydub/ffmpeg; on any
        transcoding failure (missing dep, ffmpeg error), we fall back to raw
        WAV — the frontend already handles this gracefully.
        """
        url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_TTS_PATH}"
        body = {
            "text": text,
            "target_language_code": language_code,
            "speaker": speaker or self.default_speaker,
            "model": self.model,
            "enable_preprocessing": True,
            "speech_sample_rate": 22050,
            "pitch": 0.0,
            "pace": 1.0,
            "loudness": 1.0,
        }
        headers = {
            "api-subscription-key": self.api_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            payload = resp.json()

        audios = payload.get("audios", [])
        if not audios:
            raise RuntimeError(f"Sarvam TTS returned no audio: {payload}")

        # Sarvam returns base64-encoded WAV in `audios[0]`
        wav_bytes = base64.b64decode(audios[0])

        codec = (preferred_codec or "audio/wav").lower()
        return _transcode_wav(wav_bytes, codec)
