"use client";

/**
 * useLiveConversation — full-duplex voice mode with barge-in.
 *
 * KI-044 (2026-05-14) — PCM pre-roll via AudioWorklet.
 * KI-057 (2026-05-15) — Noise-robust VAD + flush-on-stop.
 * KI-060 (2026-05-15) — Silence-end window lengthened (40 → 90 frames,
 *   ~640 ms → ~1.5 s) so natural mid-sentence pauses don't auto-submit.
 * KI-159 (2026-05-15) — Early-close on stable silence. If the user has
 *   already spoken ≥3× minUtteranceMs (~1.2 s) and silence has accumulated
 *   to half the silenceEndFrames window (~1.5 s), close the segment NOW.
 *   Prevents notification dings / transient background noise mid-pause
 *   from re-triggering `speechLike`, zeroing the silence counter, and
 *   extending the segment until either the full 3 s window or the 18 s
 *   max-cap fires — by which point the real words are buried in a bloated
 *   blob that Sarvam STT either drops or mis-transcribes.
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
  // KI-113 raised from 18 → 26 to reject ambient noise.
  // KI-139 (2026-05-15) — backed off to 18 because voice-forensics agent
  // proved 26 sat ABOVE typical speech avg on consumer mics (especially
  // built-ins with active noise gate that pin noiseFloor to 0). VAD never
  // opened → green pill rendered → zero audio posted.
  rmsThreshold: 18,
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
  // KI-113 raised to 0.50, KI-134 backed off to 0.35.
  // KI-139 (2026-05-15) — voice-forensics agent measured live bundle on
  // user's actual hardware: voiceProp sits at 0.25–0.33 on quiet laptop
  // mics with NS enabled. 0.35 still gates the user out. 0.20 puts the
  // floor well below voiced-speech minimum and only rejects pure tones
  // (constant whine of HVAC, traffic). This is the deepest pushback
  // — if HVAC noise creeps in, KI-140 will add a /api/transcribe round
  // trip that detects empty responses and surfaces "couldn't hear you".
  voiceBandMinProp: 0.20,
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
  // KI-141 (2026-05-15) — TTS-playback awareness for reliable barge-in.
  // When the bot's <audio> element is playing, the mic re-captures the
  // speaker output (echoCancellation is imperfect). The noiseFloor EMA
  // would otherwise learn the bot's voice level and pull effectiveThreshold
  // up to bot-loudness, making user voice unable to clear the gate. We
  // (a) freeze noise-floor learning, (b) bypass the post-utterance cooldown
  // (cooldown only makes sense AFTER bot finishes), and (c) drop the
  // speech-start frame count so barge-in fires in ~30 ms instead of ~80 ms.
  const ttsPlayingRef = useRef<boolean>(false);
  const ttsAudioElementsRef = useRef<Set<HTMLAudioElement>>(new Set());

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
    // KI-141 — clearing TTS-playing immediately on barge-in so the VAD
    // resumes idle-mode noise-floor learning even before the `pause`
    // event fires on the (now stopped) <audio> element.
    ttsPlayingRef.current = false;
    // KI-141 — anchor cooldown to barge-in moment so the next 700 ms
    // suppresses any residual decay tail / echo from the just-paused TTS.
    lastUtteranceEndedAtRef.current = Date.now();
  }, []);

  // KI-141 (2026-05-15) — TTS-playback observer.
  // Watch every <audio> element in the document for play/pause/ended so
  // the VAD knows when the bot is currently speaking. This is the signal
  // that flips the VAD into "barge-in mode" (no cooldown, faster start,
  // frozen noise floor). MutationObserver picks up new <audio> elements
  // as Message components mount.
  useEffect(() => {
    if (!live || typeof document === "undefined") return;

    const tracked = ttsAudioElementsRef.current;

    const refreshPlayingState = () => {
      let anyPlaying = false;
      tracked.forEach((a) => {
        if (!a.paused && !a.ended && a.currentTime > 0) anyPlaying = true;
      });
      ttsPlayingRef.current = anyPlaying;
    };

    const onPlay = () => {
      ttsPlayingRef.current = true;
    };
    const onPauseOrEnded = () => {
      refreshPlayingState();
      // KI-141 — anchor the post-utterance cooldown to the moment TTS
      // actually finished, not to when the user's previous segment closed.
      // This is what the 700 ms cooldown was always meant to gate: the
      // bot's tail decay / echo bleeding back into the mic.
      if (!ttsPlayingRef.current) {
        lastUtteranceEndedAtRef.current = Date.now();
      }
    };

    const attach = (a: HTMLAudioElement) => {
      if (tracked.has(a)) return;
      tracked.add(a);
      a.addEventListener("play", onPlay);
      a.addEventListener("playing", onPlay);
      a.addEventListener("pause", onPauseOrEnded);
      a.addEventListener("ended", onPauseOrEnded);
      a.addEventListener("emptied", onPauseOrEnded);
      // If the element is already playing when we attach, capture that.
      if (!a.paused && !a.ended) ttsPlayingRef.current = true;
    };

    const detach = (a: HTMLAudioElement) => {
      a.removeEventListener("play", onPlay);
      a.removeEventListener("playing", onPlay);
      a.removeEventListener("pause", onPauseOrEnded);
      a.removeEventListener("ended", onPauseOrEnded);
      a.removeEventListener("emptied", onPauseOrEnded);
      tracked.delete(a);
    };

    // Attach to anything already in the DOM.
    document.querySelectorAll("audio").forEach((el) => attach(el as HTMLAudioElement));

    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        m.addedNodes.forEach((n) => {
          if (n instanceof HTMLAudioElement) attach(n);
          else if (n instanceof Element) {
            n.querySelectorAll("audio").forEach((el) => attach(el as HTMLAudioElement));
          }
        });
        m.removedNodes.forEach((n) => {
          if (n instanceof HTMLAudioElement) detach(n);
          else if (n instanceof Element) {
            n.querySelectorAll("audio").forEach((el) => detach(el as HTMLAudioElement));
          }
        });
      }
      refreshPlayingState();
    });
    observer.observe(document.body, { childList: true, subtree: true });

    return () => {
      observer.disconnect();
      tracked.forEach((a) => detach(a));
      tracked.clear();
      ttsPlayingRef.current = false;
    };
  }, [live]);

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
      // KI-139 (2026-05-15) — noise-floor multiplier 2.5 → 1.8. On built-in
      // mics with active noise gate, noiseFloor EMAs to 0 → effectiveThreshold
      // pinned at max(rmsThreshold, 6). User's voice avg sits 18-22 then
      // never crosses. 1.8 keeps headroom for HVAC (which pins at 5-7) but
      // lets quiet voice through.
      const effectiveThreshold = Math.max(
        cfg.rmsThreshold,
        noiseFloorRef.current * 1.8 + 6,
      );

      // KI-057 — suppress new triggers right after we closed a segment
      // (bot's TTS onset can bleed in via the mic loopback).
      // KI-141 — but DO NOT suppress during TTS playback itself; that's
      // exactly when barge-in must work. Cooldown is only meaningful for
      // the brief window after bot speech ends.
      const ttsPlaying = ttsPlayingRef.current;
      const cooldownActive =
        !ttsPlaying &&
        Date.now() - lastUtteranceEndedAtRef.current < cfg.postUtteranceCooldownMs;

      // KI-141 — barge-in must be SNAPPY. During TTS, 2 frames (~30 ms) is
      // enough to confirm voice and pause the bot; the longer 5-frame gate
      // is only needed in idle mode where it rejects click/clack transients.
      const startFrames = ttsPlaying ? 2 : cfg.speechStartFrames;

      const speechLike =
        avg > effectiveThreshold &&
        voiceProp >= cfg.voiceBandMinProp &&
        !cooldownActive;

      if (speechLike) {
        loud++;
        quiet = 0;
        if (loud >= startFrames && !recordingRef.current) {
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
        // KI-141 — also freeze noise-floor learning while TTS is playing.
        // The bot's voice bleeding through the speakers would otherwise be
        // EMA'd into the floor, pulling effectiveThreshold up to bot loudness
        // — at which point the user's voice can't clear it. Holding the
        // pre-TTS noise floor keeps the gate at room-ambient level where
        // user speech reliably crosses.
        if (!recordingRef.current && !ttsPlaying) {
          noiseFloorRef.current =
            noiseFloorRef.current === 0
              ? avg
              : noiseFloorRef.current * 0.95 + avg * 0.05;
        }
        if (quiet === cfg.silenceEndFrames && recordingRef.current) {
          void endSpeechCapture();
        }
        // KI-159 (2026-05-15) — early-close on stable silence after enough
        // captured speech. Protects against transient noise bursts (e.g. a
        // notification ding mid-pause) that would otherwise re-trigger
        // speechLike, zero `quiet`, and extend the segment until either the
        // full silenceEndFrames (180 ≈ 3 s) accumulates AGAIN or the
        // maxUtteranceMs (18 s) hard-cap fires — by which point the real
        // words are buried in a bloated blob that Sarvam STT mis-transcribes
        // or returns empty for.
        //
        // Trigger: half the silence window AND we already have 3× the
        // minUtteranceMs (~1.2 s) of captured speech. Submit the user's
        // words IMMEDIATELY at the first stable pause, before any noise can
        // contaminate the segment.
        else if (
          recordingRef.current &&
          quiet >= Math.floor(cfg.silenceEndFrames / 2) &&
          recStartTsRef.current > 0 &&
          Date.now() - recStartTsRef.current >= cfg.minUtteranceMs * 3
        ) {
          // eslint-disable-next-line no-console
          console.debug(
            "[live-mode] early-close on stable silence (KI-159)",
            { quiet, durationMs: Date.now() - recStartTsRef.current },
          );
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
        // KI-134 (2026-05-15) — Chrome/Safari autoplay policy starts new
        // AudioContexts in state='suspended' when the click that toggled
        // Voice on has already been consumed by React state propagation.
        // Without resume(), the worklet's process() never runs, no PCM
        // frames arrive, and the green pill renders forever with zero
        // audio posted. This is THE canonical "voice on but nothing
        // happens" trap. Resume + bail visibly if the context refuses.
        if (ctx.state === "suspended") {
          try {
            await ctx.resume();
          } catch (e) {
            // eslint-disable-next-line no-console
            console.error("[live-mode] AudioContext resume failed", e);
            setMicPermissionDenied(true);
            setLive(false);
            return;
          }
        }
        // KI-139 (2026-05-15) — Safari iOS resume() can return without
        // throwing yet leave state at "suspended" — silent rejection of the
        // autoplay-policy unlock. Treat anything other than "running" as a
        // failure and surface to the user.
        if (ctx.state !== "running") {
          // eslint-disable-next-line no-console
          console.error("[live-mode] AudioContext state stuck at", ctx.state, "— giving up");
          setMicPermissionDenied(true);
          setLive(false);
          return;
        }
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
          // KI-131 (2026-05-15) — previously, a worklet-setup failure left
          // setMicPermissionDenied=false and live.live=true so the UI pill
          // stayed green ("Voice on — just speak") while no PCM frames were
          // ever delivered — silent functional break. Now flip both states
          // so the pill correctly switches to "🔇 Mic blocked" and the user
          // gets a visible signal that voice is broken on their device.
          // eslint-disable-next-line no-console
          console.error("[live-mode] AudioWorklet setup failed", e);
          setMicPermissionDenied(true);
          setLive(false);
          return;
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
