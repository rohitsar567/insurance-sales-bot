"use client";

/**
 * useLiveConversation — full-duplex voice mode with barge-in.
 *
 * KI-044 (2026-05-14) — PCM pre-roll via AudioWorklet.
 * KI-057 (2026-05-15) — Noise-robust VAD + flush-on-stop.
 * KI-060 (2026-05-15) — Silence-end window lengthened (40 → 90 frames,
 *   ~640 ms → ~1.5 s) so natural mid-sentence pauses don't auto-submit.
 *
 * Why KI-057 was needed
 * --------------------------------------------------------------------
 * Real user feedback after KI-044 shipped: "Background noise continues
 * to play a big issue, even random noise without people speaking or
 * just ambient noise keeps the live listening on, and it keeps
 * processing on something and not moving on. Also, if an entire series
 * of things have been said and I turn off the live chat, should that
 * not auto submit?"
 *
 * Two failure modes:
 *   1. Static `rmsThreshold: 18` triggered on HVAC / fan / traffic.
 *      Once triggered, `silenceEndFrames: 40` (~640 ms of silence)
 *      never accumulated because ambient noise kept the meter above
 *      threshold — segment never closed, "Hearing you…" stuck on.
 *   2. Toggling Live OFF mid-utterance ran tearDown() which silently
 *      dropped `speechBufferRef` — user's words were lost.
 *
 * Fixes layered in (defaults — overridable via opts):
 *   A. Adaptive noise floor. While not recording, EMA the ambient
 *      energy. Effective threshold = max(noise_floor * 2 + 4,
 *      cfg.rmsThreshold). HVAC keeps the bar high.
 *   B. Voice-band spectral gate. Require ≥35% of FFT energy to live
 *      in bins 2-22 (~190-2150 Hz at 48 kHz) — the voiced-speech band.
 *      Broadband noise fails this even when loud.
 *   C. `speechStartFrames: 3` (~48 ms) so a single click/clack
 *      doesn't open a segment.
 *   D. Hard cap: `maxUtteranceMs: 18 s`. If a segment runs that long
 *      without silence-end firing, force-close it. Prevents the
 *      "noise pinned the meter open forever" state.
 *   E. Post-utterance cooldown (700 ms). After we close + dispatch a
 *      segment, suppress new triggers — even with echoCancellation,
 *      the bot's TTS attack transient sometimes bleeds in.
 *   F. Flush-on-teardown. If the user toggles Live OFF while a
 *      capture is in progress and the duration meets minUtteranceMs,
 *      encode + fire `onUtterance` once before tearing down — so
 *      whatever they were saying gets submitted.
 *
 * KI-044 still applies — see below.
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
  // KI-113 (2026-05-15) — raised from 18 → 26 to reject ambient background
  // noise (HVAC, traffic, distant chatter). Effective threshold is
  // max(this, adaptive noise_floor * 2.5 + 6) — see KI-114 in the VAD loop.
  rmsThreshold: 26,
  // KI-113 — raised 3 → 5 (~80 ms sustained). Single clicks / cutlery /
  // typing transients no longer flip the gate. Preroll buffer (KI-044)
  // still captures the first phoneme via the 300 ms look-back.
  speechStartFrames: 5,
  // KI-060/064/115 (2026-05-15) — silence-end window tuning.
  //   v1 (KI-057): 40 (~640 ms) — too tight; users said pause→submit.
  //   v2 (KI-060): 90 (~1.5 s) — still cut "Hi, I'm looking to buy a
  //     new insurance ..." before "policy".
  //   v3 (KI-064): 120 (~2 s) — covers a normal thinking pause between
  //     phrases.
  //   v4 (KI-115): 180 (~3 s) — user reported the 2s window still cut
  //     mid-thought pauses ("um", "let me think", etc.). 3 s is the
  //     pause length where most speakers genuinely consider the
  //     utterance complete. Trade: +1 s tail latency before bot
  //     responds, accepted to kill the "submit on pause" UX bug.
  silenceEndFrames: 180,
  minUtteranceMs: 400,
  // KI-044 — How much pre-trigger PCM we keep in the rolling buffer.
  // 300 ms is generous; covers the ~80 ms VAD latency + ~100 ms of
  // user onset before the first detectable frame, with margin.
  prerollMs: 300,
  // KI-057 — hard cap. If silence-end never fires (e.g. continuous
  // ambient noise pinned the meter open), force-close the segment.
  maxUtteranceMs: 18000,
  // KI-057 — suppress new triggers for this long after we close a
  // segment. Avoids bot's TTS attack transient bleeding through even
  // with echoCancellation on.
  postUtteranceCooldownMs: 700,
  // KI-113 — raised 0.35 → 0.50 minimum fraction of total FFT energy
  // that must sit in the voice band (bins 2-22 ≈ 190-2150 Hz at 48 kHz).
  // Broadband HVAC / fan / traffic noise typically scores 0.20-0.30;
  // voiced speech typically scores 0.40-0.70. Lifting to 0.50 puts the
  // gate squarely above noise and just below the bottom of speech.
  voiceBandMinProp: 0.50,
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
  // KI-057 — adaptive noise floor (EMA of ambient avg while idle).
  const noiseFloorRef = useRef<number>(0);
  // KI-057 — gates "did the bot just stop talking?" cooldown.
  const lastUtteranceEndedAtRef = useRef<number>(0);

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
    maxUtteranceMs: DEFAULTS.maxUtteranceMs,
    postUtteranceCooldownMs: DEFAULTS.postUtteranceCooldownMs,
    voiceBandMinProp: DEFAULTS.voiceBandMinProp,
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
  // KI-057 — also anchors the post-utterance cooldown.
  const endSpeechCapture = useCallback(async () => {
    if (!recordingRef.current) return;
    recordingRef.current = false;
    setRecording(false);
    lastUtteranceEndedAtRef.current = Date.now();
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
  // KI-057 — adaptive threshold + voice-band gate + max-utterance cap.
  const tickVAD = useCallback(() => {
    if (!analyserRef.current) return;
    const a = analyserRef.current;
    const buf = new Uint8Array(a.frequencyBinCount);
    let loud = 0;
    let quiet = 0;
    // Voice band: bins 2-22 at fftSize=512 cover ~190-2150 Hz at 48 kHz —
    // where voiced speech lives. Capped at bin count for safety.
    const voiceBandStart = 2;
    const voiceBandEnd = Math.min(22, buf.length - 1);

    const loop = () => {
      if (!analyserRef.current) return;
      a.getByteFrequencyData(buf);

      let sum = 0;
      let voiceSum = 0;
      for (let i = 0; i < buf.length; i++) {
        sum += buf[i];
        if (i >= voiceBandStart && i <= voiceBandEnd) voiceSum += buf[i];
      }
      const avg = sum / buf.length;
      const voiceProp = sum > 0 ? voiceSum / sum : 0;

      // KI-057/114 — adaptive threshold. Floor at cfg.rmsThreshold so
      // genuinely quiet rooms don't open the gate too low. KI-114 raised
      // the multiplier 2.0→2.5 and the offset 4→6 so DISTANT speech
      // (which sits just above ambient because high frequencies attenuate
      // with distance) is rejected. Close-in speech still clears the gate
      // because the speaker's directly-radiated energy is ~10-20× ambient.
      const effectiveThreshold = Math.max(
        cfg.rmsThreshold,
        noiseFloorRef.current * 2.5 + 6,
      );

      // KI-057 — suppress new triggers right after we closed a segment
      // (bot's TTS onset can bleed in via the mic loopback).
      const cooldownActive =
        Date.now() - lastUtteranceEndedAtRef.current < cfg.postUtteranceCooldownMs;

      const speechLike =
        avg > effectiveThreshold &&
        voiceProp >= cfg.voiceBandMinProp &&
        !cooldownActive;

      if (speechLike) {
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
        // KI-057 — only learn the noise floor while idle, so ongoing
        // speech doesn't poison the EMA.
        if (!recordingRef.current) {
          noiseFloorRef.current =
            noiseFloorRef.current === 0
              ? avg
              : noiseFloorRef.current * 0.95 + avg * 0.05;
        }
        if (quiet === cfg.silenceEndFrames && recordingRef.current) {
          void endSpeechCapture();
        }
      }

      // KI-057 — max-utterance cap. If recording has run too long
      // without silence-end firing, force-close it. Prevents the
      // "noise pinned the meter open" stuck state.
      if (
        recordingRef.current &&
        recStartTsRef.current > 0 &&
        Date.now() - recStartTsRef.current > cfg.maxUtteranceMs
      ) {
        // eslint-disable-next-line no-console
        console.debug("[live-mode] force-closing at max-utterance cap");
        void endSpeechCapture();
      }

      rafIdRef.current = requestAnimationFrame(loop);
    };
    rafIdRef.current = requestAnimationFrame(loop);
  }, [
    cfg.rmsThreshold,
    cfg.silenceEndFrames,
    cfg.speechStartFrames,
    cfg.maxUtteranceMs,
    cfg.postUtteranceCooldownMs,
    cfg.voiceBandMinProp,
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
      // KI-057 — flush a mid-utterance capture before dropping refs.
      // If the user toggled Live OFF while speaking, encode + fire
      // onUtterance once (fire-and-forget) so their words still land.
      if (recordingRef.current && speechBufferRef.current.length > 0) {
        const durationMs = Date.now() - (recStartTsRef.current || Date.now());
        if (durationMs >= DEFAULTS.minUtteranceMs) {
          let total = 0;
          for (const c of speechBufferRef.current) total += c.length;
          const merged = new Float32Array(total);
          let off = 0;
          for (const c of speechBufferRef.current) {
            merged.set(c, off);
            off += c.length;
          }
          const wav = encodeWAV(merged, sampleRateRef.current);
          if (wav.size >= 3000) {
            const handler = onUtteranceRef.current;
            const abort = new AbortController();
            // Fire-and-forget. The page handler is independent of Live
            // being on, so the response will still render in the chat
            // pane after teardown completes.
            try {
              handler(wav, abort).catch((e) => {
                const name = (e as { name?: string })?.name;
                if (name !== "AbortError") {
                  // eslint-disable-next-line no-console
                  console.error("[live-mode] flush-on-stop failed:", e);
                }
              });
            } catch {}
          }
        }
      }
      recordingRef.current = false;
      speechBufferRef.current = [];
      prerollRef.current = [];
      noiseFloorRef.current = 0;
      recStartTsRef.current = 0;
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
