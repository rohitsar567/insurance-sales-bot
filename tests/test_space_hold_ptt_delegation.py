"""Regression test for #53 (PTT head-clip) + #54 (PTT start latency) —
the page.tsx SPACE-hold DELEGATION wiring — 2026-05-18.

WHY A SEPARATE TEST FROM test_ptt_preroll_warm_stream.py
-----------------------------------------------------------------------------
test_ptt_preroll_warm_stream.py pins the *hook's* pure pre-roll + hold-gate
logic (PreRollRing / evaluateHoldGate in useStreamingVoice.ts) and the
backend STT head-survival contract. It does NOT pin the remaining surface:
that page.tsx's SPACE-hold path actually DELEGATES to that hook instead of
cold-starting its own getUserMedia + MediaRecorder per press. Naive prepending
of the hook's pre-roll blobs onto page.tsx's *separate* recorder chunks would
produce a corrupt webm (two independent MediaRecorder streams); the only
correct fix is delegation. This file pins that delegation + every
non-negotiable semantic it must preserve.

WHY A SOURCE-ASSERTION TEST (no JS runner)
-----------------------------------------------------------------------------
This repo has NO JS/React test harness — frontend/package.json has no `test`
script, no jest/vitest, and `tests/` is a pure pytest (Python) suite. The
SPACE-hold delegation is browser DOM event wiring inside a React client
component; it cannot be exercised from Python, and standing up a
jest/RTL/jsdom toolchain would be a large out-of-scope change to shared
frontend tooling. The existing sibling test (test_ptt_preroll_warm_stream.py)
explicitly acknowledges this same constraint ("There is no JS test runner in
this repo, so the two contracts are pinned at the layers a Python test can
reach honestly") and pins frontend contracts via source assertions. This file
follows that established repo idiom: it asserts the structural wiring
contract in the SHIPPED page.tsx so a future refactor that silently
re-introduces the per-press cold start (or drops a preserved semantic) fails
CI loudly.

Run:
    cd /Users/rohitsar/Developer/Insurance\\ Sales\\ Bot
    PYTHONPATH=$PWD .venv/bin/python -m pytest \
        tests/test_space_hold_ptt_delegation.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PAGE = REPO / "frontend" / "src" / "app" / "page.tsx"


@pytest.fixture(scope="module")
def src() -> str:
    return PAGE.read_text()


def _strip_comments(code: str) -> str:
    """Drop // line-comment content (and blank-after-strip lines) so negative
    assertions ("must NOT call send()/setBusy(true)") match only real CODE,
    not the explanatory comments that legitimately *name* the avoided trap."""
    out = []
    for line in code.splitlines():
        # Remove an inline/standalone // comment. No string literal in the
        # delegated handlers contains "//", so a plain split is safe here.
        if "//" in line:
            line = line[: line.index("//")]
        if line.strip():
            out.append(line)
    return "\n".join(out)


def _slice(src: str, start_marker: str, end_marker: str) -> str:
    """Return the source between (and including) two anchor substrings.
    Asserts both anchors exist exactly once so the test fails loudly if the
    region is renamed rather than silently asserting against the wrong code.
    """
    assert src.count(start_marker) == 1, f"anchor not unique: {start_marker!r}"
    assert src.count(end_marker) == 1, f"anchor not unique: {end_marker!r}"
    i = src.index(start_marker)
    j = src.index(end_marker, i) + len(end_marker)
    assert j > i, f"end anchor precedes start anchor: {end_marker!r}"
    return src[i:j]


def test_space_handler_refs_point_at_delegated_funcs(src: str):
    """The SPACE keydown/keyup refs must be wired to the DELEGATED handlers
    (startSpaceHoldPTT / stopSpaceHoldPTT), NOT the legacy per-press
    cold-start startRecording/stopRecording. This is the core #53/#54 fix."""
    assert "startRecordingRef.current = startSpaceHoldPTT;" in src, (
        "SPACE keydown ref no longer points at the delegated handler — the "
        "per-press getUserMedia cold start (head-clip #53 + latency #54) may "
        "have been re-introduced."
    )
    assert "stopRecordingRef.current = stopSpaceHoldPTT;" in src
    # The legacy refs must NOT be re-bound to the cold-start path.
    assert "startRecordingRef.current = startRecording;" not in src
    assert "stopRecordingRef.current = stopRecording;" not in src


def test_delegated_start_calls_hook_beginPushToTalk(src: str):
    """startSpaceHoldPTT must engage the hook's warm-stream PTT, never spin
    its own getUserMedia / MediaRecorder."""
    fn = _slice(
        src,
        "async function startSpaceHoldPTT() {",
        "async function stopSpaceHoldPTT() {",
    )
    assert "streamingVoice.beginPushToTalk()" in fn, (
        "startSpaceHoldPTT does not delegate to the hook's beginPushToTalk"
    )
    # No independent capture device may be opened on the delegated path.
    assert "getUserMedia" not in fn, "delegated start must not cold-start a mic"
    assert "new MediaRecorder" not in fn, (
        "delegated start must not create its own recorder (corrupt-webm trap)"
    )


def test_delegated_stop_calls_hook_endPushToTalk_and_does_not_resubmit(src: str):
    """stopSpaceHoldPTT must finalize via the hook's endPushToTalk().

    The hook ALREADY delivers a deliberate-hold transcript via
    onFinalTranscript -> voiceSubmitRef -> send() (the exact downstream path
    the old recorder.onstop success branch used). Re-feeding the returned
    string into send() here would double-submit, so the delegated stop must
    NOT call send()/voiceSubmitRef itself."""
    fn = _slice(
        src,
        "async function stopSpaceHoldPTT() {",
        "  // #53/#54 — these refs now point at the DELEGATED SPACE-hold",
    )
    assert "await streamingVoice.endPushToTalk()" in fn
    # No re-submission on the delegated stop path (hook already submitted).
    # Check against CODE only — the comments legitimately name send() while
    # explaining WHY we must not call it.
    code = _strip_comments(fn)
    assert "send(" not in code, (
        "delegated stop re-submits the transcript — double-fire; the hook's "
        "onFinalTranscript already routed it through send()"
    )
    assert "voiceSubmitRef" not in code


def test_sub_threshold_tap_is_clean_noop(src: str):
    """endPushToTalk() resolves null on a sub-threshold tap / empty capture.
    The delegated stop must treat null as a clean no-op (no empty submit,
    cosmetic state reset) — a non-negotiable semantic."""
    fn = _slice(
        src,
        "async function stopSpaceHoldPTT() {",
        "  // #53/#54 — these refs now point at the DELEGATED SPACE-hold",
    )
    assert "text === null" in fn, (
        "delegated stop does not branch on the null (tap) result — a "
        "sub-threshold tap may submit empty / leave state stuck"
    )
    # On the null branch the cosmetic phase set on keydown is cleared.
    assert "setVoicePhase(null)" in fn


def test_preserved_semantics_present_on_delegated_path(src: str):
    """Enumerate the non-negotiable semantics the original SPACE path had and
    assert each survives on the delegated path."""
    start = _slice(
        src,
        "async function startSpaceHoldPTT() {",
        "async function stopSpaceHoldPTT() {",
    )
    stop = _slice(
        src,
        "async function stopSpaceHoldPTT() {",
        "  // #53/#54 — these refs now point at the DELEGATED SPACE-hold",
    )

    # interruptBotAudio("ptt-start") — silence prior bot TTS the instant the
    # user starts talking (was startRecording's first action).
    assert 'interruptBotAudio("ptt-start")' in start

    # voicePhase "transcribing" UX while STT is in flight (was recorder.onstop).
    assert 'setVoicePhase("transcribing")' in start

    # busy is NOT force-set true before delegating — send() (invoked by the
    # hook) owns busy and early-returns if busy is already true. Setting it
    # here would silently drop the turn. Pin that this trap is avoided
    # (CODE only; the comment legitimately names setBusy(true)).
    assert "setBusy(true)" not in _strip_comments(start), (
        "delegated start sets busy=true before send() runs — send() will "
        "early-return on `busy` and the spoken turn is silently dropped"
    )

    # maybeResumeLive with the userPrefersLive gate (KI-028) survives.
    assert "userPrefersLive" in stop and "live.setLive(true)" in stop

    # mic_permission_denied banner surfacing is still reachable on the
    # delegated start (defensive path) — matches the cold path's catch.
    assert 'setVoiceErrorBanner({ type: "mic_permission_denied"' in start

    # Re-entrancy guard equivalent to the old recordingRef/busyRef
    # "don't start twice" intent.
    assert "spaceHoldPttInFlightRef" in start


def test_keydown_keyup_guards_unchanged(src: str):
    """e.repeat / modifier guards, shouldSuppressSpace(), spaceHoldOwnsRecRef
    ownership tracking, setSpaceHoldActive, and preventDefault must all remain
    on the SPACE handlers (non-negotiable: a refactor must not nuke a
    legitimately-typed space in the textarea)."""
    handlers = _slice(
        src,
        "const onKeyDown = (e: KeyboardEvent) => {",
        "window.addEventListener(\"keydown\", onKeyDown);",
    )
    # e.repeat + all modifier guards.
    assert "if (e.repeat) return;" in handlers
    assert "e.metaKey || e.ctrlKey || e.altKey || e.shiftKey" in handlers
    # textarea guard still gates SPACE-hold.
    assert "if (shouldSuppressSpace()) return;" in handlers
    # ownership tracking + visual state both directions.
    assert "spaceHoldOwnsRecRef.current = true;" in handlers
    assert "spaceHoldOwnsRecRef.current = false;" in handlers
    assert "setSpaceHoldActive(true);" in handlers
    assert "setSpaceHoldActive(false);" in handlers
    # the "did THIS keydown own the press" guard before stopping.
    assert "if (!spaceHoldOwnsRecRef.current) return;" in handlers
    # preventDefault on both keydown + keyup (stop a stray typed space).
    assert handlers.count("e.preventDefault();") >= 2
    # busy still blocks a fresh SPACE-hold start (turn in flight) and the
    # delegated in-flight flag gates the stop call.
    assert "if (recordingRef.current || busyRef.current) return;" in handlers
    assert "spaceHoldPttInFlightRef.current) void sp();" in handlers


def test_onscreen_button_path_left_intact(src: str):
    """The on-screen Push-to-talk *button* uses a separate onClick handler
    (startRecording/stopRecording) and does NOT share the SPACE code path, so
    per the task it must be left untouched. Assert the legacy recorder
    functions still exist and the button still uses them."""
    assert "async function startRecording() {" in src
    assert "function stopRecording() {" in src
    # Button onClick still toggles the legacy recorder path.
    assert re.search(
        r"onClick=\{recording \? stopRecording : startRecording\}", src
    ), "on-screen Push-to-talk button onClick wiring changed unexpectedly"
