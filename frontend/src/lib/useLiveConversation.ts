"use client";

/**
 * useLiveConversation — full-duplex voice mode with barge-in.
 *
 * Differences from the existing push-to-talk + Hands-free toggle:
 *
 *   - Mic is OPEN continuously while live mode is on (single getUserMedia
 *     stream, not opened/closed per turn).
 *   - VAD (RMS on AnalyserNode + frame counters) detects when the user
 *     starts and stops speaking, with no button press.
 *   - When VAD detects speech start, we immediately:
 *       * pause + clear every <audio> currently playing (kill the bot's
 *         in-progress TTS reply mid-sentence),
 *       * abort the in-flight /api/chat fetch via AbortController,
 *       * start a new MediaRecorder for the user's utterance.
 *   - When VAD detects silence for >600ms after speech, we stop the
 *     recorder and POST the blob to the supplied `onUtterance` handler
 *     (which the page wires to transcribe → chat).
 *
 * The hook returns an AbortController slot the caller assigns to its
 * own in-flight fetches so barge-in can cancel them.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type LiveConversationOptions = {
  /** Called when the user finishes an utterance — pass it the audio blob. */
  onUtterance: (blob: Blob, abort: AbortController) => Promise<void>;
  /** Called when VAD detects speech start (so the UI can show "listening…"). */
  onSpeechStart?: () => void;
  /** Called when VAD detects speech end (so the UI can show "thinking…"). */
  onSpeechEnd?: () => void;
  /** RMS threshold above which we declare "speech". Tune in browser. */
  rmsThreshold?: number;
  /** Consecutive loud frames needed to start recording (debounce). */
  speechStartFrames?: number;
  /** Consecutive quiet frames needed to stop recording (~16 ms/frame). */
  silenceEndFrames?: number;
};

export type LiveConversationState = {
  live: boolean;
  recording: boolean;
  micPermissionDenied: boolean;
  setLive: (v: boolean) => void;
  /** Caller-managed abort slot for in-flight fetches; VAD aborts it on speech. */
  inflightAbortRef: React.MutableRefObject<AbortController | null>;
};

const DEFAULTS = {
  // KI-041 (2026-05-14) — sensitivity bumped. Previous threshold 28 was high
  // enough that normal speaking volume in a moderately-quiet room didn't
  // trigger barge-in detection, so users reported "speaking over the bot
  // doesn't interrupt it". 18 catches typical conversational volume reliably
  // while still rejecting room hum / breathing / keyboard clatter.
  rmsThreshold: 18,
  // ~48ms of speech to fire. Previous 5 frames (~80ms) added perceptible
  // latency on barge-in; 3 frames keeps false-positive immunity but cuts
  // the response time by ~32ms.
  speechStartFrames: 3,
  silenceEndFrames: 40, // ~640 ms of silence to declare utterance end
};

export function useLiveConversation(opts: LiveConversationOptions): LiveConversationState {
  const [live, setLive] = useState(false);
  const [recording, setRecording] = useState(false);
  const [micPermissionDenied, setMicPermissionDenied] = useState(false);

  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const recordingRef = useRef(false); // sync ref for VAD loop
  const rafIdRef = useRef<number | null>(null);
  const inflightAbortRef = useRef<AbortController | null>(null);

  const onUtteranceRef = useRef(opts.onUtterance);
  const onSpeechStartRef = useRef(opts.onSpeechStart);
  const onSpeechEndRef = useRef(opts.onSpeechEnd);
  useEffect(() => {
    onUtteranceRef.current = opts.onUtterance;
    onSpeechStartRef.current = opts.onSpeechStart;
    onSpeechEndRef.current = opts.onSpeechEnd;
  }, [opts.onUtterance, opts.onSpeechStart, opts.onSpeechEnd]);

  const cfg = {
    rmsThreshold: opts.rmsThreshold ?? DEFAULTS.rmsThreshold,
    speechStartFrames: opts.speechStartFrames ?? DEFAULTS.speechStartFrames,
    silenceEndFrames: opts.silenceEndFrames ?? DEFAULTS.silenceEndFrames,
  };

  const stopRecording = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      try { recorderRef.current.stop(); } catch {}
    }
  }, []);

  const interruptBotAudio = useCallback(() => {
    // Pause + reset every audio element in the DOM. Bot replies use plain
    // <audio> elements; killing src forces the loaded buffer to drop.
    if (typeof document !== "undefined") {
      document.querySelectorAll("audio").forEach((a) => {
        try {
          a.pause();
          // Don't blank src — let the existing buffer GC but leave the
          // element so the chat history scroll position doesn't jump.
          a.currentTime = a.duration || 0;
        } catch {}
      });
    }
  }, []);

  const startRecording = useCallback(() => {
    if (!streamRef.current) return;
    const mime = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
    const rec = mime
      ? new MediaRecorder(streamRef.current, { mimeType: mime })
      : new MediaRecorder(streamRef.current);
    chunksRef.current = [];
    rec.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };
    rec.onstop = async () => {
      recordingRef.current = false;
      setRecording(false);
      if (chunksRef.current.length === 0) return;
      const blob = new Blob(chunksRef.current, {
        type: rec.mimeType || "audio/webm",
      });
      // Reject blobs that are almost certainly silence or VAD false-trips.
      if (blob.size < 3000) return;
      onSpeechEndRef.current?.();
      const abort = new AbortController();
      inflightAbortRef.current = abort;
      try {
        await onUtteranceRef.current(blob, abort);
      } catch (e) {
        const name = (e as { name?: string })?.name;
        if (name !== "AbortError") {
          // surface to console; the UI's existing error toast will fire too
          // eslint-disable-next-line no-console
          console.error("[live-mode] utterance handler failed:", e);
        }
      } finally {
        if (inflightAbortRef.current === abort) {
          inflightAbortRef.current = null;
        }
      }
    };
    recorderRef.current = rec;
    recordingRef.current = true;
    setRecording(true);
    onSpeechStartRef.current?.();
    rec.start();
  }, []);

  // VAD loop — runs while `live` is true.
  const tickVAD = useCallback(() => {
    if (!analyserRef.current) return;
    const a = analyserRef.current;
    const buf = new Uint8Array(a.frequencyBinCount);
    let loud = 0;
    let quiet = 0;
    const loop = () => {
      if (!analyserRef.current) return;
      a.getByteFrequencyData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i];
      const avg = sum / buf.length;

      if (avg > cfg.rmsThreshold) {
        loud++;
        quiet = 0;
        if (loud === cfg.speechStartFrames && !recordingRef.current) {
          // Barge in: kill bot audio + cancel in-flight chat + start recording.
          interruptBotAudio();
          if (inflightAbortRef.current) {
            try { inflightAbortRef.current.abort(); } catch {}
            inflightAbortRef.current = null;
          }
          startRecording();
        }
      } else {
        quiet++;
        loud = 0;
        if (quiet === cfg.silenceEndFrames && recordingRef.current) {
          stopRecording();
        }
      }

      rafIdRef.current = requestAnimationFrame(loop);
    };
    rafIdRef.current = requestAnimationFrame(loop);
  }, [cfg.rmsThreshold, cfg.silenceEndFrames, cfg.speechStartFrames, interruptBotAudio, startRecording, stopRecording]);

  useEffect(() => {
    let cancelled = false;

    const tearDown = () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      stopRecording();
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop());
        streamRef.current = null;
      }
      if (sourceRef.current) {
        try { sourceRef.current.disconnect(); } catch {}
        sourceRef.current = null;
      }
      analyserRef.current = null;
      if (audioCtxRef.current) {
        audioCtxRef.current.close().catch(() => {});
        audioCtxRef.current = null;
      }
    };

    if (!live) {
      tearDown();
      return;
    }

    (async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        const AudioCtx =
          (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext ||
          window.AudioContext;
        const ctx = new AudioCtx();
        audioCtxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.5;
        source.connect(analyser);
        sourceRef.current = source;
        analyserRef.current = analyser;
        setMicPermissionDenied(false);
        tickVAD();
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error("[live-mode] mic permission denied or unavailable", e);
        setMicPermissionDenied(true);
        setLive(false);
      }
    })();

    return () => {
      cancelled = true;
      tearDown();
    };
  }, [live, stopRecording, tickVAD]);

  return {
    live,
    recording,
    micPermissionDenied,
    setLive,
    inflightAbortRef,
  };
}
