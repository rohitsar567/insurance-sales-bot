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

    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language_code: Optional[str] = None,
    ) -> STTResult:
        url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_STT_PATH}"
        files = {
            "file": (f"audio.{audio_format}", io.BytesIO(audio_bytes), f"audio/{audio_format}"),
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
