# ADR-028 — Single default voice mode (Live ✓ + push-to-talk fallback)

**Status:** Accepted — 2026-05-14
**Owner:** Rohit Saraf
**Supersedes:** Implicit prior design with three coexisting modes

## Context

The chat input toolbar evolved over several iterations to support three voice modes simultaneously:

1. **🎤 Push-to-talk** — click mic, talk, click stop.
2. **☐ Hands-free** (checkbox) — auto-open mic, VAD silence-cut, loop on bot's TTS-end.
3. **Live ✓** (toggle) — full-duplex, persistent mic, VAD barge-in (`useLiveConversation`).

The three were NOT mutually exclusive in code. When a user toggled Live on AND clicked the push-to-talk button (a common gesture — the mic icon looked like the universal "speak now" affordance), **both paths captured the same speech in different audio buffers**, produced slightly different transcripts ("Yeah, no, no parent plans..." vs "No, no parent plans..."), and both posted to `/api/chat`. The orchestrator advanced fact-find state twice. Two bot replies came back, both with TTS audio playing simultaneously. The user perceived the bot as broken.

Additionally:
- Hands-free's "auto-reopen mic after bot's TTS ends" loop competed with Live's continuous-mic stream.
- Bot TTS playback used detached `new Audio(url).play()` instances, invisible to the `document.querySelectorAll("audio").pause()` call in Live's barge-in handler → speaking over the bot didn't actually interrupt it.

## Decision

**One voice mode is the default. One fallback path.** Specifically:

| Path | When active | Behavior |
|---|---|---|
| **Live ✓** | Default ON. User can toggle off via the green/red pill (state persists to `localStorage.insurance_live_pref`). | Persistent mic stream + VAD detects speech start/end. Speaking over the bot triggers `interruptBotAudio()` (pauses all DOM audio) + aborts the in-flight `/api/chat`. New utterance starts immediately. |
| **🎤 Push-to-talk** | Always available — clearly labeled "Push-to-talk" button, highlighted emerald when Voice is OFF. | Click → `live.setLive(false)` (releases the persistent mic) → 120 ms wait → fresh one-shot recorder with VAD auto-stop on 2s silence → transcribe + send. On finish, if `userPrefersLive` is still true, `live.setLive(true)` resumes Live. Otherwise stays off. |

**Hands-free is removed entirely.** Its semantics — "VAD cutoff on PTT" — are now the default for PTT. Its other semantics — "re-open mic loop after TTS" — were redundant with Live.

### Barge-in fix

Bot TTS now plays through a ref'd `<audio controls>` element inside the React `Message` component, with autoplay invoked from a mount-only `useEffect`. The element is in the DOM, so `document.querySelectorAll("audio")` finds it and pauses it instantly on VAD-detected user speech. Detached `new Audio()` instances are no longer created anywhere in the playback path.

### Mutual exclusion

`startRecording()` early-returns if `live.live === true` (defensive — `live.setLive(false)` is called *before* it from PTT, so this is a safety net). The 🎤 button is always clickable; the visual treatment (emerald background + ring when `userPrefersLive === false`) draws the user's eye when Live is off.

## Why "always on" instead of a "Go Live" toggle

Earlier iterations required an explicit "Go Live" click. User feedback was that the click step was friction — most users want to just start talking. After this iteration the default flipped to ON, with the toggle available for users who explicitly want voice off (privacy, noisy environment, etc.). Preference persists across reloads.

## Consequences

| Win | Cost |
|---|---|
| Duplicate-mic class of bugs is impossible by construction — only one voice path can dispatch chat at a time | Lost the "press to start recording" UX affordance for users used to it; mitigated by the clearly-labeled PTT button which still works |
| Barge-in actually interrupts the bot mid-sentence — what Live was designed to do | First-time visitors get a mic-permission prompt immediately on page load |
| Single visual indicator (green/red pill + push-to-talk button) is easier to scan than three competing toggles | If user explicitly turns Live OFF, they have to use PTT for every turn until they click the pill back to green — which is the intended behavior |
| Mic-permission-denied users see "🔇 Mic blocked — use 🎤 to speak, or type below" + still get a working PTT + textarea | — |

## Related

- [`frontend/src/lib/useLiveConversation.ts`](../../frontend/src/lib/useLiveConversation.ts) — the always-on voice hook
- [`frontend/src/app/page.tsx`](../../frontend/src/app/page.tsx) — toolbar + PTT integration
- [`frontend/src/app/page.tsx::Message`](../../frontend/src/app/page.tsx) — in-DOM audio playback (barge-in compatibility)
