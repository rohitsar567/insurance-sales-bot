"""Regression test for #53 (head-clipping) + #54 (capture start latency)
— 2026-05-18.

NOTE (2026-05-18): the SPACE hold-to-talk path was removed entirely; the
only voice-capture control is now the on-screen Push-to-talk button. This
test no longer pins any page.tsx SPACE wiring. It still pins the two
contracts that remain live and valuable:

  1. The warm-stream PreRollRing + evaluateHoldGate exported from
     frontend/src/lib/useStreamingVoice.ts (a rolling ~PRE_ROLL_MS pre-roll
     buffer + a deliberate-hold gate that ignores sub-threshold taps).
  2. The backend STT chunking path: the pre-roll head word survives
     transcription and long audio is chunked, not truncated — this guards
     the /api/transcribe behavior the Push-to-talk button still relies on.

ORIGINAL ROOT CAUSE (kept for context)
-----------------------------------------------------------------------------
A cold-started mic loses every word spoken between capture-request and
recorder.start() (getUserMedia is 200-700ms). Real repro: user said
"Sir. My age is 29 ..." -> transcribed "S A R. My age is 29 ..." (#53);
the same cold-start is the multi-second start delay (#54). The warm-stream
PreRollRing keeps the first word at the head of the blob.

WHAT THIS TEST PINS
-----------------------------------------------------------------------------
There is no JS test runner in this repo, so the two contracts are pinned at
the layers a Python test can reach honestly:

  PART A (pure frontend logic, executed against the REAL shipped code):
    The exported PreRollRing + evaluateHoldGate from useStreamingVoice.ts are
    loaded under Node (TS types stripped; react/./api/./voice_resilience
    stubbed since the PURE exports don't use them) and asserted:
      A1  a pre-roll ring fed lead-in slices then drained returns the
          LEADING slice first  -> the first word is at the head of the blob.
      A2  the ring retains >= PRE_ROLL_MS of audio (covers the worst-case
          page.tsx cold-start gap) and evicts only OLDER audio.
      A3  evaluateHoldGate: a >= threshold hold is deliberate (submit); a
          sub-threshold tap is NOT (gated, nothing submitted).
      A4  the integrated contract: engage-after-threshold seeds the
          capture with the pre-roll so leadIn[0] precedes live audio;
          a tap drains nothing and submits nothing.

  PART B (backend STT, the bug's observable failure surface):
    The fix prepends a short pre-roll HEAD to the blob. Prove
    backend/providers/sarvam_stt.py never silently drops/truncates that
    head — i.e. the FIRST word survives transcription, including when the
    full utterance is long enough to hit the silence-chunk splitter (the
    pre-fix 30s truncation dropped tail words; a regression that mishandles
    the prepended head would drop the FIRST word instead).

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \
        tests/test_ptt_preroll_warm_stream.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

os.environ.setdefault("SARVAM_API_KEY", "test-key-for-ptt-preroll")

REPO = Path(__file__).resolve().parents[1]
HOOK = REPO / "frontend" / "src" / "lib" / "useStreamingVoice.ts"


# ===========================================================================
# PART A — pure frontend logic, executed against the REAL shipped exports.
# ===========================================================================

# The harness registers an ESM resolve/load hook (see _build_node_loader)
# that stubs `react`, `./api`, `./voice_resilience` — modules the React hook
# body imports but which the PURE exports (PreRollRing / evaluateHoldGate)
# never touch — and lets Node 24 strip the TS types on load. This exercises
# the genuine SHIPPED code, not a copy.


def _have_node() -> bool:
    return shutil.which("node") is not None


def _build_node_loader(tmpdir: Path) -> Path:
    """Write an ESM loader that stubs react/./api/./voice_resilience and
    strips TS types so the REAL hook module's pure exports can be imported.
    """
    loader = tmpdir / "loader.mjs"
    loader.write_text(
        textwrap.dedent(
            r"""
            import { readFileSync } from 'node:fs';
            import { fileURLToPath } from 'node:url';
            import vm from 'node:vm';

            const STUBS = {
              react: `
                export const useCallback = (f) => f;
                export const useEffect = () => {};
                export const useRef = (v) => ({ current: v });
                export const useState = (v) => [typeof v === 'function' ? v() : v, () => {}];
                export default {};
              `,
              api: `export const postTranscribe = async () => ({ text: '' });`,
              voice_resilience: `
                export const retryPostTranscribe = async () => null;
                export const scaleSpeechZcrBand = () => ({ min: 20, max: 250 });
                export class AdaptiveNoiseFloor { feed(){} currentThreshold(){ return 0.008; } }
              `,
            };

            export async function resolve(specifier, context, nextResolve) {
              if (specifier === 'react')
                return { url: 'stub:react', shortCircuit: true };
              if (specifier === './api' || specifier.endsWith('/api'))
                return { url: 'stub:api', shortCircuit: true };
              if (specifier === './voice_resilience' || specifier.endsWith('/voice_resilience'))
                return { url: 'stub:voice_resilience', shortCircuit: true };
              return nextResolve(specifier, context);
            }

            export async function load(url, context, nextLoad) {
              if (url.startsWith('stub:')) {
                const key = url.slice('stub:'.length);
                return { format: 'module', source: STUBS[key], shortCircuit: true };
              }
              if (url.endsWith('.ts')) {
                const path = fileURLToPath(url);
                const src = readFileSync(path, 'utf8');
                // Node 24 strips TS types natively for .ts; force module
                // format + hand it the raw source (type-strip happens in the
                // default ts transform path).
                return { format: 'module-typescript', source: src, shortCircuit: true };
              }
              return nextLoad(url, context);
            }
            """
        ).strip()
        + "\n"
    )
    return loader


def _run_node_contract(tmp_path: Path) -> dict:
    """Import the REAL PreRollRing + evaluateHoldGate under Node and run the
    four pure-logic contracts. Returns the parsed JSON verdict."""
    loader = _build_node_loader(tmp_path)
    script = tmp_path / "contract.mjs"
    # Pull the real exports + the real PRE_ROLL_MS / HOLD_THRESHOLD_MS /
    # WARM_TIMESLICE_MS constants from the shipped hook module.
    script.write_text(
        textwrap.dedent(
            f"""
            const mod = await import({json.dumps(HOOK.as_uri())});
            const {{
              PreRollRing, evaluateHoldGate,
              PRE_ROLL_MS, HOLD_THRESHOLD_MS, WARM_TIMESLICE_MS,
            }} = mod;

            const out = {{}};

            // --- A1/A2: pre-roll ring keeps the LEADING audio + >=PRE_ROLL_MS
            const ring = new PreRollRing(PRE_ROLL_MS);
            // Feed far more than the window so eviction is forced. Each slice
            // is a distinct 1-byte blob tagged by index so order is provable.
            const totalSlices = Math.ceil((PRE_ROLL_MS / WARM_TIMESLICE_MS) * 4);
            for (let i = 0; i < totalSlices; i++) {{
              ring.push(new Blob([String.fromCharCode(65 + (i % 26))]), WARM_TIMESLICE_MS);
            }}
            out.retainedMs = ring.retainedDurationMs();
            out.retainsAtLeastWindow = ring.retainedDurationMs() >= PRE_ROLL_MS;
            const drained = ring.drain();
            out.drainedCount = drained.length;
            out.ringEmptyAfterDrain = ring.retainedDurationMs() === 0;

            // Now the CORE #53 contract: a head word spoken just before the
            // window boundary must still be at the HEAD of the drained blob.
            const r2 = new PreRollRing(PRE_ROLL_MS);
            // 'HEAD' = the first word's slice. Then enough silence-ish slices
            // to *almost* (but not quite, given the +1 slice slack) fill the
            // window, so HEAD must still be retained as slice 0.
            const headBlob = new Blob(['HEAD']);
            r2.push(headBlob, WARM_TIMESLICE_MS);
            const fill = Math.floor(PRE_ROLL_MS / WARM_TIMESLICE_MS) - 1;
            for (let i = 0; i < fill; i++) r2.push(new Blob(['x']), WARM_TIMESLICE_MS);
            const d2 = r2.drain();
            out.headSurvives = d2.length > 0 && (await d2[0].text()) === 'HEAD';

            // --- A3: deliberate-hold gate
            const tap = evaluateHoldGate(1000, 1000 + (HOLD_THRESHOLD_MS - 1), HOLD_THRESHOLD_MS);
            const hold = evaluateHoldGate(1000, 1000 + HOLD_THRESHOLD_MS, HOLD_THRESHOLD_MS);
            const longHold = evaluateHoldGate(1000, 1000 + 5000, HOLD_THRESHOLD_MS);
            out.tapGated = tap.deliberate === false;
            out.holdAtThresholdDeliberate = hold.deliberate === true;
            out.longHoldDeliberate = longHold.deliberate === true;
            out.tapHeldMs = tap.heldMs;

            // --- A4: integrated — engage seeds capture with pre-roll so the
            // leading word precedes the live audio; a tap submits nothing.
            const r3 = new PreRollRing(PRE_ROLL_MS);
            r3.push(new Blob(['FIRST_WORD']), WARM_TIMESLICE_MS); // spoken in cold-start gap
            // engage: drain pre-roll into capture, THEN append live slices
            const capture = [...r3.drain()];
            capture.push(new Blob(['rest_of_sentence']));
            out.captureLeadIsFirstWord = (await capture[0].text()) === 'FIRST_WORD';
            out.captureIncludesLive = capture.length === 2;
            // tap path: gate says not deliberate -> nothing assembled
            const tapDecision = evaluateHoldGate(2000, 2050, HOLD_THRESHOLD_MS);
            out.tapAssemblesNothing = tapDecision.deliberate === false;

            out.constants = {{ PRE_ROLL_MS, HOLD_THRESHOLD_MS, WARM_TIMESLICE_MS }};
            process.stdout.write(JSON.stringify(out));
            """
        ).strip()
        + "\n"
    )
    proc = subprocess.run(
        [
            "node",
            "--no-warnings",
            f"--experimental-loader={loader.as_uri()}",
            str(script),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO / "frontend"),
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "Node pure-logic harness failed:\n"
            f"STDOUT:\n{proc.stdout}\n\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _have_node(), reason="node not available")
def test_preroll_and_hold_gate_pure_logic(tmp_path):
    """PART A — exercise the REAL exported PreRollRing + evaluateHoldGate."""
    r = _run_node_contract(tmp_path)

    # A2 — the ring always retains at least the requested pre-roll window so
    # the worst-case page.tsx cold-start gap (getUserMedia 200-700ms + the
    # 400ms Live-teardown wait at page.tsx:994) is fully covered.
    assert r["retainsAtLeastWindow"] is True, r
    assert r["retainedMs"] >= r["constants"]["PRE_ROLL_MS"], r
    assert r["ringEmptyAfterDrain"] is True, r
    assert r["drainedCount"] >= 1, r

    # A1 — the CORE #53 fix: the first word's slice is at the HEAD of the
    # drained lead-in (it is NOT evicted while it is within the window).
    assert r["headSurvives"] is True, (
        "pre-roll dropped the leading slice — first word would be clipped: "
        f"{r}"
    )

    # A3 — deliberate-hold gate (#54): a sub-threshold tap is gated; a hold
    # exactly at the threshold (and longer) is deliberate.
    assert r["tapGated"] is True, r
    assert r["holdAtThresholdDeliberate"] is True, r
    assert r["longHoldDeliberate"] is True, r
    assert r["tapHeldMs"] < r["constants"]["HOLD_THRESHOLD_MS"], r

    # A4 — integrated: engage seeds the capture with the pre-roll so the
    # leading word precedes the live audio; a tap assembles/submits nothing.
    assert r["captureLeadIsFirstWord"] is True, (
        "engaged capture does not start with the pre-roll lead-in — first "
        f"word lost: {r}"
    )
    assert r["captureIncludesLive"] is True, r
    assert r["tapAssemblesNothing"] is True, r

    # The shipped constants must stay in the spec'd bands.
    assert r["constants"]["PRE_ROLL_MS"] >= 500, r          # >= 500ms pre-roll
    assert 150 <= r["constants"]["HOLD_THRESHOLD_MS"] <= 250, r  # deliberate
    assert r["constants"]["WARM_TIMESLICE_MS"] <= 250, r    # fine-grained ring


# ===========================================================================
# PART B — backend STT: the prepended pre-roll HEAD must never be silently
# dropped/truncated (the bug's observable failure surface).
# ===========================================================================

from backend.providers.sarvam_stt import STT_CHUNK_MS, SarvamSTT  # noqa: E402


class FakeAudioSegment:
    """Minimal pydub.AudioSegment stub (same surface as
    test_stt_long_audio_chunking.py). One word per second; word_0 is the
    PRE-ROLL HEAD (the first word spoken in page.tsx's cold-start gap)."""

    def __init__(self, start_ms: int, end_ms: int):
        self.start_ms = start_ms
        self.end_ms = end_ms

    def __len__(self):
        return self.end_ms - self.start_ms

    def __getitem__(self, sl):
        if isinstance(sl, slice):
            lo = 0 if sl.start is None else sl.start
            hi = len(self) if sl.stop is None else sl.stop
            lo = max(0, min(lo, len(self)))
            hi = max(0, min(hi, len(self)))
            return FakeAudioSegment(self.start_ms + lo, self.start_ms + hi)
        raise TypeError("only slice indexing used by splitter")

    @property
    def dBFS(self):
        if self.end_ms - self.start_ms == 0:
            return float("-inf")
        # A real pause near the 25s ceiling so the splitter snaps there.
        if 24000 <= self.start_ms < 24400:
            return float("-inf")
        return -20.0

    def set_frame_rate(self, _):
        return self

    def set_channels(self, _):
        return self

    def set_sample_width(self, _):
        return self

    def export(self, buf, format="wav"):  # noqa: A002
        buf.write(f"FAKEWAV:{self.start_ms}:{self.end_ms}".encode())
        buf.seek(0)
        return buf


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
        capped_end = min(end_ms, start_ms + SARVAM_HARD_LIMIT_MS)
        words = [f"word_{ms // 1000}" for ms in range(start_ms, capped_end, 1000)]
        return _FakeResp(
            {
                "transcript": " ".join(words),
                "language_code": "en-IN",
                "language_probability": 0.99,
            }
        )


@pytest.fixture
def patch_pydub(monkeypatch):
    fake_pydub = types.ModuleType("pydub")

    class _Factory:
        @staticmethod
        def from_file(_bio, format=None):  # noqa: A002
            # 92s utterance — long enough to hit the silence splitter (>3
            # Sarvam 25s windows). word_0 is the prepended PRE-ROLL HEAD.
            return FakeAudioSegment(0, 92_000)

    fake_pydub.AudioSegment = _Factory
    monkeypatch.setitem(sys.modules, "pydub", fake_pydub)
    yield


@pytest.fixture
def patch_httpx(monkeypatch):
    import backend.providers.sarvam_stt as mod

    monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
    yield


def test_preroll_head_word_survives_backend_stt(patch_pydub, patch_httpx):
    """The FIRST word (word_0 — the pre-roll head the warm-stream fix
    prepends) must appear in the transcript. A regression that mishandled a
    prepended head, or re-introduced single-shot truncation, would drop
    word_0 (head-clipping #53 at the STT layer)."""
    stt = SarvamSTT()
    audio = b"\x00" * 4096  # > 1 KB so the short-audio guard doesn't fire
    result = asyncio.run(
        stt.transcribe(audio_bytes=audio, audio_format="webm", language_code="en-IN")
    )
    words = result.text.split()

    # CORE #53 CONTRACT at the STT boundary: the leading word is present and
    # is actually FIRST (not just somewhere in the middle).
    assert "word_0" in words, f"FIRST word dropped by STT — head clipped: {words[:5]}"
    deduped = [w for i, w in enumerate(words) if i == 0 or w != words[i - 1]]
    assert deduped[0] == "word_0", (
        f"transcript does not START with the pre-roll head word: {deduped[:5]}"
    )
    # And the WHOLE 92s utterance survives in order (no head drop, no tail
    # truncation) — the prepended pre-roll doesn't break chunking.
    expected = [f"word_{i}" for i in range(92)]
    assert deduped == expected, (
        f"transcript garbled — deduped {len(deduped)} words "
        f"(first={deduped[:3]}, last={deduped[-3:]}), expected 92 in order"
    )
    assert result.raw.get("chunked") is True
    assert result.raw.get("chunk_count", 0) >= 4


def test_short_preroll_only_blob_is_not_truncated(patch_pydub, patch_httpx, monkeypatch):
    """A short blob that is essentially pre-roll + a couple words (the common
    PTT case) must transcribe in ONE call with word_0 intact — proving the
    pre-roll head adds no truncation and no extra latency for short clips."""
    short = FakeAudioSegment(0, 5_000)  # ~5s: pre-roll + a short answer
    monkeypatch.setattr(
        sys.modules["pydub"].AudioSegment,
        "from_file",
        staticmethod(lambda *a, **k: short),
    )
    stt = SarvamSTT()
    result = asyncio.run(
        stt.transcribe(audio_bytes=b"\x00" * 4096, audio_format="webm")
    )
    words = result.text.split()
    assert words == [f"word_{i}" for i in range(5)], words
    assert words[0] == "word_0", f"pre-roll head missing on short clip: {words}"
    # Single-call path — no chunked marker, no added round-trips.
    assert result.raw.get("chunked") is not True
