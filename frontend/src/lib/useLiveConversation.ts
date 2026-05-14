"use client";

/**
 * useLiveConversation — full-duplex voice mode with barge-in.
 *
 * KI-044 (2026-05-14) — PCM pre-roll via AudioWorklet.
 * --------------------------------------------------------------------
 * Previous implementation started a MediaRecorder ONLY when VAD declared
 * "speech started" — by which point ~50-80 ms of the first word was
 * already past the mic. Users reported the bot only hearing "ello" / "i
 * am" rather than "hello" / "hi i am".
 *
 * Current implementation:
 *   - Single getUserMedia stream + AudioContext stay open while Live is on.
 *   - An AudioWorkletNode taps the raw PCM from the source — every render
 *     quantum (128 samples) is posted back to the main thread as Float32.
 *   - The main thread keeps a circular preroll buffer (~300 ms / 4800
 *     samples at 16 kHz) when no utterance is in progress.
 *   - When VAD fires speech-start, the preroll is snapshotted into the
 *     active utterance buffer and subsequent samples are appended.
 *   - When VAD fires silence-end, we encode the full utterance (preroll +
 *     speech + small post-roll) as a 16-bit PCM WAV (Sarvam Saarika's
 *     native format) and post it to `onUtterance`.
 *   - VAD itself still runs off the AnalyserNode (separate path) so its
 *     sensitivity tuning is independent from the PCM capture rate.
 *
 * Result: the user's first phoneme is in the blob. No more "ello".
 *
 * Push-to-talk path (page.tsx::startRecording) is unaffected — PTT
 * recording starts when the user clicks, the input is already primed.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type LiveConversationOptions = {
  onUtterance: (blob: Blob, abort: AbortController) => Promise<void>;
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
  rmsThreshold?: number;
  speechStartFrames?: number;
  silenceEndFrames?: number;
};

export type LiveConversationState = {
  live: boolean;
  recording: boolean;
  micPermissionDenied: boolean;
  setLive: (v: boolean) => void;
  inflightAbortRef: React.MutableRefObject<AbortController | null>;
};

const DEFAULTS = {
  // KI-041 — sensitivity bumped to catch normal conversational volume.
  rmsThreshold: 18,
  // KI-043/044 — fire on first loud frame (~16 ms) so the preroll buffer
  // snapshot captures as much pre-trigger audio as possible.
  speechStartFrames: 1,
  silenceEndFrames: 40, // ~640 ms of silence to declare utterance end
  minUtteranceMs: 400,
  // KI-044 — How much pre-trigger PCM we keep in the rolling buffer.
  // 300 ms is generous; covers the ~80 ms VAD latency + ~100 ms of
  // user onset before the first detectable frame, with margin.
  prerollMs: 300,
};

// AudioWorklet processor source — inlined as a Blob URL so we don't need
// a separate static asset route. Runs on the audio thread; posts each
// 128-sample mono Float32Array back to the main thread.
const WORKLET_SOURCE = `
class PCMCaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0];
    if (input && input[0]) {
      // Clone the buffer so it survives the transfer; the original is
      // a view onto the audio thread's internal buffer.
      this.port.postMessage(input[0].slice(0));
    }
    return true;
  }
}
registerProcessor('pcm-capture', PCMCaptureProcessor);
`;

// Encode Float32 samples as a 16-bit PCM WAV file (mono). Returns a Blob
// suitable for `<input type=file>` upload to /api/transcribe.
function encodeWAV(samples: Float32Array, sampleRate: number): Blob {
  const headerSize = 44;
  const dataSize = samples.length * 2; // 16-bit
  const buffer = new ArrayBuffer(headerSize + dataSize);
  const view = new DataView(buffer);

  const writeString = (offset: number, str: string) => {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
  };

  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);          // PCM chunk size
  view.setUint16(20, 1, true);           // PCM format
  view.setUint16(22, 1, true);           // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true); // byte rate
  view.setUint16(32, 2, true);           // block align
  view.setUint16(34, 16, true);          // bits per sample
  writeString(36, "data");
  view.setUint32(40, dataSize, true);

  let offset = headerSize;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function useLiveConversation(opts: LiveConversationOptions): LiveConversationState {
  const [live, setLive] = useState(false);
  const [recording, setRecording] = useState(false);
  const [micPermissionDenied, setMicPermissionDenied] = useState(false);

  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const workletUrlRef = useRef<string | null>(null);
  const sampleRateRef = useRef<number>(48000);

  // KI-044 — sample-level capture buffers
  const prerollRef = useRef<Float32Array[]>([]);
  const speechBufferRef = useRef<Float32Array[]>([]);

  const recordingRef = useRef(false);
  const rafIdRef = useRef<number | null>(null);
  const inflightAbortRef = useRef<AbortController | null>(null);
  const recStartTsRef = useRef<number>(0);

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
    minUtteranceMs: DEFAULTS.minUtteranceMs,
    prerollMs: DEFAULTS.prerollMs,
  };

  const interruptBotAudio = useCallback(() => {
    if (typeof document !== "undefined") {
      document.querySelectorAll("audio").forEach((a) => {
        try {
          a.pause();
          a.currentTime = a.duration || 0;
        } catch {}
      });
    }
  }, []);

  // KI-044 — open speech capture: snapshot the preroll into speechBuffer,
  // flag recording, fire callbacks. The PCM keeps flowing via the worklet
  // port; we just toggle where it lands.
  const beginSpeechCapture = useCallback(() => {
    speechBufferRef.current = [...prerollRef.current];
    prerollRef.current = [];
    recordingRef.current = true;
    recStartTsRef.current = Date.now();
    setRecording(true);
    onSpeechStartRef.current?.();
  }, []);

  // KI-044 — close speech capture: encode WAV, run guards, fire onUtterance.
  const endSpeechCapture = useCallback(async () => {
    if (!recordingRef.current) return;
    recordingRef.current = false;
    setRecording(false);
    const durationMs = Date.now() - (recStartTsRef.current || Date.now());
    const chunks = speechBufferRef.current;
    speechBufferRef.current = [];

    if (chunks.length === 0) return;
    if (durationMs < cfg.minUtteranceMs) {
      // eslint-disable-next-line no-console
      console.debug("[live-mode] dropped short utterance", durationMs, "ms");
      return;
    }

    // Concatenate Float32Array chunks
    let totalSamples = 0;
    for (const c of chunks) totalSamples += c.length;
    const merged = new Float32Array(totalSamples);
    let offset = 0;
    for (const c of chunks) {
      merged.set(c, offset);
      offset += c.length;
    }

    const wav = encodeWAV(merged, sampleRateRef.current);
    if (wav.size < 3000) return; // floor (matches prior heuristic)

    onSpeechEndRef.current?.();
    const abort = new AbortController();
    inflightAbortRef.current = abort;
    try {
      await onUtteranceRef.current(wav, abort);
    } catch (e) {
      const name = (e as { name?: string })?.name;
      if (name !== "AbortError") {
        // eslint-disable-next-line no-console
        console.error("[live-mode] utterance handler failed:", e);
      }
    } finally {
      if (inflightAbortRef.current === abort) {
        inflightAbortRef.current = null;
      }
    }
  }, [cfg.minUtteranceMs]);

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
          // Barge in: kill bot audio + cancel in-flight chat + begin capture.
          interruptBotAudio();
          if (inflightAbortRef.current) {
            try { inflightAbortRef.current.abort(); } catch {}
            inflightAbortRef.current = null;
          }
          beginSpeechCapture();
        }
      } else {
        quiet++;
        loud = 0;
        if (quiet === cfg.silenceEndFrames && recordingRef.current) {
          void endSpeechCapture();
        }
      }

      rafIdRef.current = requestAnimationFrame(loop);
    };
    rafIdRef.current = requestAnimationFrame(loop);
  }, [
    cfg.rmsThreshold,
    cfg.silenceEndFrames,
    cfg.speechStartFrames,
    interruptBotAudio,
    beginSpeechCapture,
    endSpeechCapture,
  ]);

  useEffect(() => {
    let cancelled = false;

    const tearDown = () => {
      if (rafIdRef.current !== null) {
        cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
      // Drop any pending capture without firing onUtterance
      recordingRef.current = false;
      speechBufferRef.current = [];
      prerollRef.current = [];
      if (workletRef.current) {
        try { workletRef.current.disconnect(); } catch {}
        workletRef.current = null;
      }
      if (workletUrlRef.current) {
        try { URL.revokeObjectURL(workletUrlRef.current); } catch {}
        workletUrlRef.current = null;
      }
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
        sampleRateRef.current = ctx.sampleRate;

        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.5;
        source.connect(analyser);
        sourceRef.current = source;
        analyserRef.current = analyser;

        // KI-044 — register the inline PCM-capture worklet + tap the source.
        const blob = new Blob([WORKLET_SOURCE], { type: "application/javascript" });
        const url = URL.createObjectURL(blob);
        workletUrlRef.current = url;
        try {
          await ctx.audioWorklet.addModule(url);
          const node = new AudioWorkletNode(ctx, "pcm-capture");
          workletRef.current = node;

          const prerollSamplesCap = Math.ceil((cfg.prerollMs / 1000) * ctx.sampleRate);

          node.port.onmessage = (ev: MessageEvent<Float32Array>) => {
            const chunk = ev.data;
            if (recordingRef.current) {
              speechBufferRef.current.push(chunk);
            } else {
              prerollRef.current.push(chunk);
              // Trim oldest chunks to keep total length under prerollSamplesCap.
              let total = 0;
              for (const c of prerollRef.current) total += c.length;
              while (total > prerollSamplesCap && prerollRef.current.length > 1) {
                total -= prerollRef.current[0].length;
                prerollRef.current.shift();
              }
            }
          };

          source.connect(node);
          // Worklet's process() only runs while the node is connected to a
          // destination (directly or via the graph). But we DON'T want the
          // user's mic playing back through speakers — route via a zero-gain
          // GainNode so the graph stays "live" but output is silent.
          const silentSink = ctx.createGain();
          silentSink.gain.value = 0;
          node.connect(silentSink);
          silentSink.connect(ctx.destination);
        } catch (e) {
          // eslint-disable-next-line no-console
          console.error("[live-mode] AudioWorklet setup failed; fallback to silence", e);
        }

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
  }, [live, tickVAD, cfg.prerollMs]);

  return {
    live,
    recording,
    micPermissionDenied,
    setLive,
    inflightAbortRef,
  };
}
