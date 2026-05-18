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
import re
import wave
from typing import List, Optional, Tuple

import httpx

from backend.config import settings
from backend.providers.base import TTSProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# #55 — Sarvam Bulbul has a HARD per-request character limit:
#   bulbul:v2 -> 1500 chars,  bulbul:v3 -> 2500 chars
# (confirmed from https://docs.sarvam.ai text-to-speech reference).
#
# A long advisor reply (e.g. the 6-question pricing intake) exceeds 1500
# chars once normalized. Sending it whole means Sarvam only voices the
# leading slice — the exact "stopped in ten seconds" / questions 2-6 never
# spoken symptom. So, mirroring the STT 30s-chunking house style in
# providers/sarvam_stt.py, we split the text into <= TTS_CHUNK_CHARS
# pieces at SENTENCE / NUMBERED-ITEM boundaries (so we never cut a word or
# a question mid-way), synthesize each chunk sequentially, and concatenate
# the decoded PCM into ONE gapless WAV returned to the caller. Any HTTP /
# transport error on ANY chunk is raised LOUDLY (no silent truncation,
# no partial-audio-with-HTTP-200).
#
# Ceiling is set below the documented cap so per-language preprocessing
# expansion + minor jitter never pushes a chunk back over Sarvam's real
# limit.
# BUG #19 (2026-05-19): Sarvam bulbul:v2 silently returns HTTP 200 but
# voices ONLY THE FIRST SENTENCE of any single request over its REAL
# (small) limit — far below the 1500-char documented cap. A normal
# 370–720-char recommendation reply was sent whole, so the user heard
# only "Thanks for providing all those details, Rohit!" (~2s) and never
# the policy list. The documented cap is NOT the operative limit; lower
# the per-model ceiling so the existing sentence-seam chunker engages for
# normal replies (effective v2 ceiling = 500 - 200 = 300 → a 370–720
# char reply splits into 2–3 chunks, each synthesized and concatenated by
# the already-correct `_concat_wav_bytes`).
_TTS_CHAR_LIMIT_BY_MODEL = {
    "bulbul:v2": 500,
    "bulbul:v3": 1500,
}
_TTS_CHAR_LIMIT_DEFAULT = 500   # safest assumption for unknown models
_TTS_SAFETY_MARGIN = 200        # headroom under the documented hard cap


def _tts_char_ceiling(model: str) -> int:
    """Safe per-request char ceiling for the configured Bulbul model."""
    hard = _TTS_CHAR_LIMIT_BY_MODEL.get(
        (model or "").lower(), _TTS_CHAR_LIMIT_DEFAULT
    )
    return max(200, hard - _TTS_SAFETY_MARGIN)


# Split points, longest-context first: paragraph break, then end-of-
# sentence punctuation, then a numbered-list item boundary ("\n2. "),
# then comma, then whitespace. We never split inside a word.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")
_NUMBERED_ITEM = re.compile(r"(?=(?:^|\s)\d{1,2}[.)]\s)")


def _hard_wrap(piece: str, limit: int) -> List[str]:
    """Last-resort splitter for a single 'unit' longer than `limit`.

    Splits on whitespace so a word is never cut; if a single token still
    exceeds `limit` (pathological), it is hard-sliced so synthesis still
    covers it rather than dropping it.
    """
    out: List[str] = []
    cur = ""
    for tok in piece.split(" "):
        if not tok:
            continue
        cand = tok if not cur else f"{cur} {tok}"
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            out.append(cur)
            cur = ""
        if len(tok) <= limit:
            cur = tok
        else:
            for i in range(0, len(tok), limit):
                out.append(tok[i : i + limit])
    if cur:
        out.append(cur)
    return out


def _chunk_text_for_tts(text: str, limit: int) -> List[str]:
    """Split `text` into <= `limit`-char chunks at natural speech seams.

    Order of preference for a seam: sentence end -> numbered-list item ->
    comma -> whitespace. A chunk is never cut mid-word, and a numbered
    pricing question is never split across two synthesis calls unless it
    is itself longer than `limit` (then it hard-wraps on whitespace).

    The concatenation of all chunks (joined with a single space) preserves
    every character of spoken content — nothing is dropped.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    # 1. Coarse units: prefer numbered-item boundaries (keeps each "2. ..."
    #    question intact), else fall back to sentence boundaries.
    units = [u for u in _NUMBERED_ITEM.split(text) if u and u.strip()]
    if len(units) <= 1:
        units = [u for u in _SENTENCE_BOUNDARY.split(text) if u and u.strip()]
    if len(units) <= 1:
        units = [text]

    # 2. Greedily pack units into chunks <= limit. A unit bigger than limit
    #    is itself sentence-split, then hard-wrapped as a last resort.
    chunks: List[str] = []
    cur = ""
    for unit in units:
        unit = unit.strip()
        if len(unit) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            sub_units = [
                s for s in _SENTENCE_BOUNDARY.split(unit) if s and s.strip()
            ]
            if len(sub_units) <= 1:
                sub_units = _hard_wrap(unit, limit)
            for su in sub_units:
                su = su.strip()
                if len(su) > limit:
                    chunks.extend(_hard_wrap(su, limit))
                    continue
                cand = su if not cur else f"{cur} {su}"
                if len(cand) <= limit:
                    cur = cand
                else:
                    if cur:
                        chunks.append(cur)
                    cur = su
            continue
        cand = unit if not cur else f"{cur} {unit}"
        if len(cand) <= limit:
            cur = cand
        else:
            if cur:
                chunks.append(cur)
            cur = unit
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


def _concat_wav_bytes(wav_blobs: List[bytes]) -> bytes:
    """Concatenate multiple PCM WAV blobs into ONE gapless WAV.

    All chunks come from the same Sarvam call config (same model, speaker,
    sample rate) so the PCM params match; we still assert that so a
    mismatch fails LOUD rather than producing garbled audio. Stdlib `wave`
    only — no pydub/ffmpeg dependency for the core join (pydub remains the
    optional transcoder downstream).
    """
    if not wav_blobs:
        raise RuntimeError("Sarvam TTS produced no audio chunks to concat")
    if len(wav_blobs) == 1:
        return wav_blobs[0]

    params = None
    frames: List[bytes] = []
    for idx, blob in enumerate(wav_blobs):
        with wave.open(io.BytesIO(blob), "rb") as w:
            p = w.getparams()
            if params is None:
                params = p
            elif (
                p.nchannels,
                p.sampwidth,
                p.framerate,
            ) != (
                params.nchannels,
                params.sampwidth,
                params.framerate,
            ):
                raise RuntimeError(
                    "Sarvam TTS chunk audio params diverged "
                    f"(chunk {idx}: {p} vs {params}) — refusing to "
                    "concatenate mismatched PCM"
                )
            frames.append(w.readframes(w.getnframes()))

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(b"".join(frames))
    return out.getvalue()

# The frontend sends an `X-Preferred-Codec: audio/{wav,mp4,webm}` header
# so the chat endpoint can return audio in the codec the user's browser
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


# Voice-OUTPUT error vocabulary. Mirrors the STT closed-enum contract
# (sarvam_stt.STT_ERROR_*) so the chat endpoint can surface a structured
# tts_error_code instead of returning audio_base64=None with zero signal
# to the client (e.g. when Sarvam returns HTTP 429
# {"code":"insufficient_quota_error"}).
TTS_ERROR_RATE_LIMIT = "rate_limit"
TTS_ERROR_SERVICE = "service_unavailable"
TTS_ERROR_NETWORK = "network"
TTS_ERROR_AUTH = "auth"
TTS_ERROR_UNKNOWN = "unknown"

TTS_ERROR_USER_MESSAGES = {
    # The 429 here is specifically Sarvam's `insufficient_quota_error`
    # ("No credits available") in practice — distinct from a transient
    # rate-limit. Phrase it so the user knows the TEXT answer is complete
    # and only the spoken playback is unavailable.
    TTS_ERROR_RATE_LIMIT: (
        "Voice playback is unavailable right now (speech quota exhausted) — "
        "the written answer above is complete. You can keep chatting in text."
    ),
    TTS_ERROR_SERVICE: (
        "Voice playback is temporarily unavailable — the written answer "
        "above is complete."
    ),
    TTS_ERROR_NETWORK: (
        "Couldn't load the spoken reply (network hiccup) — the written "
        "answer above is complete."
    ),
    TTS_ERROR_AUTH: (
        "Voice playback is unavailable right now — the written answer "
        "above is complete."
    ),
    TTS_ERROR_UNKNOWN: (
        "Couldn't play the spoken reply — the written answer above is "
        "complete."
    ),
}


def classify_tts_exception(exc: BaseException) -> str:
    """Map an httpx / network exception to a TTS_ERROR_* code.

    Same boundary-classification pattern as classify_stt_exception — the
    frontend never parses raw httpx text; it switches on this closed enum.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        if status == 429:
            return TTS_ERROR_RATE_LIMIT
        if status in (401, 403):
            return TTS_ERROR_AUTH
        if 500 <= status < 600:
            return TTS_ERROR_SERVICE
        return TTS_ERROR_UNKNOWN
    if isinstance(exc, httpx.TimeoutException):
        return TTS_ERROR_NETWORK
    if isinstance(exc, httpx.NetworkError):
        return TTS_ERROR_NETWORK
    if isinstance(exc, httpx.HTTPError):
        return TTS_ERROR_UNKNOWN
    # RuntimeError("SARVAM_API_KEY not set") / RuntimeError("returned no
    # audio") — treat missing-key as auth, empty-audio as service.
    if isinstance(exc, RuntimeError):
        msg = str(exc).lower()
        if "api_key" in msg or "api key" in msg or "subscription" in msg:
            return TTS_ERROR_AUTH
        return TTS_ERROR_SERVICE
    return TTS_ERROR_UNKNOWN


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

    async def _synthesize_one(
        self,
        text: str,
        language_code: str,
        speaker: Optional[str],
    ) -> bytes:
        """POST ONE <= char-limit text chunk to Sarvam, return its WAV bytes.

        Raises on transport / HTTP errors (no swallowing) so the caller's
        classifier maps them to a closed tts_error_code — mirrors
        sarvam_stt._transcribe_wav_bytes.
        """
        url = f"{settings.SARVAM_BASE_URL}{settings.SARVAM_TTS_PATH}"
        body = {
            "text": text,
            "target_language_code": language_code,
            "speaker": speaker or self.default_speaker,
            "model": self.model,
            # BUG #19: `enable_preprocessing` is NOT in Sarvam's documented
            # TTS schema; it triggers undocumented server-side first-sentence
            # segmentation (HTTP 200 + only the first sentence voiced).
            # Removed so the full chunk text is synthesized.
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

        # BUG #19 — defensive truncation guard. Sarvam can return HTTP 200
        # with audio that voices only the FIRST SENTENCE (the silent-
        # truncation symptom). Decode the WAV and, if the playback duration
        # is confidently too short for the text we sent (~30ms/char floor),
        # raise so `classify_tts_exception` surfaces it LOUDLY instead of
        # silently shipping a ~2s clip. The wave decode is wrapped so a
        # decode failure (unexpected container) never masks the real audio;
        # we ONLY raise on a confidently-short, successfully-decoded result.
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as w:
                framerate = w.getframerate()
                n_frames = w.getnframes()
            duration_s = (n_frames / framerate) if framerate else None
        except Exception:  # pragma: no cover — never raise on decode failure
            duration_s = None
        if duration_s is not None and duration_s < 0.030 * len(text):
            raise RuntimeError(
                f"sarvam tts returned truncated audio: {duration_s:.2f}s "
                f"for {len(text)} chars"
            )
        return wav_bytes

    async def synthesize_with_mime(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: Optional[str] = None,
        preferred_codec: Optional[str] = None,
    ) -> Tuple[bytes, str]:
        """Like synthesize() but also returns the actual MIME type.

        #55 FIX: Bulbul has a HARD per-request char limit (v2=1500). A long
        reply is split at sentence / numbered-item seams into <= ceiling
        chunks, each chunk is synthesized sequentially, and the decoded PCM
        is concatenated into ONE gapless WAV — so the COMPLETE reply
        (every numbered question) is spoken, not just the first ~10s.
        This mirrors the STT 30s-chunking house style. Any HTTP / transport
        error on ANY chunk propagates (no silent truncation).

        `preferred_codec` is one of "audio/wav" | "audio/mp4" | "audio/webm".
        If None or "audio/wav", returns Sarvam's raw WAV unchanged. Any other
        value triggers in-process transcoding via pydub/ffmpeg; on any
        transcoding failure (missing dep, ffmpeg error), we fall back to raw
        WAV — the frontend already handles this gracefully.
        """
        ceiling = _tts_char_ceiling(self.model)
        chunks = _chunk_text_for_tts(text or "", ceiling)

        if not chunks:
            raise RuntimeError("Sarvam TTS called with empty text")

        if len(chunks) == 1:
            # Common case: short reply, single Sarvam call — identical wire
            # behaviour to the pre-fix path.
            wav_bytes = await self._synthesize_one(
                chunks[0], language_code, speaker
            )
        else:
            logger.info(
                "TTS chunked synthesis: %d chars split into %d chunks "
                "(<= %d chars each, model=%s)",
                len(text or ""),
                len(chunks),
                ceiling,
                self.model,
            )
            # Sequential — preserves order so the concatenated audio reads
            # the questions 1..6 in order. Any chunk failure raises and the
            # boundary classifier surfaces a real error_code; we NEVER
            # return a silently-partial readout.
            wav_parts: List[bytes] = []
            for idx, chunk in enumerate(chunks):
                part = await self._synthesize_one(
                    chunk, language_code, speaker
                )
                if not part:
                    raise RuntimeError(
                        f"Sarvam TTS returned empty audio for chunk {idx} "
                        f"of {len(chunks)}"
                    )
                wav_parts.append(part)
            wav_bytes = _concat_wav_bytes(wav_parts)

        codec = (preferred_codec or "audio/wav").lower()
        return _transcode_wav(wav_bytes, codec)
