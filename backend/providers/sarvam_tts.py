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
from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import TTSProvider


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
    ) -> bytes:
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
        return base64.b64decode(audios[0])
