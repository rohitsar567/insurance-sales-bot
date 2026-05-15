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

// KI-189 (2026-05-15) — live-speak barge-in tuning constants.
// The MediaRecorder mic stream IS echo-cancelled by the browser (KI-185
// `getUserMedia` AEC constraints), so the bot's TTS bleed lands at a
// very low RMS (~0.001-0.005) while actual user speech sits at ~0.05-0.2.
// We pick a threshold in between, and require ~300ms sustained energy
// to avoid firing on coughs / room thumps / single-frame spikes.
const BARGE_IN_RMS_THRESHOLD = 0.025;
const BARGE_IN_SUSTAINED_FRAMES = 18; // ~300ms @ 60fps rAF
// KI-190 (2026-05-15) — adaptive threshold. The MediaRecorder mic stream
// has AEC, but for very loud bot TTS the residual bleed can still cross
// the static 0.025 threshold. We instead compute the threshold dynamically
// from the bot's CURRENT audio level: bot_rms * MULTIPLIER + BASE. Bot
// loud → threshold rises so user must speak loudly to overcome residual;
// bot quiet → threshold drops near floor so soft speech still wins.
const BARGE_IN_BOT_RMS_MULTIPLIER = 2.0;
const BARGE_IN_BASE_THRESHOLD = 0.005;
// KI-191 (2026-05-15) — duck bot TTS volume while voice mode is on.
// Reducing playback amplitude further widens the gap between the bot's
// residual mic bleed (after AEC) and the user's normal-volume speech,
// making barge-in trivial. 0.6 is loud enough to hear clearly on
// headphones and laptop speakers without overpowering user speech.
const VOICE_MODE_TTS_VOLUME = 0.6;

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
}

export interface UseStreamingVoiceReturn {
  start: () => void;
  stop: () => void;
  isSupported: boolean;
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
  } = opts;

  // Keep latest callback refs so the recognition handlers always call the
  // freshest closure without re-binding the recognition instance on every
  // render (re-binding mid-utterance loses interim results).
  const onInterimRef = useRef(onInterimTranscript);
  const onFinalRef = useRef(onFinalTranscript);
  const onErrorRef = useRef(onError);
  const onListeningRef = useRef(onListening);
  useEffect(() => { onInterimRef.current = onInterimTranscript; }, [onInterimTranscript]);
  useEffect(() => { onFinalRef.current = onFinalTranscript; }, [onFinalTranscript]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { onListeningRef.current = onListening; }, [onListening]);

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const finalsRef = useRef<string[]>([]);
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
  const ttsAudioElementsRef = useRef<Set<HTMLAudioElement>>(new Set());

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

  const [isSupported] = useState<boolean>(() => resolveCtor() !== null);

  const clearRestartTimer = useCallback(() => {
    if (restartTimerRef.current !== null) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
  }, []);

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
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
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
      recorderActiveRef.current = true;
      console.debug("[useStreamingVoice] MediaRecorder started", { mime: recorderMimeRef.current });
      return true;
    } catch (err) {
      console.debug("[useStreamingVoice] MediaRecorder init failed — falling back to Web Speech only", err);
      recorderActiveRef.current = false;
      return false;
    }
  }, [pickRecorderMime]);

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
      const running = (finalsRef.current.join(" ") + " " + interim).trim();
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
        onErrorRef.current(
          "Mic permission denied. Click the lock icon in your browser's URL bar to enable the microphone.",
        );
        return;
      }
      if (code === "audio-capture") {
        wantRunningRef.current = false;
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
      const webSpeechText = finalsRef.current.join(" ").trim();
      finalsRef.current = [];

      // KI-168 PHASE 2 — race guard: if a typed-text turn is in flight,
      // drop both transcripts on the floor (text wins). Don't start a
      // Sarvam fetch we'd be throwing away.
      const textRacing = isTextRequestPendingRef.current;

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

      // If there's no audio recorder, or text is racing, fall back to the
      // Phase 1 behaviour: submit the Web Speech transcript and bail.
      if (!recorderActiveRef.current || textRacing) {
        if (webSpeechText && !textRacing) {
          onFinalRef.current(webSpeechText);
        }
        // Best-effort: clear any partial audio so the next utterance isn't
        // contaminated with the previous one's tail.
        if (recorderActiveRef.current) drainChunks();
        scheduleRestart();
        return;
      }

      // Sarvam path. Fire-and-forget so we don't block the recognition
      // restart loop on the network round-trip.
      void (async () => {
        // Snapshot user-visible interim so the input area doesn't go blank
        // while Sarvam is in flight. The page-side input still shows the
        // Web Speech transcript; we'll overwrite it via onFinalTranscript
        // once Sarvam returns.
        if (webSpeechText) onInterimRef.current(webSpeechText);

        // We need to stop the recorder to get the final dataavailable
        // chunk; then we re-arm a new recorder for the next utterance.
        await stopRecorder();
        const drained = drainChunks();
        const totalSize = drained.reduce((n, b) => n + b.size, 0);
        console.debug("[useStreamingVoice] silence-detect", {
          webSpeechLen: webSpeechText.length,
          chunkCount: drained.length,
          blobBytes: totalSize,
        });

        // Re-arm audio capture for the next utterance (don't block on it).
        teardownAudio();
        // Only restart the audio pipeline if the user still wants the mic
        // live. Fire-and-forget; recognition restart is scheduled below.
        if (wantRunningRef.current) {
          void ensureAudioCapture();
        }

        // Skip submit when there's effectively no audio or no Web Speech
        // text. ~3 KB is the empirical noise floor used by the PTT path's
        // KI-134 silence guard (page.tsx uses 1 KB; we're stricter here
        // because Live mode auto-fires on every silence pause).
        const MIN_BLOB_BYTES = 3000;
        if (!webSpeechText && totalSize < MIN_BLOB_BYTES) {
          console.debug("[useStreamingVoice] skipping submit — no text and tiny blob");
          scheduleRestart();
          return;
        }

        // If Web Speech got nothing, but we have a real blob, still try
        // Sarvam — Web Speech occasionally drops short utterances on noisy
        // mics that Sarvam handles fine.
        if (!webSpeechText && totalSize < MIN_BLOB_BYTES) {
          scheduleRestart();
          return;
        }

        // Race-check again after the await — text turn may have started
        // while we were stopping the recorder.
        if (isTextRequestPendingRef.current) {
          console.debug("[useStreamingVoice] text turn started mid-await; dropping voice turn");
          scheduleRestart();
          return;
        }

        let authoritativeText = webSpeechText;
        if (drained.length > 0 && totalSize >= MIN_BLOB_BYTES) {
          const blob = new Blob(drained, { type: recorderMimeRef.current || "audio/webm" });
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 8000);
          try {
            console.debug("[useStreamingVoice] POST /api/transcribe", { bytes: blob.size, mime: blob.type, lang: language });
            const sarvam = await postTranscribe(blob, language, controller.signal);
            const sarvamText = (sarvam.text || "").trim();
            if (sarvamText) {
              authoritativeText = sarvamText;
              console.debug("[useStreamingVoice] Sarvam OK", {
                latency_ms: sarvam.latency_ms,
                webSpeechLen: webSpeechText.length,
                sarvamLen: sarvamText.length,
              });
            } else {
              console.debug("[useStreamingVoice] Sarvam returned empty; using Web Speech fallback");
            }
          } catch (err) {
            console.debug("[useStreamingVoice] Sarvam failed; using Web Speech fallback", err);
          } finally {
            clearTimeout(timeoutId);
          }
        }

        if (authoritativeText && !isTextRequestPendingRef.current) {
          onFinalRef.current(authoritativeText);
        }
        scheduleRestart();
      })();
    };

    return rec;
  }, [language, isTextRequestPendingRef, clearRestartTimer, safeStart, stopRecorder, teardownAudio, ensureAudioCapture]);

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
    // Kick off audio capture in parallel with recognition. If it fails we
    // degrade to Web Speech-only — onend handles the fallback path.
    void ensureAudioCapture();
    safeStart();
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
    }
    teardownAudio();
    finalsRef.current = [];
    onListeningRef.current(false);
  }, [clearRestartTimer, teardownAudio]);

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
      for (let i = 0; i < rmsBuf.length; i++) {
        const v = rmsBuf[i];
        sumSq += v * v;
      }
      const rms = Math.sqrt(sumSq / rmsBuf.length);
      // KI-190 — adaptive threshold: bot_rms * 2 + 0.005, floored at the
      // base BARGE_IN_RMS_THRESHOLD so we never set it absurdly low.
      const botRms = computeBotRms();
      const adaptiveThreshold = Math.max(
        BARGE_IN_RMS_THRESHOLD,
        botRms * BARGE_IN_BOT_RMS_MULTIPLIER + BARGE_IN_BASE_THRESHOLD,
      );
      if (rms >= adaptiveThreshold) {
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
          // Best-effort resume; ignore failures (autoplay policy may block
          // until next user gesture — VAD simply won't fire).
          void audioCtx.resume().catch(() => { /* ignore */ });
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
        console.debug("[useStreamingVoice] KI-188 TTS started — pausing recognition");
        const rec = recognitionRef.current;
        if (rec) {
          try { rec.abort(); } catch { /* ignore */ }
        }
        // KI-189 — start the AEC'd-mic VAD so the user can barge in by
        // simply speaking over the bot. MediaRecorder's stream IS echo-
        // cancelled at the browser level, unlike SpeechRecognition.
        startBargeInLoop();
      } else if (!anyPlaying && wasPlaying) {
        // TTS just ended — let the heartbeat/visibility listeners revive.
        // Trigger immediately too so the user doesn't wait ~4s.
        console.debug("[useStreamingVoice] KI-188 TTS ended — resuming recognition");
        stopBargeInLoop();
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
      try {
        el.volume = VOICE_MODE_TTS_VOLUME;
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

    return () => {
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
    };
  }, [clearRestartTimer, teardownAudio]);

  return { start, stop, isSupported };
}
