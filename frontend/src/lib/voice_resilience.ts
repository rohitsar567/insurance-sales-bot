/**
 * voice_resilience — KI-223..228 (2026-05-15).
 *
 * Companion module to useStreamingVoice. Holds pure helpers + small classes
 * that don't need to live inside the hook's body. Keeping them out of the
 * hook keeps the giant useStreamingVoice file readable, and makes the
 * resilience logic independently unit-testable.
 *
 * Contents
 * -------------------------------------------------------------------------
 *  - retryPostTranscribe        — exponential-backoff wrapper around the
 *                                  Sarvam STT POST so a transient network
 *                                  blip / cold start / 502 doesn't drop the
 *                                  user's utterance (V5.4).
 *  - scaleSpeechZcrBand          — derives the speech-band ZCR window for a
 *                                  given AudioContext sampleRate so the
 *                                  fftSize=2048 VAD math from KI-189 keeps
 *                                  meaning when the device delivers 16/24
 *                                  kHz instead of 48 kHz (V1.3).
 *  - AdaptiveNoiseFloor          — rolling EMA of "silent" RMS frames. Used
 *                                  by the barge-in VAD to set a speech
 *                                  threshold that adapts to the actual room
 *                                  (quiet office vs. coffee shop). Replaces
 *                                  the static BARGE_IN_RMS_THRESHOLD on the
 *                                  noise-side; the bot-RMS adaptive piece
 *                                  from KI-190 still rides on top (V6.8).
 *  - VoiceError                  — string-union of new error states the hook
 *                                  can surface to page.tsx so the UI can
 *                                  prompt the user to interact (resume
 *                                  suspended AudioContext) etc. (V1.1).
 */

// ---------------------------------------------------------------------------
// V1.1 — AudioContext suspended / V1.2 — worklet failure error states.
// We don't use AudioWorklet in this hook (the Web Speech API replaced the
// custom PCM worklet path), but the type stays here so a future re-add
// has a slot.
// ---------------------------------------------------------------------------
export type VoiceError =
  | "audio_context_suspended"
  | "worklet_failed"
  | "stream_stale"
  | "transcribe_failed";

// ---------------------------------------------------------------------------
// V5.4 — exponential-backoff transcribe retry.
// ---------------------------------------------------------------------------
export interface RetryOptions {
  maxAttempts?: number;
  baseDelayMs?: number;
  signal?: AbortSignal;
}

/**
 * Wraps an async transcribe call with up to `maxAttempts` retries on
 * network errors. Backs off 1s → 2s → 4s. Aborts propagate immediately
 * (we don't retry a user-initiated abort).
 *
 * The caller passes a thunk that performs the actual POST. The thunk MUST
 * accept its own AbortSignal so each attempt can be individually timed
 * out — we wire one in via the per-attempt controller, while still
 * honouring the outer `opts.signal` so a global cancel kills everything.
 *
 * Returns null when all attempts are exhausted (so the hook can fall back
 * to the Web Speech transcript instead of crashing the utterance).
 */
export async function retryPostTranscribe<T>(
  thunk: (signal: AbortSignal) => Promise<T>,
  opts: RetryOptions = {},
): Promise<T | null> {
  const maxAttempts = opts.maxAttempts ?? 3;
  const baseDelayMs = opts.baseDelayMs ?? 1000;
  const outerSignal = opts.signal;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    if (outerSignal?.aborted) return null;
    const perAttempt = new AbortController();
    const onOuterAbort = () => perAttempt.abort();
    if (outerSignal) outerSignal.addEventListener("abort", onOuterAbort);
    try {
      const result = await thunk(perAttempt.signal);
      if (outerSignal) outerSignal.removeEventListener("abort", onOuterAbort);
      return result;
    } catch (err) {
      if (outerSignal) outerSignal.removeEventListener("abort", onOuterAbort);
      // User-initiated abort — don't retry.
      if (outerSignal?.aborted) return null;
      // Last attempt — surface null so caller can fall back.
      if (attempt === maxAttempts) {
        console.debug("[voice_resilience] retryPostTranscribe exhausted", {
          attempt,
          err: (err as Error)?.message,
        });
        return null;
      }
      const delay = baseDelayMs * Math.pow(2, attempt - 1);
      console.debug("[voice_resilience] retryPostTranscribe attempt failed, backing off", {
        attempt,
        nextDelayMs: delay,
        err: (err as Error)?.message,
      });
      await new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, delay);
        if (outerSignal) {
          outerSignal.addEventListener("abort", () => {
            clearTimeout(t);
            reject(new Error("aborted"));
          }, { once: true });
        }
      }).catch(() => { /* outer abort — fall out of loop */ });
      if (outerSignal?.aborted) return null;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// V1.3 — sample-rate-aware ZCR band.
// The original VAD assumes fftSize=2048 @ 48 kHz, where speech ZCR sits in
// ~20..250 zero crossings per buffer. At 16 kHz the same 2048-sample window
// covers 3x as long in time → ZCR counts scale by sampleRate/48000.
//
// We expose a helper so the hook can compute the band at AudioContext init.
// ---------------------------------------------------------------------------
const REFERENCE_SAMPLE_RATE = 48000;
const REFERENCE_ZCR_MIN = 20;
const REFERENCE_ZCR_MAX = 250;

export function scaleSpeechZcrBand(actualSampleRate: number): { min: number; max: number } {
  if (!actualSampleRate || actualSampleRate <= 0) {
    return { min: REFERENCE_ZCR_MIN, max: REFERENCE_ZCR_MAX };
  }
  // ZCR scales linearly with the time-window length per buffer at fixed
  // fftSize, which scales inversely with sampleRate. So a SHORTER window
  // (higher rate) sees PROPORTIONALLY fewer crossings — but the per-second
  // speech crossing rate is roughly constant. Net: the per-buffer count
  // scales linearly with sampleRate.
  const ratio = actualSampleRate / REFERENCE_SAMPLE_RATE;
  return {
    min: Math.max(1, Math.round(REFERENCE_ZCR_MIN * ratio)),
    max: Math.max(REFERENCE_ZCR_MIN + 1, Math.round(REFERENCE_ZCR_MAX * ratio)),
  };
}

// ---------------------------------------------------------------------------
// V6.8 — adaptive noise-floor estimator.
// Maintains a 5-second EMA of "silent" RMS values. The hook samples this
// every VAD frame; when RMS is below the current speech threshold we treat
// the frame as silent and feed it into the EMA. The current speech
// threshold is `noiseFloor * 4 + 0.005`, clamped to [0.02, 0.15].
//
// Recompute cadence: caller decides. We expose a `currentThreshold()` getter
// + a `feed(rms)` setter. The hook will call feed() every frame and read
// the threshold whenever it needs to compare. Both are O(1).
// ---------------------------------------------------------------------------
const NOISE_EMA_WINDOW_SECONDS = 5;
const NOISE_EMA_ASSUMED_FPS = 60; // rAF default
const NOISE_EMA_ALPHA = 1 / (NOISE_EMA_WINDOW_SECONDS * NOISE_EMA_ASSUMED_FPS);
const NOISE_THRESHOLD_MULTIPLIER = 4;
const NOISE_THRESHOLD_BASE = 0.005;
const NOISE_THRESHOLD_MIN = 0.02;
const NOISE_THRESHOLD_MAX = 0.15;

export class AdaptiveNoiseFloor {
  private ema: number;
  // Track the "current threshold" inline so currentThreshold() stays O(1)
  // without re-running the clamp each call.
  private threshold: number;

  constructor(initialEma: number = 0.005) {
    this.ema = initialEma;
    this.threshold = this.computeThreshold(this.ema);
  }

  /** Feed every VAD-frame RMS. We update the EMA only when the frame is
   *  below the CURRENT threshold (i.e. it looks like silence). This keeps
   *  speech bursts from polluting the noise floor. */
  feed(rms: number): void {
    if (rms < this.threshold) {
      this.ema = (1 - NOISE_EMA_ALPHA) * this.ema + NOISE_EMA_ALPHA * rms;
      this.threshold = this.computeThreshold(this.ema);
    }
  }

  /** Force-reseed (used on session start). */
  reset(initialEma: number = 0.005): void {
    this.ema = initialEma;
    this.threshold = this.computeThreshold(this.ema);
  }

  currentThreshold(): number {
    return this.threshold;
  }

  currentNoiseFloor(): number {
    return this.ema;
  }

  private computeThreshold(ema: number): number {
    const raw = ema * NOISE_THRESHOLD_MULTIPLIER + NOISE_THRESHOLD_BASE;
    if (raw < NOISE_THRESHOLD_MIN) return NOISE_THRESHOLD_MIN;
    if (raw > NOISE_THRESHOLD_MAX) return NOISE_THRESHOLD_MAX;
    return raw;
  }
}
