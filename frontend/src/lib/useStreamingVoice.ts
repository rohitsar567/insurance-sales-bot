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
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
        && restartTimerRef.current === null
      ) {
        safeStart();
      }
    }, 4000);
    return () => clearInterval(tick);
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
