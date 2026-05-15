# ADR-036 — Voice VAD threshold tuning for consumer-mic noise gates

**Status:** Accepted — 2026-05-15
**Owner:** Rohit Saraf
**Related KIs:** KI-139

## Context

The voice UI pill rendered green ("listening") but no audio frames reached the recorder on multiple consumer setups (MacBook built-in mic, AirPods Pro, Logitech webcam with active noise suppression). The downstream Sarvam ASR call received either silence or a sub-100ms clip and returned an empty transcript. The user saw "green pill, zero audio" — the worst voice-UX failure mode because it's silent: no error toast, no spinner stuck, just no transcript.

Root cause: the in-browser VAD (voice activity detector) thresholds were tuned against a studio-quality reference mic. Three thresholds were too aggressive for consumer mics whose built-in noise gates / DSP heavily attenuate signal before it reaches the AudioWorklet:

- **`rmsThreshold = 26`** — the RMS floor below which a frame is "silence". Consumer noise gates attenuate noise floor AND voice energy together, pulling typical conversational RMS into the 18-24 range.
- **`voiceBandMinProp = 0.50`** — required proportion of energy in the voice band (300-3400 Hz). Noise-gated speech often loses harmonics, dropping voice-band proportion to 0.20-0.35.
- **`noiseFloor multiplier = 2.5`** — required signal-to-floor ratio for "voice detected". With consumer DSP, the noise floor itself is suppressed near-zero, making the 2.5× multiplier unreachable on real voice.

## Decision

Lower all three thresholds to match consumer-mic reality:

| Threshold | Old | New | Rationale |
|---|---|---|---|
| `rmsThreshold` | 26 | **18** | Matches the 18-24 RMS range observed on MacBook built-in + AirPods Pro |
| `voiceBandMinProp` | 0.50 | **0.20** | Accommodates harmonic loss from consumer DSP; still rejects pure tonal noise |
| `noiseFloor` multiplier | 2.5 | **1.8** | Lower the SNR bar; consumer noise gates make 2.5× unreachable on real speech |

Values are in `frontend/voice/vad-worklet.js`. No new configuration surface — the constants are the contract.

## Consequences

| Win | Cost |
|---|---|
| Voice capture works on MacBook built-in, AirPods Pro, Logitech webcam with NS, iPhone Safari — the consumer-default setups | False-positive rate (background TV / room hum triggering capture) goes up ~3-5% on noisy environments. Acceptable: ASR rejects garbage with empty transcript anyway |
| The "green pill, zero audio" silent failure is closed for the dominant consumer mic class | Studio-quality mic users now trigger on slightly quieter ambient — no functional regression, just less margin |
| Thresholds are now grounded in measured consumer-mic data, not a clean-room reference | Any future thresholds (echo cancel, AGC) should be tuned against the same consumer-mic panel, not in isolation |

## Related

- KI-139 — the threshold-tuning commit + the consumer-mic panel that produced the calibration data
- ADR-028 (voice UX single default mode) — the surface this tuning ships into
