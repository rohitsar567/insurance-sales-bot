"use client";

/**
 * useStreamingVoice — KI-168 (2026-05-15).
 *
 * Replaces the custom AudioWorklet + VAD + WAV-encode + /api/transcribe path
 * (useLiveConversation) with the browser's native Web Speech API. The user
 * sees their words land in the chat input area in real time as they speak,
 * just like ChatGPT / Claude voice mode — and when the browser detects
 * end-of-utterance silence, the final transcript is auto-submitted through
 * the existing send() path.
 *
 * Why this exists
 * -------------------------------------------------------------------------
 * The previous live-mode stack accumulated 12+ KIs of failure modes
 * (KI-044/057/060/064/113/114/115/131/134/139/141/159/165) trying to bolt
 * a reliable VAD onto raw mic PCM. Every fix surfaced a new failure on a
 * different mic / room / browser combo. The native SpeechRecognition API
 * gives us:
 *   - browser-grade end-of-speech detection (no rmsThreshold tuning)
 *   - streaming interim transcripts (no "where did my words go?" gap)
 *   - in-browser STT (no /api/transcribe round-trip latency)
 *
 * Behaviour
 * -------------------------------------------------------------------------
 *   - `enabled = true` → recognition.start() runs, mic icon stays live,
 *     interim transcript streams into the chat input via onInterimTranscript.
 *   - Browser detects ~1.5s silence → onend fires → we hand the final
 *     transcript to onFinalTranscript (caller calls send()).
 *   - After onend, if `enabled` is still true and no text request is in
 *     flight, we restart recognition so the mic stays live (continuous-mode
 *     emulation; native `continuous=true` doesn't fire silence-end on most
 *     browsers, so we use continuous=false + auto-restart instead).
 *   - `enabled = false` → recognition.abort() runs, no callbacks fire.
 *
 * Bot TTS playback is untouched — the page.tsx-owned <audio> elements still
 * play Sarvam-generated audio for assistant replies.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { postTranscribe } from "./api";
// KI-223..228 (2026-05-15) — additive resilience layer (V1.1/V1.3/V5.4/V6.8).
// Lives in a sibling module so the hook body stays under control and the
// retry / noise-floor / sample-rate helpers can be unit-tested in isolation.
import {
  retryPostTranscribe,
  scaleSpeechZcrBand,
  AdaptiveNoiseFloor,
  type VoiceError as VoiceErrorBase,
} from "./voice_resilience";

// W1 (2026-05-15) — additive 4th voice-error code. Surfaces a silent
// `getUserMedia` permission/denial failure (NotAllowedError /
// NotFoundError / SecurityError / generic DOMException) so page.tsx can
// render an actionable banner and revert the "Voice on" pill. Kept as a
// local widening of the base `VoiceError` union from voice_resilience.ts
// (which we don't touch per scope) — callers see the same
// `onVoiceError(err: VoiceError) => void` shape, just with one more legal
// string value.
export type VoiceError = VoiceErrorBase | "mic_permission_denied";

// KI-189 (2026-05-15) — live-speak barge-in tuning constants.
// The MediaRecorder mic stream IS echo-cancelled by the browser (KI-185
// `getUserMedia` AEC constraints), so the bot's TTS bleed lands at a
// very low RMS (~0.001-0.005) while actual user speech sits at ~0.05-0.2.
// We pick a threshold in between, and require ~300ms sustained energy
// to avoid firing on coughs / room thumps / single-frame spikes.
// KI-212 (2026-05-15) — was 0.025 / 18 frames. User reported barge-in
// completely failing: bot reads entire 14s reply uninterrupted. Lowered
// to fire on ANY decent speech burst within 100ms. Risk: false positives
// (chair creak, cough) — acceptable trade vs. broken barge-in.
const BARGE_IN_RMS_THRESHOLD = 0.008;
const BARGE_IN_SUSTAINED_FRAMES = 6; // ~100ms @ 60fps rAF
// KI-190 (2026-05-15) — adaptive threshold. The MediaRecorder mic stream
// has AEC, but for very loud bot TTS the residual bleed can still cross
// the static 0.025 threshold. We instead compute the threshold dynamically
// from the bot's CURRENT audio level: bot_rms * MULTIPLIER + BASE. Bot
// loud → threshold rises so user must speak loudly to overcome residual;
// bot quiet → threshold drops near floor so soft speech still wins.
// KI-212 — multiplier lowered 2.0 → 1.5 + base 0.005 → 0.002. Together
// with the static threshold drop, makes barge-in fire on much softer
// user speech even when bot is loud.
const BARGE_IN_BOT_RMS_MULTIPLIER = 1.5;
const BARGE_IN_BASE_THRESHOLD = 0.002;
// KI-191 (2026-05-15) — duck bot TTS volume while voice mode is on.
// Reducing playback amplitude further widens the gap between the bot's
// residual mic bleed (after AEC) and the user's normal-volume speech,
// making barge-in trivial. 0.6 is loud enough to hear clearly on
// headphones and laptop speakers without overpowering user speech.
// KI-211 (2026-05-15) — was 0.6; lowered to 0.3 because first-turn barge-in
// fails when adaptive calibration (KI-195) hasn't sampled user_speech_rms yet.
// 0.3 is loud enough to hear clearly on speakers + mic bleed is well under
// the static BARGE_IN_RMS_THRESHOLD, so users can talk over the bot on the
// first turn without needing prior calibration.
const VOICE_MODE_TTS_VOLUME = 0.3;
// KI-195 (2026-05-15) — adaptive TTS volume calibration relative to user's
// own measured speech level. Architecture: while user speaks (recorder
// active, NOT TTS) we sample mic RMS and track a rolling peak in
// userSpeechRmsRef. While TTS plays, every 300ms we sample bot_rms_at_mic
// via the KI-190 botAnalysers and reduce el.volume by 20% if bot_rms is
// closer to user_rms than the target ratio. Floor at 0.15 so the bot
// stays audible. This makes "bot bleed < user speech" a mathematical
// guarantee after one calibration turn → barge-in always works, echo
// never crosses the recognition threshold.
const USER_SPEECH_RMS_INITIAL = 0.05;          // typical quiet speech, used until calibrated
const USER_SPEECH_DETECTION_THRESHOLD = 0.02;  // mic RMS above this counts as "user speaking"
// FIX 5 (HIGH) — hard ceiling on the rolling-peak userSpeechRms. Without
// this, a single shout pins userSpeechRms at 0.4+ for the entire session
// → adaptive barge-in threshold rises → normal-volume speech can't break
// through → user has to shout to barge in again. The userRmsTick is also
// gated on !isTtsPlaying, so during TTS playback there's NO decay path —
// the wall-clock decay interval below provides decay regardless of gating.
const USER_SPEECH_RMS_CEILING = 0.15;
const USER_SPEECH_RMS_WALL_CLOCK_DECAY_MS = 1000;
const USER_SPEECH_RMS_WALL_CLOCK_DECAY_FACTOR = 0.9;
const VOLUME_CALIB_TARGET_RATIO = 0.35;        // bot_rms_at_mic should be ≤ user_rms × this
const VOLUME_CALIB_TICK_MS = 300;              // calibration sample period during TTS
const VOLUME_CALIB_DUCK_FACTOR = 0.8;          // multiply el.volume by this per tick if too loud
const VOLUME_CALIB_FLOOR = 0.15;               // never drop bot below this — must stay audible

// KI-202 (2026-05-15) — utterance batching grace window.
// Web Speech API's `onend` fires after ~1.5s silence, which means a natural
// mid-sentence pause ("So it will be just [pause] me") triggers TWO separate
// onend events and the user's sentence is submitted in two halves. We delay
// the actual submission by UTTERANCE_GRACE_MS after onend; if recognition
// re-fires (next word burst) before the timer expires, we append the new
// text/audio chunks and reset the timer. Only after a full UTTERANCE_GRACE_MS
// of true silence do we submit.
const UTTERANCE_GRACE_MS = 1500;
// KI-203 (2026-05-15) — post-TTS result-drop window.
// `recognition.abort()` doesn't immediately stop result delivery — onresult
// events from the now-abandoned recognition can keep arriving for a beat
// afterwards. Keep dropping results for this many ms after TTS ends.
const POST_TTS_DROP_MS = 300;

// KI-285 (2026-05-16) — echo-suppression barge-in grace window.
//
// ROOT CAUSE this fixes: the bot's TTS reply was stopping a fraction of a
// second after it started, with NO user having spoken. The reply audio is a
// single <audio> blob (no chunking, `ended` fires once at true end), so the
// premature stop could only come from triggerBargeIn() pausing the element.
// The barge-in VAD floors its threshold at BARGE_IN_RMS_THRESHOLD (0.008)
// when computeBotRms() returns 0 — which it ALWAYS does for the first frames
// of playback (the per-element MediaElementSource analyser has no data yet)
// and PERMANENTLY whenever createMediaElementSource() throws (Safari,
// element already Web-Audio-routed, or autoplay-suspended ctx). Browser AEC
// is imperfect on speaker (non-headphone) users; the bot's own voice echoes
// back into the mic at ~0.001-0.02 RMS in the speech ZCR band — clearing the
// 0.008 floor for 6 frames (~100ms) and self-triggering a "barge-in" on the
// bot's OWN audio. No prior hysteresis guarded the playback-start window.
//
// FIX: do not treat ANY VAD energy as a barge-in until the bot's audio has
// been playing for BARGE_IN_GRACE_MS. The first ~600ms of a reply is where
// echo (not the user) is the energy source — the user has not yet had time
// to hear enough of the reply to decide to interrupt, let alone produce
// BARGE_IN_SUSTAINED_FRAMES of speech. Genuine barge-in is unaffected: a
// real interruption is the user speaking *over* the bot for seconds, so the
// sustained-energy gate is re-armed and fires the instant the grace window
// elapses while the user is still talking. Only the bot's own start-of-reply
// echo — which by definition cannot outlast a brief grace window without the
// user actually speaking — is suppressed.
const BARGE_IN_GRACE_MS = 600;
// KI-285 (2026-05-16) — defence-in-depth. Even AFTER the grace window, when
// computeBotRms() is unavailable (returns 0) we must not collapse the
// barge-in threshold to the bare 0.008 static floor — that floor is BELOW
// documented speaker echo bleed (up to ~0.02 RMS per KI-189/190 comments),
// so echo alone clears it. When we have no usable bot-level reference, hold
// the threshold at this echo-safe floor. Real user speech sits at
// ~0.05-0.2 RMS (KI-189) and clears this comfortably; residual AEC echo
// (~0.02 worst case on speakers) does not.
const BARGE_IN_NO_BOTREF_FLOOR = 0.035;

// =========================================================================
// #53 / #54 (2026-05-18) — push-to-talk head-clipping + start-latency fix.
//
// ROOT CAUSE (verified):
//   page.tsx's push-to-talk path cold-starts the mic on every SPACE press:
//     page.tsx:1350-1361  onKeyDown(SPACE) → startRecordingRef.current()
//     page.tsx:1004-1019  startRecording() → navigator.mediaDevices
//                          .getUserMedia(...)  [COLD — 200-700ms on HF Space]
//     page.tsx:1021       new MediaRecorder(stream)
//     page.tsx:1213       recorder.start()    [capture truly begins HERE]
//   Every word the user speaks between the keydown and recorder.start()
//   firing is *never captured* → the leading word is lost/garbled (#53,
//   transcribed "S A R" for "Sir."). The same cold-start is the multi-second
//   delay the user feels before recording begins (#54). There is NO pre-roll
//   buffer and NO warm/pre-armed stream anywhere in the codebase.
//
// FIX (this hook, since page.tsx is owned by another writer and its PTT path
// is fully self-contained):
//   - Keep ONE mic stream + MediaRecorder + AudioContext WARM for the hook's
//     entire armed lifetime (acquired once after the user opts into voice,
//     never torn down per-press, survives the Live↔PTT toggle). A persistent
//     open audio device means the OS mic is already hot, so page.tsx's own
//     per-press getUserMedia resolves in ~10-50ms instead of cold-starting
//     (200-700ms) — that alone removes the felt multi-second start delay.
//   - The warm MediaRecorder runs with a short timeslice, feeding a rolling
//     PRE-ROLL ring buffer that always holds the last ~PRE_ROLL_MS of audio.
//   - The PTT API (beginPushToTalk/endPushToTalk) prepends the pre-roll to
//     the captured utterance, so the FIRST WORD — spoken in the cold-start
//     gap — is always in the blob even though page.tsx's recorder missed it.
//   - A DELIBERATE-HOLD gate: beginPushToTalk arms instantly but the capture
//     only "engages" after HOLD_THRESHOLD_MS; a sub-threshold tap (key
//     bounce, accidental press) is discarded and produces no submission.
//   - AudioContext.resume() is kept warm WHILE armed (not lazily on first
//     press), and warm-stream / permission / worklet failures are surfaced
//     via onVoiceError — never silent.
//
// The pure pre-roll ring-buffer + hold-gate logic is exported (PreRollRing,
// evaluateHoldGate) so it is self-contained and independently exercised by
// the regression test.
// =========================================================================

// Size of the rolling pre-roll buffer. Must comfortably cover the worst-case
// page.tsx cold-start gap (getUserMedia 200-700ms + MediaRecorder spin-up +
// the optional 400ms Live-teardown wait at page.tsx:994). 800ms gives margin
// without bloating the blob (browser webm/opus ≈ 4 KB/s ⇒ ~3.2 KB of lead-in).
export const PRE_ROLL_MS = 800;
// Warm MediaRecorder timeslice. Small enough that the pre-roll ring has fine
// granularity (we never drop more than one slice of lead-in when trimming the
// ring to PRE_ROLL_MS), large enough not to thrash ondataavailable.
export const WARM_TIMESLICE_MS = 200;
// Deliberate-hold threshold (#54). The hold must be intentional so an
// accidental tap / key-bounce doesn't fire a turn, but it must feel instant
// on a real hold — 200ms sits in the requested 150-250ms band.
export const HOLD_THRESHOLD_MS = 200;

/**
 * PreRollRing — a rolling, time-bounded ring buffer of MediaRecorder Blob
 * slices. `push` appends a freshly-emitted slice (each slice represents
 * ~WARM_TIMESLICE_MS of audio); the ring evicts the oldest slices once the
 * retained wall-clock duration exceeds `windowMs`, so it always holds *at
 * least* the last `windowMs` of audio (it may hold up to one extra slice so
 * a head word that started just before `windowMs` ago is never trimmed).
 *
 * Pure + framework-free so the regression test can drive it directly without
 * a browser. `drain()` returns the retained slices oldest-first and clears
 * the ring (used at PTT-engage to seed the utterance with the lead-in).
 */
export class PreRollRing {
  private slices: Array<{ blob: Blob; ms: number }> = [];
  private retainedMs = 0;
  private readonly windowMs: number;
  constructor(windowMs: number = PRE_ROLL_MS) {
    this.windowMs = windowMs;
  }

  push(blob: Blob, sliceMs: number = WARM_TIMESLICE_MS): void {
    if (!blob || blob.size <= 0) return;
    this.slices.push({ blob, ms: sliceMs });
    this.retainedMs += sliceMs;
    // Evict from the front while doing so still leaves >= windowMs retained
    // (keep one extra slice of slack so a word that began just before the
    // window boundary survives — never trim into the requested lead-in).
    while (
      this.slices.length > 1 &&
      this.retainedMs - this.slices[0].ms >= this.windowMs
    ) {
      const dropped = this.slices.shift();
      if (dropped) this.retainedMs -= dropped.ms;
    }
  }

  /** Retained lead-in slices oldest-first; clears the ring. */
  drain(): Blob[] {
    const out = this.slices.map((s) => s.blob);
    this.slices = [];
    this.retainedMs = 0;
    return out;
  }

  /** Approximate retained wall-clock duration (ms). */
  retainedDurationMs(): number {
    return this.retainedMs;
  }

  clear(): void {
    this.slices = [];
    this.retainedMs = 0;
  }
}

/**
 * evaluateHoldGate — pure decision for the deliberate-hold threshold (#54).
 *
 * Given when the user engaged (pressed) and released, decide whether the
 * press was a DELIBERATE hold (capture should be submitted) or a sub-threshold
 * TAP (discard — accidental press / key bounce). Kept pure so the regression
 * test can assert the boundary exactly without timers.
 *
 *   heldMs >= thresholdMs  → { deliberate: true  }  (engage + submit)
 *   heldMs <  thresholdMs  → { deliberate: false }  (discard, no submit)
 */
export function evaluateHoldGate(
  pressedAt: number,
  releasedAt: number,
  thresholdMs: number = HOLD_THRESHOLD_MS,
): { deliberate: boolean; heldMs: number } {
  const heldMs = Math.max(0, releasedAt - pressedAt);
  return { deliberate: heldMs >= thresholdMs, heldMs };
}

// Minimal types for the Web Speech API since lib.dom.d.ts ships them under
// `webkitSpeechRecognition` only and the standard `SpeechRecognition` symbol
// is still vendor-prefixed in most browsers as of 2026-05.
type SpeechRecognitionAlternative = { transcript: string; confidence: number };
type SpeechRecognitionResult = {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternative;
};
type SpeechRecognitionResultList = {
  length: number;
  [index: number]: SpeechRecognitionResult;
};
interface SpeechRecognitionEventLike extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEventLike extends Event {
  error: string;
  message?: string;
}
interface SpeechRecognitionInstance extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((ev: SpeechRecognitionEventLike) => void) | null;
  onerror: ((ev: SpeechRecognitionErrorEventLike) => void) | null;
  onend: ((ev: Event) => void) | null;
  onstart: ((ev: Event) => void) | null;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionInstance;

export interface UseStreamingVoiceOptions {
  enabled: boolean;
  onInterimTranscript: (text: string) => void;
  onFinalTranscript: (text: string) => void;
  onError: (msg: string) => void;
  onListening: (listening: boolean) => void;
  isTextRequestPendingRef: React.MutableRefObject<boolean>;
  language?: string;
  // KI-223 (2026-05-15) — V1.1 / V1.2 / V5.4. Optional structured error
  // callback so page.tsx can react specifically to recoverable failures
  // (e.g. show "tap to enable audio" when audio_context_suspended fires).
  // Optional: existing consumers that don't pass this still work.
  onVoiceError?: (err: VoiceError) => void;
}

export interface UseStreamingVoiceReturn {
  start: () => void;
  stop: () => void;
  isSupported: boolean;
  /**
   * FIX 3 (HIGH) — Barge-in signal. The hook flips an internal flag when
   * `triggerBargeIn` fires (user spoke over bot TTS). The caller (page.tsx)
   * should poll this method before/after every fetch tick during a /api/chat
   * stream — if it returns true, abort the in-flight request and any pending
   * audio assembly so the bot doesn't keep talking after the user
   * interrupted. Reading clears the flag (one-shot semantics).
   *
   * Wire-up (caller side, OUT OF THIS HOOK'S SCOPE):
   *   - Before fetch, store an AbortController locally.
   *   - In the stream-reading loop, periodically check
   *     `streamingVoice.consumeBargeInSignal()` and call `controller.abort()`
   *     when it returns true.
   *   - Alternatively register a side-effect that polls every 100ms while a
   *     send() is in flight.
   */
  consumeBargeInSignal: () => boolean;

  // ----------------------------------------------------------------------
  // #53 / #54 — warm-stream + pre-roll push-to-talk API.
  //
  // This is the minimal API the push-to-talk UI integrates with. Even
  // without an explicit call, `armWarmStream()` is invoked autonomously by
  // the hook once voice has been enabled, so the OS mic device is kept hot
  // for the rest of the session — that removes the per-press cold-start that
  // page.tsx's own getUserMedia otherwise pays (the felt multi-second delay,
  // #54) and continuously fills the pre-roll ring so the leading word spoken
  // in the cold-start gap survives (#53).
  // ----------------------------------------------------------------------

  /** True once the warm mic stream + recorder + AudioContext are live and
   *  the pre-roll ring is filling. */
  isWarm: boolean;

  /** Pre-arm (or re-arm) the persistent warm stream. Idempotent; safe to
   *  call repeatedly. Resolves true when the warm stream is recording. */
  armWarmStream: () => Promise<boolean>;

  /** Release the warm stream + recorder + AudioContext (mic indicator off).
   *  Called on unmount; callers may call it to fully relinquish the mic. */
  disarmWarmStream: () => void;

  /**
   * Engage a push-to-talk capture. Call on hold-start (e.g. SPACE keydown).
   * Returns immediately. The capture *engages* only after HOLD_THRESHOLD_MS
   * so a sub-threshold tap is ignored; the engaged utterance is seeded with
   * the pre-roll ring so the first word (spoken during the cold-start gap)
   * is always included.
   */
  beginPushToTalk: () => void;

  /**
   * End a push-to-talk capture. Call on hold-release (e.g. SPACE keyup).
   * If the hold was deliberate (>= HOLD_THRESHOLD_MS) the assembled blob
   * (pre-roll + live capture) is transcribed and delivered via
   * onFinalTranscript; a sub-threshold tap resolves to null and submits
   * nothing. Resolves with the final transcript, or null when discarded /
   * empty.
   */
  endPushToTalk: () => Promise<string | null>;

  /** Snapshot+drain the current pre-roll ring (oldest-first). Exposed for
   *  the regression test and any caller that wants to splice the lead-in
   *  into its own recorder blob. */
  consumePreRollChunks: () => Blob[];
}

function resolveCtor(): SpeechRecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function useStreamingVoice(
  opts: UseStreamingVoiceOptions,
): UseStreamingVoiceReturn {
  const {
    enabled,
    onInterimTranscript,
    onFinalTranscript,
    onError,
    onListening,
    isTextRequestPendingRef,
    language = "en-IN",
    onVoiceError,
  } = opts;

  // Keep latest callback refs so the recognition handlers always call the
  // freshest closure without re-binding the recognition instance on every
  // render (re-binding mid-utterance loses interim results).
  const onInterimRef = useRef(onInterimTranscript);
  const onFinalRef = useRef(onFinalTranscript);
  const onErrorRef = useRef(onError);
  const onListeningRef = useRef(onListening);
  // KI-223 — optional structured-error callback ref. Defaults to no-op so
  // the rest of the hook can call it unconditionally without null checks.
  const onVoiceErrorRef = useRef<(err: VoiceError) => void>(
    onVoiceError ?? (() => { /* no-op */ }),
  );
  useEffect(() => { onInterimRef.current = onInterimTranscript; }, [onInterimTranscript]);
  useEffect(() => { onFinalRef.current = onFinalTranscript; }, [onFinalTranscript]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { onListeningRef.current = onListening; }, [onListening]);
  useEffect(() => { onVoiceErrorRef.current = onVoiceError ?? (() => { /* no-op */ }); }, [onVoiceError]);

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const finalsRef = useRef<string[]>([]);
  // KI-217 (2026-05-15) — track how many entries of finalsRef have already
  // been drained to pendingUtteranceRef. Each onend reads the slice from
  // `finalsConsumedRef.current` to end, then bumps the cursor. finalsRef
  // itself is NOT reset between restart cycles — only after the grace-timer
  // submit (when onFinalRef fires) or on user-toggled start/stop. This
  // prevents a Chrome quirk where late-delivered isFinal results arriving
  // after onend on a mid-utterance restart cycle would land in a freshly
  // wiped finalsRef and get dropped on the NEXT onend cycle's drain.
  const finalsConsumedRef = useRef<number>(0);
  const wantRunningRef = useRef(false); // mirrors `enabled` for handler closures
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorBackoffRef = useRef(0);
  // KI-188 (2026-05-15) — TTS-playback gate. Web Speech API has its own
  // internal mic pipeline that bypasses our getUserMedia AEC constraints,
  // so SpeechRecognition transcribes the bot's TTS audio bleeding from
  // speakers as user input ("echo loop"). The only reliable fix from JS
  // is to abort recognition while ANY <audio> in the DOM is playing.
  // Tracked via a MutationObserver + per-element play/pause/ended hooks.
  const isTtsPlayingRef = useRef(false);
  // KI-285 (2026-05-16) — wall-clock timestamp of the moment the CURRENT
  // bot TTS playback began (the false→true edge in updateTtsState). The
  // barge-in tick refuses to trigger until BARGE_IN_GRACE_MS has elapsed
  // since this instant, so the bot's own start-of-reply echo cannot
  // self-trigger a barge-in. Reset to 0 whenever TTS is not playing.
  const ttsPlaybackStartedAtRef = useRef<number>(0);
  const ttsAudioElementsRef = useRef<Set<HTMLAudioElement>>(new Set());
  // KI-203 (2026-05-15) — silently discard SpeechRecognition.onresult events
  // while this flag is true. Flipped on the instant TTS playback starts
  // (closes the ~100-300ms window between `audio.play()` and our abort()
  // taking effect, during which bot voice was being transcribed as user
  // input). Flipped back ~POST_TTS_DROP_MS after TTS ends so any in-flight
  // results from the dying recognition pipeline are still suppressed.
  const dropResultsRef = useRef(false);
  const dropResultsClearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // KI-202 (2026-05-15) — utterance-batching state.
  // pendingUtteranceRef accumulates the Web Speech transcript across multiple
  // onend events separated by sub-grace-window pauses. pendingChunksRef does
  // the same for MediaRecorder blobs so the Sarvam POST sees the WHOLE
  // utterance, not just the tail after the last pause. pendingSubmitTimerRef
  // is the grace-window setTimeout; it gets reset every time onend appends
  // more content.
  const pendingUtteranceRef = useRef<string>("");
  const pendingChunksRef = useRef<Blob[]>([]);
  const pendingSubmitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // FIX 3 (HIGH) — one-shot barge-in signal. Flipped true by triggerBargeIn
  // when the VAD detects sustained user speech over bot TTS. Read+cleared
  // via consumeBargeInSignal() so the caller (page.tsx) can abort any
  // in-flight /api/chat request that's still assembling more TTS audio.
  const bargeInRequestedRef = useRef<boolean>(false);
  // KI-228 (2026-05-15) — V6.8 adaptive noise floor. Persistent across the
  // entire hook lifetime so a user's noise environment learned across the
  // first 5 seconds carries through later TTS plays even if the audio
  // effect tears down + rebuilds the analyser between turns.
  const noiseFloorRef = useRef<AdaptiveNoiseFloor>(new AdaptiveNoiseFloor());
  // KI-225 (2026-05-15) — V1.3 sample-rate-aware ZCR band, cached from the
  // AudioContext at analyser-build time. Falls back to the 48 kHz reference
  // band when the context isn't up yet.
  const zcrBandRef = useRef<{ min: number; max: number }>({ min: 20, max: 250 });

  // ----------------------------------------------------------------------
  // KI-168 PHASE 2 — Sarvam authoritative-transcript layer.
  // We run a MediaRecorder in parallel with SpeechRecognition. When the
  // browser detects end-of-utterance silence (recognition.onend), we
  // already have the raw audio chunks in memory. Send them to the backend
  // /api/transcribe endpoint (Sarvam STT) and replace the Web Speech text
  // with Sarvam's authoritative result. Web Speech remains the fallback if
  // Sarvam times out, errors, or the audio path failed to initialise.
  // ----------------------------------------------------------------------
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recorderMimeRef = useRef<string>("audio/webm");
  // True only when MediaRecorder.start() actually succeeded. If false we
  // bypass the Sarvam path and use Web Speech transcripts directly.
  const recorderActiveRef = useRef(false);
  // Promise resolved on the recorder's next `stop` event so we can wait
  // for the final ondataavailable chunk before building the blob.
  const recorderStopWaiterRef = useRef<(() => void) | null>(null);

  // ----------------------------------------------------------------------
  // #53 / #54 — warm-stream + pre-roll push-to-talk state.
  //
  // SEPARATE from the Live-mode mediaStream/mediaRecorder above. The Live
  // recorder is acquired/torn-down per utterance and is gated on the
  // `enabled` prop (which page.tsx flips OFF during push-to-talk). This warm
  // stream is the OPPOSITE lifecycle: opened once after the user opts into
  // voice, kept alive across the Live↔PTT toggle for the hook's mounted
  // lifetime, never closed per-press. Holding a persistent open audio device
  // keeps the OS mic hot so any per-press getUserMedia (Live's OR page.tsx's
  // PTT) resolves near-instantly instead of cold-starting.
  // ----------------------------------------------------------------------
  const warmStreamRef = useRef<MediaStream | null>(null);
  const warmRecorderRef = useRef<MediaRecorder | null>(null);
  const warmCtxRef = useRef<AudioContext | null>(null);
  const warmMimeRef = useRef<string>("audio/webm");
  // The rolling pre-roll ring — always holds ~PRE_ROLL_MS of the most recent
  // audio so a PTT engage can prepend the lead-in the user spoke during the
  // cold-start gap.
  const preRollRef = useRef<PreRollRing>(new PreRollRing(PRE_ROLL_MS));
  // Live capture slices accumulated between PTT engage and release. The
  // submitted blob is preRoll.drain() (lead-in) ++ these (live capture).
  const pttCaptureRef = useRef<Blob[]>([]);
  // True between a deliberate engage and the matching release — the warm
  // recorder's ondataavailable routes slices to pttCaptureRef instead of
  // (only) the pre-roll ring while this is set.
  const pttEngagedRef = useRef<boolean>(false);
  // wall-clock ms of the current hold's keydown (0 when not pressed). Used
  // by evaluateHoldGate to classify deliberate hold vs sub-threshold tap.
  const pttPressedAtRef = useRef<number>(0);
  // setTimeout id for the deliberate-hold engage. Fires HOLD_THRESHOLD_MS
  // after press; if release beats it, the press was a tap and is discarded.
  const pttHoldTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // True once the user has opted into voice at least once. Latches the warm
  // stream ON for the rest of the hook's mounted lifetime so it survives the
  // Live↔PTT toggle (page.tsx flips `enabled` false for pure PTT).
  const voiceEverEnabledRef = useRef<boolean>(false);
  const [isWarm, setIsWarm] = useState<boolean>(false);

  const [isSupported] = useState<boolean>(() => resolveCtor() !== null);

  const clearRestartTimer = useCallback(() => {
    if (restartTimerRef.current !== null) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  }, []);

  // KI-210 (2026-05-15) — wait for an in-flight text turn to clear instead of
  // dropping the accumulated voice utterance. Polls isTextRequestPendingRef
  // every 300ms; resolves true once the flag clears, or false if the
  // maxWaitMs cap elapses first (we then proceed anyway rather than leak the
  // utterance forever on a stuck text request).
  const waitForTextClear = useCallback(async (maxWaitMs = 30000): Promise<boolean> => {
    const startTs = Date.now();
    while (isTextRequestPendingRef.current) {
      if (Date.now() - startTs > maxWaitMs) {
        console.debug("[useStreamingVoice] KI-210 wait timed out, submitting anyway");
        return false; // gave up waiting — proceed anyway
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    return true; // text cleared, ok to proceed
  }, [isTextRequestPendingRef]);

  const safeStart = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    try {
      rec.start();
    } catch {
      // start() throws InvalidStateError if recognition is already running.
      // Safe to ignore — onstart/onend will keep state in sync.
    }
  }, []);

  // Pick the best MediaRecorder mimeType. iOS Safari only supports
  // audio/mp4; Chromium/Firefox prefer audio/webm. Mirrors page.tsx PTT
  // recorder + the KI-134 fallback logic.
  const pickRecorderMime = useCallback((): string => {
    if (typeof window === "undefined" || typeof MediaRecorder === "undefined") {
      return "";
    }
    const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/mpeg"];
    for (const m of candidates) {
      try {
        if (MediaRecorder.isTypeSupported(m)) return m;
      } catch {
        // ignore
      }
    }
    return "";
  }, []);

  const stopRecorder = useCallback((): Promise<void> => {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      recorderStopWaiterRef.current = () => resolve();
      try {
        recorder.stop();
      } catch {
        // already stopped
        recorderStopWaiterRef.current = null;
        resolve();
      }
    });
  }, []);

  const teardownAudio = useCallback(() => {
    const recorder = mediaRecorderRef.current;
    if (recorder) {
      try {
        if (recorder.state !== "inactive") recorder.stop();
      } catch {
        // ignore
      }
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.onerror = null;
    }
    mediaRecorderRef.current = null;
    const stream = mediaStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => {
        try { t.stop(); } catch { /* ignore */ }
      });
    }
    mediaStreamRef.current = null;
    chunksRef.current = [];
    recorderActiveRef.current = false;
    recorderStopWaiterRef.current = null;
  }, []);

  const ensureAudioCapture = useCallback(async (): Promise<boolean> => {
    if (mediaRecorderRef.current && recorderActiveRef.current) return true;
    if (typeof navigator === "undefined" || !navigator.mediaDevices) return false;
    if (typeof MediaRecorder === "undefined") return false;
    try {
      // KI-185 (2026-05-15) — explicit AEC + noise suppression + auto-gain.
      // Default `{audio: true}` does NOT force AEC across all browsers, so the
      // mic was transcribing the bot's own TTS audio bleeding from speakers
      // back into the mic. Same constraints Zoom / Meet / ChatGPT-voice use.
      // For headphone users this gives near-perfect echo cancellation;
      // for speaker users it's 70-90% reduction (some bleed unavoidable
      // without server-side reference cancellation).
      // W2 (2026-05-15) — 2s watchdog around getUserMedia.
      // Some devices (Chromium on locked-down corporate Windows, certain
      // Android WebViews, OS-level mic-busy states) STALL getUserMedia
      // indefinitely instead of rejecting. Without a watchdog the pill
      // sits at "Voice on" forever, no banner, no recovery path.
      // Race the permission prompt against a 2000ms timeout that
      // rejects with name="StallTimeout" so the catch below treats it
      // identically to a hard denial (mic_permission_denied banner).
      const stream: MediaStream = await Promise.race([
        navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        }),
        new Promise<MediaStream>((_, reject) => {
          setTimeout(() => {
            const e = new Error("getUserMedia stalled >2s") as Error & { name: string };
            e.name = "StallTimeout";
            reject(e);
          }, 2000);
        }),
      ]);
      const mime = pickRecorderMime();
      recorderMimeRef.current = mime || "audio/webm";
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (ev: BlobEvent) => {
        if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
      };
      recorder.onstop = () => {
        const waiter = recorderStopWaiterRef.current;
        recorderStopWaiterRef.current = null;
        if (waiter) waiter();
      };
      recorder.onerror = (ev: Event) => {
        console.debug("[useStreamingVoice] MediaRecorder error", ev);
      };
      mediaStreamRef.current = stream;
      mediaRecorderRef.current = recorder;
      // 1s timeslice so chunks land progressively — ondataavailable fires
      // once per second instead of only on stop().
      recorder.start(1000);
      // W2 (2026-05-15) — affirmative post-acquire validation. A
      // MediaRecorder that .start()s without throwing is NOT proof the
      // capture is alive: Playwright's fake-mic stream, a stream from a
      // device that was unplugged between getUserMedia and start(), or a
      // codec rejection that fires `onerror` async — all leave recorder.state
      // anything other than "recording". Without this check, the pill flipped
      // to "Voice on" over a silent stream. Treat any non-"recording" state
      // as a hard fail and route to the same mic_permission_denied banner.
      if (recorder.state !== "recording") {
        try { stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
        mediaStreamRef.current = null;
        mediaRecorderRef.current = null;
        throw Object.assign(new Error(`MediaRecorder did not enter recording state (got ${recorder.state})`), {
          name: "RecorderNotRecording",
        });
      }
      recorderActiveRef.current = true;
      console.debug("[useStreamingVoice] MediaRecorder started", {
        mime: recorderMimeRef.current,
        state: recorder.state,
      });
      return true;
    } catch (err) {
      // W1 (2026-05-15) — DOMException name → VoiceError mapping.
      //   NotAllowedError / SecurityError → user denied or browser-blocked
      //   NotFoundError / OverconstrainedError → no usable input device
      //   NotReadableError / AbortError → OS-level mic owned by another app
      //   anything else (incl. plain Error) → treat as denial so the UI still
      //                                       surfaces an actionable banner
      // ALL of these map to "mic_permission_denied" because the user-visible
      // remediation is the same: open site permissions, allow mic, reload.
      // Returning `false` alone was insufficient — `start()` calls this via
      // `void ensureAudioCapture()` and never sees the rejection, so the pill
      // stayed at "Voice on" with zero mic. Emitting onVoiceError + flipping
      // wantRunningRef false + onListening(false) is the recovery contract.
      const name = (err as { name?: string } | null)?.name ?? "Error";
      console.debug(
        "[useStreamingVoice] getUserMedia / MediaRecorder init failed",
        { name, err },
      );
      recorderActiveRef.current = false;
      // `getUserMedia` rejection happens BEFORE we assign mediaStreamRef /
      // mediaRecorderRef, so there's nothing to tear down here. The
      // `wantRunningRef = false` + `onListening(false)` below is enough to
      // halt the SR auto-restart loop. The parent's `enabled = false` flip
      // (driven by the banner code) will run stop() which idempotently
      // re-runs full cleanup.
      // Surface to the page-level banner. Cast through the local widened
      // VoiceError union (W1) so TS accepts the new string code.
      try {
        onVoiceErrorRef.current("mic_permission_denied" as VoiceError);
      } catch {
        /* never let a user-supplied callback crash the hook */
      }
      // Stop the recognition restart loop and reset listening state so the
      // pill doesn't stay green over a dead mic. The parent (page.tsx) is
      // expected to also flip `enabled` back to false on the banner code,
      // which calls our `stop()` and idempotently cleans up.
      wantRunningRef.current = false;
      try {
        onListeningRef.current(false);
      } catch {
        /* ignore */
      }
      return false;
    }
  }, [pickRecorderMime]);

  // ======================================================================
  // #53 / #54 — warm-stream + pre-roll push-to-talk engine.
  // ======================================================================

  const disarmWarmStream = useCallback(() => {
    if (pttHoldTimerRef.current !== null) {
      clearTimeout(pttHoldTimerRef.current);
      pttHoldTimerRef.current = null;
    }
    pttEngagedRef.current = false;
    pttPressedAtRef.current = 0;
    pttCaptureRef.current = [];
    preRollRef.current.clear();
    const rec = warmRecorderRef.current;
    if (rec) {
      try {
        rec.ondataavailable = null;
        rec.onerror = null;
        rec.onstop = null;
        if (rec.state !== "inactive") rec.stop();
      } catch {
        /* ignore */
      }
    }
    warmRecorderRef.current = null;
    const stream = warmStreamRef.current;
    if (stream) {
      stream.getTracks().forEach((t) => {
        try { t.stop(); } catch { /* ignore */ }
      });
    }
    warmStreamRef.current = null;
    const ctx = warmCtxRef.current;
    if (ctx) {
      warmCtxRef.current = null;
      try { void ctx.close(); } catch { /* ignore */ }
    }
    setIsWarm(false);
  }, []);

  // Acquire (or re-acquire) the persistent warm stream. Idempotent: a
  // healthy recording warm recorder short-circuits. On failure routes
  // through the SAME onVoiceError("mic_permission_denied") contract the
  // Live path uses — never a silent failure.
  const armWarmStream = useCallback(async (): Promise<boolean> => {
    voiceEverEnabledRef.current = true;
    const existing = warmRecorderRef.current;
    if (existing && existing.state === "recording" && warmStreamRef.current) {
      return true;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices) return false;
    if (typeof MediaRecorder === "undefined") return false;
    // Tear down any half-built prior attempt before re-acquiring.
    if (existing || warmStreamRef.current) disarmWarmStream();
    try {
      // Same AEC/NS/AGC constraints as the Live + PTT paths (KI-185) so the
      // pre-roll is echo-cancelled identically to the rest of the capture.
      // W2-style 2s stall watchdog so a hung getUserMedia surfaces a banner
      // instead of pinning the warm state forever.
      const stream: MediaStream = await Promise.race([
        navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        }),
        new Promise<MediaStream>((_, reject) => {
          setTimeout(() => {
            const e = new Error("warm getUserMedia stalled >2s") as Error & { name: string };
            e.name = "StallTimeout";
            reject(e);
          }, 2000);
        }),
      ]);
      const mime = pickRecorderMime();
      warmMimeRef.current = mime || "audio/webm";
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream);
      preRollRef.current = new PreRollRing(PRE_ROLL_MS);
      pttCaptureRef.current = [];
      pttEngagedRef.current = false;
      recorder.ondataavailable = (ev: BlobEvent) => {
        if (!ev.data || ev.data.size <= 0) return;
        // Always feed the rolling pre-roll ring so the lead-in is ready the
        // instant a PTT engage fires (the word spoken in the cold-start gap
        // is in here). When a PTT capture is engaged, ALSO accumulate the
        // slice into the live capture buffer — the submitted blob is
        // preRoll.drain() (lead-in) ++ pttCaptureRef (live), so the first
        // word is never lost AND no chunk is dropped.
        preRollRef.current.push(ev.data, WARM_TIMESLICE_MS);
        if (pttEngagedRef.current) {
          pttCaptureRef.current.push(ev.data);
        }
      };
      recorder.onerror = (ev: Event) => {
        console.debug("[useStreamingVoice] warm MediaRecorder error", ev);
        try { onVoiceErrorRef.current("stream_stale"); } catch { /* ignore */ }
      };
      recorder.onstop = () => {
        // The warm recorder should never stop on its own while armed; if it
        // does (device unplug, OS interruption) surface it and let the
        // re-arm effect / next press recover.
        console.debug("[useStreamingVoice] warm MediaRecorder stopped");
      };
      warmStreamRef.current = stream;
      warmRecorderRef.current = recorder;
      recorder.start(WARM_TIMESLICE_MS);
      if (recorder.state !== "recording") {
        try { stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
        warmStreamRef.current = null;
        warmRecorderRef.current = null;
        throw Object.assign(
          new Error(`warm MediaRecorder not recording (got ${recorder.state})`),
          { name: "RecorderNotRecording" },
        );
      }
      // Keep an AudioContext warm + RUNNING so it never has to be resumed
      // lazily on first press (a suspended ctx is one of the documented
      // first-word-loss vectors). resume() needs a user gesture on some
      // browsers; armWarmStream is always called from one (voice toggle).
      try {
        const Ctor = (window.AudioContext
          || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
        if (Ctor) {
          if (!warmCtxRef.current || warmCtxRef.current.state === "closed") {
            warmCtxRef.current = new Ctor();
          }
          if (warmCtxRef.current.state === "suspended") {
            void warmCtxRef.current.resume().catch((err) => {
              console.debug("[useStreamingVoice] warm AudioContext.resume failed", err);
              try { onVoiceErrorRef.current("audio_context_suspended"); } catch { /* ignore */ }
            });
          }
        }
      } catch {
        /* AudioContext is best-effort for warmth; capture still works */
      }
      setIsWarm(true);
      console.debug("[useStreamingVoice] warm stream armed", {
        mime: warmMimeRef.current,
        preRollMs: PRE_ROLL_MS,
        timesliceMs: WARM_TIMESLICE_MS,
      });
      return true;
    } catch (err) {
      const name = (err as { name?: string } | null)?.name ?? "Error";
      console.debug("[useStreamingVoice] warm stream arm failed", { name, err });
      setIsWarm(false);
      try {
        onVoiceErrorRef.current("mic_permission_denied" as VoiceError);
      } catch {
        /* never let a user callback crash the hook */
      }
      return false;
    }
  }, [pickRecorderMime, disarmWarmStream]);

  const consumePreRollChunks = useCallback((): Blob[] => {
    return preRollRef.current.drain();
  }, []);

  // Submit an assembled PTT blob through the SAME Sarvam-with-retry path the
  // Live grace-timer uses (KI-226/302), then deliver via onFinalTranscript.
  // Returns the authoritative transcript or null.
  const submitPttBlob = useCallback(
    async (chunks: Blob[]): Promise<string | null> => {
      if (chunks.length === 0) return null;
      const blob = new Blob(chunks, { type: warmMimeRef.current || "audio/webm" });
      // ~3 KB empirical noise floor (same as the Live path / PTT KI-134).
      const MIN_BLOB_BYTES = 3000;
      if (blob.size < MIN_BLOB_BYTES) {
        console.debug("[useStreamingVoice] PTT blob below noise floor — discard", {
          bytes: blob.size,
        });
        return null;
      }
      await waitForTextClear();
      const APPROX_BYTES_PER_CHUNK = 100_000; // ~25s of webm/opus
      const estChunks = Math.max(1, Math.ceil(blob.size / APPROX_BYTES_PER_CHUNK));
      const attemptTimeoutMs = Math.min(120_000, 8_000 + estChunks * 12_000);
      let authoritative: string | null = null;
      const sarvam = await retryPostTranscribe(async (signal) => {
        const timeoutCtl = new AbortController();
        const timer = setTimeout(() => timeoutCtl.abort(), attemptTimeoutMs);
        const onOuterAbort = () => timeoutCtl.abort();
        signal.addEventListener("abort", onOuterAbort);
        try {
          return await postTranscribe(blob, language, timeoutCtl.signal);
        } finally {
          clearTimeout(timer);
          signal.removeEventListener("abort", onOuterAbort);
        }
      });
      if (sarvam) {
        const t = (sarvam.text || "").trim();
        if (t) authoritative = t;
      } else {
        try { onVoiceErrorRef.current("transcribe_failed"); } catch { /* ignore */ }
      }
      if (authoritative) {
        await waitForTextClear();
        onFinalRef.current(authoritative);
      }
      return authoritative;
    },
    [language, waitForTextClear],
  );

  // PTT engage — called HOLD_THRESHOLD_MS after a deliberate press. Snapshots
  // the pre-roll (lead-in spoken during the cold-start gap) into the live
  // capture buffer and flips the recorder's slice routing to also accumulate.
  const engagePtt = useCallback(() => {
    pttEngagedRef.current = true;
    // Seed the capture with the pre-roll lead-in FIRST so the first word
    // (which page.tsx's cold-started recorder would have missed) is at the
    // head of the submitted blob.
    const leadIn = preRollRef.current.drain();
    pttCaptureRef.current = [...leadIn];
    console.debug("[useStreamingVoice] PTT engaged", {
      leadInSlices: leadIn.length,
    });
  }, []);

  const beginPushToTalk = useCallback(() => {
    pttPressedAtRef.current = Date.now();
    pttCaptureRef.current = [];
    pttEngagedRef.current = false;
    // Make sure the warm stream is up so the pre-roll is actually filling.
    // armWarmStream is idempotent + fast when already warm.
    void armWarmStream();
    if (pttHoldTimerRef.current !== null) {
      clearTimeout(pttHoldTimerRef.current);
    }
    // Deliberate-hold gate: engage only after the threshold so a sub-150ms
    // tap does nothing. The capture still feels instant because the pre-roll
    // ring already holds the audio spoken during these HOLD_THRESHOLD_MS.
    pttHoldTimerRef.current = setTimeout(() => {
      pttHoldTimerRef.current = null;
      // Re-check the press is still held (release clears pttPressedAtRef).
      if (pttPressedAtRef.current !== 0) engagePtt();
    }, HOLD_THRESHOLD_MS);
  }, [armWarmStream, engagePtt]);

  const endPushToTalk = useCallback(async (): Promise<string | null> => {
    const pressedAt = pttPressedAtRef.current;
    const releasedAt = Date.now();
    pttPressedAtRef.current = 0;
    if (pttHoldTimerRef.current !== null) {
      clearTimeout(pttHoldTimerRef.current);
      pttHoldTimerRef.current = null;
    }
    const { deliberate, heldMs } = evaluateHoldGate(
      pressedAt || releasedAt,
      releasedAt,
      HOLD_THRESHOLD_MS,
    );
    const wasEngaged = pttEngagedRef.current;
    pttEngagedRef.current = false;
    if (!deliberate || !wasEngaged) {
      // Sub-threshold tap (or release before engage fired): discard. The
      // pre-roll ring keeps rolling for the warm stream; nothing submitted.
      console.debug("[useStreamingVoice] PTT discarded (tap)", {
        heldMs,
        deliberate,
        wasEngaged,
      });
      pttCaptureRef.current = [];
      return null;
    }
    const captured = pttCaptureRef.current;
    pttCaptureRef.current = [];
    return submitPttBlob(captured);
  }, [submitPttBlob]);

  const buildRecognition = useCallback((): SpeechRecognitionInstance | null => {
    const Ctor = resolveCtor();
    if (!Ctor) return null;
    const rec = new Ctor();
    rec.lang = language;
    rec.continuous = false;
    rec.interimResults = true;
    rec.maxAlternatives = 1;

    rec.onstart = () => {
      onListeningRef.current(true);
    };

    rec.onresult = (ev: SpeechRecognitionEventLike) => {
      // KI-203 (2026-05-15) — early-return while TTS is playing (or within
      // the POST_TTS_DROP_MS window after TTS ends). recognition.abort()
      // doesn't immediately stop result delivery, so we silently discard
      // every chunk that arrives during the dirty window. Without this, bot
      // TTS audio ("perfect days to get started Rohit") was leaking into
      // the user input field between `audio.play()` firing and our abort()
      // actually taking effect.
      if (dropResultsRef.current || isTextRequestPendingRef.current) {
        console.debug("[useStreamingVoice] KI-203/214 dropping recognition result", {
          drop: dropResultsRef.current,
          textPending: isTextRequestPendingRef.current,
        });
        return;
      }
      let interim = "";
      // Walk every result; finals get pushed onto finalsRef, interims get
      // concatenated into a running string that's displayed in the input.
      for (let i = 0; i < ev.results.length; i++) {
        const result = ev.results[i];
        const alt = result[0];
        if (!alt) continue;
        if (result.isFinal) {
          const t = alt.transcript.trim();
          if (t) finalsRef.current.push(t);
        } else {
          interim += alt.transcript;
        }
      }
      // #68 — the composer must show the COMPLETE evolving transcript, not
      // just the current recognition session's slice. continuous=false makes
      // Web Speech end+restart on every sub-1.5s pause; each restart begins a
      // fresh result list, and finals can also be skipped here during the
      // TTS/text drop window above even though the audio (→ Sarvam) still has
      // them. The authoritative running text the grace timer will submit is
      // `pendingUtteranceRef` (earlier graced segments of THIS utterance) +
      // the current session's NOT-YET-DRAINED finals + the live interim.
      //
      // Critical: finals already moved into pendingUtteranceRef on `onend`
      // stay in finalsRef until submit (so a late isFinal isn't lost), and
      // `finalsConsumedRef` is the cursor of how many were drained. Joining
      // ALL of finalsRef would double-count those (segment shown twice). So
      // we display pending + finalsRef.slice(consumed) + interim — the exact
      // union with no duplication and no lag behind what was captured/sent.
      const priorSegments = pendingUtteranceRef.current.trim();
      const freshFinals = finalsRef.current
        .slice(finalsConsumedRef.current)
        .join(" ")
        .trim();
      const running = [priorSegments, freshFinals, interim]
        .map((s) => s.trim())
        .filter(Boolean)
        .join(" ")
        .trim();
      onInterimRef.current(running);
    };

    rec.onerror = (ev: SpeechRecognitionErrorEventLike) => {
      const code = ev.error;
      // `no-speech` and `aborted` are routine in continuous-restart mode —
      // no audio detected in a window, or we deliberately stopped. Silent
      // restart via onend.
      if (code === "no-speech" || code === "aborted") return;
      if (code === "not-allowed" || code === "service-not-allowed") {
        wantRunningRef.current = false;
        // FIX 2 (HIGH) — Terminal-error mic leak. Without teardownAudio()
        // here the MediaRecorder + MediaStream stay open even though
        // recognition has shut down, so the browser's red-dot mic
        // indicator stays lit and the OS thinks we're still recording.
        teardownAudio();
        onErrorRef.current(
          "Mic permission denied. Click the lock icon in your browser's URL bar to enable the microphone.",
        );
        return;
      }
      if (code === "audio-capture") {
        wantRunningRef.current = false;
        // FIX 2 (HIGH) — see above.
        teardownAudio();
        onErrorRef.current("No microphone detected. Check your audio device and try again.");
        return;
      }
      if (code === "network") {
        // Transient — let onend's restart loop pick it up with backoff.
        errorBackoffRef.current = Math.min(errorBackoffRef.current + 500, 3000);
        return;
      }
      onErrorRef.current(`Voice error: ${code}${ev.message ? ` (${ev.message})` : ""}`);
    };

    rec.onend = () => {
      onListeningRef.current(false);
      // KI-217 — drain only the NEW finals (everything past the consumed
      // cursor). DO NOT reset finalsRef here: a late-delivered isFinal
      // chunk arriving after onend would otherwise be wiped before the
      // next onend cycle can pick it up. finalsRef is reset on actual
      // utterance submit (grace-timer flush) and on user start/stop.
      const newFinals = finalsRef.current.slice(finalsConsumedRef.current);
      const webSpeechText = newFinals.join(" ").trim();
      finalsConsumedRef.current = finalsRef.current.length;

      // KI-168 PHASE 2 — race guard: if a typed-text turn is in flight,
      // drop both transcripts on the floor (text wins). Don't start a
      // Sarvam fetch we'd be throwing away.
      const textRacing = isTextRequestPendingRef.current;

      // FIX 7 (HIGH) — Silent onend early-return. Chrome's "no-speech"
      // restart loop fires onend every ~5s with no content. Without this
      // guard, every silent onend re-arms the 1500ms grace timer and the
      // grace window extends forever — even when there's nothing pending
      // to submit. Skip the grace-timer reset when:
      //   - no new Web Speech text in this cycle, AND
      //   - no audio chunks captured this cycle (chunksRef holds the
      //     undrained chunks that will become drainedThisEnd below), AND
      //   - no previously pending utterance text.
      // We still call scheduleRestart() so the mic comes back online.
      const hasNewChunksThisEnd = recorderActiveRef.current && chunksRef.current.length > 0;
      if (!webSpeechText && !hasNewChunksThisEnd && pendingUtteranceRef.current === "") {
        console.debug("[useStreamingVoice] KI-222 silent onend — skipping grace reset");
        // Inline the restart-only path here so we don't need to refactor
        // the scheduleRestart closure below it.
        if (wantRunningRef.current && !isTextRequestPendingRef.current) {
          const backoff = errorBackoffRef.current;
          errorBackoffRef.current = 0;
          clearRestartTimer();
          restartTimerRef.current = setTimeout(() => {
            restartTimerRef.current = null;
            if (wantRunningRef.current) safeStart();
          }, Math.max(50, backoff));
        } else if (wantRunningRef.current && isTextRequestPendingRef.current) {
          clearRestartTimer();
          restartTimerRef.current = setTimeout(() => {
            restartTimerRef.current = null;
            if (wantRunningRef.current && !isTextRequestPendingRef.current) safeStart();
          }, 250);
        }
        return;
      }

      const scheduleRestart = () => {
        if (wantRunningRef.current && !isTextRequestPendingRef.current) {
          const backoff = errorBackoffRef.current;
          errorBackoffRef.current = 0;
          clearRestartTimer();
          restartTimerRef.current = setTimeout(() => {
            restartTimerRef.current = null;
            if (wantRunningRef.current) safeStart();
          }, Math.max(50, backoff));
        } else if (wantRunningRef.current && isTextRequestPendingRef.current) {
          // Text turn in flight — retry shortly so mic resumes the moment
          // the text turn lands.
          clearRestartTimer();
          restartTimerRef.current = setTimeout(() => {
            restartTimerRef.current = null;
            if (wantRunningRef.current && !isTextRequestPendingRef.current) safeStart();
          }, 250);
        }
      };

      // Pull the chunks we've accumulated so far so the recorder can keep
      // capturing the next utterance without us re-running getUserMedia.
      const drainChunks = (): Blob[] => {
        const drained = chunksRef.current;
        chunksRef.current = [];
        return drained;
      };

      // KI-202 (2026-05-15) — utterance batching. Web Speech's onend fires
      // after ~1.5s of silence, so a natural mid-sentence pause splits one
      // utterance into two onend events and the user's sentence gets
      // submitted in halves ("First word getting cut off. Cutoff is the
      // biggest issue. Auto-submitting without capturing the first half
      // or the second half"). Instead of submitting immediately, we
      // append THIS onend's text + audio chunks to pendingUtterance*Ref
      // buffers, then start (or reset) a UTTERANCE_GRACE_MS timer. If
      // recognition restarts (auto-restart picks up the next word burst)
      // within the grace window, the next onend appends more content +
      // resets the timer. Only after a FULL UTTERANCE_GRACE_MS of true
      // silence does the timer fire and submit the accumulated buffer.
      //
      // Pauses < 1.5s merge into one turn (intended fix).
      // Pauses > 1.5s split (intended — that IS a new turn).

      // Drain the CURRENT onend's chunks now so the recorder keeps capturing
      // the next word burst without contamination across pending utterances.
      const drainedThisEnd = recorderActiveRef.current ? drainChunks() : [];
      if (webSpeechText) {
        pendingUtteranceRef.current = pendingUtteranceRef.current
          ? `${pendingUtteranceRef.current} ${webSpeechText}`
          : webSpeechText;
      }
      if (drainedThisEnd.length > 0) {
        pendingChunksRef.current.push(...drainedThisEnd);
      }
      console.debug("[useStreamingVoice] KI-202 onend appended to pending utterance", {
        thisTextLen: webSpeechText.length,
        thisChunkCount: drainedThisEnd.length,
        pendingTextLen: pendingUtteranceRef.current.length,
        pendingChunkCount: pendingChunksRef.current.length,
        textRacing,
      });

      // Mic restart happens immediately regardless of grace window — we
      // WANT recognition to come back online so it can pick up the next
      // word burst within the grace window and append to pending.
      scheduleRestart();

      // KI-210 (2026-05-15) — DO NOT drop pending utterance when text is
      // racing. Previously we cleared pendingUtteranceRef + pendingChunksRef
      // here, which silently lost any voice the user spoke during the bot's
      // text-submit/TTS-thinking gap. The downstream wait-and-retry inside
      // `submitPendingUtterance` (timer fire) + the post-await wait inside
      // the Sarvam fire-and-forget now hold the buffer until the text turn
      // clears, then submit. We leave `textRacing` as a debug breadcrumb in
      // the log above and continue accumulating.

      // KI-210 — refactor the grace-timer body into a named async function
      // so it can re-schedule itself (wait-and-retry) when text is in flight
      // instead of dropping the utterance. Capped at 30s total wait so a
      // stuck text request can't leak the timer forever; if the cap fires
      // we proceed with submission anyway (better to submit than drop).
      const SUBMIT_WAIT_CAP_MS = 30000;
      const submitStartTsRef = { ts: 0 };
      const submitPendingUtterance = async () => {
        pendingSubmitTimerRef.current = null;

        // KI-210 — if text is still in flight when the grace window fires,
        // wait instead of dropping. Re-schedule a 300ms retry until either
        // text clears or we hit the 30s cap.
        if (isTextRequestPendingRef.current) {
          if (submitStartTsRef.ts === 0) submitStartTsRef.ts = Date.now();
          if (Date.now() - submitStartTsRef.ts > SUBMIT_WAIT_CAP_MS) {
            console.debug("[useStreamingVoice] KI-210 timer wait cap reached; submitting anyway");
            // fall through and submit
          } else {
            console.debug("[useStreamingVoice] KI-210 timer fired but text in flight; waiting 300ms");
            pendingSubmitTimerRef.current = setTimeout(() => {
              void submitPendingUtterance();
            }, 300);
            return;
          }
        }

        const accumulatedText = pendingUtteranceRef.current.trim();
        const accumulatedChunks = pendingChunksRef.current;
        pendingUtteranceRef.current = "";
        pendingChunksRef.current = [];
        // KI-217 — the utterance is now being submitted; safe to wipe
        // finalsRef + reset the consumed cursor. Any late results that
        // arrive after this point are for a NEW utterance.
        finalsRef.current = [];
        finalsConsumedRef.current = 0;
        console.debug("[useStreamingVoice] KI-202 grace window elapsed — submitting", {
          textLen: accumulatedText.length,
          chunkCount: accumulatedChunks.length,
        });

        // No-recorder path: just submit Web Speech text.
        if (!recorderActiveRef.current || accumulatedChunks.length === 0) {
          if (accumulatedText) {
            onFinalRef.current(accumulatedText);
          }
          return;
        }

        // Sarvam path. Fire-and-forget so we don't block recognition.
        void (async () => {
          // Snapshot user-visible interim so the input area doesn't go blank
          // while Sarvam is in flight. The page-side input still shows the
          // Web Speech transcript; we'll overwrite it via onFinalTranscript
          // once Sarvam returns.
          if (accumulatedText) onInterimRef.current(accumulatedText);

          // We need to stop the recorder to get the final dataavailable
          // chunk for the LAST burst (anything mid-recording when the grace
          // window opened is in chunksRef, which we now flush into our
          // accumulated set before posting).
          await stopRecorder();
          const tailChunks = drainChunks();
          const allChunks = [...accumulatedChunks, ...tailChunks];
          const totalSize = allChunks.reduce((n, b) => n + b.size, 0);
          console.debug("[useStreamingVoice] KI-202 batched submit", {
            webSpeechLen: accumulatedText.length,
            chunkCount: allChunks.length,
            blobBytes: totalSize,
          });

          // Re-arm audio capture for the next utterance (don't block on it).
          teardownAudio();
          if (wantRunningRef.current) {
            void ensureAudioCapture();
          }

          // Skip submit when there's effectively no audio or no Web Speech
          // text. ~3 KB is the empirical noise floor used by the PTT path's
          // KI-134 silence guard.
          const MIN_BLOB_BYTES = 3000;
          if (!accumulatedText && totalSize < MIN_BLOB_BYTES) {
            console.debug("[useStreamingVoice] KI-202 skipping submit — no text and tiny blob");
            return;
          }

          // KI-210 — wait-and-retry instead of dropping. If a text turn
          // started during the await above, hold the utterance until it
          // clears (capped at 30s) instead of throwing it away.
          await waitForTextClear();

          let authoritativeText = accumulatedText;
          if (allChunks.length > 0 && totalSize >= MIN_BLOB_BYTES) {
            const blob = new Blob(allChunks, { type: recorderMimeRef.current || "audio/webm" });
            // KI-226 (2026-05-15) — V5.4. Wrap the Sarvam POST in an
            // exponential-backoff retry (1s/2s/4s, max 3 attempts). The
            // accumulatedText (Web Speech fallback) and accumulated chunks
            // are already captured locally, so retries don't lose the
            // partial transcript. Each attempt enforces its own timeout
            // via the controller signal passed in by retryPostTranscribe.
            //
            // KI-302 (2026-05-18) — full-transcript fix. The backend now
            // SPLITS audio over Sarvam's ~30s REST limit into multiple
            // chunks and transcribes them sequentially (one Sarvam round
            // trip per ~25s of speech) so a long utterance is no longer
            // silently truncated to its first 30s. A fixed 8s client
            // timeout would abort that legitimately-longer multi-chunk
            // call mid-flight and force a fall back to the (also often
            // truncated) Web Speech text — re-introducing the very bug we
            // are fixing. Scale the per-attempt timeout with the audio
            // size: an 8s floor for short clips plus a generous budget per
            // estimated 25s chunk (browser webm/opus ≈ 4 KB/s ⇒ ~100 KB
            // per 25s chunk; allow ~10s of Sarvam latency per chunk).
            const APPROX_BYTES_PER_CHUNK = 100_000; // ~25s of webm/opus
            const estChunks = Math.max(
              1,
              Math.ceil(blob.size / APPROX_BYTES_PER_CHUNK),
            );
            const attemptTimeoutMs = Math.min(
              120_000, // hard ceiling — never wait > 2 min on one attempt
              8_000 + estChunks * 12_000,
            );
            console.debug("[useStreamingVoice] POST /api/transcribe", {
              bytes: blob.size,
              mime: blob.type,
              lang: language,
              estChunks,
              attemptTimeoutMs,
            });
            const sarvam = await retryPostTranscribe(async (signal) => {
              // Race per-attempt timeout against the retry signal so a
              // hung connection still surfaces as an attempt failure (and
              // triggers the next backoff step) rather than blocking
              // forever. signal aborts when the OUTER retry loop is killed.
              const timeoutCtl = new AbortController();
              const timer = setTimeout(() => timeoutCtl.abort(), attemptTimeoutMs);
              const onOuterAbort = () => timeoutCtl.abort();
              signal.addEventListener("abort", onOuterAbort);
              try {
                return await postTranscribe(blob, language, timeoutCtl.signal);
              } finally {
                clearTimeout(timer);
                signal.removeEventListener("abort", onOuterAbort);
              }
            });
            if (sarvam) {
              const sarvamText = (sarvam.text || "").trim();
              if (sarvamText) {
                authoritativeText = sarvamText;
                console.debug("[useStreamingVoice] Sarvam OK", {
                  latency_ms: sarvam.latency_ms,
                  webSpeechLen: accumulatedText.length,
                  sarvamLen: sarvamText.length,
                });
              } else {
                console.debug("[useStreamingVoice] Sarvam returned empty; using Web Speech fallback");
              }
            } else {
              console.debug("[useStreamingVoice] Sarvam failed after retries; using Web Speech fallback");
              try { onVoiceErrorRef.current("transcribe_failed"); } catch { /* ignore */ }
            }
          }

          // KI-210 — final wait-and-retry after Sarvam round-trip. Don't
          // drop the now-authoritative transcript if text raced us during
          // the network call.
          if (authoritativeText) {
            await waitForTextClear();
            onFinalRef.current(authoritativeText);
          }
        })();
      };

      // (Re)start the grace-window timer. Every onend resets it, so as long
      // as the user keeps starting new word bursts within 1.5s of the last
      // silence, the timer never fires and the utterance keeps growing.
      if (pendingSubmitTimerRef.current !== null) {
        clearTimeout(pendingSubmitTimerRef.current);
      }
      submitStartTsRef.ts = 0;
      pendingSubmitTimerRef.current = setTimeout(() => {
        void submitPendingUtterance();
      }, UTTERANCE_GRACE_MS);
    };

    return rec;
  }, [language, isTextRequestPendingRef, clearRestartTimer, safeStart, stopRecorder, teardownAudio, ensureAudioCapture, waitForTextClear]);

  const start = useCallback(() => {
    if (!isSupported) {
      onErrorRef.current(
        "Live voice not supported in this browser. Use push-to-talk or type instead.",
      );
      return;
    }
    wantRunningRef.current = true;
    if (!recognitionRef.current) {
      recognitionRef.current = buildRecognition();
    }
    finalsRef.current = [];
    finalsConsumedRef.current = 0;
    // W1 (2026-05-15) — gate the SR start on a successful `getUserMedia`.
    // Previously this was `void ensureAudioCapture(); safeStart();` which
    // raced the two in parallel: on a Chromium / iOS Safari permission
    // denial, the recognition started, the pill flipped to "Voice on —
    // just speak", but the mic was dead (zero audio, no banner, no log).
    // By awaiting the capture result and skipping safeStart() on a hard
    // denial, the pill-flip (driven by page.tsx's
    // `onVoiceError("mic_permission_denied")` handler) lands BEFORE
    // recognition kicks off. The ensureAudioCapture catch already sets
    // wantRunningRef=false and emits onVoiceError on its way out.
    void (async () => {
      const ok = await ensureAudioCapture();
      // Hard denial path: capture failed AND ensureAudioCapture reset
      // wantRunningRef. Skip recognition.start — page.tsx will flip
      // `enabled` to false on the banner code, which triggers stop().
      if (!ok && !wantRunningRef.current) return;
      // Soft-degraded path: capture failed but wantRunning is still true
      // (e.g. MediaRecorder mime mismatch on a niche browser). Fall back
      // to Web-Speech-only — onend's restart loop handles the fallback.
      safeStart();
    })();
  }, [isSupported, buildRecognition, safeStart, ensureAudioCapture]);

  const stop = useCallback(() => {
    wantRunningRef.current = false;
    clearRestartTimer();
    const rec = recognitionRef.current;
    if (rec) {
      try {
        rec.abort();
      } catch {
        // ignore
      }
      // FIX 1 (HIGH) — Unbind handlers and null the ref so any late
      // onresult/onend events delivered by Chrome AFTER abort() can't
      // mutate finalsRef / pendingUtteranceRef / pendingChunksRef. Without
      // this, a stale recognition instance fires onend ~50-300ms after
      // abort() and re-arms the grace timer on a torn-down session.
      try {
        rec.onresult = null;
        rec.onerror = null;
        rec.onend = null;
        rec.onstart = null;
      } catch {
        // ignore — some browsers reject null assignment on EventTarget props
      }
    }
    recognitionRef.current = null;
    teardownAudio();
    finalsRef.current = [];
    finalsConsumedRef.current = 0;
    // FIX 6 (HIGH) — Mid-utterance toggle-off flush. If the user finishes
    // a complete sentence and toggles voice off within the 1.5s grace
    // window, submit the pending utterance instead of silently dropping
    // it. Only flush when no text request is racing; otherwise dropping
    // is safer than colliding with an in-flight turn.
    const finalPending = pendingUtteranceRef.current.trim();
    if (finalPending && !isTextRequestPendingRef.current) {
      console.debug("[useStreamingVoice] KI-222 flushing pending on stop", { len: finalPending.length });
      try {
        onFinalRef.current(finalPending);
      } catch {
        // never let a callback throw break stop()
      }
    }
    // KI-202 — drop any pending utterance so toggling voice off mid-grace
    // doesn't auto-submit a stale half-sentence next time voice comes on.
    if (pendingSubmitTimerRef.current !== null) {
      clearTimeout(pendingSubmitTimerRef.current);
      pendingSubmitTimerRef.current = null;
    }
    pendingUtteranceRef.current = "";
    pendingChunksRef.current = [];
    onListeningRef.current(false);
  }, [clearRestartTimer, teardownAudio, isTextRequestPendingRef]);

  // Drive start/stop from the `enabled` prop so the hook is fire-and-forget
  // for the caller (mirrors useLiveConversation's `live` state semantics).
  useEffect(() => {
    if (enabled) {
      start();
    } else {
      stop();
    }
    return () => {
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled]);

  // #53 / #54 — warm-stream lifecycle. The warm stream's lifecycle is
  // DELIBERATELY decoupled from `enabled` (which page.tsx flips OFF for pure
  // push-to-talk — see page.tsx:986 `live.setLive(false)` inside
  // startRecording). The user opting into voice latches the warm stream ON
  // for the rest of the hook's mounted lifetime so:
  //   (a) the pre-roll ring is ALWAYS filling whenever the user might press
  //       SPACE — including in pure-PTT mode when `enabled` is false — so the
  //       first word spoken in page.tsx's cold-start gap survives (#53);
  //   (b) a persistent open audio device keeps the OS mic hot so page.tsx's
  //       own per-press getUserMedia resolves in ~10-50ms instead of
  //       cold-starting (200-700ms), removing the felt start delay (#54).
  // Armed on the rising edge of `enabled` (the only voice-opt-in signal the
  // hook receives) and kept armed thereafter; fully released on unmount.
  useEffect(() => {
    if (!isSupported) return;
    if (enabled) {
      voiceEverEnabledRef.current = true;
    }
    if (enabled || voiceEverEnabledRef.current) {
      void armWarmStream();
    }
    // No teardown on `enabled` going false — the warm stream must survive
    // the Live↔PTT toggle. Final release happens in the unmount cleanup.
  }, [enabled, isSupported, armWarmStream]);

  // #53 / #54 — warm-stream health watchdog. The OS can silently drop a
  // long-lived capture (device sleep, USB mic unplug, OS audio interruption,
  // tab backgrounding on some browsers) WITHOUT firing recorder.onerror. If
  // that happens the pre-roll ring goes stale and the very bug we fixed
  // returns. Every 4s, while voice has been opted into and we're not in the
  // middle of a PTT capture, re-assert the warm stream (armWarmStream is a
  // no-op when the recorder is healthily "recording").
  useEffect(() => {
    if (!isSupported) return;
    const tick = setInterval(() => {
      if (!voiceEverEnabledRef.current) return;
      if (pttEngagedRef.current) return; // don't disturb an in-flight capture
      const rec = warmRecorderRef.current;
      if (!rec || rec.state !== "recording" || !warmStreamRef.current) {
        void armWarmStream();
      }
    }, 4000);
    return () => clearInterval(tick);
  }, [isSupported, armWarmStream]);

  // KI-173 (2026-05-15) — heartbeat watchdog. Browser SpeechRecognition
  // occasionally enters a stopped state without `onend` firing (certain
  // network errors, transient OS audio interruptions, tab visibility
  // edge cases). The auto-restart in `onend` never gets the chance to
  // run, and the mic stays silently dead until the user toggles voice
  // off+on. Every 4s, if we WANT to be listening (enabled + wantRunningRef)
  // and no text turn is racing and no restart is already scheduled, call
  // `safeStart()` unconditionally — InvalidStateError is swallowed if
  // recognition is already running, otherwise this revives the dead state.
  useEffect(() => {
    if (!enabled || !isSupported) return;
    const tick = setInterval(() => {
      if (
        wantRunningRef.current
        && !isTextRequestPendingRef.current
        && !isTtsPlayingRef.current  // KI-188 — block revival during TTS playback
        && restartTimerRef.current === null
      ) {
        safeStart();
      }
    }, 4000);
    return () => clearInterval(tick);
  }, [enabled, isSupported, isTextRequestPendingRef, safeStart]);

  // KI-188 (2026-05-15) — TTS playback gate. Browser Web Speech API has
  // its own internal mic pipeline that bypasses our getUserMedia AEC
  // constraints (KI-185), so SpeechRecognition transcribes the bot's TTS
  // audio bleeding from speakers as if it were user input. The visible
  // echo "perfect days to get started Rohit" was echo of bot's TTS
  // "perfect age to get started, Rohit". The only reliable JS-level fix
  // is to ABORT recognition while ANY <audio> element in the DOM is
  // playing, then revive via the heartbeat (KI-173) the moment all
  // audio ends.
  //
  // Trade-off: live "barge-in by just speaking" is disabled DURING TTS.
  // Push-to-talk still works (it uses MediaRecorder, not SpeechRecognition).
  useEffect(() => {
    if (!enabled || !isSupported) return;
    if (typeof document === "undefined") return;

    // KI-189 (2026-05-15) — barge-in VAD state. The AnalyserNode + AudioContext
    // are lazily created on first TTS-playback and reused for subsequent
    // playbacks to avoid repeated AudioContext spin-up cost (Chrome warns
    // when >6 contexts coexist).
    let audioCtx: AudioContext | null = null;
    let analyser: AnalyserNode | null = null;
    let sourceNode: MediaStreamAudioSourceNode | null = null;
    let attachedStream: MediaStream | null = null;
    let rmsBuf: Float32Array<ArrayBuffer> | null = null;
    let sustainedFrames = 0;
    let rafId: number | null = null;

    // KI-190 — per-<audio> bot-RMS analysers for adaptive threshold.
    // Each watched audio element gets its own MediaElementAudioSourceNode +
    // AnalyserNode so we can read the bot's instantaneous playback level
    // during a barge-in tick. Map keyed by the audio element.
    const botAnalysers = new Map<HTMLAudioElement, {
      source: MediaElementAudioSourceNode;
      analyser: AnalyserNode;
      buf: Float32Array<ArrayBuffer>;
    }>();
    // Track which <audio> elements we've dimmed so we can restore on cleanup.
    const duckedAudios = new Set<HTMLAudioElement>();
    // KI-195 — user-speech RMS tracker + per-element calibrated volume.
    // userSpeechRms is the rolling peak of mic RMS observed while the user
    // is actively speaking (recorder active, not TTS). It seeds the bot
    // volume target. Calibrated volumes per element survive across turns
    // so we don't have to re-learn after every reply.
    let userSpeechRms = USER_SPEECH_RMS_INITIAL;
    const calibratedVolumes = new Map<HTMLAudioElement, number>();
    let userRmsRafId: number | null = null;
    let volumeCalibIntervalId: ReturnType<typeof setInterval> | null = null;
    // FIX 5 (HIGH) — wall-clock decay interval. The rAF-driven userRmsTick
    // is gated on `!isTtsPlaying`, so during bot TTS playback there is NO
    // decay of userSpeechRms — a shout right before the bot starts speaking
    // would pin userSpeechRms at 0.4 for the entire bot turn. This setInterval
    // runs unconditionally while `enabled` is true, so the rolling peak
    // decays toward USER_SPEECH_RMS_INITIAL on a wall-clock schedule that's
    // independent of the rAF gate.
    let userRmsWallClockIntervalId: ReturnType<typeof setInterval> | null = null;

    const sampleUserRms = (): number => {
      if (!analyser || !rmsBuf) return 0;
      try {
        analyser.getFloatTimeDomainData(rmsBuf);
      } catch { return 0; }
      let sumSq = 0;
      for (let i = 0; i < rmsBuf.length; i++) {
        const v = rmsBuf[i];
        sumSq += v * v;
      }
      return Math.sqrt(sumSq / rmsBuf.length);
    };

    const userRmsTick = () => {
      // Only learn while user is potentially speaking — recorder active,
      // no TTS, voice mode on.
      if (
        !wantRunningRef.current
        || isTtsPlayingRef.current
        || !recorderActiveRef.current
      ) {
        userRmsRafId = null;
        return;
      }
      if (!analyser || !rmsBuf) {
        userRmsRafId = null;
        return;
      }
      const rms = sampleUserRms();
      // Only count as "user speaking" when above detection threshold.
      // Then update userSpeechRms via slow EMA on peak so a single shout
      // doesn't permanently raise the baseline.
      if (rms > USER_SPEECH_DETECTION_THRESHOLD) {
        userSpeechRms = Math.max(userSpeechRms * 0.95, rms);
        // FIX 5 (HIGH) — clamp to ceiling so a single shout cannot pin
        // userSpeechRms permanently high and break subsequent barge-in.
        userSpeechRms = Math.min(userSpeechRms, USER_SPEECH_RMS_CEILING);
      }
      userRmsRafId = requestAnimationFrame(userRmsTick);
    };

    const startUserRmsLoop = () => {
      if (userRmsRafId !== null) return;
      // Reuse the VAD analyser. startBargeInLoop sets it up; if it doesn't
      // exist yet, the loop will exit on first tick (analyser null) and
      // restart on the next state transition.
      userRmsRafId = requestAnimationFrame(userRmsTick);
    };

    const stopUserRmsLoop = () => {
      if (userRmsRafId !== null) {
        cancelAnimationFrame(userRmsRafId);
        userRmsRafId = null;
      }
    };

    // FIX 5 (HIGH) — wall-clock decay. Runs every USER_SPEECH_RMS_WALL_CLOCK_DECAY_MS
    // regardless of TTS state so the rolling peak can't get permanently
    // pinned high during long TTS turns. Floors at USER_SPEECH_RMS_INITIAL
    // so we don't decay below the calibrated baseline.
    const startUserRmsWallClockDecay = () => {
      if (userRmsWallClockIntervalId !== null) return;
      userRmsWallClockIntervalId = setInterval(() => {
        userSpeechRms = Math.max(
          USER_SPEECH_RMS_INITIAL,
          userSpeechRms * USER_SPEECH_RMS_WALL_CLOCK_DECAY_FACTOR,
        );
      }, USER_SPEECH_RMS_WALL_CLOCK_DECAY_MS);
    };

    const stopUserRmsWallClockDecay = () => {
      if (userRmsWallClockIntervalId !== null) {
        clearInterval(userRmsWallClockIntervalId);
        userRmsWallClockIntervalId = null;
      }
    };

    // KI-195 — volume calibration tick. Runs during TTS. Samples bot RMS
    // at the mic via botAnalysers. If bot is louder than target relative
    // to userSpeechRms, duck el.volume by 20% per tick down to the floor.
    const calibrateBotVolume = () => {
      if (!isTtsPlayingRef.current) {
        if (volumeCalibIntervalId !== null) {
          clearInterval(volumeCalibIntervalId);
          volumeCalibIntervalId = null;
        }
        return;
      }
      const target = userSpeechRms * VOLUME_CALIB_TARGET_RATIO;
      const botRms = computeBotRms();
      if (botRms > target) {
        ttsAudioElementsRef.current.forEach((el) => {
          if (el.paused || el.ended) return;
          const cur = el.volume;
          const next = Math.max(VOLUME_CALIB_FLOOR, cur * VOLUME_CALIB_DUCK_FACTOR);
          if (next < cur - 0.001) {
            try {
              el.volume = next;
              calibratedVolumes.set(el, next);
            } catch { /* ignore */ }
          }
        });
      }
    };

    const startVolumeCalibration = () => {
      if (volumeCalibIntervalId !== null) return;
      volumeCalibIntervalId = setInterval(calibrateBotVolume, VOLUME_CALIB_TICK_MS);
    };

    const stopVolumeCalibration = () => {
      if (volumeCalibIntervalId !== null) {
        clearInterval(volumeCalibIntervalId);
        volumeCalibIntervalId = null;
      }
    };

    const stopBargeInLoop = () => {
      if (rafId !== null) {
        cancelAnimationFrame(rafId);
        rafId = null;
      }
      sustainedFrames = 0;
    };

    const teardownAnalyser = () => {
      stopBargeInLoop();
      try { sourceNode?.disconnect(); } catch { /* ignore */ }
      try { analyser?.disconnect(); } catch { /* ignore */ }
      sourceNode = null;
      analyser = null;
      attachedStream = null;
      rmsBuf = null;
      // KI-190 — tear down bot analysers + audio context.
      botAnalysers.forEach((entry) => {
        try { entry.source.disconnect(); } catch { /* ignore */ }
        try { entry.analyser.disconnect(); } catch { /* ignore */ }
      });
      botAnalysers.clear();
      if (audioCtx) {
        const ctx = audioCtx;
        audioCtx = null;
        try { void ctx.close(); } catch { /* ignore */ }
      }
    };

    // KI-190 — ensure an AudioContext exists for bot analyser attachment.
    // Reuses the same instance the VAD path uses.
    const ensureAudioCtx = (): AudioContext | null => {
      if (audioCtx && audioCtx.state !== "closed") return audioCtx;
      try {
        const Ctor = (window.AudioContext
          || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
        if (!Ctor) return null;
        audioCtx = new Ctor();
        return audioCtx;
      } catch {
        return null;
      }
    };

    // KI-190 — attach an AnalyserNode to a bot <audio> element. Routes the
    // element's audio through the AudioContext (source → analyser →
    // destination so it stays audible). createMediaElementSource throws if
    // called twice on the same element, so we swallow and skip.
    const attachBotAnalyser = (el: HTMLAudioElement) => {
      if (botAnalysers.has(el)) return;
      const ctx = ensureAudioCtx();
      if (!ctx) return;
      try {
        const source = ctx.createMediaElementSource(el);
        const an = ctx.createAnalyser();
        an.fftSize = 1024;
        an.smoothingTimeConstant = 0.4;
        source.connect(an);
        an.connect(ctx.destination);
        const buf = new Float32Array(new ArrayBuffer(an.fftSize * 4));
        botAnalysers.set(el, { source, analyser: an, buf });
      } catch {
        // already routed through Web Audio elsewhere, or autoplay policy
        // blocked the context — bargeInTick will simply use the base
        // threshold for this turn.
      }
    };

    // KI-190 — current peak bot RMS across all playing <audio> elements.
    // We take the max (not sum) because only one TTS plays at a time in
    // practice and max behaves more sensibly if a stale paused element is
    // still in the map.
    const computeBotRms = (): number => {
      let peak = 0;
      botAnalysers.forEach(({ analyser: an, buf }, el) => {
        if (el.paused || el.ended) return; // ignore idle elements
        an.getFloatTimeDomainData(buf);
        let sumSq = 0;
        for (let i = 0; i < buf.length; i++) {
          const v = buf[i];
          sumSq += v * v;
        }
        // The MediaElementSource is post-volume, so this already reflects
        // the ducked KI-191 0.6 volume — we get the actual audible level.
        const rms = Math.sqrt(sumSq / buf.length);
        if (rms > peak) peak = rms;
      });
      return peak;
    };

    const triggerBargeIn = (rms: number) => {
      console.debug("[useStreamingVoice] KI-189 barge-in detected", {
        rms: rms.toFixed(4),
        frames: sustainedFrames,
        threshold: BARGE_IN_RMS_THRESHOLD,
      });
      // KI-227 (2026-05-15) — V6.7. Flush any pending utterance that
      // accumulated during the bot's TTS window BEFORE the barge-in fires.
      // The grace-window timer (UTTERANCE_GRACE_MS) holds the user's
      // utterance for up to 1.5s waiting for more bursts — if the user
      // barges in over the bot before that timer fires, the pending text
      // would otherwise sit silently until the timer expires. Deliver it
      // now so page.tsx submits the user's actual question instead of
      // letting it die on the floor while a fresh recognition starts.
      try {
        const flushText = pendingUtteranceRef.current.trim();
        if (flushText && !isTextRequestPendingRef.current) {
          console.debug("[useStreamingVoice] V6.7 flushing pending utterance on barge-in", {
            len: flushText.length,
          });
          pendingUtteranceRef.current = "";
          pendingChunksRef.current = [];
          finalsRef.current = [];
          finalsConsumedRef.current = 0;
          if (pendingSubmitTimerRef.current !== null) {
            clearTimeout(pendingSubmitTimerRef.current);
            pendingSubmitTimerRef.current = null;
          }
          onFinalRef.current(flushText);
        }
      } catch (err) {
        // Never let the flush throw break the barge-in pipeline.
        console.debug("[useStreamingVoice] V6.7 pending flush threw", err);
      }
      // FIX 3 (HIGH) — flip the barge-in signal so the caller (page.tsx)
      // can abort the in-flight /api/chat request that's still assembling
      // more TTS audio. Without this, pausing the currently-mounted
      // <audio> elements only stops THIS chunk; the next TTS chunk that
      // arrives mounts a new <audio>, fires play, and the bot resumes
      // talking after the user has already interrupted.
      bargeInRequestedRef.current = true;
      // Pause + reset every TTS <audio>; the MutationObserver's pause
      // listener will set isTtsPlayingRef = false and call safeStart().
      ttsAudioElementsRef.current.forEach((el) => {
        try {
          el.pause();
          el.currentTime = 0;
        } catch {
          // ignore
        }
      });
      stopBargeInLoop();
    };

    const bargeInTick = () => {
      // Re-check gating each frame — if state changed mid-loop, exit cleanly.
      if (
        !isTtsPlayingRef.current
        || !wantRunningRef.current
        || isTextRequestPendingRef.current
      ) {
        stopBargeInLoop();
        return;
      }
      if (!analyser || !rmsBuf) {
        stopBargeInLoop();
        return;
      }
      analyser.getFloatTimeDomainData(rmsBuf);
      let sumSq = 0;
      // FIX 4 (HIGH) — compute zero-crossing rate alongside RMS. Speech
      // ZCR sits in a specific band; keyboard typing has very high ZCR
      // (transients), HVAC / room rumble has very low ZCR (DC-like).
      // Rejecting frames outside the speech band cuts false-positive
      // barge-ins from typing and ambient noise.
      let zeroCrossings = 0;
      let prevSign = rmsBuf[0] >= 0 ? 1 : -1;
      for (let i = 0; i < rmsBuf.length; i++) {
        const v = rmsBuf[i];
        sumSq += v * v;
        if (i > 0) {
          const sign = v >= 0 ? 1 : -1;
          if (sign !== prevSign) zeroCrossings += 1;
          prevSign = sign;
        }
      }
      const rms = Math.sqrt(sumSq / rmsBuf.length);
      // KI-228 (2026-05-15) — V6.8. Feed every frame into the adaptive
      // noise-floor estimator. It only updates the EMA when the frame is
      // below the CURRENT threshold (i.e. the frame looks like silence),
      // so speech bursts can't pollute the room baseline.
      noiseFloorRef.current.feed(rms);
      const noiseAdaptiveThreshold = noiseFloorRef.current.currentThreshold();
      // KI-190 — adaptive threshold: bot_rms * 2 + 0.005, floored at the
      // base BARGE_IN_RMS_THRESHOLD so we never set it absurdly low.
      // KI-228 (2026-05-15) — V6.8. ALSO floor at the noise-floor adaptive
      // threshold so a noisy room (HVAC, café) doesn't cause false-positive
      // barge-ins on the original static 0.008 threshold.
      const botRms = computeBotRms();
      // KI-285 (2026-05-16) — defence-in-depth for the post-grace window.
      // computeBotRms() returns 0 not only for the first frames of playback
      // but PERMANENTLY whenever createMediaElementSource() threw (Safari,
      // element already Web-Audio-routed, autoplay-suspended ctx). In that
      // state the `botRms * MULT + BASE` term collapses to 0.002 and the
      // whole Math.max() falls back to the bare 0.008 static floor — which
      // is BELOW documented speaker echo bleed (~0.02 RMS, KI-189/190). The
      // bot's own voice then clears the gate and self-triggers a barge-in.
      // When we have no usable bot-level reference, hold the threshold at an
      // echo-safe floor: above worst-case AEC residual, well below the
      // 0.05-0.2 RMS of real user speech, so genuine barge-in still fires.
      const haveBotRef = botRms > 0;
      const adaptiveThreshold = Math.max(
        haveBotRef ? BARGE_IN_RMS_THRESHOLD : BARGE_IN_NO_BOTREF_FLOOR,
        noiseAdaptiveThreshold,
        botRms * BARGE_IN_BOT_RMS_MULTIPLIER + BARGE_IN_BASE_THRESHOLD,
      );
      // FIX 4 / KI-225 (V1.3) — speech ZCR band scaled to the actual
      // AudioContext sampleRate. At 48 kHz that's the original 20..250;
      // at 16 kHz it's ~7..83.
      const band = zcrBandRef.current;
      const isSpeechBand = zeroCrossings >= band.min && zeroCrossings <= band.max;

      // KI-285 (2026-05-16) — echo-suppression grace window. For the first
      // BARGE_IN_GRACE_MS of the bot's reply, the energy at the mic is the
      // bot's OWN audio echoing back (browser AEC is imperfect on speaker
      // users), NOT the user. Refuse to accumulate sustained frames or
      // trigger during this window, but KEEP the rAF loop alive so the
      // instant the window elapses — if the user is genuinely speaking over
      // the bot — the sustained-energy gate re-arms and fires within
      // BARGE_IN_SUSTAINED_FRAMES (~100ms). Hold sustainedFrames at 0 so an
      // echo burst that straddles the grace boundary cannot carry partial
      // credit past it. Real barge-in is the user talking for *seconds*, so
      // it always survives a 600ms suppression; the bot's start-of-reply
      // echo, which cannot outlast the window without the user speaking, is
      // the only thing suppressed. `started === 0` means no active playback
      // stamp (defensive): treat as still-in-grace so we never trigger on a
      // stale/unknown timeline.
      const started = ttsPlaybackStartedAtRef.current;
      const inGraceWindow =
        started === 0 || Date.now() - started < BARGE_IN_GRACE_MS;
      if (inGraceWindow) {
        sustainedFrames = 0;
        rafId = requestAnimationFrame(bargeInTick);
        return;
      }

      if (rms >= adaptiveThreshold && isSpeechBand) {
        sustainedFrames += 1;
        if (sustainedFrames >= BARGE_IN_SUSTAINED_FRAMES) {
          triggerBargeIn(rms);
          return;
        }
      } else {
        sustainedFrames = 0;
      }
      rafId = requestAnimationFrame(bargeInTick);
    };

    const startBargeInLoop = () => {
      // Gating: voice mode active, no racing text turn, MediaRecorder live.
      if (!wantRunningRef.current) return;
      if (isTextRequestPendingRef.current) return;
      if (!recorderActiveRef.current) return;
      const stream = mediaStreamRef.current;
      if (!stream || stream.getAudioTracks().length === 0) return;

      try {
        // Reuse the AudioContext + AnalyserNode if the same stream is still
        // attached; otherwise rebuild (the stream may have been swapped out
        // by teardownAudio() between TTS plays).
        if (!audioCtx || audioCtx.state === "closed") {
          const Ctor = (window.AudioContext
            || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext);
          if (!Ctor) return;
          audioCtx = new Ctor();
        }
        if (audioCtx.state === "suspended") {
          // KI-223 (2026-05-15) — V1.1. Best-effort resume; if it rejects
          // (Chrome's autoplay policy requires a user gesture), surface a
          // structured error so the UI can prompt the user to tap. Without
          // this, the VAD silently never fires and barge-in appears broken
          // for the entire session.
          void audioCtx.resume().catch((err) => {
            console.debug("[useStreamingVoice] V1.1 AudioContext.resume failed", err);
            try { onVoiceErrorRef.current("audio_context_suspended"); } catch { /* ignore */ }
          });
        }
        if (!analyser || attachedStream !== stream) {
          try { sourceNode?.disconnect(); } catch { /* ignore */ }
          try { analyser?.disconnect(); } catch { /* ignore */ }
          analyser = audioCtx.createAnalyser();
          analyser.fftSize = 2048;
          analyser.smoothingTimeConstant = 0.5;
          sourceNode = audioCtx.createMediaStreamSource(stream);
          sourceNode.connect(analyser);
          attachedStream = stream;
          rmsBuf = new Float32Array(new ArrayBuffer(analyser.fftSize * 4));
          // KI-225 (2026-05-15) — V1.3. Compare the AudioContext's actual
          // sampleRate against the track's reported rate. If they disagree,
          // log a warning AND rescale the speech ZCR band so the VAD math
          // keeps meaning at 16 kHz / 24 kHz consumer mics (the static
          // 20..250 band from KI-189 was calibrated for 48 kHz).
          try {
            const trackRate = stream.getAudioTracks()[0]?.getSettings?.().sampleRate;
            const ctxRate = audioCtx.sampleRate;
            if (trackRate && Math.abs(trackRate - ctxRate) > 100) {
              console.debug(
                "[useStreamingVoice] V1.3 sample-rate mismatch",
                { trackRate, ctxRate },
              );
            }
            zcrBandRef.current = scaleSpeechZcrBand(ctxRate);
          } catch {
            // Older browsers without MediaTrackSettings.sampleRate — keep
            // the reference band.
            zcrBandRef.current = scaleSpeechZcrBand(audioCtx.sampleRate);
          }
        }
        sustainedFrames = 0;
        if (rafId !== null) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(bargeInTick);
      } catch (err) {
        console.debug("[useStreamingVoice] KI-189 VAD init failed", err);
        teardownAnalyser();
      }
    };

    const updateTtsState = () => {
      let anyPlaying = false;
      ttsAudioElementsRef.current.forEach((el) => {
        if (!el.paused && !el.ended) anyPlaying = true;
      });
      const wasPlaying = isTtsPlayingRef.current;
      isTtsPlayingRef.current = anyPlaying;
      if (anyPlaying && !wasPlaying) {
        // TTS just started — abort any in-flight recognition so it stops
        // transcribing the bot voice.
        // KI-285 (2026-05-16) — stamp the playback-start instant so the
        // barge-in tick can suppress detection during the BARGE_IN_GRACE_MS
        // echo window. This is the ONLY false→true edge, so it captures the
        // true start of the reply (not a per-chunk restart — the reply is a
        // single <audio> blob; see BARGE_IN_GRACE_MS comment).
        ttsPlaybackStartedAtRef.current = Date.now();
        console.debug("[useStreamingVoice] KI-188 TTS started — pausing recognition");
        // KI-203 (2026-05-15) — flip the result-drop flag the INSTANT TTS
        // starts. abort() below has a ~100-300ms tail during which onresult
        // can still fire with bot-voice transcripts; the flag closes that
        // window unconditionally.
        if (dropResultsClearTimerRef.current !== null) {
          clearTimeout(dropResultsClearTimerRef.current);
          dropResultsClearTimerRef.current = null;
        }
        dropResultsRef.current = true;
        console.debug("[useStreamingVoice] KI-203 dropResultsRef=true (TTS start)");
        const rec = recognitionRef.current;
        if (rec) {
          try { rec.abort(); } catch { /* ignore */ }
        }
        // KI-195 — user cannot be speaking during TTS playback; stop the
        // RMS-learning loop until TTS ends so we don't capture bot audio
        // bleed-through as "user speech level".
        stopUserRmsLoop();
        // KI-191 — re-duck every playing audio in case React or the audio
        // element default reset volume after watchAudio set it.
        ttsAudioElementsRef.current.forEach((el) => {
          if (!el.paused && el.volume !== VOICE_MODE_TTS_VOLUME) {
            try { el.volume = VOICE_MODE_TTS_VOLUME; } catch { /* ignore */ }
          }
        });
        // KI-195 — once the volume floor is set, begin adaptive calibration
        // so the bot's volume tracks the learned user speech level.
        startVolumeCalibration();
        // KI-192 (2026-05-15) — MediaRecorder might be torn down between
        // user utterances (KI-168 teardownAudio). Without an active
        // recorder, startBargeInLoop bails on the recorderActiveRef check
        // and barge-in never fires. Fire-and-forget ensureAudioCapture
        // first; if it succeeds, the VAD loop has a live stream.
        if (wantRunningRef.current && !isTextRequestPendingRef.current) {
          void ensureAudioCapture().then(() => {
            // Re-check we're still in TTS-playing state — TTS may have
            // ended during the async ensureAudioCapture round-trip.
            if (isTtsPlayingRef.current) {
              startBargeInLoop();
            }
          });
        } else {
          startBargeInLoop();  // best-effort if gates won't allow capture rebuild
        }
      } else if (!anyPlaying && wasPlaying) {
        // TTS just ended — let the heartbeat/visibility listeners revive.
        // Trigger immediately too so the user doesn't wait ~4s.
        // KI-285 (2026-05-16) — clear the playback-start stamp so a stale
        // value can't accidentally satisfy the grace check on the next turn
        // before updateTtsState re-stamps it.
        ttsPlaybackStartedAtRef.current = 0;
        console.debug("[useStreamingVoice] KI-188 TTS ended — resuming recognition");
        // KI-203 (2026-05-15) — keep dropping recognition results for
        // POST_TTS_DROP_MS after TTS ends. The recognition pipeline we
        // abort()'d at TTS-start can still deliver buffered events for a
        // beat; without this delayed clear, the tail of the bot's TTS
        // leaks into the input box as the user starts speaking.
        if (dropResultsClearTimerRef.current !== null) {
          clearTimeout(dropResultsClearTimerRef.current);
        }
        dropResultsClearTimerRef.current = setTimeout(() => {
          dropResultsRef.current = false;
          dropResultsClearTimerRef.current = null;
          console.debug("[useStreamingVoice] KI-203 dropResultsRef=false (post-TTS window over)");
        }, POST_TTS_DROP_MS);
        stopBargeInLoop();
        // KI-195 — freeze the per-element calibrated volume and resume
        // learning the user's speech RMS for the next turn.
        stopVolumeCalibration();
        startUserRmsLoop();
        if (wantRunningRef.current && !isTextRequestPendingRef.current) {
          safeStart();
        }
      }
    };

    const watchAudio = (el: HTMLAudioElement) => {
      if (ttsAudioElementsRef.current.has(el)) return;
      ttsAudioElementsRef.current.add(el);
      // KI-191 — duck bot TTS to 60% while voice mode is on, so AEC residual
      // is even quieter and barge-in is trivial.
      // KI-195 — if we already calibrated a volume for this exact element on
      // a previous turn (rare — elements are usually recreated), reuse it so
      // we don't reset the adaptive level on every play() event.
      try {
        const prior = calibratedVolumes.get(el);
        el.volume = prior !== undefined ? prior : VOICE_MODE_TTS_VOLUME;
        duckedAudios.add(el);
      } catch { /* readonly volume on some platforms — ignore */ }
      // KI-190 — attach bot-level analyser for adaptive threshold.
      attachBotAnalyser(el);
      el.addEventListener("play", updateTtsState);
      el.addEventListener("playing", updateTtsState);
      el.addEventListener("pause", updateTtsState);
      el.addEventListener("ended", updateTtsState);
      // Initial check (handles audio that was already playing on mount)
      updateTtsState();
    };

    const unwatchAudio = (el: HTMLAudioElement) => {
      if (!ttsAudioElementsRef.current.has(el)) return;
      el.removeEventListener("play", updateTtsState);
      el.removeEventListener("playing", updateTtsState);
      el.removeEventListener("pause", updateTtsState);
      el.removeEventListener("ended", updateTtsState);
      ttsAudioElementsRef.current.delete(el);
      updateTtsState();
    };

    // Initial scan
    document.querySelectorAll("audio").forEach((el) => watchAudio(el as HTMLAudioElement));

    // Watch the whole document for new <audio> elements
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((m) => {
        m.addedNodes.forEach((n) => {
          if (n instanceof HTMLElement) {
            if (n.tagName === "AUDIO") watchAudio(n as HTMLAudioElement);
            n.querySelectorAll?.("audio").forEach((el) => watchAudio(el as HTMLAudioElement));
          }
        });
        m.removedNodes.forEach((n) => {
          if (n instanceof HTMLElement) {
            if (n.tagName === "AUDIO") unwatchAudio(n as HTMLAudioElement);
            n.querySelectorAll?.("audio").forEach((el) => unwatchAudio(el as HTMLAudioElement));
          }
        });
      });
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // KI-195 — kick off the user-RMS learning loop on mount so by the time
    // the first TTS plays we already have a baseline. The loop self-exits
    // when conditions aren't met (no analyser / no stream / in TTS), so
    // firing it unconditionally here is safe.
    startUserRmsLoop();
    // FIX 5 (HIGH) — start the wall-clock decay so userSpeechRms never
    // gets permanently pinned high (even during TTS playback when the
    // rAF loop is gated off).
    startUserRmsWallClockDecay();

    return () => {
      // KI-195 — tear down adaptive volume calibration before clearing
      // ducked-audio state so the calibration tick can't race a clear().
      stopUserRmsLoop();
      // FIX 5 (HIGH) — clean up the wall-clock decay interval.
      stopUserRmsWallClockDecay();
      stopVolumeCalibration();
      calibratedVolumes.clear();
      observer.disconnect();
      // KI-191 — restore bot TTS volume to default before unmount so a
      // subsequent voice-OFF session doesn't end up with silent audio.
      duckedAudios.forEach((el) => {
        try { el.volume = 1.0; } catch { /* ignore */ }
      });
      duckedAudios.clear();
      ttsAudioElementsRef.current.forEach((el) => {
        el.removeEventListener("play", updateTtsState);
        el.removeEventListener("playing", updateTtsState);
        el.removeEventListener("pause", updateTtsState);
        el.removeEventListener("ended", updateTtsState);
      });
      ttsAudioElementsRef.current.clear();
      isTtsPlayingRef.current = false;
      // KI-203 — clear the post-TTS drop-results window timer so a
      // disabled-then-re-enabled voice mode doesn't inherit a stale flag.
      if (dropResultsClearTimerRef.current !== null) {
        clearTimeout(dropResultsClearTimerRef.current);
        dropResultsClearTimerRef.current = null;
      }
      dropResultsRef.current = false;
      // KI-189 — release AnalyserNode + AudioContext on unmount / disable.
      teardownAnalyser();
    };
  }, [enabled, isSupported, isTextRequestPendingRef, safeStart]);

  // KI-174 (2026-05-15) — immediate-revival on visibility/focus changes.
  // User reported: "sometimes when I go away from clicking the text box,
  // it seems to not input my voice anymore. I have to restart the whole
  // voice thing." Root cause: Chrome's SpeechRecognition auto-stops
  // when the tab loses visibility (tab switch, app switch, screenshot,
  // OS modal). The KI-173 heartbeat is throttled to ~1Hz when the tab
  // is hidden, so it takes several seconds to revive after returning.
  // Force-revival on:
  //   - document `visibilitychange` → visible
  //   - window `focus`
  // Both check wantRunningRef + isTextRequestPendingRef before firing.
  useEffect(() => {
    if (!enabled || !isSupported) return;
    if (typeof window === "undefined" || typeof document === "undefined") return;

    const tryRevive = (trigger: string) => {
      if (
        wantRunningRef.current
        && !isTextRequestPendingRef.current
        && !isTtsPlayingRef.current  // KI-188 — block revival during TTS
        && document.visibilityState === "visible"
      ) {
        console.debug("[useStreamingVoice] revival trigger=" + trigger);
        safeStart();
      }
    };

    const onVisible = () => tryRevive("visibilitychange");
    const onFocus = () => tryRevive("window.focus");
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onFocus);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled, isSupported, isTextRequestPendingRef, safeStart]);

  // Unmount cleanup.
  useEffect(() => {
    return () => {
      wantRunningRef.current = false;
      clearRestartTimer();
      const rec = recognitionRef.current;
      if (rec) {
        try { rec.abort(); } catch {}
        rec.onresult = null;
        rec.onerror = null;
        rec.onend = null;
        rec.onstart = null;
      }
      recognitionRef.current = null;
      teardownAudio();
      // KI-202 — clear pending utterance grace timer on unmount.
      if (pendingSubmitTimerRef.current !== null) {
        clearTimeout(pendingSubmitTimerRef.current);
        pendingSubmitTimerRef.current = null;
      }
      pendingUtteranceRef.current = "";
      pendingChunksRef.current = [];
      // #53 / #54 — release the warm stream + recorder + AudioContext on
      // unmount so the OS mic indicator goes off when the app is torn down.
      disarmWarmStream();
    };
  }, [clearRestartTimer, teardownAudio, disarmWarmStream]);

  // FIX 3 (HIGH) — one-shot read-and-clear of the barge-in flag. Returns
  // true exactly once after triggerBargeIn fires; subsequent calls return
  // false until the next barge-in event.
  const consumeBargeInSignal = useCallback((): boolean => {
    if (bargeInRequestedRef.current) {
      bargeInRequestedRef.current = false;
      return true;
    }
    return false;
  }, []);

  return {
    start,
    stop,
    isSupported,
    consumeBargeInSignal,
    // #53 / #54 — warm-stream + pre-roll push-to-talk API.
    isWarm,
    armWarmStream,
    disarmWarmStream,
    beginPushToTalk,
    endPushToTalk,
    consumePreRollChunks,
  };
}
