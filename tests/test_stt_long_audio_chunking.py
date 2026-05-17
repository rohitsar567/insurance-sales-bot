"""Regression test for KI-302 (2026-05-18) — full voice transcript.

BUG (user-reported, live): "Full transcript still not coming while
speaking." A long spoken utterance was truncated to a partial transcript.

ROOT CAUSE: Sarvam's saarika REST /speech-to-text endpoint has a hard ~30s
audio limit. It does NOT 4xx on longer audio — it returns HTTP 200 with a
`transcript` containing ONLY the first ~30s and silently drops the rest.
The live-voice hook's grace-window batching deliberately merges multiple
pause-separated speech bursts into ONE blob, so any real-world utterance
with natural pauses easily exceeds 30s and was silently cut.

FIX: `SarvamSTT.transcribe` now decodes the audio, and if it exceeds the
safe REST ceiling it splits it into <= STT_CHUNK_MS chunks (at silence
boundaries where possible), transcribes each chunk, and concatenates the
transcripts in order so the COMPLETE utterance survives.

This test pins that contract WITHOUT needing ffmpeg/pydub by stubbing a
minimal AudioSegment that supports the slicing / len / dBFS / export
surface the splitter uses, and a fake Sarvam HTTP layer that returns a
DIFFERENT transcript per 25s window — so a regression to single-shot
behaviour (only the first window transcribed) fails loudly.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \
        tests/test_stt_long_audio_chunking.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("SARVAM_API_KEY", "test-key-for-stt-chunking")

from backend.providers.sarvam_stt import (  # noqa: E402
    STT_CHUNK_MS,
    SarvamSTT,
)


# ---------------------------------------------------------------------------
# Minimal pydub.AudioSegment stub.
# The splitter only uses: len(seg) -> ms, seg[a:b] -> sub-segment,
# seg.dBFS -> float, seg.export(buf, format=...) , set_frame_rate/channels/
# sample_width (chained no-ops), AudioSegment.from_file(...).
# Each "ms" carries a sentinel byte so a concatenated/exported chunk can be
# decoded back into the exact ms range it represents — that lets the fake
# Sarvam server return the words spoken in that window.
# ---------------------------------------------------------------------------
class FakeAudioSegment:
    def __init__(self, start_ms: int, end_ms: int):
        self.start_ms = start_ms
        self.end_ms = end_ms  # exclusive

    # --- duration -----------------------------------------------------------
    def __len__(self):
        return self.end_ms - self.start_ms

    # --- slicing (pydub uses ms-based slicing) ------------------------------
    def __getitem__(self, sl):
        if isinstance(sl, slice):
            lo = 0 if sl.start is None else sl.start
            hi = len(self) if sl.stop is None else sl.stop
            lo = max(0, min(lo, len(self)))
            hi = max(0, min(hi, len(self)))
            return FakeAudioSegment(self.start_ms + lo, self.start_ms + hi)
        raise TypeError("only slice indexing used by splitter")

    # --- loudness -----------------------------------------------------------
    @property
    def dBFS(self):
        # Constant moderate loudness everywhere EXCEPT a deliberate silent
        # gap at [24000, 24400) ms so _split_on_silence has a real pause to
        # snap to near the 25s ceiling.
        if self.end_ms - self.start_ms == 0:
            return float("-inf")
        if 24000 <= self.start_ms < 24400:
            return float("-inf")
        return -20.0

    # --- transcode no-ops ---------------------------------------------------
    def set_frame_rate(self, _):
        return self

    def set_channels(self, _):
        return self

    def set_sample_width(self, _):
        return self

    def export(self, buf, format="wav"):  # noqa: A002 - pydub signature
        # Encode the ms range so the fake Sarvam server can recover which
        # words this chunk covers.
        buf.write(f"FAKEWAV:{self.start_ms}:{self.end_ms}".encode())
        buf.seek(0)
        return buf


class FakeAudioSegmentFactory:
    @staticmethod
    def from_file(_bio, format=None):  # noqa: A002 - pydub signature
        # A 92-second utterance (> 3 Sarvam windows of 25s).
        return FakeAudioSegment(0, 92_000)


@pytest.fixture
def patch_pydub(monkeypatch):
    fake_pydub = types.ModuleType("pydub")
    fake_pydub.AudioSegment = FakeAudioSegmentFactory
    monkeypatch.setitem(sys.modules, "pydub", fake_pydub)
    yield


# ---------------------------------------------------------------------------
# Fake Sarvam HTTP layer. Returns the words spoken in the chunk's ms window.
# The "spoken script" is one word per second: word_0 .. word_91. Sarvam's
# real 30s truncation is simulated by capping any single call at 30000 ms of
# audio (it ignores everything past 30s of the blob it receives) — exactly
# the silent-truncation behaviour the fix must defeat by never sending a
# chunk longer than ~25s.
# ---------------------------------------------------------------------------
SARVAM_HARD_LIMIT_MS = 30_000


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, files=None, data=None):
        raw = files["file"][1].read()
        text = raw.decode()
        assert text.startswith("FAKEWAV:"), text
        _, s, e = text.split(":")
        start_ms, end_ms = int(s), int(e)
        # Simulate Sarvam's silent 30s truncation on the received blob.
        capped_end = min(end_ms, start_ms + SARVAM_HARD_LIMIT_MS)
        words = [
            f"word_{ms // 1000}"
            for ms in range(start_ms, capped_end, 1000)
        ]
        return _FakeResp({
            "transcript": " ".join(words),
            "language_code": "en-IN",
            "language_probability": 0.99,
        })


@pytest.fixture
def patch_httpx(monkeypatch):
    import backend.providers.sarvam_stt as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
    yield


def test_long_utterance_is_fully_transcribed(patch_pydub, patch_httpx):
    """A 92s utterance must yield ALL 92 words, not just the first ~30."""
    stt = SarvamSTT()
    # > 1 KB so the short-audio guard doesn't short-circuit.
    audio = b"\x00" * 4096
    result = asyncio.run(
        stt.transcribe(audio_bytes=audio, audio_format="webm", language_code="en-IN")
    )

    words = result.text.split()
    # CORE CONTRACT: every one of the 92 spoken words must survive — the
    # pre-fix single-shot path returned only word_0..word_29 (Sarvam's 30s
    # silent truncation). A word landing exactly on a silence-snap seam can
    # legitimately appear in BOTH adjoining chunks (a duplicated word at the
    # seam is harmless; a LOST word is the bug). So we assert: (1) no word
    # is missing, (2) order is non-decreasing, (3) the count is ~92 (never
    # ~30). De-duplicating consecutive repeats must reproduce the exact
    # script.
    expected = [f"word_{i}" for i in range(92)]
    deduped = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
    assert deduped == expected, (
        f"transcript truncated/garbled: deduped {len(deduped)} words "
        f"(first={deduped[:3]}, last={deduped[-3:]}), expected 92 in order"
    )
    # Hard proof this is NOT the pre-fix 30s truncation.
    assert len(words) >= 90
    assert "word_91" in words and "word_60" in words
    assert result.raw.get("chunked") is True
    # 92s split at <=25s ceiling => at least 4 chunks.
    assert result.raw.get("chunk_count", 0) >= 4
    assert result.language_code == "en-IN"


def test_short_utterance_single_call_unchanged(patch_pydub, patch_httpx, monkeypatch):
    """A sub-ceiling clip must take exactly ONE Sarvam call (no behaviour
    change / no extra latency for the common case)."""
    # Patch the factory to return a short 8s clip.
    short = FakeAudioSegment(0, 8_000)
    monkeypatch.setattr(
        FakeAudioSegmentFactory, "from_file", staticmethod(lambda *a, **k: short)
    )
    stt = SarvamSTT()
    result = asyncio.run(
        stt.transcribe(audio_bytes=b"\x00" * 4096, audio_format="webm")
    )
    words = result.text.split()
    assert words == [f"word_{i}" for i in range(8)]
    # Single-call path does NOT set the chunked marker.
    assert result.raw.get("chunked") is not True


def test_no_chunk_exceeds_sarvam_safe_ceiling(patch_pydub):
    """Every produced chunk must be <= STT_CHUNK_MS so Sarvam never silently
    truncates a chunk. This is the core invariant the fix rests on."""
    seg = FakeAudioSegment(0, 92_000)
    chunks = SarvamSTT._split_on_silence(seg, STT_CHUNK_MS)
    assert len(chunks) >= 4
    for c in chunks:
        assert len(c) <= STT_CHUNK_MS, f"chunk {len(c)} ms exceeds {STT_CHUNK_MS}"
    # Chunks must tile the whole utterance with no gap and no overlap.
    assert chunks[0].start_ms == 0
    assert chunks[-1].end_ms == 92_000
    for a, b in zip(chunks, chunks[1:]):
        assert a.end_ms == b.start_ms, "chunk boundary gap/overlap loses audio"
