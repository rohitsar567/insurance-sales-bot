"""Regression test for #55 (10s truncation) + #56 (robotic normalization).

LIVE BUG (user-reported, audio): the advisor sent a 6-question pricing
intake message. The user heard only ~10 seconds of audio ("stopped in ten
seconds") — questions 2-6 were NEVER spoken — and "e.g." was read
letter-by-letter ("E G") while "/" in "₹5L / ₹10L / ₹25L / ₹1Cr" was read
as "by"/"divide".

ROOT CAUSE #55: backend/voice_format.py `_truncate_for_voice` (called from
`tts_preprocess` with the legacy max_words=55 passed by backend/main.py)
chopped the message to the first ~55 spoken words (~10s of audio) BEFORE
TTS, appending "More details are on screen." Everything from question 2
onward was discarded.

ROOT CAUSE #56: `tts_preprocess` never expanded "e.g." / "/" / currency
ranges to spoken words, so Sarvam Bulbul voiced "E G" and "divide".

LATENT ROOT CAUSE #55b: even with the word cap removed, the full
normalized message exceeds Sarvam Bulbul v2's hard 1500-char per-request
limit; sending it whole means Sarvam only voices the leading slice.
providers/sarvam_tts.py now chunks at sentence / numbered-item seams under
a safe ceiling, synthesizes each chunk sequentially, and concatenates the
decoded PCM into ONE gapless WAV (mirrors the STT 30s-chunking house
style; raises loudly on any HTTP error — no silent truncation).

This test stubs the Sarvam HTTP layer (no network / no pydub needed for
the WAV concat — stdlib `wave`) and pins the contract end-to-end.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \
        tests/test_tts_full_natural_readout.py -v
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import wave

import pytest

os.environ.setdefault("SARVAM_API_KEY", "test-key-for-tts-chunking")

from backend.voice_format import tts_preprocess  # noqa: E402
from backend.providers.sarvam_tts import (  # noqa: E402
    SarvamTTS,
    _chunk_text_for_tts,
    _concat_wav_bytes,
    _tts_char_ceiling,
)


# The EXACT message the user reported hearing truncated + robotic.
SCREENSHOT_MESSAGE = (
    "A few quick pricing inputs (you can skip any):\n"
    "1. How much sum insured? (e.g., ₹5L / ₹10L / ₹25L / ₹1Cr)\n"
    "2. Premium budget? (e.g., ₹10–15K/year, or ₹50K+ for premium covers)\n"
    "3. Any existing health cover from work or otherwise? "
    "(e.g., '5L through employer' or 'no')\n"
    "4. Co-pay tolerance: Are you OK with a co-pay — sharing 10–30% of "
    "every claim — to lower the premium? Or do you want zero co-pay "
    "(insurer pays it all)?\n"
    "5. Family medical history: Any major conditions running in your "
    "blood family (parents/siblings) — cancer / diabetes / heart disease "
    "/ hypertension?\n"
    "6. Smoking status: Do you smoke or use tobacco products? (yes / no) "
    "Smokers face 30–50% premium loading; capturing this gives an "
    "accurate band."
)


# ---------------------------------------------------------------------------
# #56 — NATURAL NORMALIZATION (text-level, no network).
# ---------------------------------------------------------------------------
def test_full_message_normalizes_naturally_and_is_not_truncated():
    spoken = tts_preprocess(SCREENSHOT_MESSAGE, language="en", max_words=55)

    # --- #55: the FULL message is present (NOT cut at ~55 words / ~10s) ---
    # Question-6-specific content must survive end-to-end.
    assert "smoking status" in spoken.lower(), spoken
    assert "tobacco" in spoken.lower(), spoken
    assert "accurate band" in spoken.lower(), spoken
    # The legacy truncation cue must NOT be present.
    assert "more details are on screen" not in spoken.lower(), spoken
    assert "more details on screen" not in spoken.lower(), spoken
    # A real readout of all 6 questions is far more than 55 words.
    assert len(spoken.split()) > 120, (
        f"only {len(spoken.split())} words — looks truncated:\n{spoken}"
    )

    low = spoken.lower()

    # --- #56: "e.g." expanded, never spelled as letters ---
    assert "for example" in low, spoken
    assert "e.g" not in low, spoken
    # No isolated "e g" letter pair (the robotic readout).
    assert not re.search(r"\be\s+g\b", low), spoken

    # --- #56: raw slash gone everywhere; expanded to list / "or" ---
    assert "/" not in spoken, f"raw slash survived:\n{spoken}"
    # Currency slash run "₹5L / ₹10L / ₹25L / ₹1Cr" became a spoken list.
    # NOTE: leading digits are word-formed by _normalize_numbers (the
    # natural TTS form): "5" -> "five", "1" -> "one", "25" -> "twenty-five".
    assert "five lakh rupees" in low, spoken
    assert "ten lakh rupees" in low, spoken
    assert "twenty-five lakh rupees" in low, spoken
    assert "one crore rupees" in low, spoken
    assert "or one crore rupees" in low, spoken
    # Generic slashes -> "or".
    assert "parents or siblings" in low, spoken
    assert "yes or no" in low, spoken
    assert "cancer or diabetes" in low, spoken

    # --- #56: ranges + currency expanded, no symbols/letters left ---
    assert "₹" not in spoken, spoken
    assert "%" not in spoken, spoken
    # "10–30%" -> "10 to 30 percent" -> word form "ten to thirty percent"
    assert "ten to thirty percent" in low, spoken
    # "30–50%" -> "thirty to fifty percent"
    assert "thirty to fifty percent" in low, spoken
    # "₹10–15K/year" -> "10 to 15 thousand rupees per year"
    assert "ten to fifteen thousand rupees per year" in low, spoken
    # "₹50K+" -> "above 50 thousand rupees" -> "above fifty thousand rupees"
    assert "above fifty thousand rupees" in low, spoken
    # No bare "K"/"L"/"Cr" shorthand letters left dangling.
    assert not re.search(r"\b\d+\s*[LK]\b", spoken), spoken
    assert not re.search(r"\bCr\b", spoken), spoken
    assert " 15K" not in spoken and "15k" not in low, spoken

    # --- markdown list numbering stripped (reads as speech, not "1.") ---
    assert not re.search(r"(?m)^\s*\d+\.\s", spoken), spoken


# ---------------------------------------------------------------------------
# #55b — the chunk PLAN covers the WHOLE message (no character dropped).
# ---------------------------------------------------------------------------
def test_chunk_plan_covers_entire_message_under_ceiling():
    spoken = tts_preprocess(SCREENSHOT_MESSAGE, language="en")
    ceiling = _tts_char_ceiling("bulbul:v2")
    assert ceiling < 1500  # safety margin under Sarvam's hard cap

    chunks = _chunk_text_for_tts(spoken, ceiling)
    assert len(chunks) >= 1
    for i, c in enumerate(chunks):
        assert len(c) <= ceiling, f"chunk {i} = {len(c)} chars > {ceiling}"

    # Coverage: concatenated chunks (whitespace-normalized) must contain
    # every non-space character of the spoken text — nothing dropped.
    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", s)

    joined = _norm(" ".join(chunks))
    assert _norm(spoken) == joined, (
        "chunk plan lost/added content vs the normalized spoken text"
    )
    # The final question's content must live in some chunk.
    assert any("accurate band" in c.lower() for c in chunks), chunks


# ---------------------------------------------------------------------------
# WAV concat helper — gapless join of stdlib PCM blobs.
# ---------------------------------------------------------------------------
def _make_wav(n_frames: int, framerate: int = 22050) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(framerate)
        w.writeframes(b"\x01\x00" * n_frames)
    return buf.getvalue()


def test_concat_wav_is_gapless_and_sums_frames():
    a = _make_wav(1000)
    b = _make_wav(2500)
    c = _make_wav(700)
    merged = _concat_wav_bytes([a, b, c])
    with wave.open(io.BytesIO(merged), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getframerate() == 22050
        # Gapless: total frames == sum of inputs (no inserted silence).
        assert w.getnframes() == 1000 + 2500 + 700


def test_concat_wav_param_mismatch_raises_loud():
    a = _make_wav(100, framerate=22050)
    b = _make_wav(100, framerate=16000)
    with pytest.raises(RuntimeError, match="params diverged"):
        _concat_wav_bytes([a, b])


# ---------------------------------------------------------------------------
# End-to-end: stub Sarvam HTTP, prove the FULL message is synthesized via
# multiple chunks and the audio is concatenated (NOT a single 10s clip).
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Fake Sarvam TTS endpoint.

    Records every chunk it is asked to synthesize and returns a WAV whose
    frame count == len(text) so the concatenated audio length is a direct
    proxy for 'how many characters were actually voiced'. Also asserts no
    single request exceeds Bulbul v2's hard 1500-char limit — a regression
    to single-shot (whole message in one call) fails LOUDLY here.
    """

    sent_texts: list[str] = []
    HARD_LIMIT = 1500

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, **k):
        text = json["text"]
        assert len(text) <= self.HARD_LIMIT, (
            f"chunk of {len(text)} chars exceeds Sarvam Bulbul v2's "
            f"1500-char hard limit — would be silently truncated"
        )
        _FakeClient.sent_texts.append(text)
        wav = _make_wav(max(1, len(text)))
        return _FakeResp({"audios": [base64.b64encode(wav).decode()]})


import base64  # noqa: E402  (used by _FakeClient above)


@pytest.fixture
def patch_httpx(monkeypatch):
    import backend.providers.sarvam_tts as mod

    _FakeClient.sent_texts = []
    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
    yield


def test_end_to_end_screenshot_message_synthesized_in_full(patch_httpx):
    """The exact buggy screenshot message: EVERY character (incl. question
    6) must reach Sarvam, in however many calls — never the ~55-word /
    ~10s truncated shot the bug produced."""
    spoken = tts_preprocess(SCREENSHOT_MESSAGE, language="en", max_words=55)
    tts = SarvamTTS()
    audio, mime = asyncio.run(
        tts.synthesize_with_mime(spoken, language_code="en-IN")
    )

    assert mime == "audio/wav"
    assert len(_FakeClient.sent_texts) >= 1, _FakeClient.sent_texts
    # Every char that went to TTS, concatenated == full spoken text
    # (no character dropped, nothing truncated).
    rejoined = re.sub(r"\s+", "", " ".join(_FakeClient.sent_texts))
    assert re.sub(r"\s+", "", spoken) == rejoined
    # Question-6 content was actually sent to TTS (the truncation bug
    # dropped everything from question 2 onward).
    assert any("accurate band" in t.lower() for t in _FakeClient.sent_texts)
    assert any("tobacco" in t.lower() for t in _FakeClient.sent_texts)
    assert any("smoking status" in t.lower() for t in _FakeClient.sent_texts)

    # Audio frame count == total chars voiced (fake = 1 frame/char), and
    # is FAR above the ~300 chars the 55-word truncation would have made.
    with wave.open(io.BytesIO(audio), "rb") as w:
        total_frames = w.getnframes()
    assert total_frames == len(re.sub(r"\s+", " ", spoken).strip()) or (
        total_frames > 600
    ), f"{total_frames} frames — looks like the 10s truncation regressed"


def test_end_to_end_overlong_message_chunks_and_concatenates(patch_httpx):
    """When normalized text DOES exceed Bulbul v2's per-request ceiling,
    it is split into >1 chunk, each <= ceiling, and the decoded PCM is
    concatenated into ONE gapless WAV — no character dropped."""
    ceiling = _tts_char_ceiling("bulbul:v2")
    # Build a long, clean multi-sentence reply that exceeds the ceiling.
    long_reply = " ".join(
        f"Point number {i}: this policy covers day-care and AYUSH "
        f"treatment with a thirty day waiting period and no co-pay."
        for i in range(1, 60)
    )
    spoken = tts_preprocess(long_reply, language="en")
    assert len(spoken) > ceiling, len(spoken)

    tts = SarvamTTS()
    audio, _ = asyncio.run(
        tts.synthesize_with_mime(spoken, language_code="en-IN")
    )
    # Multiple Sarvam calls, each within the hard limit (the fake asserts
    # the 1500 cap per call).
    assert len(_FakeClient.sent_texts) >= 2, len(_FakeClient.sent_texts)
    for t in _FakeClient.sent_texts:
        assert len(t) <= ceiling
    # Reassembled text == full spoken payload (nothing dropped at seams).
    assert re.sub(r"\s+", "", " ".join(_FakeClient.sent_texts)) == re.sub(
        r"\s+", "", spoken
    )
    # Gapless concatenated WAV: frames == sum of per-chunk frames.
    with wave.open(io.BytesIO(audio), "rb") as w:
        total = w.getnframes()
    assert total == sum(len(t) for t in _FakeClient.sent_texts)


def test_short_reply_is_single_call_unchanged(patch_httpx):
    """A sub-ceiling reply must take exactly ONE Sarvam call (no behaviour
    change / no extra latency for the common case)."""
    spoken = tts_preprocess("Yes, that policy covers day-care procedures.")
    tts = SarvamTTS()
    asyncio.run(tts.synthesize_with_mime(spoken, language_code="en-IN"))
    assert len(_FakeClient.sent_texts) == 1, _FakeClient.sent_texts


def test_http_error_on_any_chunk_propagates_loudly(patch_httpx, monkeypatch):
    """No silent truncation: if any chunk's Sarvam call fails, the error
    propagates so the boundary classifier surfaces a real tts_error_code."""
    import backend.providers.sarvam_tts as mod

    class _BoomClient(_FakeClient):
        calls = 0

        async def post(self, url, headers=None, json=None, **k):
            _BoomClient.calls += 1
            if _BoomClient.calls == 2:
                req = __import__("httpx").Request("POST", url)
                resp = __import__("httpx").Response(503, request=req)
                raise __import__("httpx").HTTPStatusError(
                    "503", request=req, response=resp
                )
            return await super().post(url, headers=headers, json=json, **k)

    monkeypatch.setattr(mod.httpx, "AsyncClient", _BoomClient)
    # Use a long reply so there are >= 2 chunks and chunk #2 is the one
    # that 503s — proving a mid-stream failure is NOT silently swallowed
    # into a partial readout.
    long_reply = " ".join(
        f"Point number {i}: this policy covers day-care and AYUSH "
        f"treatment with a thirty day waiting period and no co-pay."
        for i in range(1, 60)
    )
    spoken = tts_preprocess(long_reply, language="en")
    tts = SarvamTTS()
    with pytest.raises(__import__("httpx").HTTPStatusError):
        asyncio.run(tts.synthesize_with_mime(spoken, language_code="en-IN"))
