"use client";

import React, { useEffect, useRef, useState } from "react";
import {
  audioBlobURLFromBase64,
  BACKEND_URL,
  Citation,
  ChatMessage,
  CompareResponse,
  CoverageResponse,
  getCompare,
  getCoverage,
  getHealth,
  getInsurerReviews,
  getMarketplace,
  getPredictedPremiumBand,
  getProfileCompleteness,
  getScorecard,
  InsurerReviews,
  MarketplacePolicy,
  MarketplaceResponse,
  postChat,
  postPremiumEstimate,
  postProfileUpdate,
  postSessionClear,
  postTranscribe,
  PremiumEstimateResponse,
  PredictedPremiumBandResponse,
  ProfileCompletenessResponse,
  ScorecardResponse,
  uploadPolicy,
  UserProfile,
} from "@/lib/api";
import { translate, UILang, StringKey, GLOSSARY } from "@/lib/i18n";
import PolicyCompareModal, { parseScorecardFacts, SnapshotView, GlossaryTip, fmtSumInsured } from "@/components/PolicyCompareModal";
import PolicyPremiumWidget from "@/components/PolicyPremiumWidget";
import PolicyScorecardWidget from "@/components/PolicyScorecardWidget";
import type { PremiumBulkProfile, BulkScorecardProfile } from "@/lib/api";
// KI-168 (2026-05-15) — voice path migrated from custom-VAD `useLiveConversation`
// to native browser SpeechRecognition via `useStreamingVoice`. The old hook
// remains on disk as a graveyard reference until KI-168 is field-verified.
import { useStreamingVoice } from "@/lib/useStreamingVoice";
import { useIsTouch } from "@/lib/useIsTouch";
import HelpTip from "@/components/HelpTip";

type DisplayMessage = ChatMessage & {
  id: string;
  citations?: Citation[];
  audioUrl?: string;
  brain?: string;
  latencyMs?: number;
  blocked?: boolean;
  // KI-278 (2026-05-16) — when the backend couldn't synthesize voice for
  // this reply (e.g. Sarvam 429 / out of credits), it ships a friendly
  // explanation here. Rendered as a small inline notice under the bubble
  // so a voice-less reply never looks broken/unexplained.
  ttsNotice?: string;
};

export default function Page() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // Voice-display fix — the composer <textarea> must grow as the live
  // interim voice transcript accumulates so the user sees their FULL
  // spoken sentence (not just the first line) while using Push-to-talk.
  // The textarea has resize-none + a small min-height, so without this
  // it stays clamped to one line and the transcript visually truncates
  // even though capture/submit are correct. We size height to scrollHeight
  // (capped by the max-h-32 CSS) and auto-scroll to the newest text.
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // KI-038 (2026-05-14) — voicePhase exposes the otherwise-invisible
  // "between recording and showing the user bubble" Sarvam-STT gap (~1-2s)
  // and the "between user bubble and bot reply" /api/chat gap (~5-15s).
  // null = idle / typed flow, "transcribing" = STT in flight, "thinking" =
  // brain in flight, "speaking" = TTS playing (informational).
  const [voicePhase, setVoicePhase] = useState<null | "transcribing" | "thinking" | "speaking">(null);
  const [recording, setRecording] = useState(false);
  // KI-257 — voice reply + tts language UI toggles removed per user request.
  // returnAudio stays true (bot always replies with audio); ttsLang stays
  // en-IN by default (Sarvam STT auto-detects user's input language; LLM
  // mirrors via SYSTEM_PROMPT RULE 8 Indic mirroring). Underlying state
  // preserved so backend contract is unchanged.
  const [returnAudio] = useState(true);
  const [ttsLang] = useState<"en-IN" | "hi-IN">("en-IN");
  // KI-257 — master Voice toggle. When OFF, the chat input shows only
  // textarea + Send. When ON, reveals the Live (BETA) option and the
  // Push-to-talk button. Persisted via localStorage.
  const [voiceMasterOn, setVoiceMasterOn] = useState(false);
  // #3 mobile — touch vs pointer changes the voice copy ("tap" vs
  // "click" the mic). Shared SSR-safe hook.
  const isTouch = useIsTouch();
  // Live (BETA) risk-confirmation gate. Opening Live always-on must surface
  // a clear, styled WARNING modal listing the real failure modes EVERY time
  // (no localStorage "seen once" bypass — the old window.confirm gate let
  // production users skip the warning entirely after one ack, or whenever
  // the browser suppressed the native dialog). The live session only starts
  // on explicit Confirm; Cancel reverts the toggle. PTT is never gated.
  const [showLiveGate, setShowLiveGate] = useState(false);
  // Visual UI language — same source as ttsLang so the toggle controls both
  const uiLang: UILang = ttsLang === "hi-IN" ? "hi" : "en";
  const t = (key: StringKey, vars?: Record<string, string | number>) => translate(uiLang, key, vars);
  const [health, setHealth] = useState<{ status: string; missing: string[] } | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [showCoverage, setShowCoverage] = useState(false);
  const [showPremium, setShowPremium] = useState(false);
  const [showMarketplace, setShowMarketplace] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  // Admin panel: iframe-embedded LLM control surface. Backend admin API
  // is password-gated (KI-097); the embedded dashboard prompts for the
  // X-Admin-Password header and shows its login view when missing.
  const [showAdmin, setShowAdmin] = useState(false);
  const [marketplace, setMarketplace] = useState<MarketplaceResponse | null>(null);
  const [openPolicy, setOpenPolicy] = useState<MarketplacePolicy | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [profileCompleteness, setProfileCompleteness] = useState<ProfileCompletenessResponse | null>(null);
  // #101 — live profile-completion % reported by the open ProfileBuilderPanel
  // so the header pill shows the SAME number as the panel's progress bar.
  const [liveProfilePct, setLiveProfilePct] = useState<number | null>(null);
  // Predicted-premium BAND chip — sits next to the "X% DONE" profile pill.
  // Refetches reactively on every completeness_pct change (same trigger as
  // the completeness bar) so the user sees their personal premium envelope
  // tighten as they fill in slots. Debounced 500ms to coalesce bursts.
  const [premiumBand, setPremiumBand] = useState<PredictedPremiumBandResponse | null>(null);

  // KI-Z7 (2026-05-15) — Feature B. Welcome-back banner state. Populated
  // when /api/chat returns `returning_user_recalled: true` on the
  // assistant turn that hydrated the session from a stored named-profile.
  // Cleared by either the "Use this profile" / "Update my info" actions
  // OR by handleClearChat.
  const [welcomeBack, setWelcomeBack] = useState<{
    name: string;
    bandText: string | null;
  } | null>(null);

  // Re-fetch profile completeness whenever sessionId changes (after first chat
  // turn) — drives the score-gate on marketplace cards + detail modal.
  useEffect(() => {
    if (typeof window !== "undefined" && sessionId) {
      // KI-118 (2026-05-15) — sessionStorage clears on tab close so no
      // persistent ghost session. Within-tab refresh still rehydrates.
      sessionStorage.setItem("insurance_session_id", sessionId);
      getProfileCompleteness(sessionId)
        .then(setProfileCompleteness)
        .catch(() => setProfileCompleteness(null));
    }
  }, [sessionId]);

  // Debounced refetch of the premium band whenever the profile's
  // completeness_pct shifts. We deliberately key off the percentage (not
  // the whole completeness object) because the underlying signal we care
  // about is "the user answered another slot". 500ms debounce coalesces
  // rapid-fire updates from a single chat turn that fills multiple slots.
  const completenessPct = profileCompleteness?.completeness_pct ?? 0;
  useEffect(() => {
    if (!sessionId) return;
    const handle = setTimeout(() => {
      getPredictedPremiumBand(sessionId)
        .then(setPremiumBand)
        .catch(() => { /* keep prior on transient error */ });
    }, 500);
    return () => clearTimeout(handle);
  }, [sessionId, completenessPct]);

  // Session persistence: rehydrate chat history + sessionId on mount so the
  // user's conversation survives view changes, page reloads, and tab switches.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const savedMessages = localStorage.getItem("insurance_chat_messages");
    if (savedMessages) {
      try {
        const parsed = JSON.parse(savedMessages) as DisplayMessage[];
        if (Array.isArray(parsed) && parsed.length > 0) setMessages(parsed);
      } catch {
        // corrupt cache — wipe so we don't retry
        localStorage.removeItem("insurance_chat_messages");
      }
    }
    // KI-118 (2026-05-15) — sessionStorage clears on tab close. Cross-tab
    // re-entry is name-based: when the user provides their name in chat,
    // the backend pulls the named profile via profile_store.load_profile().
    const savedSession = sessionStorage.getItem("insurance_session_id");
    if (savedSession) setSessionId(savedSession);
  }, []);

  // Persist chat history on every change. Strip transient blob audio URLs —
  // they expire across reloads anyway, and the base64 source is gone.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (messages.length === 0) return; // don't overwrite with an empty array on first render before rehydrate
    const trimmed = messages.map(({ audioUrl: _audioUrl, ...rest }) => rest);
    try {
      localStorage.setItem("insurance_chat_messages", JSON.stringify(trimmed));
    } catch {
      // localStorage full or unavailable — silently drop persistence rather than break the chat
    }
  }, [messages]);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  // ADR-044 (2026-05-27) — extractionInFlight stays true from the moment
  // a PDF starts uploading until the background LLM extraction either
  // completes or hits its hard timeout. Voice auto-submit is gated on
  // this so ambient noise / TTS playback during the 30-60s extraction
  // window can no longer fire an unprompted chat turn.
  const [extractionInFlight, setExtractionInFlight] = useState<boolean>(false);
  // KI-027 (2026-05-14) — voice UX simplification. The legacy `handsFree`
  // mode (its own VAD auto-cutoff + post-turn mic re-open loop) has been
  // removed. We now have exactly two voice paths, mutually exclusive:
  //   • Live ✓ — full-duplex with barge-in (the default, owned by
  //     useLiveConversation).
  //   • 🎤 push-to-talk — click to start, VAD auto-stops on 2s silence,
  //     submits, mic closes. One utterance per click.
  // The "Hands-free continuous loop" was the source of the duplicate-mic
  // bug in the 2026-05-14 user screenshot and added zero value over Live.

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadFrameRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // KI-213 (2026-05-15) — PTT-scoped browser SpeechRecognition. Runs in
  // PARALLEL with the existing MediaRecorder + Sarvam pipeline so the user
  // sees an interim transcript in the chat input as they speak (matching the
  // live-voice UX) instead of staring at an empty input until Sarvam returns.
  // Final Sarvam result still wins; the recognition transcript is only used
  // as a fallback if Sarvam fails or returns empty.
  //
  // Types redeclared locally (instead of imported from useStreamingVoice.ts)
  // because that module doesn't export them and they're tiny — keeping PTT a
  // self-contained path in page.tsx per the existing architecture.
  type PTTSpeechRecognitionAlternative = { transcript: string; confidence: number };
  type PTTSpeechRecognitionResult = {
    isFinal: boolean;
    length: number;
    [index: number]: PTTSpeechRecognitionAlternative;
  };
  type PTTSpeechRecognitionResultList = {
    length: number;
    [index: number]: PTTSpeechRecognitionResult;
  };
  interface PTTSpeechRecognitionEventLike extends Event {
    resultIndex: number;
    results: PTTSpeechRecognitionResultList;
  }
  interface PTTSpeechRecognitionErrorEventLike extends Event {
    error: string;
    message?: string;
  }
  interface PTTSpeechRecognitionInstance extends EventTarget {
    lang: string;
    continuous: boolean;
    interimResults: boolean;
    maxAlternatives: number;
    start: () => void;
    stop: () => void;
    abort: () => void;
    onresult: ((ev: PTTSpeechRecognitionEventLike) => void) | null;
    onerror: ((ev: PTTSpeechRecognitionErrorEventLike) => void) | null;
    onend: ((ev: Event) => void) | null;
    onstart: ((ev: Event) => void) | null;
  }
  type PTTSpeechRecognitionCtor = new () => PTTSpeechRecognitionInstance;
  const pttRecognitionRef = useRef<PTTSpeechRecognitionInstance | null>(null);
  const pttFinalTranscriptRef = useRef<string>("");
  // FIX #17 (A) — accumulator of finalized SR segments across the WHOLE PTT
  // cycle (continuous=false makes Web Speech end+restart on every <1.5s
  // pause; each restart begins a fresh `ev.results` list). Without this the
  // running display rebuilt from only the current session's results loses
  // every earlier finalized word. Mirrors useStreamingVoice's finalsRef.
  const pttFinalSegmentsRef = useRef<string[]>([]);
  // FIX #17 (A) — per-session cursor of how many of THIS recognition
  // session's finals we've already pushed onto pttFinalSegmentsRef, so a
  // re-fired onresult for the same session doesn't double-push the same
  // final index. Reset to 0 on every onstart (new session). Mirrors the
  // hook's finalsConsumedRef intent at session granularity.
  const pttSessionFinalsConsumedRef = useRef<number>(0);
  // FIX #17 (B) — intent flag mirroring useStreamingVoice's wantRunningRef.
  // `true` from just-before rec.start() until any teardown / abort / error
  // early-return. The onend auto-restart and the heartbeat both gate on it
  // so recognition only restarts while we genuinely still want it running.
  const pttSrWantRunningRef = useRef(false);
  // FIX #17 (B) — KI-173-style heartbeat interval handle. Browser
  // SpeechRecognition occasionally enters a stopped state without onend
  // firing; this 3s watchdog re-issues rec.start() while we still want it.
  const pttSrHeartbeatRef = useRef<number | null>(null);

  // KI-168 (2026-05-15) — streaming-voice path replaces the legacy
  // useLiveConversation full-duplex VAD machinery. Interim transcript shows
  // in the chat input as the user speaks; browser silence-detection auto-
  // submits the final transcript through send().
  //
  // KI-165 (2026-05-15) — typed-text request inflight flag, observed by the
  // voice hook so a transcript that finalises while a typed-text turn is
  // racing is dropped silently instead of clobbering the text response.
  // Set to true at the start of `send()`, reset to false in its finally.
  // Use a ref (not state) so the voice hook reads the latest value without
  // re-rendering / re-subscribing.
  const isTextRequestPendingRef = useRef(false);

  // KI-222 FIX 2 (2026-05-15) — AbortController for the in-flight /api/chat
  // call inside send(). triggerBargeIn() (or any code path that wants to
  // cancel a pending bot turn) can fire a `barge-in-abort` window event and
  // the useEffect below will call .abort() on this controller. The signal
  // IS threaded through postChat() → api.ts → fetch (see send()), so the
  // abort genuinely cancels the in-flight /api/chat request.
  const currentSendAbortRef = useRef<AbortController | null>(null);

  // Compatibility surface: the rest of the component (PTT path, UI pill, mic
  // blocked indicator) still references live.live / live.setLive /
  // live.recording / live.micPermissionDenied — preserve that shape so the
  // rename is contained.
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceListening, setVoiceListening] = useState(false);
  const [voicePermDenied, setVoicePermDenied] = useState(false);
  // Hydration guard — `streamingVoice.isSupported` resolves via `typeof
  // window` inside `useStreamingVoice`, so it's `false` on the SSR pass and
  // (typically) `true` on the client. Rendering JSX that branches on it
  // before hydration completes triggers React error #418 (text content
  // mismatch / hydration failure). We pin the SSR + first-client-render
  // output to the same shape, then flip on a post-mount effect.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  // KI-223 (2026-05-15) — V1.1 / V1.2. Structured voice-error banner state.
  // Populated by useStreamingVoice's onVoiceError callback when the hook hits
  // a recoverable failure mode that the user can act on (tap to unlock audio,
  // retry mic, reload page). Auto-dismissed after 8s via the useEffect below.
  // The hook (Phase 2 voice agent) owns emission; page.tsx owns presentation.
  const [voiceErrorBanner, setVoiceErrorBanner] = useState<
    | { type: string; ts: number }
    | null
  >(null);

  // V4 FIX 1 — Live PTT interim transcript. Mirrors the running browser-SR
  // transcript so the user can see what's being captured BELOW the mic
  // button in gray italic (rather than only inside the chat input).
  // Throttled to ~200ms via pttInterimTimerRef so we don't thrash React on
  // every SR partial. Cleared atomically by V4 FIX 3 when the final
  // transcript arrives.
  const [pttInterim, setPttInterim] = useState<string>("");
  const pttInterimTimerRef = useRef<number | null>(null);
  const pttInterimLatestRef = useRef<string>("");
  // Voice-display fix — ref to the PTT interim strip so we can keep it
  // scrolled to the newest words once the accumulated transcript exceeds
  // the strip's capped height. Previously this strip used `truncate`
  // (white-space:nowrap + overflow:hidden) which clipped the live
  // transcript to a single line.
  const pttInterimBoxRef = useRef<HTMLDivElement | null>(null);
  // V4 FIX 2 — dedup window for final transcripts. Some browsers fire the
  // final SpeechRecognition result twice (Safari quirk). Suppress
  // identical strings arriving within 500ms.
  const lastFinalTextRef = useRef<{ text: string; at: number }>({ text: "", at: 0 });
  // V4 FIX 4 — when the input contains a freshly-committed transcript
  // fragment (set programmatically by the voice path, not typed by the
  // user), Backspace should erase the last WORD instead of one character.
  // Tracks whether the current input contents originated from voice;
  // cleared as soon as the user types or sends.
  const inputFromTranscriptRef = useRef<boolean>(false);
  const setInputFromTranscript = (text: string) => {
    inputFromTranscriptRef.current = !!text;
    setInput(text);
  };

  // Submit handler — bound to the latest send() via a ref so the hook
  // doesn't need to re-subscribe on every closure change.
  const voiceSubmitRef = useRef<((text: string) => void) | null>(null);
  const streamingVoice = useStreamingVoice({
    enabled: voiceEnabled,
    language: ttsLang,
    isTextRequestPendingRef,
    onInterimTranscript: (text) => {
      // Show the running transcript in the chat input area as the user speaks.
      // V4 FIX 4 — mark the input as transcript-sourced so Backspace
      // erases the last word, not one character.
      setInputFromTranscript(text);
    },
    onFinalTranscript: (text) => {
      // Browser detected end-of-speech — auto-submit through the regular
      // send() path (which clears the input).
      const submit = voiceSubmitRef.current;
      if (submit) submit(text);
    },
    onError: (msg) => {
      // Surface as an inline assistant message + drop the pill into blocked
      // state if it's a permission failure. Match the legacy
      // micPermissionDenied UX so the existing "🔇 Mic blocked" branch fires.
      if (/Mic permission denied|No microphone/i.test(msg)) {
        setVoicePermDenied(true);
        setVoiceEnabled(false);
      }
      setMessages((m) => [
        ...m,
        { id: `sys_${Date.now()}`, role: "assistant", content: msg },
      ]);
    },
    onListening: setVoiceListening,
    // KI-223 (2026-05-15) — V1.1 / V1.2. Surface recoverable voice failures
    // as a top-right banner. The hook emits one of four error strings; the
    // banner state stamps a fresh `ts` so the auto-dismiss timer restarts on
    // every new emission (useful when the same error fires twice in a row).
    // W1 (2026-05-15) — added "mic_permission_denied". When the hook reports
    // a getUserMedia DOMException (NotAllowedError / NotFoundError / etc.)
    // we also revert the pill back to grey + set the legacy permDenied flag
    // so the "🔇 Mic blocked" branch fires. Without these flips the pill
    // stayed green over a dead mic ("green pill, zero audio") until the user
    // manually toggled off — the exact silent-failure mode this fix targets.
    onVoiceError: (error) => {
      setVoiceErrorBanner({ type: error, ts: Date.now() });
      if (error === "mic_permission_denied") {
        setVoicePermDenied(true);
        setVoiceEnabled(false);
      }
    },
  });

  // Auto-dismiss the voice error banner 8s after the latest emission. Keyed
  // on `ts` so a fresh error mid-countdown resets the clock instead of
  // dismissing early. Cleanup cancels stale timers on unmount / re-render.
  useEffect(() => {
    if (!voiceErrorBanner) return;
    const handle = window.setTimeout(() => setVoiceErrorBanner(null), 8000);
    return () => window.clearTimeout(handle);
  }, [voiceErrorBanner?.ts]);

  // Best-effort AudioContext unlock on user tap. The hook owns its own
  // AudioContext (we can't reach into it), but Chrome's autoplay policy
  // unlocks ALL contexts on the page once a user gesture creates+resumes
  // one. So we spin up a throwaway context, resume it, then close it — the
  // hook's next VAD attach will find its own context unsuspended.
  const resumeAudio = async () => {
    try {
      const Ctor =
        window.AudioContext
        || (window as unknown as { webkitAudioContext?: typeof AudioContext })
          .webkitAudioContext;
      if (!Ctor) return;
      const ctx = new Ctor();
      try {
        await ctx.resume();
      } finally {
        // Close async so we don't block the click handler.
        void ctx.close().catch(() => { /* ignore */ });
      }
    } catch {
      /* ignore — banner stays until 8s timeout */
    } finally {
      setVoiceErrorBanner(null);
    }
  };
  // Legacy-shape adapter so the rest of the file's `live.*` refs keep working.
  const live = {
    live: voiceEnabled,
    recording: voiceListening,
    // Hydration-safe: until the post-mount effect runs, force this to false
    // so the SSR pass and the first client render emit identical JSX (the
    // Voice toggle pill, not the "Mic blocked" badge). After mount we trust
    // the hook's `isSupported` flag (which reads `window.SpeechRecognition`).
    micPermissionDenied: mounted
      ? (voicePermDenied || !streamingVoice.isSupported)
      : false,
    setLive: setVoiceEnabled,
  };

  useEffect(() => {
    getHealth()
      .then((h) => setHealth({ status: h.status, missing: h.missing_keys }))
      .catch(() => setHealth({ status: "unreachable", missing: [] }));
    getCoverage()
      .then(setCoverage)
      .catch(() => setCoverage(null));
    // Initial marketplace pull — no session yet, uses generic baseline scoring
    getMarketplace()
      .then(setMarketplace)
      .catch(() => setMarketplace(null));
  }, []);

  // Re-fetch marketplace WITH session_id whenever profile completeness flips
  // to personalised — backend re-scores each card against the user's profile,
  // so grades reflect "this policy for THIS buyer" rather than the generic
  // baseline. Without this useEffect, the cards stay generic even after
  // profile is saved.
  useEffect(() => {
    if (sessionId && profileCompleteness?.is_personalized) {
      getMarketplace(sessionId)
        .then(setMarketplace)
        .catch(() => {}); // keep prior data on transient errors
    }
  }, [sessionId, profileCompleteness?.is_personalized]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // #42 — auto-grow the composer textarea to fit its FULL content so the
  // live interim voice transcript is visible in its entirety (no string
  // slice, no single-line clamp, no overflow:hidden clip). The textarea has
  // NO Tailwind max-h; instead we cap it here in JS to a generous height
  // that is larger WHILE listening (so a full spoken paragraph shows) and
  // tighter when idle. Only when the accumulated text genuinely exceeds the
  // cap do we enable internal scroll and pin to the newest words. Runs on
  // every `input` change AND when recording toggles so the cap switches
  // live as the user starts/stops speaking.
  useEffect(() => {
    const ta = composerRef.current;
    if (!ta) return;
    // While capturing voice (PTT click or Live), give the box
    // far more room so the entire accumulated interim transcript is on
    // screen. Idle/typed flow keeps a calmer cap. Both are well above one
    // line — the previous max-h-32 (128px) was the perceived "truncation".
    // #91 — the 240px capture cap meant a long spoken query only ever
    // showed its last ~10 lines (scroll pinned to newest), so the user
    // "couldn't see the whole transcript even though it was all captured".
    // While capturing, grow up to ~half the viewport so the full utterance
    // is visible; only the genuinely huge tail still scrolls.
    const isCapturing = recording || live.recording;
    const vh = typeof window !== "undefined" ? window.innerHeight : 800;
    const maxH = isCapturing ? Math.min(Math.round(vh * 0.5), 520) : 160;
    // Collapse first so shrinking text (word-erase / send-clear) recomputes
    // the natural height instead of staying tall.
    ta.style.height = "auto";
    const needed = ta.scrollHeight;
    const applied = Math.min(needed, maxH);
    ta.style.height = `${applied}px`;
    // Enable internal scroll ONLY when content truly overflows the cap;
    // otherwise the whole transcript is shown with no scrollbar. Pin to the
    // newest text (the word just spoken) when we do scroll.
    if (needed > maxH) {
      ta.style.overflowY = "auto";
      ta.scrollTop = ta.scrollHeight;
    } else {
      ta.style.overflowY = "hidden";
    }
  }, [input, recording, live.recording]);

  // Voice-display fix — keep the PTT interim strip pinned to its newest
  // text. The strip now wraps + caps its height + scrolls internally, so
  // as the spoken sentence grows past the cap the user still sees the
  // most-recent words rather than a clipped first line.
  useEffect(() => {
    const box = pttInterimBoxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [pttInterim]);

  // Hands-free continuous loop: when toggled ON, immediately open mic. When
  // toggled OFF, close any in-progress recording. The send() function takes
  // care of re-opening the mic after each assistant TTS finishes.
  // KI-028 + KI-042 (2026-05-14) — Live is now OFF by default on first
  // visit. User feedback: always-on mic was uncontrollable in noisy
  // environments and surprising for first-timers. The user must click the
  // status pill (grey → green) to opt in; preference persists across
  // reloads. userPrefersLive = user's *intent*, persisted; live.live =
  // actual mic state, PTT temporarily flips it to false during a recording
  // even while userPrefersLive stays true (so Live resumes after PTT).
  const [userPrefersLive, setUserPrefersLive] = useState(false);
  // KI-131 (2026-05-15) — voice is OFF by default. Previously persisted
  // "on" preferences are wiped on next load so users with a stale green
  // pill from before this change also see grey and have to re-opt in.
  // This amends ADR-028 "Default ON" — see ADR-033 for rationale.
  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.removeItem("insurance_live_pref");
  }, []);
  // Persist + sync to the live hook whenever the user toggles preference.
  // V3 FIX 3 — if the user clicks the toggle while the bot is mid-sentence,
  // run the interrupt cleanup so the audio stops, the blob is revoked, and
  // the half-painted message gets the "⏸ paused" suffix. Only fires on
  // OFF — toggling ON shouldn't pause anything (there's nothing to pause).
  useEffect(() => {
    if (typeof window !== "undefined") {
      localStorage.setItem("insurance_live_pref", userPrefersLive ? "on" : "off");
    }
    if (!userPrefersLive) {
      try { interruptBotAudio("user-toggle"); } catch { /* ignore */ }
    }
    live.setLive(userPrefersLive);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userPrefersLive]);

  // KI-257 — restore master Voice pref from localStorage on first mount.
  // Master toggle off by default. When master goes OFF we also force
  // userPrefersLive=false so the Live always-on path is suspended.
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const m = localStorage.getItem("insurance_voice_master") === "on";
      if (m) setVoiceMasterOn(true);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => {
    if (typeof window === "undefined") return;
    localStorage.setItem("insurance_voice_master", voiceMasterOn ? "on" : "off");
    if (!voiceMasterOn && userPrefersLive) {
      setUserPrefersLive(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceMasterOn]);

  // V3 FIX 2 + FIX 3 — Hardened TTS interrupt cleanup. When the bot is
  // mid-sentence and the user starts speaking / toggles voice off / clicks
  // PTT, we need to:
  //   (a) pause the currently-mounted <audio> elements,
  //   (b) clear their `src` so the element releases the underlying decoder
  //       and stops buffering further data (just .pause() leaves the blob
  //       attached and Safari can resume autonomously after a tab refocus),
  //   (c) URL.revokeObjectURL() the blob URL so the in-memory blob is GC'd
  //       — without this, every interrupted reply leaks a multi-second WAV.
  //   (d) tag the last assistant message with a gray italic "⏸ paused"
  //       suffix so the user can see WHICH reply they cut off (V3 #3).
  // Safe to call even when nothing is playing — every step is wrapped.
  function interruptBotAudio(reason: "barge-in" | "user-toggle" | "ptt-start") {
    let didPause = false;
    if (typeof document !== "undefined") {
      document.querySelectorAll("audio").forEach((el) => {
        const audioEl = el as HTMLAudioElement;
        const wasPlaying = !audioEl.paused && !audioEl.ended;
        try {
          audioEl.pause();
        } catch { /* ignore */ }
        const src = audioEl.src;
        try {
          if (src && src.startsWith("blob:")) URL.revokeObjectURL(src);
        } catch { /* ignore */ }
        try {
          audioEl.removeAttribute("src");
          // setting empty string makes some browsers attempt a refetch;
          // load() after removing the attribute fully resets the element.
          audioEl.load();
        } catch { /* ignore */ }
        if (wasPlaying) didPause = true;
      });
    }
    // V3 FIX 3 — append "⏸ paused" suffix to the most recent assistant
    // message ONLY if we actually paused mid-playback. We don't want to
    // mark every reply as paused just because the user clicked the toggle
    // before any audio existed. Guard with `didPause` and the existence of
    // a trailing assistant bubble that still has its blob URL.
    if (!didPause) return;
    setMessages((prev) => {
      if (prev.length === 0) return prev;
      const lastIdx = prev.length - 1;
      const last = prev[lastIdx];
      if (last.role !== "assistant") return prev;
      // Idempotent — don't double-append the suffix if the user fires
      // multiple barge-ins back to back.
      if (last.content.endsWith("⏸ paused")) return prev;
      const updated = [...prev];
      updated[lastIdx] = {
        ...last,
        content: `${last.content} ⏸ paused`,
        // Drop the audioUrl so the inline player no longer offers replay
        // of a blob URL we just revoked.
        audioUrl: undefined,
      };
      void reason; // reserved for future telemetry
      return updated;
    });
  }

  // KI-222 FIX 2 (2026-05-15) — listen for the custom "barge-in-abort" DOM
  // event so useStreamingVoice's triggerBargeIn (or any other code path)
  // can cancel an in-flight send() turn. Hook dispatches via
  //   window.dispatchEvent(new CustomEvent("barge-in-abort"))
  // Idempotent: if no request is in-flight, the call is a no-op.
  // V3 FIX 2 — also runs the audio cleanup helper so any in-flight TTS
  // blob is released, not just paused.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onAbort = () => {
      try {
        currentSendAbortRef.current?.abort();
      } catch { /* ignore — controller may already be released */ }
      try {
        interruptBotAudio("barge-in");
      } catch { /* ignore */ }
    };
    window.addEventListener("barge-in-abort", onAbort);
    return () => window.removeEventListener("barge-in-abort", onAbort);
    // interruptBotAudio is referentially stable enough — closures over
    // setMessages (stable) and DOM globals; safe to omit from deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function pushUser(text: string) {
    setMessages((m) => [...m, { id: `u_${Date.now()}`, role: "user", content: text }]);
  }
  function pushAssistant(content: string, extras: Partial<DisplayMessage> = {}) {
    setMessages((m) => [...m, { id: `a_${Date.now()}`, role: "assistant", content, ...extras }]);
  }

  // Last user message that actually went to the LLM (post retry-detection).
  // Used to resolve "try again" / "retry" intent back to the original turn.
  const lastSubmittedTextRef = useRef<string>("");

  // Phrases that mean "resend my previous turn", not "answer this literally".
  function _isRetryIntent(text: string): boolean {
    const s = text.toLowerCase().trim().replace(/[.!?,]+$/, "");
    return [
      "try again", "try that again", "retry", "retry that",
      "say it again", "say that again", "once more", "one more time",
      "repeat", "repeat that", "ek baar aur", "phir se",
    ].includes(s);
  }

  // KI-196 (ADR-041) — Clean Clear-chat semantic.
  //   1. POST /api/session/clear with the current session_id.
  //   2. Adopt the returned new_session_id going forward (sessionStorage).
  //   3. Wipe message array, profile chip, and chat-history localStorage.
  // The legacy `dropProfile` parameter is retained for backwards compatibility
  // with any other callsite — both true and false now route through the new
  // endpoint since the server-side semantic is identical (in-memory wipe +
  // fresh UUID; on-disk profile JSON untouched).
  // #67 — single-active panel model. Exactly ONE side panel can be open at
  // a time. Every open/toggle routes through here so there is no path that
  // leaves two panels (and two close buttons) stacked. `togglePanel` flips
  // the named panel and force-closes the rest; `closeAllPanels` is the
  // shared dismiss.
  type PanelName = "marketplace" | "profile" | "admin";
  const closeAllPanels = () => {
    setShowMarketplace(false);
    setShowProfile(false);
    setShowAdmin(false);
    setShowPremium(false);
    setShowCoverage(false);
  };
  const openPanel = (name: PanelName) => {
    setShowMarketplace(name === "marketplace");
    setShowProfile(name === "profile");
    setShowAdmin(name === "admin");
    setShowPremium(false);
    setShowCoverage(false);
  };
  const togglePanel = (name: PanelName, isOpen: boolean) => {
    if (isOpen) closeAllPanels();
    else openPanel(name);
  };

  async function handleClearChat(_dropProfile: boolean = false) {
    // Always wipe the visible chat + local storage.
    setMessages([]);
    setInput("");
    // KI-Z7 — clear the welcome-back banner so a brand-new visitor doesn't
    // see stale "Welcome back, …" copy after the session reset.
    setWelcomeBack(null);
    // KI-073 (2026-05-15) — clear the profile-completeness chip immediately
    // so the header doesn't show stale "55% DONE" for a brand-new visitor
    // while the new session_id fetch is in flight.
    setProfileCompleteness(null);
    if (typeof window !== "undefined") {
      localStorage.removeItem("insurance_chat_messages");
    }
    // Ask the backend to rotate the session and wipe in-memory state. We
    // always do this, even without a sessionId, so the user gets a guaranteed
    // fresh UUID for their next turn.
    try {
      const res = await postSessionClear({ session_id: sessionId ?? "" });
      setSessionId(res.new_session_id);
      if (typeof window !== "undefined") {
        sessionStorage.setItem("insurance_session_id", res.new_session_id);
      }
    } catch (e) {
      console.warn("session clear failed", e);
      // Even if backend failed, drop client-side session so the next message
      // starts a fresh server-side session (handle_turn mints one when none
      // is supplied).
      setSessionId(undefined);
      if (typeof window !== "undefined") {
        sessionStorage.removeItem("insurance_session_id");
      }
    }
  }

  async function send(text: string) {
    // V4 FIX 6 — empty-message guard. The trim()-check below already
    // covers Enter-on-empty + the disabled Send button; keeping the
    // explicit early-return here so the guard survives any future change
    // that bypasses the input-clear path.
    if (!text.trim() || busy) return;
    // ADR-044 defense-in-depth (2026-05-27) — fail-closed during the
    // upload + extraction window. The Send button is also disabled
    // via the `disabled` prop AND every voice path is gated, but
    // this is the last-line guard so a programmatic / future path
    // can never race the upload-staging flow.
    if (uploadStatus || extractionInFlight) return;
    // KI-204 (2026-05-15) — silence any prior bot TTS BEFORE submitting.
    // User starting a new turn always takes precedence over the bot's
    // current reply audio. Covers typed sends, voice barge-in, manual Send
    // button, programmatic submits — every path through send() gets this.
    // V3 FIX 2 — route through interruptBotAudio so the previous reply's
    // blob URL is revoked (not just paused) and the half-painted message
    // gets the "⏸ paused" suffix.
    try { interruptBotAudio("barge-in"); } catch { /* ignore */ }
    setBusy(true);
    // KI-222 FIX 2 (2026-05-15) — create an AbortController for this turn so
    // a subsequent barge-in (or any external cancel) can interrupt the
    // in-flight /api/chat call. The controller is exposed on
    // currentSendAbortRef; a window-level "barge-in-abort" event listener
    // (see useEffect below) calls .abort() on it.
    // controller.signal is passed to postChat() below → api.ts
    // _fetchWithRetry forwards it to fetch(), so a `barge-in-abort`
    // event genuinely cancels the in-flight /api/chat call; the catch
    // block treats the resulting AbortError as an intentional cancel
    // (stays silent, no error bubble).
    const controller = new AbortController();
    currentSendAbortRef.current = controller;
    // KI-165 (2026-05-15) — flip the text-in-flight flag so the voice hook
    // (useLiveConversation) discards any captures that close during this
    // request. Prevents background notification dings from opening the mic,
    // submitting an empty STT round-trip, and clobbering the typed-text
    // response in the chat pane. Reset in finally regardless of outcome.
    isTextRequestPendingRef.current = true;
    setVoicePhase("thinking"); // KI-038 — show "..." while waiting on brain
    setInput("");

    // Bug C — if the user is asking us to retry, resubmit the previous user
    // turn (the one that actually had policy / fact-find context) instead of
    // hitting Gate-1 with no retrieval.
    let actualText = text;
    if (_isRetryIntent(text) && lastSubmittedTextRef.current) {
      actualText = lastSubmittedTextRef.current;
      // Show the user we understood the retry — surface a system note instead
      // of echoing "try again" back through retrieval.
      pushUser(text);
      pushAssistant(`Retrying: "${actualText}"`, {});
    } else {
      pushUser(text);
    }
    lastSubmittedTextRef.current = actualText;

    try {
      const history: ChatMessage[] = messages.map((m) => ({ role: m.role, content: m.content }));
      // Real-time copilot context — tells the backend what the user is
      // currently looking at, so answers can be grounded in that view rather
      // than asking the user to re-state their context.
      const active_view: "chat" | "marketplace" | "profile" | "premium" | "policy_detail" =
        openPolicy ? "policy_detail" :
        showMarketplace ? "marketplace" :
        showProfile ? "profile" :
        showPremium ? "premium" :
        "chat";
      // V3 FIX 4 — Safari has no webm/opus playback. Detect MediaSource
      // codec support up front and ask the backend for audio/mp4 when the
      // default opus would fail. Falls back to audio/wav (the historical
      // default) when MediaSource isn't available at all (very old
      // browsers / test environments).
      const preferredCodec = (() => {
        if (typeof window === "undefined") return undefined;
        const MS = (window as unknown as { MediaSource?: { isTypeSupported: (t: string) => boolean } }).MediaSource;
        if (!MS || typeof MS.isTypeSupported !== "function") return "audio/wav";
        if (MS.isTypeSupported("audio/webm; codecs=opus")) return "audio/webm; codecs=opus";
        if (MS.isTypeSupported("audio/mp4")) return "audio/mp4";
        return "audio/wav";
      })();
      const res = await postChat({
        user_text: actualText,
        session_id: sessionId,
        chat_history: history,
        return_audio: returnAudio,
        tts_language_code: ttsLang,
        preferred_codec: preferredCodec,
        // KI-222 FIX 2 — thread the abort signal so a barge-in / external
        // cancel actually aborts the in-flight fetch (AbortError is caught
        // below and treated as an intentional, silent cancel).
        signal: controller.signal,
        view_context: {
          active_view,
          active_policy_id: openPolicy?.policy_id,
        },
        onRetry: (attempt) => {
          // Show transient "warming up" hint while postChat retries the
          // cold-started Space behind the scenes. Don't push as a message;
          // use the input area's status string so it doesn't clutter chat.
          setUploadStatus(
            attempt === 1
              ? "Connection slow — retrying…"
              : `Still warming up (attempt ${attempt} of 3)…`,
          );
        },
      });
      setUploadStatus(null);
      setSessionId(res.session_id);
      // Refresh profileCompleteness after every chat turn so that any profile
      // fields the backend extracted from the user's message (age, conditions,
      // budget, etc.) immediately flip `is_personalized` and re-rank the
      // marketplace. Without this, profile updates only land when sessionId
      // changes — which is once, after the first message.
      getProfileCompleteness(res.session_id)
        .then(setProfileCompleteness)
        .catch(() => { /* keep prior on transient error */ });
      // V3 FIX 4 — honour the actual mime the backend produced when present
      // (Safari refuses to play mp4 bytes wrapped in a wav-typed Blob).
      // Falls back to wav for legacy backends that don't echo audio_mime.
      const audioUrl = res.audio_base64
        ? audioBlobURLFromBase64(res.audio_base64, res.audio_mime || "audio/wav")
        : undefined;
      pushAssistant(res.reply_text, {
        citations: res.citations,
        audioUrl,
        brain: res.brain_used,
        latencyMs: res.latency_ms,
        blocked: res.blocked,
        // KI-278 — surface the structured voice-output failure (if any) so
        // the reply doesn't look silently broken. Only set when the backend
        // actually attempted + failed TTS (returnAudio path).
        ttsNotice: res.tts_user_message ?? undefined,
      });
      // KI-Z7 (2026-05-15) — Feature B. Surface the "Welcome back" banner
      // when the backend recalled a stored named-profile on this turn.
      // The predicted-premium band is fetched separately below by the
      // usual completeness-pct effect, but we ALSO pre-load it eagerly
      // here so the banner has a value to render on first paint.
      if (res.returning_user_recalled && res.session_id) {
        getPredictedPremiumBand(res.session_id)
          .then((band) => {
            let bandText: string | null = null;
            if (band && band.min_inr && band.max_inr) {
              const minK = Math.round(band.min_inr / 1000);
              const maxK = Math.round(band.max_inr / 1000);
              bandText = `₹${minK}k-₹${maxK}k/year`;
            }
            // Display name from the freshly-refreshed completeness fetch
            // (it queries the same session.profile we just hydrated).
            getProfileCompleteness(res.session_id)
              .then((pc) => {
                const display =
                  (pc?.profile as { name?: string } | undefined)?.name ||
                  "there";
                setWelcomeBack({ name: display, bandText });
              })
              .catch(() => setWelcomeBack({ name: "there", bandText }));
          })
          .catch(() => setWelcomeBack({ name: "there", bandText: null }));
      }
      // KI-030 (2026-05-14) — playback moved into the in-DOM <audio> element
      // owned by the Message component (autoplay on mount). Detached
      // `new Audio()` instances were invisible to
      // useLiveConversation.interruptBotAudio()'s document.querySelectorAll
      // pause, which broke barge-in. Now the DOM audio handles playback +
      // can be paused by querySelectorAll when the user speaks over the bot.
    } catch (e: unknown) {
      const err = e as { name?: string; message?: string };
      if (err?.name === "AbortError") {
        // Live-mode barge-in cancelled this turn intentionally; stay silent.
        return;
      }
      const msg = err?.message || String(e);
      // Bug A — friendlier message for the cold-start / network failure case
      // that Safari surfaces as "Load failed". Suggest the retry-intent path
      // so the user doesn't lose their actual question.
      if (/Load failed|Failed to fetch|NetworkError|chat failed: 5\d\d/i.test(msg)) {
        pushAssistant(
          `Connection hiccup — the bot may have been sleeping (HF Space cold-start). ` +
          `Say "try again" or tap Send again and I'll re-run your last question.`,
        );
      } else {
        pushAssistant(`Sorry — backend error: ${msg}`);
      }
    } finally {
      setUploadStatus(null);
      setBusy(false);
      setVoicePhase(null);
      // KI-165 (2026-05-15) — clear the text-in-flight flag so subsequent
      // genuine voice captures can be submitted again.
      isTextRequestPendingRef.current = false;
      // KI-222 FIX 2 — release the abort controller now that the request
      // has resolved (or thrown). If a later barge-in event fires after
      // this point there's no in-flight turn to cancel.
      if (currentSendAbortRef.current === controller) {
        currentSendAbortRef.current = null;
      }
    }
  }

  // KI-168 (2026-05-15) — streaming-voice submit binding. The browser does
  // the STT; we just route the finalised transcript through send() so it
  // shares the typed-text code path (history, view_context, retries, TTS
  // reply, profile completeness refresh, etc.). The input clears as part
  // of send().
  useEffect(() => {
    voiceSubmitRef.current = (text: string) => {
      const t = text.trim();
      if (t.length < 2) return;
      // Suppress voice auto-submit while a PDF upload is in flight OR
      // while the background LLM extraction is still running (ADR-044).
      // A long upload + active mic + bot's TTS playing through speakers
      // can otherwise auto-transcribe ambient sound and fire an
      // "unprompted analysis" chat turn that drowns the upload-flow's
      // choice prompt. Real user input still goes through the
      // typed-input path / explicit Push-to-talk press.
      if (uploadStatus || extractionInFlight) return;
      // V4 FIX 2 — dedup repeated finals within 500ms.
      const { text: prevText, at: prevAt } = lastFinalTextRef.current;
      const now = Date.now();
      if (t === prevText && now - prevAt < 500) return;
      lastFinalTextRef.current = { text: t, at: now };
      // Mirror the typed-input flow: drop transcript into the input
      // (so the user sees their final words land in the box for a frame
      // before send() clears it) then submit.
      // V4 FIX 4 — flag the input as transcript-sourced.
      setInputFromTranscript(t);
      void send(t);
    };
    // send() reads `messages` / `sessionId` / `ttsLang` / view flags via
    // closure; rebind whenever they change so the latest values are used.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, sessionId, ttsLang, openPolicy, showMarketplace, showProfile, showPremium, uploadStatus, extractionInFlight]);

  async function startRecording() {
    // KI-222 FIX 1 — silence any prior bot TTS BEFORE PTT recording starts.
    // Mirrors the same pause-all-audio block from send() (KI-204). Without
    // this, the previous reply's <audio> element keeps playing after the
    // user clicks Push-to-talk, and Sarvam transcribes the bot's own voice
    // as user input.
    // V3 FIX 2 — use the unified interrupt helper so the blob URL is
    // revoked and the half-painted message picks up the "⏸ paused" suffix
    // when PTT cuts the bot off mid-sentence.
    try { interruptBotAudio("ptt-start"); } catch { /* ignore */ }
    // KI-027 — Push-to-talk briefly SUSPENDS Live mode (which is otherwise
    // always on). This avoids the duplicate-mic / duplicate-/api/chat bug
    // from the 2026-05-14 screenshot: only one path captures + dispatches
    // any given utterance. Live resumes when PTT finishes (recorder.onstop).
    if (live.live) {
      live.setLive(false);
      // Small wait so useLiveConversation tears down its stream before we
      // open a fresh one; without this, two AudioContexts can briefly grab
      // the same input device.
      // KI-134 (2026-05-15) — bumped 120ms → 400ms because AudioContext.close()
      // returns a Promise that's discarded; on HF Space hardware the previous
      // context can still hold the device when getUserMedia fires, producing
      // a silent stream with no error.
      await new Promise((r) => setTimeout(r, 400));
    }
    try {
      // KI-185 (2026-05-15) — match the useStreamingVoice AEC constraints
      // on the PTT path too, so echo cancellation applies whether the user
      // is in live-voice mode OR push-to-talk.
      // W2 (2026-05-15) — 2s watchdog so a stalled getUserMedia
      // (corporate-locked Chromium, OS-mic-busy, certain Android WebViews)
      // doesn't leave the PTT button red-pulsing over a dead mic with no
      // banner. Mirrors useStreamingVoice.ensureAudioCapture's W2 race.
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
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size > 0) audioChunksRef.current.push(ev.data); };

      // KI-213 (2026-05-15) — start browser SpeechRecognition in parallel
      // for interim transcript display. Sarvam still produces the
      // authoritative transcript; this is purely UX (so the input fills as
      // the user speaks). Best-effort: if SR is unsupported or start() throws
      // we silently continue with the existing Sarvam-only flow.
      pttFinalTranscriptRef.current = "";
      // FIX #17 (A) — reset the cross-session final accumulator + session
      // cursor at the start of every PTT cycle alongside pttFinalTranscriptRef
      // so finalized words from a previous utterance don't leak forward.
      pttFinalSegmentsRef.current = [];
      pttSessionFinalsConsumedRef.current = 0;
      // V4 FIX 1 / FIX 3 — reset both the visible interim strip AND the
      // throttle ref each new PTT cycle so a stale gray-italic transcript
      // from the previous turn doesn't leak through.
      pttInterimLatestRef.current = "";
      setPttInterim("");
      if (pttInterimTimerRef.current !== null) {
        clearTimeout(pttInterimTimerRef.current);
        pttInterimTimerRef.current = null;
      }
      try {
        const w = window as unknown as {
          SpeechRecognition?: PTTSpeechRecognitionCtor;
          webkitSpeechRecognition?: PTTSpeechRecognitionCtor;
        };
        const Ctor = w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
        if (Ctor) {
          const rec = new Ctor();
          rec.continuous = false;
          rec.interimResults = true;
          rec.maxAlternatives = 1;
          // ttsLang is the same locale the live-voice path uses; en-IN is the
          // default fallback per the spec.
          rec.lang = ttsLang || "en-IN";
          rec.onresult = (ev: PTTSpeechRecognitionEventLike) => {
            // FIX #17 (A) — accumulate finals across the WHOLE PTT cycle.
            // continuous=false ends+restarts SR on every <1.5s pause, and
            // each restart begins a fresh `ev.results` list. The old code
            // rebuilt the display from ONLY the current session's results
            // and OVERWROTE pttFinalTranscriptRef with `final`, so every
            // earlier finalized word was lost. Mirror useStreamingVoice:
            // walk this session's results, push NOT-YET-CONSUMED finals
            // onto the cross-session accumulator (tracking a per-session
            // cursor so a re-fired onresult can't double-push), and build
            // the display from accumulator + live interim.
            let interim = "";
            let sessionFinalCount = 0;
            for (let i = 0; i < ev.results.length; i++) {
              const r = ev.results[i];
              const alt = r[0];
              if (!alt) continue;
              if (r.isFinal) {
                // The i-th final result of THIS session. Only push it once:
                // sessionFinalCount counts finals seen so far in this event;
                // pttSessionFinalsConsumedRef is how many of this session's
                // finals are already in the accumulator.
                if (sessionFinalCount >= pttSessionFinalsConsumedRef.current) {
                  const t = alt.transcript.trim();
                  if (t) pttFinalSegmentsRef.current.push(t);
                  pttSessionFinalsConsumedRef.current = sessionFinalCount + 1;
                }
                sessionFinalCount += 1;
              } else {
                interim += alt.transcript;
              }
            }
            const accumulated = pttFinalSegmentsRef.current.join(" ").trim();
            // V4 FIX 2 — dedup repeated finals within 500ms (kept for the
            // Safari double-final quirk; now compares the ACCUMULATED text
            // rather than only the last session's final).
            if (accumulated) {
              const { text: prevText, at: prevAt } = lastFinalTextRef.current;
              const now = Date.now();
              if (accumulated !== prevText || now - prevAt > 500) {
                lastFinalTextRef.current = { text: accumulated, at: now };
              }
              // FIX #17 (A) — Sarvam fallback transcript is the ACCUMULATED
              // text (all finalized words this cycle), not last-session-only.
              pttFinalTranscriptRef.current = accumulated;
            }
            const display = [accumulated, interim.trim()]
              .filter(Boolean)
              .join(" ")
              .trim();
            // V4 FIX 4 — interim transcript flows into the input as a
            // transcript-sourced fragment so Backspace can word-erase.
            if (display) setInputFromTranscript(display);
            // V4 FIX 1 — feed the below-mic ghost-italic display. Throttle
            // to 200ms so very chatty SR engines (Chrome fires ~20 partials/s
            // on fast speakers) don't thrash React.
            pttInterimLatestRef.current = display;
            if (pttInterimTimerRef.current === null) {
              pttInterimTimerRef.current = window.setTimeout(() => {
                setPttInterim(pttInterimLatestRef.current);
                pttInterimTimerRef.current = null;
              }, 200);
            }
          };
          // FIX #17 (A) — each recognition session restarts with a fresh
          // `ev.results` list, so the per-session consumed cursor must reset
          // to 0 at the start of every session (mirrors the hook resetting
          // its session view on restart). The cross-session accumulator
          // (pttFinalSegmentsRef) is NOT reset here — only on PTT-cycle start.
          rec.onstart = () => { pttSessionFinalsConsumedRef.current = 0; };
          rec.onerror = (ev: PTTSpeechRecognitionErrorEventLike) => {
            // FIX #17 (B) — on a terminal permission/device error, stop
            // wanting to run so the onend auto-restart + heartbeat don't
            // fight a dead device. `no-speech` / `aborted` are routine in
            // continuous=false restart mode — keep wanting, onend revives.
            const code = ev.error;
            if (code === "not-allowed" || code === "service-not-allowed" || code === "audio-capture") {
              pttSrWantRunningRef.current = false;
            }
            /* otherwise best-effort — Sarvam remains the source of truth */
          };
          // FIX #17 (B) — continuous=false makes Web Speech END after the
          // first ~1.5s pause. The old no-op onend meant recognition was
          // NEVER restarted, so the live interim froze mid-utterance. Port
          // useStreamingVoice's restart contract: only restart while we
          // still WANT to be running; swallow InvalidStateError (already
          // running) since the heartbeat covers genuinely-dead states.
          rec.onend = () => {
            if (!pttSrWantRunningRef.current) return;
            try { rec.start(); } catch { /* InvalidState — heartbeat covers */ }
          };
          pttRecognitionRef.current = rec;
          // FIX #17 (B) — declare intent to be running BEFORE start() so a
          // synchronous onend (some engines) sees want=true and restarts.
          pttSrWantRunningRef.current = true;
          rec.start();
          // FIX #17 (B) — KI-173-style 3s heartbeat. SpeechRecognition can
          // enter a stopped state without onend firing (transient OS audio
          // interruption, tab visibility edge cases); the onend restart
          // never gets a chance to run and the mic stays silently dead.
          // While we're recording AND still want SR running, re-issue
          // start() unconditionally — InvalidStateError (already running)
          // is swallowed, otherwise this revives the dead state.
          if (pttSrHeartbeatRef.current !== null) {
            clearInterval(pttSrHeartbeatRef.current);
            pttSrHeartbeatRef.current = null;
          }
          pttSrHeartbeatRef.current = window.setInterval(() => {
            if (!pttSrWantRunningRef.current) return;
            const r = pttRecognitionRef.current;
            if (!r) return;
            try { r.start(); } catch { /* already running — fine */ }
          }, 3000);
        }
      } catch {
        // SR unavailable or already running — fall through, Sarvam still works.
        pttRecognitionRef.current = null;
      }
      recorder.onstop = async () => {
        stopVAD();
        stream.getTracks().forEach((t) => t.stop());
        // KI-213 (2026-05-15) — tear down the parallel SpeechRecognition.
        // abort() is preferred over stop() to avoid a trailing onresult
        // event firing AFTER we've already set the input to the Sarvam
        // transcript (which would clobber it). The final transcript captured
        // so far is preserved in pttFinalTranscriptRef as a Sarvam fallback.
        // FIX #17 (B) — stop WANTING recognition to run BEFORE abort() so
        // the onend auto-restart (which fires on abort) and the heartbeat
        // both no-op instead of reviving a recognition we're tearing down.
        pttSrWantRunningRef.current = false;
        if (pttSrHeartbeatRef.current !== null) {
          clearInterval(pttSrHeartbeatRef.current);
          pttSrHeartbeatRef.current = null;
        }
        const sr = pttRecognitionRef.current;
        pttRecognitionRef.current = null;
        if (sr) {
          try { sr.abort(); } catch { /* already stopped */ }
        }
        // FIX #17 (C) — trailing flush. The 200ms throttle only SCHEDULES a
        // setPttInterim when no timer is pending; the last burst of words
        // arriving while a timer is still pending (or right at release) was
        // dropped because the block below clears it. Flush the latest value
        // once more so the final spoken words are visible until the Sarvam
        // authoritative transcript replaces them.
        const lastInterim = pttInterimLatestRef.current;
        // V4 FIX 3 — atomically clear the interim ghost text (both the
        // pending throttled update AND any visible state). Without this,
        // the gray-italic strip below the mic can keep showing the last
        // partial transcript after the final has already been committed.
        if (pttInterimTimerRef.current !== null) {
          clearTimeout(pttInterimTimerRef.current);
          pttInterimTimerRef.current = null;
        }
        pttInterimLatestRef.current = "";
        // FIX #17 (C) — push the last burst into the input as a
        // transcript-sourced fragment (Backspace word-erase still works)
        // so it isn't lost; it is replaced by the Sarvam authoritative
        // text below the moment transcription completes.
        if (lastInterim) setInputFromTranscript(lastInterim);
        setPttInterim("");
        const srFallback = pttFinalTranscriptRef.current.trim();
        const blob = new Blob(audioChunksRef.current, { type: recorder.mimeType || "audio/webm" });
        setRecording(false);
        // KI-028 — Resume Live ONLY if the user's persistent preference is
        // still "on". If the user explicitly turned Live off (red dot), PTT
        // does its one turn and leaves Live off. They'll keep using PTT
        // until they click the dot back to green themselves.
        const maybeResumeLive = () => { if (userPrefersLive) live.setLive(true); };
        // KI-134 (2026-05-15) — surface the silent-recording case to the user
        // instead of returning quietly. Previously, holding PTT briefly and
        // releasing produced no feedback at all; now they at least see why.
        if (blob.size < 1000) {
          // KI-213 — clear the lingering interim that SR may have left in the
          // input so the user isn't confused by a stale partial transcript.
          setInput("");
          pushAssistant("Didn't catch any audio — try holding the mic button while speaking.");
          maybeResumeLive();
          return;
        }
        setBusy(true);
        setVoicePhase("transcribing"); // KI-038 — STT in flight on PTT
        // KI-213 — keep the interim transcript visible in the input as a
        // placeholder while Sarvam runs. send() will clear it when (and only
        // when) we actually submit, so the user sees their words throughout.
        try {
          const tr = await postTranscribe(blob, ttsLang);
          // KI-242 — Backend now returns HTTP 200 + error_code on Sarvam
          // failures (rate_limit / service_unavailable / network / auth /
          // unknown). Prefer the SR fallback transcript when present so the
          // turn still goes through; otherwise surface the friendly
          // user_message and DO NOT call send() with empty text.
          // ADR-044 — defensive: suppress voice-driven send() during the
          // upload-index window AND while the background LLM extraction
          // is still running. Even an explicit PTT press shouldn't drop
          // a chat turn into the middle of the wait — the user can
          // re-press once the card lands. Mirrors the voiceSubmitRef
          // guard for the browser SpeechRecognition path.
          const __voiceBlocked = uploadStatus || extractionInFlight;
          if (__voiceBlocked) {
            setInput("");
            maybeResumeLive();
            return;
          }
          if (tr.error_code) {
            if (srFallback) {
              setInputFromTranscript(srFallback);
              try { await send(srFallback); } catch { /* send handles its own errors */ }
            } else {
              setInput("");
              pushAssistant(
                tr.user_message ||
                  "Couldn't transcribe that — please try again or type your question.",
              );
            }
          } else if (tr.text && tr.text.trim()) {
            // KI-213 — replace the interim SR transcript with Sarvam's
            // authoritative version, then submit.
            // #105 — the live composer shows the Web-Speech INTERIM (instant
            // but approximate / shorter); the SENT message is this Sarvam
            // authoritative text (fuller, punctuated). Before, setInput then
            // immediate send() cleared it in the same tick, so the user only
            // ever saw the shorter interim and read it as "truncated". Show
            // the full authoritative transcript for a perceptible beat so
            // what they see == what is sent, THEN submit.
            // V4 FIX 4 — transcript-sourced.
            setInputFromTranscript(tr.text);
            await new Promise((r) => setTimeout(r, 380));
            // send() flips voicePhase to "thinking" itself; no need to set here
            await send(tr.text);
          } else if (srFallback) {
            // KI-213 — Sarvam returned empty but the browser caught
            // something. Better than telling the user "couldn't hear that
            // clearly" when we actually have a usable transcript.
            setInputFromTranscript(srFallback);
            await send(srFallback);
          } else {
            setInput("");
            pushAssistant("Sorry, I couldn't hear that clearly. Please try again.");
          }
        } catch (e: unknown) {
          // KI-242 — postTranscribe only throws on transport-level failures
          // now (5xx from a different middlebox, abort, etc.). Sarvam errors
          // arrive as HTTP 200 + error_code above and never reach this
          // branch. Fall back to SR if we have one; else show a friendly
          // generic message — NEVER leak raw httpx text to the user.
          void e;
          if (srFallback) {
            setInputFromTranscript(srFallback);
            try { await send(srFallback); } catch { /* send handles its own errors */ }
          } else {
            setInput("");
            pushAssistant(
              "Voice service is temporarily unavailable — please type your question or try voice again shortly.",
            );
          }
        } finally {
          setBusy(false);
          setVoicePhase(null);
          maybeResumeLive();
        }
      };
      recorder.start();
      // W2 (2026-05-15) — affirmative post-acquire validation. recorder.start()
      // returns void and does NOT throw on a fake-mic / dead-stream / codec
      // rejection; the only reliable signal is recorder.state. Without this
      // check, Playwright's fake stream let the button flip to red-pulsing
      // "Stop" over a silent capture with no banner. Treat any non-"recording"
      // state as a hard fail and surface mic_permission_denied.
      if (recorder.state !== "recording") {
        try { stream.getTracks().forEach((t) => t.stop()); } catch { /* ignore */ }
        mediaRecorderRef.current = null;
        throw Object.assign(
          new Error(`MediaRecorder did not enter recording state (got ${recorder.state})`),
          { name: "RecorderNotRecording" },
        );
      }
      setRecording(true);

      // KI-027 — VAD auto-cutoff is now ALWAYS on for the push-to-talk
      // fallback. Click the mic, talk, and the recording auto-stops after
      // ~2s of silence. Was previously gated behind `handsFree` which we
      // removed. Push-to-talk is one-shot per click — no auto re-open.
      if (true) {
        try {
          const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
          const audioCtx = new AC();
          const source = audioCtx.createMediaStreamSource(stream);
          const analyser = audioCtx.createAnalyser();
          analyser.fftSize = 1024;
          source.connect(analyser);
          audioContextRef.current = audioCtx;
          analyserRef.current = analyser;
          silenceStartRef.current = null;
          const recordingStartTime = Date.now();
          const buf = new Uint8Array(analyser.fftSize);
          // Tuned 2026-05-13 after live bug report — VAD was cutting off too early
          // on quiet speakers. Require minimum 1.5s of recording before allowing
          // auto-stop; raise silence threshold + duration.
          const MIN_RECORDING_MS = 1500;
          const SILENCE_THRESHOLD = 0.012;       // RMS — was 0.018; lowered to allow quieter voices
          const SILENCE_DURATION_MS = 2000;       // was 1500
          const tick = () => {
            if (!analyserRef.current) return;
            analyser.getByteTimeDomainData(buf);
            let sumSquares = 0;
            for (let i = 0; i < buf.length; i++) {
              const v = (buf[i] - 128) / 128;
              sumSquares += v * v;
            }
            const rms = Math.sqrt(sumSquares / buf.length);
            const now = Date.now();
            const recordedFor = now - recordingStartTime;

            if (recordedFor < MIN_RECORDING_MS) {
              // Force-keep recording — user might still be inhaling
              silenceStartRef.current = null;
            } else if (rms < SILENCE_THRESHOLD) {
              if (silenceStartRef.current === null) silenceStartRef.current = now;
              else if (now - silenceStartRef.current > SILENCE_DURATION_MS) {
                stopRecording();
                return;
              }
            } else {
              silenceStartRef.current = null;
            }
            vadFrameRef.current = requestAnimationFrame(tick);
          };
          vadFrameRef.current = requestAnimationFrame(tick);
        } catch (err) {
          console.warn("VAD setup failed; falling back to manual stop", err);
        }
      }
    } catch (e) {
      console.error(e);
      // FIX #17 (B) — if we threw AFTER wiring up the parallel SR (e.g. the
      // W2 `recorder.state !== "recording"` hard-fail), the SR + 3s
      // heartbeat were already started. Stop wanting it, clear the
      // heartbeat, and abort recognition so the mic doesn't stay silently
      // alive behind a failed recorder.
      pttSrWantRunningRef.current = false;
      if (pttSrHeartbeatRef.current !== null) {
        clearInterval(pttSrHeartbeatRef.current);
        pttSrHeartbeatRef.current = null;
      }
      if (pttRecognitionRef.current) {
        try { pttRecognitionRef.current.abort(); } catch { /* already stopped */ }
        pttRecognitionRef.current = null;
      }
      // W2 (2026-05-15) — route to the structured banner the same way the
      // live-voice path does, so PTT denials / stalls / fake-stream failures
      // surface in the same red banner (not just an in-chat assistant
      // message). Also revert the button by clearing recording state and
      // setting voicePermDenied so the "🔇 Mic blocked" branch fires.
      setRecording(false);
      setVoicePermDenied(true);
      setVoiceErrorBanner({ type: "mic_permission_denied", ts: Date.now() });
      pushAssistant(`Sorry — mic permission denied or unavailable.`);
    }
  }
  function stopVAD() {
    if (vadFrameRef.current !== null) cancelAnimationFrame(vadFrameRef.current);
    vadFrameRef.current = null;
    silenceStartRef.current = null;
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {});
      audioContextRef.current = null;
    }
    analyserRef.current = null;
  }
  function stopRecording() { mediaRecorderRef.current?.stop(); }

  async function handleFile(ev: React.ChangeEvent<HTMLInputElement>) {
    const f = ev.target.files?.[0];
    if (!f) return;
    // ADR-044 (2026-05-27) — new staged upload flow:
    //   1. POST /api/upload-policy → indexes + persists + kicks the
    //      background LLM extraction.
    //   2. push assistant ack (NO card yet — we don't render the card
    //      on the partial heuristic record).
    //   3. push choice prompt (finish profile / dive into PDF).
    //   4. poll /api/upload/extraction-status/{id} every 3s for up to
    //      120s. While polling, `extractionInFlight=true` blocks voice
    //      auto-submit so ambient sound during the wait can't trigger
    //      an "unprompted please-upload" chat turn.
    //   5. when status === "complete", push a NEW assistant message
    //      with the citations → card renders inline at that point
    //      with FULL data (catalogued-grade depth).
    //   6. on "failed" / timeout, push a fallback ack with whatever
    //      heuristic data we have, so the user is never stranded.
    setUploadStatus(t("upload.indexing", { name: f.name }));
    setExtractionInFlight(true);
    try {
      // Pass the live chat session so the backend scopes the uploaded doc
      // to this user — the assistant can then answer questions about it
      // for the rest of THIS conversation.
      const r = await uploadPolicy(f, sessionId);
      setUploadStatus(t("upload.success", { name: r.policy_name }));
      // Step 2 — ack ONLY (no card, no choice prompt yet). Per user
      // directive: nothing else surfaces in chat until the card is
      // fully populated and ready to render.
      pushAssistant(t("upload.chat_ack_reading", { name: r.policy_name }));
      // Refresh coverage so the uploaded doc shows up
      getCoverage().then(setCoverage).catch(() => {});

      // Step 4 — poll extraction status until COMPLETE / FAILED / TIMEOUT.
      // Per user directive (2026-05-27): NO choice prompt, NO card,
      // NOTHING else fires during this wait — the user is asked to wait,
      // the Send button + voice paths are all gated by extractionInFlight,
      // and the chat only progresses once the card data is fully populated.
      const POLL_INTERVAL_MS = 3000;
      const MAX_TRIES = 40; // 40 × 3s = 120s
      let landed = false;
      let finalCompleteness: number | null = null;
      let finalGrade: string | null = null;
      let finalInsurerSlug: string = r.policy_id.startsWith("user-upload__") ? "user-upload" : "";
      for (let i = 0; i < MAX_TRIES; i++) {
        try {
          const resp = await fetch(
            `${BACKEND_URL}/api/upload/extraction-status/${encodeURIComponent(r.policy_id)}`,
          );
          if (resp.ok) {
            const s = await resp.json();
            if (s.status === "complete") {
              landed = true;
              finalCompleteness = s.completeness_pct ?? null;
              finalGrade = s.overall_grade ?? null;
              finalInsurerSlug = s.insurer_slug || finalInsurerSlug;
              break;
            }
            if (s.status === "failed") {
              break;
            }
            // pending / running / unknown — keep polling
            if (s.insurer_slug) finalInsurerSlug = s.insurer_slug;
          }
        } catch (_) {
          // tolerant of transient fetch errors; keep polling
        }
        await new Promise((res) => setTimeout(res, POLL_INTERVAL_MS));
      }

      // Step 5 — push the card-bearing assistant message + THEN the
      // choice prompt. Order matters — the user explicitly directed
      // (2026-05-27): no choice prompt until the card has landed, so
      // they see the full picture before being asked what to do next.
      if (landed) {
        pushAssistant(
          t("upload.chat_card_ready", { name: r.policy_name }),
          {
            citations: [
              {
                policy_id: r.policy_id,
                policy_name: r.policy_name,
                insurer_slug: finalInsurerSlug || "user-upload",
                page_start: 1,
                page_end: r.pages_indexed,
                source_url: "",
                score: 1.0,
              },
            ],
          },
        );
        // Choice prompt fires AFTER the card, not before — that was the
        // race the previous flow exhibited.
        pushAssistant(t("upload.chat_choice"));
      } else {
        // Failure / timeout fallback: surface honestly + still defer the
        // choice prompt (we never want to ask the user to "dive into the
        // PDF" before they can SEE the analysis).
        pushAssistant(
          t("upload.chat_extraction_failed", { name: r.policy_name }),
        );
        pushAssistant(t("upload.chat_choice"));
      }
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? e.message : String(e);
      setUploadStatus(t("upload.error", { err: errMsg }));
      // Surface the failure in chat too.
      pushAssistant(t("upload.error", { err: errMsg }));
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
      setTimeout(() => setUploadStatus(null), 8000);
      setExtractionInFlight(false);
    }
  }

  // V4 FIX 5 — `min-h-[100dvh]` uses the dynamic viewport unit so the
  // layout shrinks correctly when the iOS soft keyboard opens (vs the
  // legacy `100vh` which stays fixed and pushes the composer behind the
  // keyboard). `min-h-screen` is kept as a fallback for browsers that
  // don't understand `dvh`.
  return (
    <div className="min-h-screen min-h-[100dvh] flex flex-col bg-[var(--background)] text-[var(--foreground)]">
      {/* KI-223 (2026-05-15) — V1.1 / V1.2. Voice-error banner pinned to the
          top-right. On mobile (<sm), the `left-4 right-4` + `max-w-[…]`
          combination keeps the banner inside the viewport without overlapping
          the chat composer (composer sits at the bottom). `pointer-events`
          only active on the banner itself, so clicks elsewhere on the page
          aren't blocked. */}
      {voiceErrorBanner && (
        <div
          role="alert"
          className={`fixed top-4 left-4 right-4 sm:left-auto sm:right-4 sm:max-w-sm z-50 rounded-lg shadow-lg border px-4 py-3 text-sm flex items-start gap-3 ${
            voiceErrorBanner.type === "audio_context_suspended"
              ? "bg-yellow-50 border-yellow-300 text-yellow-900 cursor-pointer hover:bg-yellow-100"
              : "bg-red-50 border-red-300 text-red-900"
          }`}
          onClick={
            voiceErrorBanner.type === "audio_context_suspended"
              ? () => { void resumeAudio(); }
              : undefined
          }
        >
          <div className="flex-1 leading-snug">
            {voiceErrorBanner.type === "audio_context_suspended" && (
              <span>Tap anywhere to enable audio</span>
            )}
            {voiceErrorBanner.type === "transcribe_failed" && (
              <span>
                Voice transcription failed — using text-only for now. Try the
                mic again or type your message.
              </span>
            )}
            {voiceErrorBanner.type === "worklet_failed" && (
              <span>Audio capture failed — please reload the page.</span>
            )}
            {/* W1 (2026-05-15) — silent getUserMedia denial. The default
                non-yellow branch above already renders a red background;
                we just supply the human-readable copy here. Triggered when
                the user (or browser policy / OS / another app holding the
                mic) rejects the permission prompt. */}
            {voiceErrorBanner.type === "mic_permission_denied" && (
              <span>
                Microphone access denied. Click the lock icon next to the URL
                bar and allow microphone, then reload.
              </span>
            )}
          </div>
          <button
            type="button"
            aria-label="Dismiss"
            className="shrink-0 -mr-1 -mt-1 px-1.5 py-0.5 rounded text-base leading-none opacity-60 hover:opacity-100"
            onClick={(e) => {
              e.stopPropagation();
              setVoiceErrorBanner(null);
            }}
          >
            ×
          </button>
        </div>
      )}
      <header className="border-b border-[var(--border)] bg-[var(--card)]">
        {/* Polish pass (2026-05-15): tagline + subtitle removed from this
            row — the landing hero already carries the brand voice. The h1
            is now a compact, single-line brand mark so the chip row never
            wraps mid-text and each chip can lay out as a clean tile. */}
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex items-center gap-3 sm:gap-4 flex-wrap">
          <div className="flex items-center gap-2.5 shrink-0">
            <div className="w-9 h-9 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-bold text-sm shadow-sm">IA</div>
            <h1 className="font-semibold text-sm sm:text-base leading-tight tracking-tight whitespace-nowrap">
              {uiLang === "hi" ? "बीमा सलाहकार" : "Insurance Advisor"}
            </h1>
          </div>
          {/* #49 — on phones this row scrolls horizontally (chip-row-scroll)
              instead of wrapping into a tall ragged stack. */}
          <div className="chip-row-scroll flex items-center gap-2 sm:gap-2.5 flex-wrap ml-auto">
            <button
              onClick={() => togglePanel("marketplace", showMarketplace)}
              className={`chip-tile group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
                showMarketplace
                  ? "ring-2 ring-[var(--primary)]"
                  : ""
              }`}
              title="Browse all indexed policies"
            >
              <div className="absolute inset-0 bg-gradient-to-br from-teal-600 via-teal-500 to-emerald-500 dark:from-teal-700 dark:via-teal-600 dark:to-emerald-600" />
              <div className="relative flex items-stretch text-white">
                <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                  <LibraryIcon />
                </div>
                <div className="px-3 py-2 text-left">
                  <div className="text-[11px] uppercase tracking-wider font-semibold leading-none whitespace-nowrap">
                    {uiLang === "hi" ? "पॉलिसी लाइब्रेरी" : "Policy Library"}
                  </div>
                  <div className="text-[12px] leading-tight whitespace-nowrap opacity-90 mt-1">
                    {coverage
                      ? (uiLang === "hi"
                          ? `${coverage.total_policies} plans · ${coverage.total_insurers} insurers`
                          : `${coverage.total_policies} plans · ${coverage.total_insurers} insurers`)
                      : (uiLang === "hi" ? "Loading…" : "Loading…")}
                  </div>
                </div>
              </div>
            </button>
            {/* #60 (2026-05-16) — the profile chip and the premium-range chip
                were two separate pills that BOTH opened the same merged
                Profile & premium view (#47c). Unified into ONE pill with two
                segments (profile | premium); the whole pill opens the merged
                view. */}
            {(() => {
              const hasMeaningfulBand =
                profileCompleteness &&
                profileCompleteness.completeness_pct >= 50 &&
                premiumBand &&
                premiumBand.sample_size > 0;
              return (
              <button
                type="button"
                onClick={() => togglePanel("profile", showProfile)}
                className={`chip-tile group relative overflow-hidden rounded-xl shadow-sm transition-all hover:shadow-md ${
                  showProfile ? "ring-2 ring-[var(--primary)]" : ""
                }`}
                title={uiLang === "hi"
                  ? `आपकी profile + similar buyers जो typically pay करते हैं वो range${premiumBand?.sum_insured_used ? ` (₹${premiumBand.sum_insured_used.toLocaleString("en-IN")} cover पर)` : ""}. किसी एक specific plan का live premium इस typical range से ऊपर/नीचे हो सकता है — ये सामान्य है।`
                  : `Your profile + the TYPICAL range similar buyers pay${premiumBand?.sum_insured_used ? ` (priced at ₹${premiumBand.sum_insured_used.toLocaleString("en-IN")} cover)` : ""}. A specific plan's live premium can sit above or below this typical range — that's expected, not a contradiction.`}
              >
                <div className="relative flex items-stretch text-white">
                  {/* Profile segment */}
                  <div className="flex items-stretch bg-gradient-to-br from-violet-600 via-purple-600 to-fuchsia-600">
                    <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2" /></svg>
                    </div>
                    <div className="px-3 py-2 text-left">
                      <div className="text-[11px] uppercase tracking-wider font-semibold leading-none whitespace-nowrap">
                        {uiLang === "hi" ? "आपकी profile" : "Your profile"}
                      </div>
                      <div className="text-[12px] leading-tight whitespace-nowrap opacity-90 mt-1">
                        {(() => {
                          // #101 — single source: prefer the live % the open
                          // panel reports (so the pill === the panel bar);
                          // fall back to the saved backend % when the panel
                          // isn't open; "Tap to build" only when neither.
                          const pct = liveProfilePct ?? profileCompleteness?.completeness_pct ?? null;
                          if (pct == null) return uiLang === "hi" ? "शुरू करें" : "Tap to build";
                          return uiLang === "hi" ? `${pct}% पूर्ण` : `${pct}% complete`;
                        })()}
                      </div>
                    </div>
                  </div>
                  {/* Premium segment */}
                  <div className="flex items-stretch bg-gradient-to-br from-amber-500 via-orange-500 to-amber-600 border-l border-white/25">
                    <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                      <RupeeIcon />
                    </div>
                    <div className="px-3 py-2 text-left">
                      <div className="text-[11px] uppercase tracking-wider font-semibold leading-none whitespace-nowrap">
                        {uiLang === "hi" ? "प्रीमियम रेंज" : "Premium range"}
                      </div>
                      <div className="text-[12px] leading-tight whitespace-nowrap opacity-90 mt-1">
                        {hasMeaningfulBand
                          ? `${uiLang === "hi" ? "आम तौर पर" : "Typically"} ₹${premiumBand!.min_inr.toLocaleString("en-IN")}–₹${premiumBand!.max_inr.toLocaleString("en-IN")}/yr`
                          : (uiLang === "hi" ? "अनुमान देखें" : "Tap to estimate")}
                      </div>
                    </div>
                    <div className="flex items-center justify-center px-2 py-2 bg-white/15 border-l border-white/20 transition-transform group-hover:translate-x-0.5">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="M4 21v-4l11-11 4 4-11 11H4z" />
                        <path d="M14 6l4 4" />
                      </svg>
                    </div>
                  </div>
                </div>
              </button>
              );
            })()}
            {/* Admin access — opens the LLM control panel in an embedded view.
                Backend admin API is password-gated (KI-097); enter the admin
                password in the embedded dashboard to unlock the live data. */}
            <button
              onClick={() => togglePanel("admin", showAdmin)}
              className={`chip-tile group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
                showAdmin ? "ring-2 ring-[var(--primary)]" : ""
              }`}
              title="Admin console — password-protected"
            >
              <div className="absolute inset-0 bg-gradient-to-br from-slate-700 via-slate-600 to-zinc-700" />
              <div className="relative flex items-stretch text-white">
                <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z" /><path d="M9 12l2 2 4-4" /></svg>
                </div>
                <div className="px-3 py-2 text-left">
                  <div className="text-[11px] uppercase tracking-wider font-semibold leading-none whitespace-nowrap">Admin</div>
                  <div className="text-[12px] leading-tight whitespace-nowrap opacity-90 mt-1">Access panel</div>
                </div>
              </div>
            </button>
            {/* KI-257 — header EN/HI toggle removed per user request.
                Sarvam STT auto-detects user language; LLM mirrors via
                SYSTEM_PROMPT RULE 8. ttsLang stays en-IN by default. */}
          </div>
        </div>
      </header>
      {openPolicy && <PolicyDetailModal policy={openPolicy} onClose={() => setOpenPolicy(null)} />}

      {/* Two-column layout on desktop (chat | panel), stacked on mobile.
          When no panel is open, chat takes the full width. The chat column
          never unmounts, so messages, voice, and view-context stay live no
          matter which view the user is focused on. */}
      <div className="flex-1 flex flex-col lg:flex-row min-h-0 w-full">
        {/* #49 — on phones, an open panel "takes over": the chat main is
            hidden (NOT unmounted, so voice + view-context stay live) and
            the panel gets the full viewport. Desktop keeps the two-column
            split. `flex` restores the column on lg. */}
        <main className={`flex-col min-h-0 px-4 sm:px-6 py-4 sm:py-6 ${
          (showMarketplace || showPremium || showProfile || showAdmin)
            ? "hidden"
            : "flex max-w-6xl w-full mx-auto"
        }`}>
        {messages.length === 0 ? (
          <>
            <EmptyState coverage={coverage} t={t} uiLang={uiLang} />
            {/* KI-038 — dots visible even on the very first turn (no messages
                yet but the bot is hearing you / thinking) */}
            {(busy || voicePhase) && (
              <div className="mt-4">
                <ThinkingDots phase={voicePhase} />
              </div>
            )}
          </>
        ) : (
          <>
            {/* KI-020 / KI-039 / KI-040 — single Clear-chat control. One
                click wipes the visible chat history but KEEPS the server-side
                profile so the bot doesn't have to re-fact-find the user. The
                bot will pick up with the existing profile on the next turn.
                (A named-profile feature for fully-different identities is on
                the roadmap — see backend/profile_store.py.) */}
            <div className="flex items-center justify-end gap-2 mb-2 text-[11px]">
              <button
                onClick={() => handleClearChat(false)}
                disabled={busy}
                className="px-2 py-1 rounded-md border border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:border-[var(--primary)] disabled:opacity-40 transition"
                title="Clear the visible chat. Your profile is preserved so the bot picks up where it left off."
              >
                Clear chat
              </button>
            </div>
            {/* KI-Z7 (2026-05-15) — Feature B. Welcome-back banner. Renders
                when the backend matched + hydrated a stored named-profile on
                this turn. "Use this profile" dismisses the banner (keeps the
                hydrated profile in place); "Update my info" opens the profile
                builder so the user can revise any fact before continuing. */}
            {welcomeBack && (
              <div className="mb-3 rounded-xl border border-[var(--primary)] bg-[var(--primary)]/10 px-3 py-2 text-sm flex items-center justify-between gap-2">
                <div className="flex-1">
                  <span className="font-semibold text-[var(--primary)]">
                    Welcome back, {welcomeBack.name}!
                  </span>{" "}
                  <span className="text-[var(--muted-foreground)]">
                    Your profile is loaded.
                  </span>
                  {welcomeBack.bandText && (
                    <span className="text-[var(--muted-foreground)]">
                      {" "}Last predicted premium:{" "}
                      <strong className="text-[var(--foreground)]">
                        {welcomeBack.bandText}
                      </strong>
                      .
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => setWelcomeBack(null)}
                    className="px-2 py-1 rounded-md text-xs border border-[var(--primary)] text-[var(--primary)] hover:bg-[var(--primary)] hover:text-white transition"
                    title="Continue with the loaded profile"
                  >
                    Use this profile
                  </button>
                  <button
                    onClick={() => {
                      setWelcomeBack(null);
                      openPanel("profile");
                    }}
                    className="px-2 py-1 rounded-md text-xs border border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--primary)] hover:text-[var(--foreground)] transition"
                    title="Open the profile builder to revise"
                  >
                    Update my info
                  </button>
                </div>
              </div>
            )}
            <div ref={scrollRef} className="flex-1 overflow-y-auto scrollbar-thin space-y-4 mb-4 pr-1">
              {messages.map((m) => (
                <Message
                  key={m.id}
                  m={m}
                  marketplace={marketplace}
                  profile={profileCompleteness?.profile}
                  premiumBand={premiumBand}
                  onOpenMarketplace={() => openPanel("marketplace")}
                />
              ))}
              {(busy || voicePhase) && <ThinkingDots phase={voicePhase} />}
            </div>
          </>
        )}

        {uploadStatus && (
          <div className="mb-3 text-xs px-3 py-2 rounded-lg bg-[var(--accent)] border border-[var(--border)] text-[var(--foreground)]">
            {uploadStatus}
          </div>
        )}

        {/* V4 FIX 5 — pb-[env(safe-area-inset-bottom)] keeps the composer
            above the iOS home-indicator strip even when the soft keyboard
            is open. Combined with viewport-fit=cover on the meta + the
            min-h-0 wrapper above, the chat scroll container hands the
            keyboard its space instead of getting hidden behind it. */}
        <div
          className="border border-[var(--border)] rounded-2xl bg-[var(--card)] p-3 transition-shadow focus-within:border-[color-mix(in_srgb,var(--primary)_36%,var(--border))]"
          style={{
            paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))",
            boxShadow:
              "0 1px 2px color-mix(in srgb, var(--foreground) 4%, transparent), 0 16px 40px -34px color-mix(in srgb, var(--foreground) 30%, transparent)",
          }}
        >
          <div className="flex items-end gap-2">
            <textarea
              ref={composerRef}
              value={input}
              onChange={(e) => {
                // V4 FIX 4 — once the user starts typing, the input is no
                // longer "transcript-sourced", so the next Backspace should
                // behave normally (single-character erase).
                inputFromTranscriptRef.current = false;
                setInput(e.target.value);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  // V4 FIX 6 — guard against empty Enter.
                  if (!input.trim()) return;
                  send(input);
                  return;
                }
                // V4 FIX 4 — if the current input is a freshly-committed
                // transcript fragment AND the caret is at the end of the
                // text, Backspace erases the last word. Once the user has
                // typed anything (or moved the caret mid-string), the
                // transcript flag was cleared by onChange — so this branch
                // no longer fires and Backspace behaves normally.
                if (
                  e.key === "Backspace"
                  && inputFromTranscriptRef.current
                  && !e.metaKey
                  && !e.ctrlKey
                  && !e.altKey
                ) {
                  const ta = e.currentTarget;
                  const atEnd = ta.selectionStart === input.length && ta.selectionEnd === input.length;
                  if (atEnd && input.length > 0) {
                    e.preventDefault();
                    // Strip trailing whitespace, then drop the last word.
                    const stripped = input.replace(/\s+$/, "");
                    const lastSpace = stripped.lastIndexOf(" ");
                    const erased = lastSpace >= 0 ? stripped.slice(0, lastSpace) : "";
                    setInput(erased);
                    // Keep the transcript-sourced flag set so subsequent
                    // Backspaces continue to erase by word until the box is
                    // empty.
                    inputFromTranscriptRef.current = erased.length > 0;
                  }
                }
              }}
              placeholder="Ask about coverage, waiting periods, exclusions, or compare policies…"
              rows={1}
              aria-label="Message"
              // #42 — NO Tailwind max-h here; the auto-grow effect caps the
              // height in JS (taller while listening so the full interim
              // transcript shows) and toggles overflow only on real
              // overflow. overflowY starts hidden; the effect manages it.
              className="flex-1 resize-none scrollbar-thin bg-transparent outline-none text-sm sm:text-base px-2 py-2 min-h-[40px]"
              style={{ overflowY: "hidden" }}
              disabled={busy || extractionInFlight}
            />
            {/* Hidden file input — the visible 📎 control drives it. PDF
                only; the backend rejects non-PDF magic bytes anyway, but
                the accept filter keeps the OS picker focused. */}
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,application/pdf"
              onChange={handleFile}
              className="hidden"
              aria-hidden="true"
              tabIndex={-1}
            />
            {/* Attach-PDF control. Same 44px tap height + radius language
                as the Send button (.btn-primary → 12px radius, h-11); a
                hairline-bordered secondary so it reads as a companion to
                Send, not a competing primary.
                ADR-044 (2026-05-27): also disabled while an extraction is
                in flight so the user can't queue a second upload mid-wait. */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || extractionInFlight}
              aria-label={t("input.upload")}
              title={t("input.upload")}
              className="shrink-0 h-11 px-3.5 rounded-xl border border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--primary)] hover:border-[var(--primary)] transition-colors flex items-center gap-1.5 text-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <PaperclipIcon />
              <span className="hidden sm:inline">PDF</span>
            </button>
            <button
              type="button"
              onClick={() => send(input)}
              disabled={busy || !input.trim() || extractionInFlight}
              className="btn-primary shrink-0 h-11 px-5 text-sm"
              title={extractionInFlight ? "Reading the uploaded PDF — a moment, please." : undefined}
            >
              Send
            </button>
          </div>

          {/* KI-257 — Voice control row. Master "Enable voice" toggle on
              the left; helper text "Enter to submit chat" on the right.
              When Voice is ON the Live + Push-to-talk sub-row appears
              below this. */}
          <div className="flex items-center justify-between gap-3 mt-2 pt-2 px-2 text-xs text-[var(--muted-foreground)]">
            <button
              type="button"
              onClick={() => setVoiceMasterOn((v) => !v)}
              className={`flex items-center gap-2 px-3 py-1 rounded-full border text-xs font-medium transition cursor-pointer ${
                voiceMasterOn
                  ? "border-emerald-300 bg-emerald-50 text-emerald-700 hover:bg-emerald-100 dark:border-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                  : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--primary)] hover:text-[var(--primary)]"
              }`}
              title={voiceMasterOn ? "Voice is on. Click to disable voice." : "Enable voice (shows Live and Push-to-talk options)"}
            >
              <span className={`inline-block w-2 h-2 rounded-full ${voiceMasterOn ? "bg-emerald-500 animate-pulse" : "bg-gray-400"}`} />
              {voiceMasterOn ? "Voice ON" : "Enable voice"}
            </button>
            <div className="hidden sm:block">Enter to submit chat</div>
          </div>

          {voiceMasterOn && (
            <div className="mt-2 px-2 flex flex-wrap items-center gap-2">
              {/* Live (BETA) toggle — gated behind the confirm dialog */}
              {!live.micPermissionDenied ? (
                <button
                  type="button"
                  onClick={() => {
                    // Turning Live OFF is immediate (nothing to warn about).
                    if (userPrefersLive) {
                      setUserPrefersLive(false);
                      return;
                    }
                    // Turning Live ON ALWAYS opens the styled risk modal —
                    // the live session begins only on explicit Confirm
                    // (handled in the modal's onConfirm). No bypass.
                    setShowLiveGate(true);
                  }}
                  className={`flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-medium transition cursor-pointer ${
                    userPrefersLive
                      ? "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                      : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--primary)] hover:text-[var(--primary)]"
                  }`}
                  title={userPrefersLive
                    ? "Live voice is BETA — may cut you off / echo. Click to turn off."
                    : "Enable Live always-on voice (BETA — unstable). Prefer Push-to-talk for reliable input."}
                >
                  <span className={`inline-block w-2 h-2 rounded-full ${
                    userPrefersLive
                      ? (live.recording ? "bg-red-500 animate-pulse" : "bg-amber-500 animate-pulse")
                      : "bg-gray-400"
                  }`} />
                  {userPrefersLive
                    ? (live.recording ? "Listening… (BETA)" : "Live ON · BETA")
                    : "Live (BETA — unstable)"}
                </button>
              ) : (
                <span className="text-rose-500 text-xs" title="Allow mic in your browser site settings, or use Push-to-talk">
                  🔇 Mic blocked
                </span>
              )}

              {/* Push-to-talk button */}
              <button
                type="button"
                onClick={recording ? stopRecording : startRecording}
                disabled={busy && !recording}
                className={`h-9 px-3 rounded-full flex items-center gap-1.5 text-xs font-medium transition-all ${
                  recording
                    ? "bg-[var(--error)] text-white animate-record-pulse"
                    : "bg-emerald-600 hover:bg-emerald-700 text-white shadow-md ring-2 ring-emerald-300 dark:ring-emerald-700"
                } disabled:opacity-40`}
                title={recording
                  ? (isTouch
                      ? "Recording… tap to stop and send"
                      : "Recording… click to stop and submit")
                  : (isTouch
                      ? "Tap to talk; tap again to stop. A pause auto-sends."
                      : "Push-to-talk: click to start, click again to stop.")}
              >
                {recording ? <StopIcon /> : <MicIcon />}
                <span>{recording ? "Stop & send" : "Push-to-talk"}</span>
              </button>

              {/* PTT status hint. */}
              <span className="text-xs text-[var(--muted-foreground)] italic">
                {recording
                  ? (isTouch ? "Listening… tap Stop to send" : "Listening… click Stop to submit")
                  : (isTouch
                      ? "Tap Push-to-talk, then pause — it sends itself"
                      : "Click Push-to-talk, then pause — it sends itself")}
              </span>
            </div>
          )}

          {/* #42 — PTT interim transcript strip. Visible while recording.
              Generous cap (max-h-40 ≈ 160px) so a full spoken sentence is
              readable here too, wraps + scrolls (newest pinned by the
              effect) instead of clipping to one line. */}
          {voiceMasterOn && recording && pttInterim && (
            <div
              ref={pttInterimBoxRef}
              className="mt-2 px-3 py-2 rounded-xl border border-[color-mix(in_srgb,var(--primary)_22%,var(--border))] bg-[color-mix(in_srgb,var(--primary)_4%,var(--card))] text-xs sm:text-[13px] italic text-[var(--muted-foreground)] leading-relaxed whitespace-pre-wrap break-words max-h-40 overflow-y-auto scrollbar-thin"
              aria-live="polite"
              aria-atomic="true"
              title={pttInterim}
            >
              {pttInterim}
            </div>
          )}
        </div>
      </main>

        {/* Panel column — sits beside the chat on desktop, takes over on
            mobile. Stays mounted as long as a panel is open; chat in the
            other column remains fully interactive (real-time copilot). */}
        {(showMarketplace || showProfile || showAdmin) && (
          <aside className="w-full flex-1 min-h-0 overflow-y-auto bg-[var(--background)]">
            {/* #67 — strict single-active: an explicit if/else-if chain so
                only ONE panel can ever mount even if two flags raced true.
                Every close routes through closeAllPanels (one close path,
                never two stacked close buttons). */}
            {showMarketplace && marketplace ? (
              <MarketplacePanel
                data={marketplace}
                onOpenPolicy={(p) => setOpenPolicy(p)}
                onClose={closeAllPanels}
                t={t}
                isPersonalized={profileCompleteness?.is_personalized === true}
              />
            ) : showProfile ? (
              <ProfileBuilderPanel
                sessionId={sessionId}
                setSessionId={setSessionId}
                initialProfile={profileCompleteness?.profile || {}}
                onSaved={(resp) => { setProfileCompleteness(resp); }}
                onClose={closeAllPanels}
                uiLang={uiLang}
                onProgress={setLiveProfilePct}
              />
            ) : showAdmin ? (
              <div className="flex flex-col h-full">
                <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)] bg-[var(--card)]">
                  <div>
                    <h2 className="text-sm font-semibold">Admin Console</h2>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      Password-protected. Sign in to view system health, usage, saved profiles, and controls.
                    </p>
                  </div>
                  <button
                    onClick={closeAllPanels}
                    className="text-xs text-[var(--muted-foreground)] hover:underline"
                  >
                    close
                  </button>
                </div>
                <iframe
                  src="/admin/llm-control.html"
                  title="Admin console"
                  className="flex-1 w-full border-0 bg-white"
                  sandbox="allow-scripts allow-same-origin allow-forms"
                />
              </div>
            ) : null}
          </aside>
        )}
      </div>

      <footer className="border-t border-[var(--border)] py-3 px-6 text-center text-xs text-[var(--muted-foreground)]">
        Advisory only. Information based on policy documents; verify with the insurer before purchase. All policy ratings are illustrative and based on publicly disclosed data.
      </footer>

      {/* Live (BETA) risk-confirmation gate. Confirm starts the live
          always-on session (setUserPrefersLive(true) → the existing
          useEffect calls live.setLive(true)); Cancel just closes and the
          toggle stays OFF. Shown EVERY time Live is enabled — no bypass. */}
      {showLiveGate && (
        <LiveBetaGateModal
          hindi={uiLang === "hi"}
          onConfirm={() => {
            setShowLiveGate(false);
            // This is the ONLY place the live session is armed: flipping
            // userPrefersLive → true triggers the existing effect that
            // calls live.setLive(true) (start of the useLiveConversation
            // flow / live.recording). Untouched machinery downstream.
            setUserPrefersLive(true);
          }}
          onCancel={() => {
            // Revert: toggle stays OFF, live session never starts.
            setShowLiveGate(false);
          }}
        />
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// #47 — shared continuous-slider primitives + ₹ helpers. All discrete
// pill/band selectors for money fields (sum insured, existing cover,
// co-pay, annual premium budget) are replaced by these so the user can
// pick ANY value, not a coarse band.
// ────────────────────────────────────────────────────────────────────────

// Compact ₹ label: ₹X cr / ₹X L / ₹Xk / ₹X. One decimal max per the
// decimal-numbering rule (1–2 digit → ≤1 decimal; ≥3 digit → 0 decimals).
function fmtRupeeShort(v: number): string {
  if (v <= 0) return "₹0";
  if (v >= 10000000) {
    const cr = v / 10000000;
    return `₹${cr % 1 === 0 ? cr : cr.toFixed(1)} cr`;
  }
  if (v >= 100000) {
    const l = v / 100000;
    return `₹${l % 1 === 0 ? l : l.toFixed(1)}L`;
  }
  if (v >= 1000) {
    const k = v / 1000;
    return `₹${k % 1 === 0 ? k : k.toFixed(1)}k`;
  }
  return `₹${Math.round(v)}`;
}

// Annual-premium-budget numeric value ⇄ backend `budget_band` string.
// The backend Profile contract field is `budget_band` (a string enum);
// we keep sending that (contract unchanged) but the UI is now a precise
// numeric slider. This maps the numeric rupee value onto the documented
// band so the profile save still persists it (#47b).
function budgetInrToBand(v: number): string {
  if (v < 15000) return "under_15k";
  if (v < 30000) return "15k_30k";
  if (v < 60000) return "30k_60k";
  return "60k+";
}
function budgetBandToInr(band?: string | null): number | null {
  switch (band) {
    case "under_15k":
      return 12000;
    case "15k_30k":
      return 22000;
    case "30k_60k":
      return 45000;
    case "60k+":
      return 75000;
    default:
      return null;
  }
}

// One continuous money/number slider with a live ₹ readout. Themed via the
// existing .app-range token so it matches the editorial-fintech system.
// ≥44px effective tap target (touch-action + generous padding on mobile).
//
// ARCHITECTURE (rebuild — two prior patches failed): the slider is FULLY
// self-contained. It owns a local `draft` value that tracks the pointer in
// real time. The parent is only told the new value on COMMIT — pointer
// release, keyboard change, or blur — never per drag tick. The component is
// React.memo'd and never reads parent state mid-drag, so dragging one slider
// cannot re-render the panel (no premium recompute, no sibling re-render, no
// "blink every fractional second"). The thumb position is driven purely by
// `draft`, so the drag always completes smoothly.
const RupeeSlider = React.memo(function RupeeSlider({
  label,
  hint,
  value,
  min,
  max,
  step,
  format = fmtRupeeShort,
  unsetLabel,
  onCommit,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  value: number | null;
  min: number;
  max: number;
  step: number;
  format?: (v: number) => string;
  unsetLabel?: string;
  // Called ONLY when the user settles (pointer up / keyboard / blur). Never
  // per drag tick — that is the decoupling that kills the re-render storm.
  onCommit: (v: number) => void;
}) {
  const mid = Math.round((min + max) / 2);
  // Local draft — the single source of truth WHILE interacting. Seeded from
  // the parent value but never re-synced mid-drag.
  const [draft, setDraft] = React.useState<number>(value ?? mid);
  const draggingRef = React.useRef(false);

  // Re-sync the draft to the parent value ONLY when the parent value changes
  // AND we are not mid-drag (e.g. profile loaded from chat, recall, reset).
  React.useEffect(() => {
    if (draggingRef.current) return;
    setDraft(value ?? mid);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // The readout reflects the live draft so the number tracks the thumb with
  // zero parent involvement. "Not set" only shows before the user touches it.
  const display =
    value == null && !draggingRef.current && draft === mid
      ? (unsetLabel ?? "—")
      : format(draft);

  const commit = (v: number) => {
    draggingRef.current = false;
    if (v !== value) onCommit(v);
  };

  return (
    <div>
      <label className="flex items-baseline justify-between gap-3 text-xs mb-2">
        <span className="font-semibold">{label}</span>
        <span className="font-mono text-sm text-[var(--primary)] font-semibold tabular-nums">
          {display}
        </span>
      </label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={draft}
        // Drag tick: update LOCAL state only. Smooth thumb tracking, zero
        // parent re-render, zero premium recompute.
        onInput={(e) => {
          draggingRef.current = true;
          setDraft(parseInt((e.target as HTMLInputElement).value, 10));
        }}
        onChange={(e) => {
          // Fires for keyboard arrows / click-to-step. Treat as a commit.
          const v = parseInt((e.target as HTMLInputElement).value, 10);
          setDraft(v);
          if (!draggingRef.current) commit(v);
        }}
        // Settle: pointer release / touch end / blur all commit the final
        // value to the parent exactly once.
        onPointerUp={() => commit(draft)}
        onPointerCancel={() => commit(draft)}
        onTouchEnd={() => commit(draft)}
        onKeyUp={() => commit(draft)}
        onBlur={() => commit(draft)}
        className="app-range app-range-lg"
        style={{ touchAction: "none" }}
      />
      <div className="flex justify-between text-[10px] text-[var(--muted-foreground)] mt-1 tabular-nums">
        <span>{format(min)}</span>
        <span>{format(max)}</span>
      </div>
      {hint && (
        <p className="text-[10.5px] text-[var(--muted-foreground)] mt-1.5 leading-snug">
          {hint}
        </p>
      )}
    </div>
  );
});

// #100 — module-scope so its identity is STABLE across ProfileBuilderPanel
// re-renders (an in-render definition remounted the whole form every
// keystroke). Purely presentational; depends only on its props.
function Group({
  n, title, hint, children,
}: { n: number; title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="field-group p-4 sm:p-5">
      <div className="flex items-start gap-3 mb-4">
        <div className="field-medallion shrink-0">{n}</div>
        <div className="min-w-0">
          <h3 className="panel-title text-[15px] leading-tight">{title}</h3>
          {hint && (
            <p className="text-[11.5px] text-[var(--muted-foreground)] leading-snug mt-0.5">{hint}</p>
          )}
        </div>
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function ProfileBuilderPanel({
  sessionId,
  setSessionId,
  initialProfile,
  onSaved,
  onClose,
  uiLang,
  onProgress,
}: {
  sessionId: string | undefined;
  setSessionId: (id: string) => void;
  initialProfile: UserProfile;
  onSaved: (r: ProfileCompletenessResponse) => void;
  onClose: () => void;
  uiLang: UILang;
  // #101 — report the LIVE completion % up so the header pill shows the
  // SAME number as this panel's progress bar (was: pill=backend 0%, bar=14%).
  onProgress?: (pct: number) => void;
}) {
  // KI-077 — pre-fill from initialProfile (the chat-captured state). If the
  // chat already heard "I am Rohit Sar, 29, just me, Mumbai", every chip
  // below renders with those values selected when the panel opens.
  const [name, setName] = useState<string>(initialProfile.name ?? "");
  const [age, setAge] = useState<number | null>(initialProfile.age ?? null);
  const [dependents, setDependents] = useState<string>(initialProfile.dependents ?? "self");
  // #47b — Annual premium budget is now a precise NUMERIC value (continuous
  // slider), seeded from the stored `budget_band` string. It IS captured +
  // persisted: handleSave derives `budget_band` (the backend contract field)
  // from this number via budgetInrToBand so the profile save round-trips it.
  const [budgetInr, setBudgetInr] = useState<number | null>(
    // #64 — prefer the EXACT ₹ the user stated/slid; only fall back to the
    // lossy 4-band representative when no exact value was ever captured.
    initialProfile.budget_inr ??
      (initialProfile.budget_band
        ? budgetBandToInr(initialProfile.budget_band)
        : null),
  );
  const [income, setIncome] = useState<string>(initialProfile.income_band ?? "");
  const [city, setCity] = useState<string>(initialProfile.location_tier ?? "");
  const [conditions, setConditions] = useState<string[]>(initialProfile.health_conditions ?? []);
  const [existingCover, setExistingCover] = useState<number | null>(initialProfile.existing_cover_inr ?? null);
  const [primaryGoal, setPrimaryGoal] = useState<string>(initialProfile.primary_goal ?? "");
  const [parentsHasPed, setParentsHasPed] = useState<boolean | null>(initialProfile.parents_has_ped ?? null);
  const [parentsAgeMax, setParentsAgeMax] = useState<number | null>(initialProfile.parents_age_max ?? null);
  // New pricing/scoring slots — backend consumes these (see save report).
  // desired_sum_insured_inr → premium_calculator.estimate() sum_insured_inr
  //   + retrieval query band (single_brain.py RULE 2.5).
  const [desiredSI, setDesiredSI] = useState<number | null>(initialProfile.desired_sum_insured_inr ?? null);
  // smoker → premium_calculator smoker_multiplier (+30-50%).
  const [smoker, setSmoker] = useState<boolean | null>(initialProfile.smoker ?? null);
  // copay_pct → premium_calculator _copay_discount; 0/10/20/30 tiers.
  const [copay, setCopay] = useState<number | null>(initialProfile.copay_pct ?? null);
  // family_medical_history → premium_calculator _family_history_loading
  //   + retrieval rider-boost keywords.
  const [familyHistory, setFamilyHistory] = useState<string[]>(initialProfile.family_medical_history ?? []);
  const [busy, setBusy] = useState(false);

  // KI-077 — keep panel in sync if the chat captures new fields while the
  // panel is open. Otherwise the user sees stale state.
  useEffect(() => {
    if (initialProfile.name && !name) setName(initialProfile.name);
    if (initialProfile.age != null && age == null) setAge(initialProfile.age);
    if (initialProfile.dependents && dependents === "self") setDependents(initialProfile.dependents);
    if (budgetInr == null) {
      const _seed =
        initialProfile.budget_inr ??
        (initialProfile.budget_band
          ? budgetBandToInr(initialProfile.budget_band)
          : null);
      if (_seed != null) setBudgetInr(_seed);
    }
    if (initialProfile.income_band && !income) setIncome(initialProfile.income_band);
    if (initialProfile.location_tier && !city) setCity(initialProfile.location_tier);
    if (initialProfile.health_conditions?.length && !conditions.length) setConditions(initialProfile.health_conditions);
    if (initialProfile.existing_cover_inr != null && existingCover == null) setExistingCover(initialProfile.existing_cover_inr);
    if (initialProfile.primary_goal && !primaryGoal) setPrimaryGoal(initialProfile.primary_goal);
    if (initialProfile.parents_age_max != null && parentsAgeMax == null) setParentsAgeMax(initialProfile.parents_age_max);
    if (initialProfile.parents_has_ped != null && parentsHasPed == null) setParentsHasPed(initialProfile.parents_has_ped);
    if (initialProfile.desired_sum_insured_inr != null && desiredSI == null) setDesiredSI(initialProfile.desired_sum_insured_inr);
    if (initialProfile.smoker != null && smoker == null) setSmoker(initialProfile.smoker);
    if (initialProfile.copay_pct != null && copay == null) setCopay(initialProfile.copay_pct);
    if (initialProfile.family_medical_history?.length && !familyHistory.length) setFamilyHistory(initialProfile.family_medical_history);
    // #62 — depend on the PRIMITIVE fields, not the initialProfile object.
    // The parent passes `profileCompleteness?.profile || {}`, so the object
    // identity changes every parent render; keying on it re-ran this sync on
    // every render and compounded the slider re-render storm.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    initialProfile.name, initialProfile.age, initialProfile.dependents,
    initialProfile.budget_band, initialProfile.income_band,
    initialProfile.location_tier, initialProfile.existing_cover_inr,
    initialProfile.primary_goal, initialProfile.parents_age_max,
    initialProfile.parents_has_ped, initialProfile.desired_sum_insured_inr,
    initialProfile.smoker, initialProfile.copay_pct,
    (initialProfile.health_conditions || []).join(","),
    (initialProfile.family_medical_history || []).join(","),
  ]);

  const hindi = uiLang === "hi";

  const toggleCondition = (c: string) => {
    setConditions((prev) => prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]);
  };
  const toggleFamilyHistory = (c: string) => {
    setFamilyHistory((prev) => prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]);
  };

  const handleSave = async () => {
    if (busy) return;
    setBusy(true);
    let sid = sessionId;
    if (!sid) {
      sid = `s_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      setSessionId(sid);
      if (typeof window !== "undefined") sessionStorage.setItem("insurance_session_id", sid);
    }
    try {
      const resp = await postProfileUpdate({
        session_id: sid,
        name: name.trim() || undefined,  // KI-077 — submit the name too
        age: age ?? undefined,
        dependents: dependents || undefined,
        // #47b — persist the numeric annual-premium budget by mapping it
        // onto the backend's documented `budget_band` contract field.
        budget_band: budgetInr != null ? budgetInrToBand(budgetInr) : undefined,
        // #64 — also persist the EXACT ₹ so the slider round-trips to the
        // precise value next load (not the band representative).
        budget_inr: budgetInr ?? undefined,
        income_band: income || undefined,
        location_tier: city || undefined,
        health_conditions: conditions.length ? conditions : undefined,
        existing_cover_inr: existingCover ?? undefined,
        primary_goal: primaryGoal || undefined,
        parents_to_insure: dependents.includes("parent") ? true : null,
        parents_has_ped: parentsHasPed,
        parents_age_max: parentsAgeMax ?? undefined,
        // Pricing/scoring slots — now fully whitelisted by the backend
        // ProfileUpdateRequest (main.py) AND consumed by premium_calculator
        // + retrieval, so they round-trip end-to-end.
        desired_sum_insured_inr: desiredSI ?? undefined,
        smoker: smoker,
        copay_pct: copay ?? undefined,
        family_medical_history: familyHistory.length ? familyHistory : undefined,
      });
      onSaved(resp);
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  };

  // LIVE premium — ARCHITECTURE REBUILD.
  //
  // ONE premium figure. ONE recompute path. The recompute is fully decoupled
  // from slider drag: every slider commits to parent state only on release
  // (self-contained RupeeSlider above), so a dep change here can only happen
  // AFTER the user settles — never per tick. A single debounce coalesces
  // rapid commits (e.g. tabbing through several fields). No pointer guards,
  // no recompute nonce, no dual band — the prior layered patches that fought
  // each other are gone. The estimate uses the SAME contract the premium
  // engine owns (postPremiumEstimate) so this number and the header chip
  // never diverge.
  const [livePremium, setLivePremium] = useState<PremiumEstimateResponse | null>(null);
  const [livePremiumBusy, setLivePremiumBusy] = useState(false);

  const deriveFamilySize = (dep: string): number => {
    const d = dep.toLowerCase();
    if (d.includes("parents") && d.includes("spouse")) return 4;
    if (d.includes("parents")) return 2;
    if (d.includes("kids") || d.includes("children")) return 3;
    if (d.includes("spouse")) return 1;
    return 0;
  };
  const derivePed = (
    conds: string[],
  ): "none" | "diabetes_or_hypertension" | "heart_disease" | "multiple" => {
    const lower = conds.map((c) => (c || "").toLowerCase()).filter((c) => c && c !== "none");
    if (lower.length === 0) return "none";
    if (lower.length >= 2) return "multiple";
    if (lower.some((c) => c.includes("heart"))) return "heart_disease";
    if (lower.some((c) => c.includes("diabetes") || c.includes("hypertension") || c.includes("bp")))
      return "diabetes_or_hypertension";
    return "diabetes_or_hypertension";
  };
  const deriveCityTier = (loc: string): "metro" | "tier1" | "tier2" => {
    const l = loc.toLowerCase().replace(/[-_\s]/g, "");
    if (l === "metro") return "metro";
    if (l.includes("1")) return "tier1";
    if (l.includes("2")) return "tier2";
    return "tier2";
  };

  // Single debounced recompute. Deps only change when the user COMMITS a
  // field (slider release / pill tap / typed field blur). A 450ms debounce
  // coalesces a quick burst of commits into one estimate. On failure we keep
  // the last good number (never blink to empty). Because no dep can change
  // mid-drag, the premium pane never re-renders while a slider is held.
  useEffect(() => {
    const handle = setTimeout(() => {
      setLivePremiumBusy(true);
      postPremiumEstimate({
        age: age ?? 35,
        sum_insured_inr: desiredSI ?? existingCover ?? 1000000,
        city_tier: city ? deriveCityTier(city) : "metro",
        smoker: smoker === true,
        family_size: deriveFamilySize(dependents),
        pre_existing_conditions: derivePed(conditions),
        copayment_pct: copay ?? 0,
      })
        .then(setLivePremium)
        .catch(() => { /* keep the last good estimate — never blink to empty */ })
        .finally(() => { setLivePremiumBusy(false); });
    }, 450);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [age, desiredSI, existingCover, city, smoker, dependents, conditions, copay]);

  const conditionOptions = hindi
    ? [["diabetes", "मधुमेह"], ["hypertension", "BP"], ["thyroid", "थायरॉइड"], ["heart", "हृदय रोग"], ["asthma", "अस्थमा"], ["cancer", "कैंसर इतिहास"]]
    : [["diabetes", "Diabetes"], ["hypertension", "BP / Hypertension"], ["thyroid", "Thyroid"], ["heart", "Heart"], ["asthma", "Asthma"], ["cancer", "Cancer history"]];
  const familyHistoryOptions = hindi
    ? [["diabetes", "मधुमेह"], ["heart_disease", "हृदय रोग"], ["cancer", "कैंसर"], ["hypertension", "BP"], ["stroke", "स्ट्रोक"], ["kidney", "किडनी"]]
    : [["diabetes", "Diabetes"], ["heart_disease", "Heart disease"], ["cancer", "Cancer"], ["hypertension", "Hypertension"], ["stroke", "Stroke"], ["kidney", "Kidney disease"]];

  // Live progress — the 7 slots scorecard.profile_completeness weights
  // (name, age, dependents, income_band, primary_goal, location_tier,
  // health_conditions). `conditions` counts as answered once the user
  // picks "None" or any condition (i.e. the field is non-empty OR they
  // explicitly cleared it via the None pill, which we treat as answered
  // only when something else signals intent — here, any other filled slot
  // plus an explicit conditions interaction). We keep it simple: a slot
  // counts when it holds a value; conditions counts when it has entries.
  const requiredFilled = [
    !!name.trim(),
    age != null,
    !!dependents,
    !!income,
    !!primaryGoal,
    !!city,
    conditions.length > 0,
  ].filter(Boolean).length;
  const progressPct = Math.round((requiredFilled / 7) * 100);
  // #101 — single source of truth: the header "Your profile" pill mirrors
  // THIS live bar, so they can never disagree (was 14% bar vs 0% pill).
  useEffect(() => {
    onProgress?.(progressPct);
  }, [progressPct, onProgress]);

  // #100 — `Group` is now a MODULE-SCOPE component (hoisted out of this
  // render). Defining it inline gave it a new function identity on every
  // ProfileBuilderPanel re-render, so React unmounted+remounted the whole
  // form subtree on each keystroke/slider tick → the name input lost focus
  // after every letter, the panel scroll jumped to the top, and it
  // "flickered". Hoisting reuses the DOM across re-renders; the input keeps
  // focus and scroll position. (#92 only removed the entrance animation —
  // this removes the actual remount.)

  return (
    <div className="app-panel app-panel-mobile-full border-t border-[var(--border)] max-h-[80vh] overflow-y-auto scrollbar-thin">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
        {/* #47c — consolidated header. The old separate "Your profile" and
            "Premium range" panels are merged into this ONE view: profile on
            the left, the live premium translation + range on the right. */}
        <div className="flex items-start justify-between gap-4 mb-5">
          <div className="min-w-0">
            <div className="panel-kicker mb-1.5">
              <span className="dot" />
              {hindi ? "प्रोफ़ाइल और प्रीमियम" : "Profile & premium"}
            </div>
            <h2 className="panel-title text-2xl sm:text-[28px] leading-[1.1]">
              {hindi ? "आपकी profile, आपका premium — एक ही जगह" : "Your profile, your premium — together"}
            </h2>
            <p className="text-[13px] text-[var(--muted-foreground)] mt-2 max-w-xl leading-relaxed">
              {hindi
                ? "हर field बदलते ही premium live update होता है। ये जवाब इसी chat में रहते हैं; जब तक आप न चाहें किसी बीमाकर्ता से साझा नहीं।"
                : "Edit any field and the premium recomputes live beside it. Your answers stay in this chat — nothing is shared with any insurer until you choose to buy."}
            </p>
          </div>
          <button onClick={onClose} className="shrink-0 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition min-h-[44px] px-2">{hindi ? "बंद करें" : "Close"}</button>
        </div>

        {/* Live progress rail */}
        <div className="mb-5">
          <div className="flex items-center justify-between text-[11px] mb-1.5">
            <span className="text-[var(--muted-foreground)] uppercase tracking-wider font-semibold">
              {hindi ? "स्कोरिंग के लिए ज़रूरी फ़ील्ड" : "Fields the score uses"}
            </span>
            <span className="font-mono text-[var(--primary)] font-semibold">{progressPct}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-[color-mix(in_srgb,var(--primary)_14%,var(--border))] overflow-hidden">
            <div
              className="h-full rounded-full bg-[var(--primary)] transition-[width] duration-500"
              style={{ width: `${progressPct}%` }}
            />
          </div>
        </div>

        {/* #47c — consolidated two-pane grid: profile fields on the left,
            the live premium translation + range on the right. On phones it
            stacks (premium pane drops below the form but stays sticky-ish
            at the top of the stack for at-a-glance feedback). */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-5 lg:gap-6 items-start">
        <div className="space-y-4 min-w-0 order-2 lg:order-1">
          {/* ── Group 1 · About you ───────────────────────────── */}
          <Group
            n={1}
            title={hindi ? "आपके बारे में" : "About you"}
            hint={hindi ? "उम्र + परिवार premium, eligibility और renewal-age तय करते हैं।" : "Age and family shape premium, eligibility, and renewal age."}
          >
            <div>
              <label className="flex items-baseline justify-between text-xs mb-1.5">
                <span className="font-semibold">{hindi ? "आपका नाम" : "Your name"}</span>
                {initialProfile.name && (
                  <span className="text-[10px] text-[var(--primary)] font-semibold">{hindi ? "chat से लिया गया" : "from chat"}</span>
                )}
              </label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={hindi ? "जैसे, रोहित" : "e.g., Rohit Sar"}
                maxLength={50}
                className="app-input"
              />
              <p className="text-[10.5px] text-[var(--muted-foreground)] mt-1">
                {hindi ? "अगली बार आने पर आपकी जानकारी पहले से भरी मिलेगी — दोबारा बताने की ज़रूरत नहीं।" : "Saved for next time, so you never have to enter this again."}
              </p>
            </div>

            <RupeeSlider
              label={hindi ? "आपकी उम्र" : "Your age"}
              value={age}
              min={18}
              max={80}
              step={1}
              format={(v) => `${v}`}
              unsetLabel="—"
              onCommit={setAge}
            />

            <div>
              <label className="block text-xs font-semibold mb-2">{hindi ? "किसको cover करना है" : "Who needs cover"}</label>
              <div className="flex flex-wrap gap-2">
                {[
                  ["self", hindi ? "सिर्फ मैं" : "Just me"],
                  ["self+spouse", hindi ? "मैं + पति/पत्नी" : "Self + spouse"],
                  ["self+spouse+kids", hindi ? "मैं + पति/पत्नी + बच्चे" : "Self + spouse + kids"],
                  ["self+parents", hindi ? "मैं + माता-पिता" : "Self + parents"],
                  ["self+spouse+kids+parents", hindi ? "पूरा परिवार" : "Whole family"],
                ].map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setDependents(key)} className="opt-pill" data-on={dependents === key}>{label}</button>
                ))}
              </div>
            </div>

            {dependents.includes("parent") && (
              <div className="subfield p-4 space-y-4">
                <div className="text-[11px] uppercase tracking-wider font-semibold text-[var(--primary)]">
                  {hindi ? "माता-पिता का विवरण" : "Parents detail"}
                </div>
                <RupeeSlider
                  label={hindi ? "सबसे बड़े parent की उम्र" : "Eldest parent's age"}
                  value={parentsAgeMax}
                  min={45}
                  max={85}
                  step={1}
                  format={(v) => `${v}`}
                  unsetLabel="—"
                  onCommit={setParentsAgeMax}
                />
                <div>
                  <label className="block text-xs font-semibold mb-2">{hindi ? "क्या उन्हें diabetes / BP / heart है?" : "Any pre-existing conditions (diabetes / BP / heart)?"}</label>
                  <div className="flex gap-2">
                    <button type="button" onClick={() => setParentsHasPed(true)} className="opt-pill" data-on={parentsHasPed === true}>{hindi ? "हाँ" : "Yes"}</button>
                    <button type="button" onClick={() => setParentsHasPed(false)} className="opt-pill" data-on={parentsHasPed === false}>{hindi ? "नहीं" : "No"}</button>
                  </div>
                </div>
              </div>
            )}
          </Group>

          {/* ── Group 2 · Health ──────────────────────────────── */}
          <Group
            n={2}
            title={hindi ? "सेहत" : "Health"}
            hint={hindi ? "सच बताइए — बीमाकर्ता claim time पर records मिलाते हैं।" : "Be honest — insurers cross-check records at claim time."}
          >
            <div>
              <label className="block text-xs font-semibold mb-1.5">{hindi ? "आपकी pre-existing conditions" : "Your pre-existing conditions"}</label>
              <p className="text-[10.5px] text-amber-700 dark:text-amber-400 mb-2 leading-snug">
                {hindi ? "₹500 की आज की बचत = बाद में ₹8L का denied claim।" : "₹500 saved today turns into an ₹8L denied claim tomorrow."}
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => setConditions([])} className="opt-pill" data-on={conditions.length === 0}>{hindi ? "कुछ नहीं" : "None"}</button>
                {conditionOptions.map(([key, label]) => (
                  <button key={key} type="button" onClick={() => toggleCondition(key)} className="opt-pill" data-on={conditions.includes(key)}>{label}</button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold mb-1.5">{hindi ? "क्या आप तंबाकू/धूम्रपान करते हैं?" : "Do you use tobacco or smoke?"}</label>
              <p className="text-[10.5px] text-[var(--muted-foreground)] mb-2">{hindi ? "Premium पर +30-50% — पर सच बताना claim बचाता है।" : "Loads premium +30–50% — but disclosing it protects your claim."}</p>
              <div className="flex gap-2">
                <button type="button" onClick={() => setSmoker(false)} className="opt-pill" data-on={smoker === false}>{hindi ? "नहीं" : "Non-smoker"}</button>
                <button type="button" onClick={() => setSmoker(true)} className="opt-pill" data-on={smoker === true}>{hindi ? "हाँ" : "Smoker / tobacco"}</button>
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold mb-1.5">{hindi ? "खून के रिश्ते में कोई बड़ी बीमारी?" : "Family medical history (blood relatives)"}</label>
              <p className="text-[10.5px] text-[var(--muted-foreground)] mb-2">{hindi ? "केवल माता-पिता/भाई-बहन। यह उन riders की ओर खोज झुकाता है।" : "Parents / siblings only. Biases the search toward relevant riders."}</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={() => setFamilyHistory([])} className="opt-pill" data-on={familyHistory.length === 0}>{hindi ? "कुछ नहीं" : "None"}</button>
                {familyHistoryOptions.map(([key, label]) => (
                  <button key={key} type="button" onClick={() => toggleFamilyHistory(key)} className="opt-pill" data-on={familyHistory.includes(key)}>{label}</button>
                ))}
              </div>
            </div>
          </Group>

          {/* ── Group 3 · Cover & cost ────────────────────────── */}
          {/* #47a — every money field here is now a CONTINUOUS slider
              (any value, sensible min/max/step, ₹ formatting). The old
              discrete pill bands are gone. Each slider feeds the live
              premium translation on the right pane. */}
          <Group
            n={3}
            title={hindi ? "कवर और लागत" : "Cover & cost"}
            hint={hindi ? "ये premium अनुमान और cover-band की खोज तय करते हैं।" : "These drive the premium estimate and the cover band we search."}
          >
            <RupeeSlider
              label={
                <>
                  {hindi ? "आप कितना cover चाहते हैं" : "Sum insured you want"}
                  <HelpTip id="sum_insured" />
                </>
              }
              value={desiredSI}
              min={300000}
              max={20000000}
              step={100000}
              unsetLabel={hindi ? "तय नहीं" : "Not set"}
              onCommit={setDesiredSI}
              hint={hindi ? "Slide कर कोई भी राशि चुनें। खाली छोड़ें तो आय से ~5-7×।" : "Slide to any amount. Leave at default and we estimate ~5–7× your income."}
            />

            <RupeeSlider
              label={
                <>
                  {hindi ? "पहले से कोई health insurance?" : "Existing health cover you hold"}
                  <HelpTip id="existing_cover" />
                </>
              }
              value={existingCover}
              min={0}
              max={10000000}
              step={100000}
              format={(v) => (v <= 0 ? (hindi ? "कोई नहीं" : "None") : fmtRupeeShort(v))}
              unsetLabel={hindi ? "कोई नहीं" : "None"}
              onCommit={setExistingCover}
              hint={hindi ? "है तो हम top-up की ओर देखते हैं, नई base policy की नहीं।" : "If you have cover, we look at top-ups, not a fresh base policy."}
            />

            <RupeeSlider
              label={
                <>
                  {hindi ? "हर claim में आपका हिस्सा (co-pay)" : "Your share per claim (co-pay)"}
                  <HelpTip id="copay" />
                </>
              }
              value={copay}
              min={0}
              max={40}
              step={5}
              format={(v) => `${v}%`}
              unsetLabel="0%"
              onCommit={setCopay}
              hint={
                copay && copay > 0
                  ? (hindi
                      ? `Premium ~${Math.round(copay * 0.7)}% घटता है, पर हर अस्पताल बिल का ${copay}% आप भरते हैं।`
                      : `Premium drops ~${Math.round(copay * 0.7)}%, but you pay ${copay}% of every hospital bill.`)
                  : (hindi ? "0% = बीमाकर्ता पूरा भरता है (premium ज़्यादा)।" : "0% = insurer pays it all (higher premium).")
              }
            />

            <RupeeSlider
              label={
                <>
                  {hindi ? "सालाना premium budget" : "Annual premium budget"}
                  <HelpTip id="budget" />
                </>
              }
              value={budgetInr}
              min={5000}
              max={150000}
              step={1000}
              unsetLabel={hindi ? "तय नहीं" : "Not set"}
              onCommit={setBudgetInr}
              hint={hindi ? "आप हर साल कितना premium दे सकते हैं — slide कर सटीक राशि चुनें।" : "What you can pay per year — slide to the exact figure. Saved with your profile."}
            />
          </Group>

          {/* ── Group 4 · Context ─────────────────────────────── */}
          <Group
            n={4}
            title={hindi ? "संदर्भ" : "Context"}
            hint={hindi ? "शहर cashless network तय करता है; आय + लक्ष्य scoring weight घुमाते हैं।" : "City sets cashless depth; income and goal tune the scoring weights."}
          >
            <div>
              <label className="block text-xs font-semibold mb-2">{hindi ? "आपका शहर" : "Your city"}</label>
              <div className="flex flex-wrap gap-2">
                {[["metro", hindi ? "Metro (Mumbai/Delhi/...)" : "Metro"], ["tier1", "Tier 1"], ["tier2", hindi ? "Tier 2 / छोटा शहर" : "Tier 2 / smaller"], ["tier3", "Tier 3"]].map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setCity(key)} className="opt-pill" data-on={city === key}>{label}</button>
                ))}
              </div>
              <p className="text-[10.5px] text-[var(--muted-foreground)] mt-1.5">{hindi ? "आपके शहर में कितने cashless अस्पताल हैं — बड़ा फर्क।" : "How many cashless hospitals exist in your city makes a huge difference."}</p>
            </div>

            <div>
              <label className="block text-xs font-semibold mb-2">{hindi ? "सालाना आय" : "Annual income"}</label>
              <div className="flex flex-wrap gap-2">
                {[["under_5L", hindi ? "₹5L से कम" : "Under ₹5L"], ["5L-10L", "₹5–10L"], ["10L-25L", "₹10–25L"], ["25L+", "₹25L+"]].map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setIncome(key)} className="opt-pill" data-on={income === key}>{label}</button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold mb-2">{hindi ? "आज यहाँ क्यों?" : "What brought you here today?"}</label>
              <div className="flex flex-wrap gap-2">
                {[["first_buy", hindi ? "पहली policy" : "First policy"], ["upgrade", hindi ? "Cover बढ़ानी है" : "Upgrade cover"], ["compare_specific", hindi ? "Specific policies compare" : "Compare specific policies"], ["tax_planning", hindi ? "Tax (80D)" : "Tax planning (80D)"]].map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setPrimaryGoal(key)} className="opt-pill" data-on={primaryGoal === key}>{label}</button>
                ))}
              </div>
            </div>
          </Group>
        </div>

        {/* ── Live premium — ONE figure, decoupled from drag ───────────── */}
        <aside className="order-1 lg:order-2 lg:sticky lg:top-4">
          <div className="rounded-2xl border border-[color-mix(in_srgb,var(--primary)_22%,var(--border))] bg-[color-mix(in_srgb,var(--primary)_5%,var(--card))] p-4 sm:p-5">
            <div className="panel-kicker mb-2">
              <span className="dot" />
              {hindi ? "लाइव प्रीमियम" : "Live premium"}
            </div>
            <div className="text-[11px] text-[var(--muted-foreground)] uppercase tracking-wide font-semibold">
              {(desiredSI == null && existingCover == null && !copay && budgetInr == null)
                ? (hindi ? "सामान्य प्रोफ़ाइल (डिफ़ॉल्ट) · ₹/वर्ष" : "For a typical profile (defaults) · ₹/year")
                : (hindi ? "इन settings के लिए · ₹/वर्ष" : "For these settings · ₹/year")}
            </div>
            {livePremium ? (
              <>
                {/* Exactly ONE premium block — the indicative annual range. */}
                <div className="font-display text-2xl sm:text-[26px] font-semibold mt-1 leading-tight tabular-nums">
                  ₹{livePremium.low_inr.toLocaleString("en-IN")}
                  <span className="text-[var(--muted-foreground)] text-base font-normal"> – </span>
                  ₹{livePremium.high_inr.toLocaleString("en-IN")}
                </div>
                <div className="text-xs text-[var(--muted-foreground)] mt-1 tabular-nums">
                  {hindi ? "विशिष्ट अनुमान" : "Typical"} ₹{livePremium.point_estimate_inr.toLocaleString("en-IN")}/{hindi ? "वर्ष" : "yr"}
                  {livePremiumBusy && <span className="ml-1.5 italic">· {hindi ? "अपडेट हो रहा है…" : "updating…"}</span>}
                </div>
                {/* State the cover this is priced on. fmtRupeeShort ALREADY
                    returns a leading ₹ — no extra ₹ prefix (fixes ₹₹15L). */}
                <div className="text-[10.5px] text-[var(--muted-foreground)] mt-1 leading-snug">
                  {hindi
                    ? `${fmtRupeeShort(desiredSI ?? existingCover ?? 1000000)} cover पर अनुमानित${(desiredSI == null && existingCover == null) ? " — personalize के लिए ऊपर sliders सेट करें" : ""}.`
                    : `Estimated on ${fmtRupeeShort(desiredSI ?? existingCover ?? 1000000)} cover${(desiredSI == null && existingCover == null) ? " — set the sliders above to personalize" : ""}.`}
                </div>
                {/* Budget-fit signal — compares the user's saved annual
                    budget against the live estimate so the slider has a
                    visible consequence. */}
                {budgetInr != null && (
                  <div
                    className={`mt-2.5 text-[11.5px] rounded-lg px-2.5 py-1.5 leading-snug ${
                      livePremium.point_estimate_inr <= budgetInr
                        ? "bg-emerald-50 text-emerald-800 dark:bg-emerald-900/25 dark:text-emerald-300"
                        : "bg-amber-50 text-amber-800 dark:bg-amber-900/25 dark:text-amber-300"
                    }`}
                  >
                    {livePremium.point_estimate_inr <= budgetInr
                      ? (hindi
                          ? `आपके ₹${budgetInr.toLocaleString("en-IN")}/वर्ष बजट के अंदर।`
                          : `Within your ₹${budgetInr.toLocaleString("en-IN")}/year budget.`)
                      : (hindi
                          ? `आपके ₹${budgetInr.toLocaleString("en-IN")}/वर्ष बजट से ऊपर — cover घटाएँ या co-pay बढ़ाएँ।`
                          : `Above your ₹${budgetInr.toLocaleString("en-IN")}/year budget — lower the cover or raise co-pay.`)}
                  </div>
                )}
              </>
            ) : (
              <div className="text-sm text-[var(--muted-foreground)] mt-2 leading-snug">
                {livePremiumBusy
                  ? (hindi ? "कीमत निकाल रहे…" : "Pricing…")
                  : (hindi ? "अपनी profile बनाते रहें — रुकते ही कीमत दिखा दूँगा।" : "Keep building your profile — I'll show the price the moment you pause.")}
              </div>
            )}

            {livePremium?.disclaimer && (
              <div className="mt-3 text-[10.5px] text-amber-700 dark:text-amber-300 leading-snug">
                ⚠ {livePremium.disclaimer}
              </div>
            )}
          </div>
        </aside>
        </div>

        <div className="save-bar sticky bottom-0 mt-5 -mx-4 sm:-mx-6 px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
          <span className="text-[11px] text-[var(--muted-foreground)] hidden sm:block">
            {hindi ? "Save करते ही हर policy आपके लिए re-score होती है।" : "Saving instantly re-scores every policy for you."}
          </span>
          <div className="flex items-center gap-3 ml-auto">
            <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">{hindi ? "रद्द करें" : "Cancel"}</button>
            <button
              onClick={handleSave}
              disabled={busy}
              className="btn-primary text-sm px-5 py-2.5"
            >
              {busy ? (hindi ? "Save हो रहा है…" : "Saving…") : (hindi ? "Save & Score" : "Save & Score")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CoveragePanel({ coverage, onClose }: { coverage: CoverageResponse; onClose: () => void }) {
  return (
    <div className="border-t border-[var(--border)] bg-[var(--muted)]">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-4">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-sm font-semibold">What this bot can answer questions about</h2>
          <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:underline">close</button>
        </div>
        <p className="text-xs text-[var(--muted-foreground)] mb-3">
          {coverage.total_policies} policies · {coverage.total_chunks.toLocaleString()} indexed text chunks · {coverage.total_insurers} insurers. Click any insurer to open their site; click a policy to open its PDF.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {coverage.insurers.map((ins) => (
            <div key={ins.slug} className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-3 text-xs">
              <a
                href={ins.home_url || "#"}
                target="_blank"
                rel="noopener"
                className="font-semibold text-[var(--foreground)] hover:text-[var(--primary)] block mb-1.5"
              >
                {ins.name} <span className="opacity-50 font-normal">· {ins.policy_count}</span>
              </a>
              <ul className="space-y-0.5">
                {ins.sample_policies.map((p, i) => (
                  <li key={i} className="text-[var(--muted-foreground)]">
                    {p.source_url ? (
                      <a href={p.source_url} target="_blank" rel="noopener" className="hover:text-[var(--primary)] hover:underline">
                        {p.name}
                      </a>
                    ) : (
                      <span>{p.name}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function HealthBadge({ health }: { health: { status: string; missing: string[] } | null }) {
  if (!health) return <span className="text-xs text-[var(--muted-foreground)]">checking…</span>;
  const ok = health.status === "ok";
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className={`w-2 h-2 rounded-full ${ok ? "bg-emerald-500" : health.status === "unreachable" ? "bg-red-500" : "bg-amber-500"}`} />
      <span className="text-[var(--muted-foreground)]">
        {ok ? "healthy" : health.status === "unreachable" ? "backend unreachable" : `degraded`}
      </span>
    </div>
  );
}

// EmptyState — landing page shown before the user's first message.
// Premium editorial-fintech redesign (2026-05-16). Direction: warm,
// trustworthy, magazine-grade — Fraunces display serif over Plus Jakarta
// body, a textured hero slab, a numbered "journey" spine for the 3 steps,
// crisp one-line fact bullets (no prose blobs, no dangling lines), refined
// input-mode list, and one orchestrated staggered page-load reveal.
// The narrative reads top-to-bottom: what this is -> how it works ->
// how you talk to it -> what to know before you start -> (the composer).
// All copy is bilingual; long honesty/voice copy still reuses the existing
// welcome.* i18n keys so Hindi comes through untouched. Functionality
// (chat, voice, header chips, composer) lives entirely outside this
// component and is not touched.
function EmptyState({
  coverage,
  t,
  uiLang,
}: {
  coverage: CoverageResponse | null;
  t: (k: StringKey, v?: Record<string, string | number>) => string;
  uiLang: UILang;
}) {
  const isHi = uiLang === "hi";
  const L = (en: string, hi: string) => (isHi ? hi : en);

  // Coverage facts, rendered as one-line bullets (never a wrapping blob).
  const policyCount = coverage ? coverage.total_policies : 169;
  const insurerCount = coverage ? coverage.total_insurers : 20;

  return (
    <div className="landing-root flex-1 flex flex-col items-center px-4 py-7 sm:py-12">
      <div className="w-full max-w-3xl flex flex-col gap-6 sm:gap-9">
        {/* HERO — eyebrow kicker -> serif headline -> one-line value
            sub-line -> inline trust strip. The headline phrase is kept
            whole and balanced (text-wrap: balance) so it never produces an
            ugly single-word orphan; the emphasised word stays glued to its
            punctuation in a nowrap span. */}
        <section className="hero-card reveal reveal-1 px-6 sm:px-12 py-9 sm:py-14 text-center">
          <div className="flex flex-col items-center">
            <span className="kicker mb-6">
              <span className="dot" aria-hidden="true" />
              {L("AI advisor · Indian health cover", "AI सलाहकार · भारतीय स्वास्थ्य बीमा")}
            </span>

            <div className="w-14 h-14 sm:w-[60px] sm:h-[60px] rounded-2xl bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center text-2xl font-semibold mb-6 shadow-[0_8px_24px_-8px_color-mix(in_srgb,var(--primary)_60%,transparent)]">
              IA
            </div>

            {(() => {
              const headA = t("welcome.heading_a"); // "Find a health policy that genuinely fits "
              const headB = t("welcome.heading_b"); // "you"
              const headC = t("welcome.heading_c"); // "."
              return (
                <h1
                  className="font-display text-[2rem] leading-[1.12] sm:text-[3.1rem] sm:leading-[1.08] font-semibold text-[var(--foreground)] mb-4 max-w-[18ch] mx-auto"
                  style={{ textWrap: "balance" }}
                >
                  {headA.replace(/\s+$/, "")}{" "}
                  <em className="not-italic text-[var(--primary)] whitespace-nowrap">
                    {headB}{headC}
                  </em>
                </h1>
              );
            })()}

            <p className="text-[15px] sm:text-[16.5px] text-[var(--muted-foreground)] leading-relaxed max-w-[44ch] mx-auto">
              {L(
                "A few short questions. Then policies ranked by how well they fit you — plus the premium you'd actually pay.",
                "कुछ छोटे सवाल। फिर आपके लिए कितनी सही हैं, उसके हिसाब से रैंक की गई पॉलिसियाँ — और जो premium आप असल में देंगे।"
              )}
            </p>

            <div className="mt-7 flex flex-wrap items-center justify-center gap-x-6 gap-y-2.5 text-[13px] font-medium text-[var(--muted-foreground)]">
              <span className="inline-flex items-center gap-2">
                <TrustTick />
                {L(`${policyCount} policies · ${insurerCount} insurers`, `${policyCount} पॉलिसियाँ · ${insurerCount} बीमाकर्ता`)}
              </span>
              <span className="inline-flex items-center gap-2">
                <TrustTick />
                {L("Ranked for your best fit", "आपके लिए सबसे सही fit के हिसाब से")}
              </span>
              <span className="inline-flex items-center gap-2">
                <TrustTick />
                {L("Every fact has a source", "हर तथ्य का source")}
              </span>
            </div>
          </div>
        </section>

        {/* SECTION 01 - How it works. Three steps on a shared dashed
            spine; each body is two crisp one-line fact bullets. */}
        <section className="section-card reveal reveal-2 px-6 sm:px-10 py-8 sm:py-10">
          <SectionHead
            num="01"
            kind="lightbulb"
            title={L("How it works", "यह कैसे काम करता है")}
          />
          <ol className="step-grid grid grid-cols-1 sm:grid-cols-3 gap-4 sm:gap-5 mt-7">
            <StepCard
              n={1}
              title={L("A few quick questions", "कुछ छोटे सवाल")}
              points={[
                L("Age, family, location, budget", "उम्र, परिवार, location, budget"),
                L("One short answer at a time", "एक-एक करके, एक उत्तर"),
              ]}
            />
            <StepCard
              n={2}
              title={L("Matched to you", "आपके लिए match")}
              points={[
                L(`${policyCount} policies · ${insurerCount} Indian insurers`, `${policyCount} पॉलिसियाँ · ${insurerCount} बीमाकर्ता`),
                L("Personalised to your profile — ranked for your best fit", "आपकी profile के अनुसार — सबसे सही fit के लिए रैंक"),
              ]}
            />
            <StepCard
              n={3}
              title={L("Your premium band", "आपका premium")}
              points={[
                L("Illustrative annual figure", "अनुमानित वार्षिक premium"),
                L("Tuned to your exact profile", "आपकी profile के अनुसार"),
              ]}
            />
          </ol>
          <p className="mt-6 text-[13px] text-[var(--muted-foreground)] flex items-center gap-2">
            <UploadGlyph />
            {L(
              "Already have a policy? Upload the PDF — it's analysed the same way.",
              "पहले से policy है? PDF upload करें — उसी तरह analyse होगी।"
            )}
          </p>
        </section>

        {/* SECTION 02 - How to talk to it. Four input modes, each a
            single-line description; mirrors the composer + voice
            controls the user is about to use below. */}
        <section className="section-card reveal reveal-3 px-6 sm:px-10 py-8 sm:py-10">
          <SectionHead
            num="02"
            kind="mic"
            title={L("Talk to it your way", "अपने तरीके से बात करें")}
          />
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3.5 sm:gap-4 mt-7">
            <ModeRow
              icon="keyboard"
              title={L("Type", "टाइप करें")}
              body={L("Write below, press Enter to send", "नीचे लिखें, Enter दबाएं")}
            />
            <ModeRow
              icon="mic"
              title={L("Push-to-talk", "Push-to-talk")}
              body={L("Tap the green mic for one turn", "हरे mic पर tap करें")}
            />
            <ModeRow
              icon="wave"
              title={L("Live (BETA)", "Live (BETA)")}
              body={L("Always-on with barge-in", "हमेशा सुनना · barge-in")}
            />
          </ul>
          <p className="mt-6 text-[13px] text-[var(--muted-foreground)] flex items-center gap-2">
            <SpeakerGlyph />
            {L(
              "The bot speaks back automatically — English or Hindi, your call.",
              "Bot आवाज़ में जवाब देता है — English या हिन्दी, आपकी पसंद।"
            )}
          </p>
        </section>

        {/* SECTION 03 - Before you start. Two callouts: honesty (amber,
            distinct) + how to speak. Honesty reuses welcome.trust_* i18n
            keys so Hindi is untouched. */}
        <section className="section-card reveal reveal-4 px-6 sm:px-10 py-8 sm:py-10">
          <SectionHead
            num="03"
            kind="shield"
            title={L("Before you start", "शुरू करने से पहले")}
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 sm:gap-5 items-stretch mt-7">
            {/* Honesty — kept visually distinct in warm amber. */}
            <div className="callout-card callout-amber border border-amber-300/80 bg-gradient-to-br from-amber-50 to-amber-100/40 dark:from-amber-900/20 dark:to-amber-800/10 px-6 py-6 text-left shadow-sm flex flex-col">
              <div className="flex items-center gap-2.5 mb-3">
                <span className="inline-flex w-8 h-8 rounded-xl bg-amber-200/70 dark:bg-amber-800/40 items-center justify-center text-amber-700 dark:text-amber-300">
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 9v4" />
                    <path d="M12 17h.01" />
                    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
                  </svg>
                </span>
                <div className="font-display text-[16px] font-semibold text-amber-800 dark:text-amber-300 leading-tight">
                  {t("welcome.trust_title")}
                </div>
              </div>
              {/* Was a 7-line prose blob (welcome.trust_body). Converted
                  to crisp one-line bullets to match the rest of the
                  landing; every fact preserved, EN/HI parity via L(). */}
              <ul className="flex flex-col gap-2">
                {[
                  L(
                    "Don't hide a condition to lower your premium.",
                    "premium कम करने के लिए कोई condition मत छिपाइए।"
                  ),
                  L(
                    "Insurers cross-check disclosed history against hospital records at claim time.",
                    "बीमाकर्ता claim time पर बताया गया इतिहास hospital records से मिलाते हैं।"
                  ),
                  L(
                    "₹500/month saved today turns into an ₹8 lakh denied claim later.",
                    "आज के ₹500/महीने की बचत बाद में ₹8 लाख का denied claim बन जाती है।"
                  ),
                  L(
                    "Your honest answers stay in this chat.",
                    "आपके ईमानदार जवाब इसी chat में रहते हैं।"
                  ),
                  L(
                    "Not shared with any insurer until you choose to buy.",
                    "जब तक आप खरीदना न चाहें, किसी insurer के साथ शेयर नहीं।"
                  ),
                ].map((line, i) => (
                  <li
                    key={i}
                    className="flex items-start gap-2.5 text-[13px] leading-snug text-amber-900/85 dark:text-amber-200/80"
                  >
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      className="shrink-0 mt-[3px] text-amber-700 dark:text-amber-400"
                      aria-hidden="true"
                    >
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </div>

            {/* How to speak. */}
            <div className="callout-card border border-[color-mix(in_srgb,var(--primary)_18%,var(--border))] bg-gradient-to-br from-[color-mix(in_srgb,var(--primary)_5%,var(--card))] to-[var(--card)] px-6 py-6 text-left shadow-sm flex flex-col">
              <div className="flex items-center gap-2.5 mb-3">
                <span className="inline-flex w-8 h-8 rounded-xl bg-teal-100/70 dark:bg-teal-900/40 items-center justify-center text-[var(--primary)]">
                  <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="2" width="6" height="12" rx="3" />
                    <path d="M5 10a7 7 0 0 0 14 0" />
                    <path d="M12 17v4" />
                  </svg>
                </span>
                <div className="font-display text-[16px] font-semibold text-[var(--foreground)] leading-tight">
                  {L("Speaking to the bot", "Bot से बात करना")}
                </div>
              </div>
              <ul className="fact-list">
                <li>
                  <TickIcon />
                  <span>{L("Speak slowly and clearly for the voice AI.", "voice AI के लिए धीरे और साफ़ बोलिए।")}</span>
                </li>
                <li>
                  <TickIcon />
                  <span>{L("English or Hindi — I reply in your language.", "English या हिन्दी — मैं उसी भाषा में जवाब दूंगा।")}</span>
                </li>
                <li>
                  <TickIcon />
                  <span>{L("Unclear? Just repeat or rephrase — no penalty.", "अस्पष्ट? दोबारा बोलिए — कोई penalty नहीं।")}</span>
                </li>
              </ul>
            </div>
          </div>

          {/* Closing nudge that hands the user to the composer below. */}
          <div className="mt-7 flex items-center justify-center gap-2.5 text-[13.5px] font-medium text-[var(--primary)]">
            <span>{L("Ready when you are — send your first message below", "तैयार हों तो — नीचे अपना पहला message भेजें")}</span>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className="animate-bounce" style={{ animationDuration: "1.6s" }} aria-hidden="true">
              <path d="M12 5v14" />
              <path d="m19 12-7 7-7-7" />
            </svg>
          </div>
        </section>
      </div>
    </div>
  );
}

// SectionHead — chaptered section title: serif title + index number +
// brand icon + a hairline rule that grows toward the right edge so every
// section reads as a numbered chapter rather than a stacked box.
function SectionHead({
  num,
  kind,
  title,
}: {
  num: string;
  kind: "lightbulb" | "mic" | "shield";
  title: string;
}) {
  return (
    <div className="flex items-center gap-3.5">
      <span className="inline-flex w-9 h-9 rounded-xl items-center justify-center bg-[color-mix(in_srgb,var(--primary)_10%,var(--card))] border border-[color-mix(in_srgb,var(--primary)_20%,var(--border))]">
        <SectionIcon kind={kind} />
      </span>
      <span className="section-num">{num}</span>
      <h2 className="font-display text-[20px] sm:text-[23px] font-semibold text-[var(--foreground)] leading-tight whitespace-nowrap">
        {title}
      </h2>
      <span className="section-rule" aria-hidden="true" />
    </div>
  );
}

// StepCard — one step on the shared journey spine. Body is two crisp
// one-line fact bullets; the serif numbered medallion anchors the spine.
function StepCard({
  n,
  title,
  points,
}: {
  n: number;
  title: string;
  points: string[];
}) {
  return (
    <li className="step-card group px-5 py-6 flex flex-col">
      <div className="step-medallion mb-5" aria-hidden="true">
        {n}
      </div>
      <div className="font-display text-[17px] font-semibold text-[var(--foreground)] mb-3 leading-tight">
        {title}
      </div>
      <ul className="fact-list">
        {points.map((p, i) => (
          <li key={i}>
            <TickIcon />
            <span>{p}</span>
          </li>
        ))}
      </ul>
    </li>
  );
}

// ModeRow — one input mode. Title + a single-line description; a left
// accent rail wipes in on hover.
function ModeRow({
  icon,
  title,
  body,
}: {
  icon: "keyboard" | "mic" | "wave";
  title: string;
  body: string;
}) {
  return (
    <li className="mode-row group flex items-center gap-3.5 px-4 py-3.5">
      <div className="mode-glyph shrink-0 w-10 h-10 rounded-xl flex items-center justify-center">
        <ModeIcon kind={icon} />
      </div>
      <div className="min-w-0">
        <div className="text-[14.5px] font-semibold text-[var(--foreground)] leading-tight">
          {title}
        </div>
        <p className="text-[12.5px] text-[var(--muted-foreground)] leading-snug mt-0.5 truncate">
          {body}
        </p>
      </div>
    </li>
  );
}

// TickIcon — small brand-teal check used on every one-line fact bullet.
function TickIcon() {
  return (
    <svg
      className="tick"
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="3"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m5 13 4 4L19 7" />
    </svg>
  );
}

// TrustTick — filled-circle check for the hero trust strip.
function TrustTick() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="color-mix(in srgb, var(--primary) 14%, transparent)" />
      <path d="m8 12 2.5 2.5L16 9" stroke="var(--primary)" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function UploadGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="M12 3v13" />
      <path d="m7 8 5-5 5 5" />
    </svg>
  );
}

function SpeakerGlyph() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--primary)" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 5 6 9H2v6h4l5 4z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7" />
      <path d="M18.5 6a9 9 0 0 1 0 12" />
    </svg>
  );
}

// SectionIcon — leading icon for each section heading.
function SectionIcon({ kind }: { kind: "lightbulb" | "mic" | "shield" }) {
  const common = {
    width: 19,
    height: 19,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2.2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: "text-[var(--primary)]",
  };
  if (kind === "lightbulb") {
    return (
      <svg {...common}>
        <path d="M9 18h6" />
        <path d="M10 22h4" />
        <path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.2 1 2V17h6v-.3c0-.8.4-1.5 1-2A7 7 0 0 0 12 2Z" />
      </svg>
    );
  }
  if (kind === "mic") {
    return (
      <svg {...common}>
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10a7 7 0 0 0 14 0" />
        <path d="M12 17v4" />
      </svg>
    );
  }
  // shield
  return (
    <svg {...common}>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

// ModeIcon — small icons next to each input mode.
function ModeIcon({ kind }: { kind: "keyboard" | "mic" | "wave" }) {
  const common = {
    width: 18,
    height: 18,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (kind === "keyboard") {
    return (
      <svg {...common}>
        <rect x="2" y="6" width="20" height="12" rx="2" />
        <path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10" />
      </svg>
    );
  }
  if (kind === "mic") {
    return (
      <svg {...common}>
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10a7 7 0 0 0 14 0" />
        <path d="M12 17v4" />
      </svg>
    );
  }
  // wave
  return (
    <svg {...common}>
      <path d="M3 12h2" />
      <path d="M7 8v8" />
      <path d="M11 5v14" />
      <path d="M15 8v8" />
      <path d="M19 11v2" />
      <path d="M21 12h.01" />
    </svg>
  );
}

function stripInlineCitations(text: string): string {
  // Customer-facing: hide inline [Source: ...] tags from prose; the citation
  // list below the message already shows them.
  // #44 — CRITICAL: do NOT collapse newlines. The old `\s{2,}→" "` rule
  // flattened every blank line + list break into a single space, which
  // destroyed all markdown structure before MarkdownMessage could parse it
  // (this is what made replies render as a literal "**…1.…2.…" wall).
  // We now strip only the citation tags + collapse runs of spaces/tabs
  // (NOT \n), and tidy excess blank lines.
  return text
    .replace(/[ \t]*\[(?:Source|Regulation):[^\]]+\]/gi, "")
    .replace(/[ \t]{2,}/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

// ────────────────────────────────────────────────────────────────────────
// #44 — lightweight, dependency-free, SANITIZED markdown renderer for
// assistant replies. The backend (single_brain) emits real markdown:
// `**bold policy names**`, numbered lists (`1. … 2. … 3. …`), bullet
// lists (`- …` / `* …`), inline `code`, links, and blank-line-separated
// paragraphs. Before this, the chat printed the raw source so users saw
// literal `**` and a run-on "1. …2. …3. …" wall.
//
// Why hand-rolled (no react-markdown / remark): the task forbids touching
// package.json. This parser is intentionally tiny and produces ONLY React
// elements (never dangerouslySetInnerHTML), so there is no HTML-injection
// surface — any `<script>` / raw HTML in the model output renders as inert
// text. EN/HI agnostic (it only keys off markdown punctuation).
// ────────────────────────────────────────────────────────────────────────

type MdInlineToken =
  | { t: "text"; v: string }
  | { t: "strong"; v: MdInlineToken[] }
  | { t: "em"; v: MdInlineToken[] }
  | { t: "code"; v: string }
  | { t: "link"; v: string; href: string };

// Parse inline markdown (bold / italic / code / links). Recursion is
// bounded by string length so a pathological input can't blow the stack.
function parseInline(src: string): MdInlineToken[] {
  const out: MdInlineToken[] = [];
  let i = 0;
  let buf = "";
  const flush = () => {
    if (buf) {
      out.push({ t: "text", v: buf });
      buf = "";
    }
  };
  while (i < src.length) {
    const rest = src.slice(i);
    // FIX #22 — unescape the `\.` emitted by sanitizeStrayNumberedLines so
    // a guarded sentence-final number renders as a normal "15." with no
    // visible backslash. Scoped to `\.` only (the single escape we emit)
    // so we don't accidentally swallow legitimate backslashes elsewhere.
    if (rest.startsWith("\\.")) {
      buf += ".";
      i += 2;
      continue;
    }
    // Links: [label](http(s)://… or relative). Only http/https/mailto or
    // root-relative hrefs are kept; anything else (javascript:, data:) is
    // rendered as plain text so there's no injection vector.
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(rest);
    if (link) {
      const rawHref = link[2];
      const safe = /^(https?:\/\/|mailto:|\/)/i.test(rawHref);
      if (safe) {
        flush();
        out.push({ t: "link", v: link[1], href: rawHref });
        i += link[0].length;
        continue;
      }
    }
    // Bold: **…** or __…__
    const strong = /^(\*\*|__)([\s\S]+?)\1/.exec(rest);
    if (strong) {
      flush();
      out.push({ t: "strong", v: parseInline(strong[2]) });
      i += strong[0].length;
      continue;
    }
    // Italic: *…* or _…_ (single delimiter, not part of a ** run)
    const emM = /^(\*|_)(?!\1)([\s\S]+?)\1/.exec(rest);
    if (emM && emM[2].trim()) {
      flush();
      out.push({ t: "em", v: parseInline(emM[2]) });
      i += emM[0].length;
      continue;
    }
    // Inline code: `…`
    const codeM = /^`([^`]+)`/.exec(rest);
    if (codeM) {
      flush();
      out.push({ t: "code", v: codeM[1] });
      i += codeM[0].length;
      continue;
    }
    buf += src[i];
    i += 1;
  }
  flush();
  return out;
}

function renderInline(tokens: MdInlineToken[], keyPrefix: string): React.ReactNode[] {
  return tokens.map((tok, idx) => {
    const k = `${keyPrefix}-${idx}`;
    switch (tok.t) {
      case "strong":
        return (
          <strong key={k} className="font-semibold text-[var(--foreground)]">
            {renderInline(tok.v, k)}
          </strong>
        );
      case "em":
        return <em key={k}>{renderInline(tok.v, k)}</em>;
      case "code":
        return (
          <code
            key={k}
            className="px-1 py-0.5 rounded bg-[var(--muted)] text-[0.92em] font-mono break-words"
          >
            {tok.v}
          </code>
        );
      case "link":
        return (
          <a
            key={k}
            href={tok.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--primary)] underline underline-offset-2 hover:opacity-80 break-words"
          >
            {tok.v}
          </a>
        );
      default:
        return <span key={k}>{tok.v}</span>;
    }
  });
}

// FIX #22 — sentence-final number guard. A bot reply like
//   "...complaints per 10,000 policies are low at 15. Niva Bupa is..."
// has "15." followed by whitespace + more prose. parseBlocks' run-on
// splitter (`/\s(?=\d{1,2}\.\s)/ → "\n"`) turns the space before "15."
// into a hard break, then the ol matcher `/^(\d{1,2})[.)]\s+/` renders
// it as `<ol start=15>` — breaking the sentence into a bogus list item
// ("15." / "43." / "52." were the live repros).
//
// A GENUINE recommendation list is small sequential numbers (1, 2, 3)
// each at the START of its own line / preceded by blank or list context.
// So the SAFE rule: a number is NOT an intended list marker when EITHER
//   (a) the number is >= 10  (recommendation lists never start at 10+;
//       they enumerate 1,2,3…), OR
//   (b) it appears MID-LINE after sentence prose (text precedes it on the
//       same physical line) — that is unambiguously a sentence wrapping.
// In those cases we escape the dot as `\.` so neither the run-on splitter
// nor the ol matcher fires; renderInline unescapes `\.`→`.` so the user
// still sees a normal "15." with no backslash. Single small digits at a
// real line start (a true "1. …" / "2. …" list) are left untouched.
function sanitizeStrayNumberedLines(src: string): string {
  // Walk physical lines so we never touch a number that legitimately
  // starts its own line as item 1..9 of a real list.
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const isListLine = (s: string) =>
    /^\s*(\d+)[.)]\s+/.test(s) || /^\s*[-*•]\s+/.test(s);
  let prevNonEmpty = "";
  // A run-on list the model streamed on ONE physical line looks like
  // "...: 1. A 2. B 3. C" — small SEQUENTIAL numbers. parseBlocks already
  // splits those into a real <ol> via its `/\s(?=\d{1,2}\.\s)/→"\n"` rule,
  // so we must NOT escape them. We treat a line as a run-on list when it
  // contains a "1." AND a "2." marker (sequential small markers); only
  // then do we leave its small-number dots alone.
  const looksLikeRunOnList = (s: string) =>
    /(^|\s)1\.\s/.test(s) && /(^|\s)2\.\s/.test(s);
  return lines
    .map((rawLine) => {
      const runOn = looksLikeRunOnList(rawLine);
      // (b) MID-LINE: a "<digits>. " with non-space text before it on the
      // same line is a wrapped sentence ("...are low at 15. Niva..."),
      // NOT a list. Escape it so the run-on splitter + ol matcher skip it.
      // Scope: numbers >= 10 are ALWAYS a wrapped sentence here (a real
      // list never enumerates a bare 10+ mid-line); numbers 1-9 are only
      // escaped when the line is NOT a streamed run-on list (so a genuine
      // "Options: 1. A 2. B 3. C" still becomes a real <ol>). Negative
      // lookbehind for `\` so we don't double-escape on a re-run.
      let out = rawLine.replace(
        /(\S[^\n]*?)(?<!\\)\b(\d+)\.(\s)/g,
        (_m, pre: string, num: string, ws: string) => {
          const n = parseInt(num, 10);
          if (n >= 10) return `${pre}${num}\\.${ws}`;
          if (runOn) return _m; // genuine streamed list marker — keep
          return `${pre}${num}\\.${ws}`;
        },
      );
      // (a) LINE-START with a number >= 10 (e.g. a hard-wrapped "15." that
      // landed at column 0 after the model wrapped a sentence). A real
      // recommendation list enumerates 1,2,3… so it only reaches 10+ when
      // the IMMEDIATELY-PRECEDING non-empty line is itself a list item
      // (a genuine long list). If the previous line is prose / blank /
      // absent, a leading 10+ is a wrapped sentence number → escape it.
      // Small line-start digits (1-9) are always left as a real list.
      out = out.replace(
        /^(\s*)(\d+)\.(\s)/,
        (m, lead: string, num: string, ws: string) => {
          const n = parseInt(num, 10);
          if (n < 10) return m; // genuine list item 1..9 — never touch
          const prevIsListContext = isListLine(prevNonEmpty);
          return prevIsListContext ? m : `${lead}${num}\\.${ws}`;
        },
      );
      if (rawLine.trim()) prevNonEmpty = rawLine;
      return out;
    })
    .join("\n");
}

// Block-level grouping: paragraphs, ordered lists, unordered lists,
// headings. Consecutive list lines coalesce into one <ol>/<ul> so a
// "1. … 2. … 3. …" run renders as a real numbered list, not a wall.
type MdBlock =
  | { t: "p"; lines: string[] }
  | { t: "ol"; items: string[]; start: number }
  | { t: "ul"; items: string[] }
  | { t: "h"; level: number; text: string };

function parseBlocks(src: string): MdBlock[] {
  // FIX #22 — neutralise sentence-final / wrapped numbers ("...at 15.")
  // BEFORE the run-on splitter below converts the space before them into a
  // hard break and the ol matcher list-ifies them. Genuine 1./2./3.
  // recommendation lists are untouched (see sanitizeStrayNumberedLines).
  const guarded = sanitizeStrayNumberedLines(src);
  // Normalise newlines; also break a run-on "1. a 2. b 3. c" that arrived
  // on a single physical line into separate list lines so it still renders
  // as an ordered list (the model sometimes streams without hard breaks).
  const normalised = guarded
    .replace(/\r\n?/g, "\n")
    .replace(/\s(?=\d{1,2}\.\s)/g, "\n")
    .replace(/\s(?=[•]\s)/g, "\n");
  const rawLines = normalised.split("\n");
  const blocks: MdBlock[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      blocks.push({ t: "p", lines: para });
      para = [];
    }
  };
  for (let idx = 0; idx < rawLines.length; idx++) {
    const line = rawLines[idx];
    const trimmed = line.trim();
    if (!trimmed) {
      flushPara();
      continue;
    }
    const headingM = /^(#{1,4})\s+(.*)$/.exec(trimmed);
    if (headingM) {
      flushPara();
      blocks.push({ t: "h", level: headingM[1].length, text: headingM[2] });
      continue;
    }
    const olM = /^(\d{1,2})[.)]\s+(.*)$/.exec(trimmed);
    if (olM) {
      flushPara();
      const last = blocks[blocks.length - 1];
      if (last && last.t === "ol") {
        last.items.push(olM[2]);
      } else {
        blocks.push({ t: "ol", items: [olM[2]], start: parseInt(olM[1], 10) || 1 });
      }
      continue;
    }
    const ulM = /^[-*•]\s+(.*)$/.exec(trimmed);
    if (ulM) {
      flushPara();
      const last = blocks[blocks.length - 1];
      if (last && last.t === "ul") {
        last.items.push(ulM[1]);
      } else {
        blocks.push({ t: "ul", items: [ulM[1]] });
      }
      continue;
    }
    para.push(trimmed);
  }
  flushPara();
  return blocks;
}

function MarkdownMessage({ source }: { source: string }) {
  const blocks = parseBlocks(source);
  return (
    <div className="md-body text-sm sm:text-[15px] leading-relaxed text-[var(--foreground)]">
      {blocks.map((b, i) => {
        if (b.t === "h") {
          const cls =
            b.level <= 2
              ? "font-display text-[1.05em] font-semibold mt-3 first:mt-0 mb-1"
              : "font-semibold text-[0.98em] mt-2.5 first:mt-0 mb-1";
          return (
            <p key={i} className={cls}>
              {renderInline(parseInline(b.text), `h${i}`)}
            </p>
          );
        }
        if (b.t === "ol") {
          return (
            <ol
              key={i}
              start={b.start}
              className="md-ol list-decimal pl-5 my-2 space-y-1.5 marker:text-[var(--primary)] marker:font-semibold"
            >
              {b.items.map((it, j) => (
                <li key={j} className="pl-1 leading-relaxed">
                  {renderInline(parseInline(it), `ol${i}-${j}`)}
                </li>
              ))}
            </ol>
          );
        }
        if (b.t === "ul") {
          return (
            <ul
              key={i}
              className="md-ul list-disc pl-5 my-2 space-y-1.5 marker:text-[var(--primary)]"
            >
              {b.items.map((it, j) => (
                <li key={j} className="pl-1 leading-relaxed">
                  {renderInline(parseInline(it), `ul${i}-${j}`)}
                </li>
              ))}
            </ul>
          );
        }
        // paragraph — preserve soft line breaks inside the paragraph
        return (
          <p key={i} className="my-2 first:mt-0 last:mb-0">
            {b.lines.map((ln, j) => (
              <span key={j}>
                {j > 0 && <br />}
                {renderInline(parseInline(ln), `p${i}-${j}`)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

// #45 (UI half) — recommendation-change transparency. The concurrent
// backend agent makes single_brain emit an explicit "I removed X because
// …" line in the reply when the gate drops a previously-shown policy on a
// new constraint. We detect that sentence in the rendered reply and ALSO
// lift it into a small, visually-distinct notice rendered ABOVE the cited
// cards so the change is impossible to miss (it still reads inline in the
// prose via the markdown renderer too). Matches several natural phrasings
// the model uses; EN + HI. Returns the matched sentence(s) or null.
function extractRecommendationChange(text: string): string | null {
  if (!text) return null;
  // Sentence-ish split that keeps Devanagari danda + ./!/? boundaries.
  const sentences = text
    .replace(/\n+/g, " ")
    .split(/(?<=[.!?।])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const triggers =
    /\b(I (?:have |'ve )?(?:removed|dropped|taken (?:out|off)|excluded|swapped out)|no longer (?:recommend|showing|suggesting)|removed (?:it|this|that) because|replacing|replaced)\b|(?:हटा दिया|अब (?:नहीं|recommend नहीं)|निकाल दिया|बदल दिया)/i;
  const hits = sentences.filter((s) => triggers.test(s));
  if (hits.length === 0) return null;
  // Cap at the first two matched sentences so a long reply doesn't dump a
  // paragraph into the notice; the full reasoning stays in the prose.
  return hits.slice(0, 2).join(" ");
}

function Message({
  m,
  marketplace,
  profile,
  premiumBand,
  onOpenMarketplace,
}: {
  m: DisplayMessage;
  marketplace?: MarketplaceResponse | null;
  profile?: UserProfile;
  premiumBand?: PredictedPremiumBandResponse | null;
  onOpenMarketplace?: () => void;
}) {
  const isUser = m.role === "user";
  // V3 FIX 3 — split off the trailing "⏸ paused" marker (appended by
  // interruptBotAudio) so we can render it as gray italic instead of plain
  // body text. Only matches an exact-suffix; embedded "paused" in normal
  // prose is untouched.
  const rawContent = isUser ? m.content : stripInlineCitations(m.content);
  const PAUSED_SUFFIX = " ⏸ paused";
  const isPaused = !isUser && rawContent.endsWith(PAUSED_SUFFIX);
  const displayContent = isPaused
    ? rawContent.slice(0, -PAUSED_SUFFIX.length)
    : rawContent;
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // KI-030 — Auto-play the bot's TTS reply when the message first mounts.
  // Replaces the old detached `new Audio(url).play()` approach which created
  // an element OUTSIDE the DOM tree — that element couldn't be found by the
  // `document.querySelectorAll("audio")` call in useLiveConversation's
  // barge-in handler, so the bot kept reading the full TTS even when the
  // user spoke over it. Now playback lives on a DOM audio element, which
  // querySelectorAll DOES find — so saying anything during the bot's reply
  // pauses it instantly.
  // Played only on mount (one-shot) so chat-history rehydration doesn't
  // replay every old reply. (audioUrl is also stripped from localStorage on
  // persist, so old messages don't have URLs to replay anyway.)
  //
  // V3 FIX 1 — autoplay/observer race. An IntersectionObserver (or any
  // mount-time effect) may try to play() the element before its metadata is
  // ready, resulting in a NotSupportedError or a silent no-op. Wait for
  // `loadedmetadata` before calling play(); if the metadata has already
  // arrived by the time the effect runs, play() immediately. readyState ≥ 1
  // means HAVE_METADATA per the HTMLMediaElement spec.
  useEffect(() => {
    const el = audioRef.current;
    if (!m.audioUrl || !el) return;
    const tryPlay = () => {
      el.play().catch(() => {
        /* autoplay blocked — user can click the inline control to listen */
      });
    };
    if (el.readyState >= 1 /* HAVE_METADATA */) {
      tryPlay();
      return;
    }
    el.addEventListener("loadedmetadata", tryPlay, { once: true });
    return () => el.removeEventListener("loadedmetadata", tryPlay);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={`flex animate-fade-up ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] sm:max-w-[75%] px-4 py-3 ${
        isUser ? "bubble-user" : "bubble-assistant"
      }`}>
        {!isUser && (
          <div className="flex items-center gap-1.5 mb-1.5 text-[10px] uppercase tracking-[0.14em] font-semibold text-[color-mix(in_srgb,var(--primary)_72%,var(--muted-foreground))]">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--primary)]" />
            Advisor
          </div>
        )}
        {isUser ? (
          <div className="text-sm sm:text-[15px] whitespace-pre-wrap leading-relaxed break-words">
            {displayContent}
          </div>
        ) : (
          <div className="break-words">
            {/* #45 — recommendation-change notice, surfaced ABOVE the prose
                so a dropped/swapped policy is impossible to miss. The same
                sentence still reads inline in the markdown body below. */}
            {(() => {
              const change = extractRecommendationChange(displayContent);
              if (!change) return null;
              return (
                <div
                  className="mb-2.5 flex items-start gap-2 rounded-xl border border-amber-300/80 bg-amber-50 dark:bg-amber-900/20 dark:border-amber-700/60 px-3 py-2"
                  role="status"
                >
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.3"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="shrink-0 mt-px text-amber-600 dark:text-amber-400"
                    aria-hidden="true"
                  >
                    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
                    <path d="M12 9v4" />
                    <path d="M12 17h.01" />
                  </svg>
                  <span className="text-[12.5px] leading-snug text-amber-900 dark:text-amber-200">
                    {renderInline(parseInline(change), "rec-change")}
                  </span>
                </div>
              );
            })()}
            <MarkdownMessage source={displayContent} />
            {isPaused && (
              <span className="ml-1 italic opacity-70 text-sm">
                ⏸ paused
              </span>
            )}
          </div>
        )}
        {m.audioUrl && (
          <audio
            ref={audioRef}
            controls
            src={m.audioUrl}
            className="mt-2 w-full max-w-xs"
            style={{ height: 32 }}
          />
        )}
        {/* KI-278 (2026-05-16) — voice-output failure notice. The bug:
            Sarvam ran out of TTS credits → backend returned a text reply
            with no audio and NO explanation, so the user saw a voice-less
            answer and asked "no voice in reply. wtf?". The failure is now
            LOUD: a small inline line under the bubble that tells the user
            the written answer is complete and why voice is unavailable. */}
        {!isUser && !m.audioUrl && m.ttsNotice && (
          <div
            className="mt-2 flex items-start gap-1.5 text-xs text-[var(--muted-foreground)]"
            role="status"
          >
            <span aria-hidden className="mt-px">🔇</span>
            <span>{m.ttsNotice}</span>
          </div>
        )}
        {!isUser && m.citations && m.citations.length > 0 && (
          <CitedPolicyCards
            citations={m.citations}
            marketplace={marketplace}
            profile={profile}
            premiumBand={premiumBand}
            onOpenMarketplace={onOpenMarketplace}
          />
        )}
      </div>
    </div>
  );
}

function gradeColor(grade: string): string {
  const map: Record<string, string> = {
    A: "bg-emerald-500 text-white",
    B: "bg-teal-500 text-white",
    C: "bg-amber-500 text-white",
    D: "bg-orange-500 text-white",
    F: "bg-red-500 text-white",
  };
  return map[grade] || "bg-stone-400 text-white";
}

// FIX #21 — Reviews / claim-settlement cell for the in-chat COMPARE modal.
// The compare modal previously showed POLICY DETAILS + premium but NO
// reviews; the earlier Bug-3 fix only added the inline-card "Reviews:"
// line. This cell is wired into PolicyCompareModal via renderReviewsFor
// and reuses the SAME getInsurerReviews fetch + fields as the inline card
// (letter_grade / value_0_100 / claim_settlement_ratio_pct / headline) so
// the two surfaces stay consistent. Keyed per insurer_slug; one fetch per
// column. ALWAYS renders a labelled state — loading / compiled / data —
// never silently vanishes (#76 rule).
function CompareReviewsCell({ insurerSlug }: { insurerSlug: string }) {
  // Keyed-by-slug result map (mirrors CitedPolicyCards' `reviews[slug]`
  // pattern, which is the lint-clean shape used elsewhere in this file):
  // the effect ONLY calls the setter from inside the async .then/.catch
  // callbacks — never synchronously in the effect body — so it does not
  // trip `react-hooks/set-state-in-effect`. The displayed state for the
  // current slug is derived from the map at render time.
  const [byslug, setBySlug] = useState<
    Record<string, InsurerReviews | null>
  >({});
  useEffect(() => {
    let alive = true;
    if (!insurerSlug) return;
    if (insurerSlug in byslug) return;
    getInsurerReviews(insurerSlug)
      .then((r) => {
        if (alive) setBySlug((p) => ({ ...p, [insurerSlug]: r }));
      })
      .catch(() => {
        if (alive) setBySlug((p) => ({ ...p, [insurerSlug]: null }));
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insurerSlug]);

  // undefined → not yet fetched (loading); null → fetched-but-empty;
  // object → data. Empty slug is treated as null (graceful missing).
  const rv: InsurerReviews | null | undefined = !insurerSlug
    ? null
    : insurerSlug in byslug
      ? byslug[insurerSlug]
      : undefined;

  if (rv === undefined) {
    return (
      <div
        style={{
          fontSize: 12,
          color: "var(--muted-foreground)",
          fontStyle: "italic",
        }}
      >
        Loading insurer reputation…
      </div>
    );
  }
  if (rv === null) {
    return (
      <div
        style={{
          fontSize: 12,
          color: "var(--muted-foreground)",
          fontStyle: "italic",
        }}
      >
        Reputation data being compiled for this insurer.
      </div>
    );
  }
  // FIX #32 — render the SAME FULL reputation section the policy DETAIL
  // modal shows (page.tsx:6169-6171) instead of a condensed summary.
  // Reuse the existing InsurerReviewsBlock component (no duplication);
  // mirror the detail modal's guard exactly: full 6-bucket block when
  // the payload has at least one headline metric, otherwise the same
  // one-line graceful fallback (never a blank box — #76).
  const s = rv.aggregate_score || {};
  const cm = rv.claim_metrics || {};
  const csr = cm.claim_settlement_ratio_pct;
  const hasHeadlineMetric =
    !!s.letter_grade ||
    s.value_0_100 != null ||
    csr != null ||
    !!s.headline;
  if (!hasHeadlineMetric) {
    return (
      <div
        style={{
          fontSize: 12,
          color: "var(--muted-foreground)",
          fontStyle: "italic",
        }}
      >
        No published reputation metrics for this insurer yet.
      </div>
    );
  }
  return <InsurerReviewsBlock reviews={rv} />;
}

// CitedPolicyCards — structured per-policy cards rendered BELOW the
// assistant's prose reply. One card per cited policy with insurer logo,
// policy name, scorecard grade + one-liner, source-PDF link, and a
// "View details" button. A top-right "Compare all" button opens the new
// PolicyCompareModal in side-by-side mode.
function CitedPolicyCards({
  citations,
  marketplace,
  profile,
  premiumBand,
  onOpenMarketplace,
}: {
  citations: Citation[];
  marketplace?: MarketplaceResponse | null;
  profile?: UserProfile;
  // Profile-level predicted premium band (same number rendered in the chat
  // header chip). Threaded into PolicyCompareModal so non-curated policies
  // can surface it as their indicative reference.
  premiumBand?: PredictedPremiumBandResponse | null;
  onOpenMarketplace?: () => void;
}) {
  const [cards, setCards] = useState<Record<string, ScorecardResponse | null>>({});
  // Keyed by insurer_slug (reviews are per-insurer, not per-policy) so
  // several cited policies from the same insurer share one fetch/result.
  const [reviews, setReviews] = useState<Record<string, InsurerReviews | null>>({});
  const [compareOpen, setCompareOpen] = useState(false);

  // Build a policy_id → MarketplacePolicy lookup so the modal can render the
  // same 4-stat grid + highlights that the marketplace cards show. When the
  // marketplace hasn't loaded yet (or a cited policy isn't in the corpus),
  // the modal's PolicyHighlights section silently skips.
  // Canonical-resolving index (#57/#58/#59 root cause): the compare modal
  // looks up the marketplace row by the CITED policy_id. A recommendation
  // can cite a doctype/variant/alias id that is not byte-equal to the
  // marketplace card's policy_id (same canonical-identity class #40 solved
  // for grades). An exact-only map then returns undefined → the card loses
  // its Hospitals link, SI falls back to "As per policy schedule", and it
  // renders fewer fields (asymmetric). So we also register weaker canonical
  // keys: the doctype-stripped product_key, the normalised policy_name, and
  // every alias name — without ever overwriting a real exact-id hit.
  const _DOCT_RE = /__(wordings|brochure|cis|prospectus)$/;
  const _pkOf = (s: string) => (s || "").replace(_DOCT_RE, "");
  const _nmKey = (s: string) =>
    "nm:" + (s || "").trim().toLowerCase().replace(/\s+/g, " ");
  const policyById: Record<string, MarketplacePolicy> = (() => {
    const out: Record<string, MarketplacePolicy> = {};
    if (!marketplace) return out;
    // Pass 1 — exact policy_id is the strongest key; set first.
    for (const p of marketplace.policies) out[p.policy_id] = p;
    // Pass 2 — weaker canonical keys, only when not already a real id hit.
    for (const p of marketplace.policies) {
      const k = _pkOf(p.policy_id);
      if (k && !(k in out)) out[k] = p;
      const nk = _nmKey(p.policy_name);
      if (nk !== "nm:" && !(nk in out)) out[nk] = p;
      for (const a of p.aliases ?? []) {
        const ak = _nmKey(a);
        if (ak !== "nm:" && !(ak in out)) out[ak] = p;
      }
    }
    return out;
  })();
  const _resolvePolicy = (
    id: string,
    name?: string,
  ): MarketplacePolicy | undefined =>
    policyById[id] ??
    policyById[_pkOf(id)] ??
    (name ? policyById[_nmKey(name)] : undefined);

  // Translate the live UserProfile (chat-side) into the shapes the
  // premium-bulk and scorecard-bulk endpoints expect. These two shapes are
  // similar but not identical — premium ignores parents_*; scorecard uses
  // them — so we pick the subset each widget cares about.
  const premiumProfile: PremiumBulkProfile | undefined = profile
    ? {
        age: profile.age ?? undefined,
        family_size: undefined,
        dependents: profile.dependents ?? undefined,
        location_tier: profile.location_tier ?? undefined,
        // #52A — was hardcoded `undefined`, so the per-policy panel ignored
        // smoker while the header band reflected it. Pass the real flag
        // (?? undefined preserves an explicit non-smoker `false`).
        smoker: profile.smoker ?? undefined,
        pre_existing_conditions:
          profile.health_conditions && profile.health_conditions.length > 0
            ? (profile.health_conditions.includes("diabetes") ||
              profile.health_conditions.includes("hypertension")
                ? "diabetes_or_hypertension"
                : profile.health_conditions.includes("heart_disease")
                  ? "heart_disease"
                  : "none")
            : undefined,
      }
    : undefined;
  const scorecardProfile: BulkScorecardProfile | undefined = profile
    ? {
        age: profile.age ?? undefined,
        dependents: profile.dependents ?? undefined,
        health_conditions: profile.health_conditions ?? undefined,
        primary_goal: profile.primary_goal ?? undefined,
        location_tier: profile.location_tier ?? undefined,
        income_band: profile.income_band ?? undefined,
        budget_band: profile.budget_band ?? undefined,
        existing_cover_inr: profile.existing_cover_inr ?? undefined,
        parents_to_insure: profile.parents_to_insure ?? undefined,
        parents_age_max: profile.parents_age_max ?? undefined,
        parents_has_ped: profile.parents_has_ped ?? undefined,
        // Task #31 — feed the deterministic profile_summary generator: the
        // copay-preference tag, family-history-aware PED caveat, and the
        // SI-headroom strength all read these.
        copay_pct: profile.copay_pct ?? undefined,
        desired_sum_insured_inr: profile.desired_sum_insured_inr ?? undefined,
        family_medical_history: profile.family_medical_history ?? undefined,
      }
    : undefined;

  // KI-278 — the backend now sends EXACTLY the policies the assistant named
  // in its prose, in the order it presented them (single source of truth).
  // Dedupe by policy_id only as a belt-and-braces guard; do NOT cap the
  // count — capping is what made "4 named, 3 cards". One card per
  // recommended policy so the panel mirrors the answer 1:1.
  const seen = new Set<string>();
  const topPolicies = citations.filter((c) => {
    if (seen.has(c.policy_id)) return false;
    seen.add(c.policy_id);
    return true;
  });

  useEffect(() => {
    // Task #31 — pass session_id so each cited card's grade +
    // profile_summary are profile-aware (same as the marketplace card).
    const sid = typeof window !== "undefined" ? sessionStorage.getItem("insurance_session_id") || undefined : undefined;
    for (const c of topPolicies) {
      if (cards[c.policy_id] !== undefined) continue;
      getScorecard(c.policy_id, sid)
        .then((s) => setCards((p) => ({ ...p, [c.policy_id]: s })))
        .catch(() => setCards((p) => ({ ...p, [c.policy_id]: null })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citations.map((c) => c.policy_id).join("|")]);

  // ── Re-fetch loop for USER-UPLOADED policies (ADR-044, 2026-05-27) ──
  // The upload endpoint kicks off LLM-assisted extraction in a
  // background asyncio task that lands ~30-60s later. Re-poll each
  // uploaded card every 5s for up to 90s so the chat card refreshes
  // in place when the new extraction → higher completeness lands.
  // Catalogued cards never enter this loop (their extraction was done
  // offline; the initial fetch above is the final state).
  useEffect(() => {
    const sid = typeof window !== "undefined" ? sessionStorage.getItem("insurance_session_id") || undefined : undefined;
    const uploaded = topPolicies.filter((c) => c.policy_id.startsWith("user-upload__"));
    if (uploaded.length === 0) return;
    let cancelled = false;
    let tries = 0;
    const MAX_TRIES = 18; // 18 × 5s ≈ 90s
    const tick = () => {
      if (cancelled) return;
      tries += 1;
      Promise.all(
        uploaded.map((c) =>
          getScorecard(c.policy_id, sid)
            .then((s) => {
              if (cancelled) return null;
              const prev = cards[c.policy_id];
              const completenessJumped = s?.data_completeness_pct != null
                && (prev == null || (s.data_completeness_pct ?? 0) > (prev.data_completeness_pct ?? 0));
              if (completenessJumped) {
                setCards((p) => ({ ...p, [c.policy_id]: s }));
                return true;
              }
              return false;
            })
            .catch(() => false),
        ),
      ).then(() => {
        if (!cancelled && tries < MAX_TRIES) {
          setTimeout(tick, 5000);
        }
      });
    };
    const handle = setTimeout(tick, 5000); // first re-fetch 5s after initial
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topPolicies.filter((c) => c.policy_id.startsWith("user-upload__")).map((c) => c.policy_id).join("|")]);

  // Insurer reputation / reviews. The full detail modal shows a reviews
  // section; the inline cited cards omitted it (user-flagged — same class
  // as #65's claim-experience gap). Fetch once per DISTINCT insurer_slug.
  useEffect(() => {
    const slugs = Array.from(new Set(topPolicies.map((c) => c.insurer_slug)));
    for (const slug of slugs) {
      if (!slug || reviews[slug] !== undefined) continue;
      getInsurerReviews(slug)
        .then((r) => setReviews((p) => ({ ...p, [slug]: r })))
        .catch(() => setReviews((p) => ({ ...p, [slug]: null })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topPolicies.map((c) => c.insurer_slug).join("|")]);

  if (topPolicies.length === 0) return null;

  return (
    <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-[color-mix(in_srgb,var(--primary)_72%,var(--muted-foreground))] font-semibold">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--primary)]" />
          Cited policies
        </div>
        {topPolicies.length >= 2 && (
          <button
            onClick={() => setCompareOpen(true)}
            className="text-[10px] uppercase tracking-wide font-semibold px-2.5 py-1 rounded-full border border-[var(--primary)] text-[var(--primary)] hover:bg-[var(--primary)] hover:text-[var(--primary-foreground)] transition"
          >
            Compare all
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 gap-2.5">
        {topPolicies.map((c) => {
          const sc = cards[c.policy_id];
          const insurerName = c.insurer_slug.replace(/-/g, " ");
          return (
            <div
              key={c.policy_id}
              className="cited-card p-3.5"
            >
              <div className="flex items-start gap-3">
                <InsurerLogo slug={c.insurer_slug} name={insurerName} size={38} />
                <div className="flex-1 min-w-0">
                  <div className="text-[10px] uppercase tracking-[0.12em] text-[var(--muted-foreground)] line-clamp-2 break-words">
                    {insurerName}
                  </div>
                  <div className="font-semibold text-sm line-clamp-2 break-words leading-snug mt-0.5">{c.policy_name}</div>
                  {sc ? (
                    <div className="mt-1">
                      <ProfileSummaryBlock
                        summary={sc.profile_summary}
                        fallback={sc.one_liner}
                        max={3}
                        compact
                      />
                    </div>
                  ) : sc === null ? (
                    <div className="text-[11px] text-[var(--muted-foreground)] italic mt-1">
                      Rating unavailable
                    </div>
                  ) : (
                    <div className="text-[11px] text-[var(--muted-foreground)] italic mt-1">
                      Loading rating…
                    </div>
                  )}
                  {/* #65 — surface the claim-experience (insurer
                      claim-settlement) signal on the inline card too; the
                      full modal had it but these cards omitted it, which
                      the user flagged. Only when the scorecard is loaded
                      and a claim sub-score exists. */}
                  {sc &&
                    (() => {
                      const ce = sc.sub_scores?.find((s) =>
                        /claim/i.test(s.name),
                      );
                      if (!ce) return null;
                      const sig = ce.signals && ce.signals[0];
                      return (
                        <div className="text-[10.5px] text-[var(--muted-foreground)] mt-1 line-clamp-2">
                          <span className="font-semibold text-[var(--foreground)]">
                            Claim experience:
                          </span>{" "}
                          {ce.score}/100
                          {sig ? ` · ${sig}` : ""}
                        </div>
                      );
                    })()}
                  {/* Reviews — the full detail modal renders an insurer
                      reputation/reviews section; these inline cards
                      omitted it (user-flagged). Mirrors the #65
                      claim-experience pattern + the #76 rule: ALWAYS
                      render the labelled line, never silently vanish
                      when the fetch is slow / null. */}
                  {(() => {
                    const rv = reviews[c.insurer_slug];
                    const lbl = (
                      <span className="font-semibold text-[var(--foreground)]">
                        Reviews:
                      </span>
                    );
                    if (rv) {
                      const s = rv.aggregate_score || {};
                      const csr = rv.claim_metrics?.claim_settlement_ratio_pct;
                      const bits: string[] = [];
                      if (s.letter_grade) bits.push(s.letter_grade);
                      if (s.value_0_100 != null) bits.push(`${s.value_0_100}/100`);
                      if (csr != null) bits.push(`${csr}% claims settled`);
                      return (
                        <div className="text-[10.5px] text-[var(--muted-foreground)] mt-1 line-clamp-2">
                          {lbl} {bits.join(" · ")}
                          {s.headline ? ` — ${s.headline}` : ""}
                        </div>
                      );
                    }
                    return (
                      <div className="text-[10.5px] text-[var(--muted-foreground)] italic mt-1">
                        {lbl}{" "}
                        {rv === null
                          ? "reputation data being compiled"
                          : "loading reputation…"}
                      </div>
                    );
                  })()}
                </div>
                {sc && (
                  <div
                    className={`shrink-0 flex flex-col items-center rounded-lg overflow-hidden ${gradeColor(sc.grade)}`}
                    title={`Grade ${sc.grade} · ${sc.overall_score}/100`}
                  >
                    <div className="px-2 pt-0.5 text-[9px] font-semibold opacity-90 uppercase tracking-wide">
                      {sc.grade}
                    </div>
                    <div className="px-2 pb-0.5 text-sm font-bold leading-none">
                      {sc.overall_score}
                      <span className="text-[8px] font-normal opacity-80">/100</span>
                    </div>
                  </div>
                )}
              </div>
              <div className="mt-2.5 flex items-center justify-end gap-2">
                {c.source_url && (
                  <a
                    href={c.source_url}
                    target="_blank"
                    rel="noopener"
                    className="inline-flex items-center gap-1 text-[10px] font-semibold text-[var(--muted-foreground)] hover:text-[var(--primary)] px-2.5 py-1 rounded-full border border-[var(--border)] hover:border-[var(--primary)] transition"
                    title="Open policy PDF"
                  >
                    <PdfIcon /> PDF
                  </a>
                )}
                <button
                  onClick={() => setCompareOpen(true)}
                  className="text-[10px] uppercase tracking-wide font-semibold px-3 py-1 rounded-full bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition"
                >
                  View details
                </button>
              </div>
            </div>
          );
        })}
      </div>
      {compareOpen && (
        <PolicyCompareModal
          policies={topPolicies}
          onClose={() => setCompareOpen(false)}
          profile={profile}
          policyDataFor={(id, name) => _resolvePolicy(id, name)}
          renderPremiumFor={(policyId, policyName) => (
            <PolicyPremiumWidget
              policyId={policyId}
              policyName={policyName}
              profile={premiumProfile}
            />
          )}
          renderScorecardFor={(policyId, policyName) => (
            <PolicyScorecardWidget
              policyId={policyId}
              policyName={policyName}
              profile={scorecardProfile}
            />
          )}
          renderReviewsFor={(insurerSlug) => (
            <CompareReviewsCell insurerSlug={insurerSlug} />
          )}
          onOpenMarketplace={onOpenMarketplace}
        />
      )}
    </div>
  );
}

function ScorecardBadgesForCitations({ citations }: { citations: Citation[] }) {
  const [cards, setCards] = useState<Record<string, ScorecardResponse | null>>({});
  const [expanded, setExpanded] = useState<string | null>(null);

  // Unique top 3 policy_ids from citations (preserve order, dedupe)
  const seen = new Set<string>();
  const topPolicies = citations
    .filter((c) => {
      if (seen.has(c.policy_id)) return false;
      seen.add(c.policy_id);
      return true;
    })
    .slice(0, 3);

  useEffect(() => {
    // Task #31 — profile-aware scorecard fetch (session_id ⇒ same grade +
    // profile_summary as the marketplace card).
    const sid = typeof window !== "undefined" ? sessionStorage.getItem("insurance_session_id") || undefined : undefined;
    for (const c of topPolicies) {
      if (cards[c.policy_id] !== undefined) continue;
      getScorecard(c.policy_id, sid)
        .then((s) => setCards((prev) => ({ ...prev, [c.policy_id]: s })))
        .catch(() => setCards((prev) => ({ ...prev, [c.policy_id]: null })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citations.map((c) => c.policy_id).join("|")]);

  const ready = topPolicies.filter((c) => cards[c.policy_id]);
  if (ready.length === 0) return null;

  return (
    <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">
        Policy Scorecards
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ready.map((c) => {
          const sc = cards[c.policy_id]!;
          const isOpen = expanded === c.policy_id;
          const lowData = sc.data_completeness_pct < 50;
          return (
            <button
              key={c.policy_id}
              onClick={() => setExpanded(isOpen ? null : c.policy_id)}
              className={`text-xs px-2.5 py-1 rounded-lg border transition flex items-center gap-2 ${
                isOpen
                  ? "border-[var(--primary)] bg-[var(--accent)]"
                  : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--primary)]"
              }`}
              title={`${sc.policy_name} · ${sc.one_liner}`}
            >
              <span className={`inline-flex items-center justify-center w-5 h-5 rounded font-bold text-[11px] ${gradeColor(sc.grade)}`}>
                {sc.grade}
              </span>
              <span className="font-medium truncate max-w-[140px]">{sc.policy_name}</span>
              <span className="opacity-60">{sc.overall_score}</span>
              {lowData && <span title="Some policy terms not yet published" className="opacity-50">⚠</span>}
            </button>
          );
        })}
      </div>
      {expanded && cards[expanded] && (
        <ScorecardCard sc={cards[expanded]!} />
      )}
    </div>
  );
}

// Task #31 — the deterministic, profile-aware {strengths, caveat} summary,
// rendered at the TOP of every scorecard surface. ✓-bulleted strengths in a
// positive tone + a single amber "⚠" caveat line. When the structured
// summary is empty / insufficient, `fallback` (the generic one_liner) is
// shown instead so a surface never goes blank. `max` caps the strength
// count on dense surfaces (cited card = 3, modals = 5).
function ProfileSummaryBlock({
  summary,
  fallback,
  max = 5,
  compact = false,
}: {
  summary?: { strengths: string[]; caveat: string | null } | null;
  fallback?: string | null;
  max?: number;
  compact?: boolean;
}) {
  const strengths = (summary?.strengths ?? []).slice(0, max);
  const caveat = summary?.caveat ?? null;
  if (strengths.length === 0) {
    if (!fallback) return null;
    return (
      <div
        className={`text-[var(--muted-foreground)] leading-snug ${compact ? "text-[11.5px]" : "text-xs"}`}
      >
        {fallback}
      </div>
    );
  }
  return (
    <div className={compact ? "space-y-1" : "space-y-1.5"}>
      <ul className="space-y-1">
        {strengths.map((s, i) => (
          <li
            key={i}
            className={`flex items-start gap-1.5 leading-snug ${compact ? "text-[11.5px]" : "text-xs"}`}
          >
            <span
              aria-hidden
              className="shrink-0 mt-[1px] font-bold text-[var(--primary)]"
            >
              ✓
            </span>
            <span className="text-[var(--foreground)]">{s}</span>
          </li>
        ))}
      </ul>
      {caveat && (
        <div
          className={`flex items-start gap-1.5 leading-snug text-amber-700 dark:text-amber-400 ${compact ? "text-[11px]" : "text-[11.5px]"}`}
        >
          <span aria-hidden className="shrink-0 mt-[1px]">
            ⚠
          </span>
          <span>{caveat}</span>
        </div>
      )}
    </div>
  );
}

// Plain-English label per criterion — shown as a sub-line under the name
// so the buyer doesn't need to mentally translate "Cost Predictability" etc.
const CRITERION_BLURB: Record<string, string> = {
  "Coverage Breadth": "What's actually covered when you claim",
  "Cost Predictability": "How likely you'll face surprise out-of-pocket bills",
  "Waiting-Period Friction": "How soon you can actually use the policy",
  "Claim Experience": "Will the insurer actually pay when you claim?",
  "Renewal Protection": "Can you keep this policy at 70+ when you need it most",
  "Bonus & Loyalty": "Rewards for staying claim-free + renewing",
};

function ScorecardCard({ sc }: { sc: ScorecardResponse }) {
  // Sort sub-scores high-to-low so strengths surface first, weaknesses last —
  // mirrors how a human would explain it
  const sortedSubs = [...sc.sub_scores].sort((a, b) => b.score - a.score);
  return (
    <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 text-xs animate-fade-up">
      <div className="flex items-start justify-between mb-3 gap-3">
        <div className="flex items-start gap-3 min-w-0 flex-1">
          <span className={`inline-flex items-center justify-center w-10 h-10 rounded-lg font-bold text-base ${gradeColor(sc.grade)} shrink-0`}>
            {sc.grade}
          </span>
          <div className="min-w-0">
            <div className="font-semibold text-sm truncate">{sc.policy_name}</div>
            <div className="text-[var(--muted-foreground)] text-[11px] leading-snug mt-0.5">{sc.one_liner}</div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-2xl font-bold leading-none">{sc.overall_score}<span className="text-[var(--muted-foreground)] text-sm font-normal">/100</span></div>
          {sc.data_completeness_pct < 50 && (
            <div className="text-[10px] text-[var(--muted-foreground)] mt-0.5">some terms not yet published</div>
          )}
        </div>
      </div>
      <div className="space-y-2.5 mt-4">
        {sortedSubs.map((s) => {
          const barColor = s.score >= 75 ? "bg-emerald-500" : s.score >= 55 ? "bg-amber-500" : "bg-red-400";
          const blurb = CRITERION_BLURB[s.name];
          return (
            <div key={s.name}>
              <div className="flex items-baseline justify-between mb-0.5">
                <div className="min-w-0 flex-1 pr-2">
                  <div className="text-[11px] font-semibold leading-tight">{s.name}</div>
                  {blurb && <div className="text-[10px] text-[var(--muted-foreground)] leading-tight mt-0.5">{blurb}</div>}
                </div>
                <div className="text-right shrink-0">
                  <span className="text-sm font-bold">{s.score}</span>
                  <span className="text-[10px] text-[var(--muted-foreground)] ml-1">/ 100</span>
                  <div className="text-[10px] text-[var(--muted-foreground)] leading-tight">{s.summary}</div>
                </div>
              </div>
              <div className="h-2 rounded-full bg-[var(--muted)] overflow-hidden">
                <div className={`h-full ${barColor} transition-[width] duration-500`} style={{ width: `${Math.max(2, s.score)}%` }} />
              </div>
              {s.signals && s.signals.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-1">
                  {s.signals.slice(0, 4).map((sig, i) => {
                    const isNegative = sig.startsWith("−") || sig.startsWith("-");
                    return (
                      <span
                        key={i}
                        className={`inline-block text-[10px] px-1.5 py-0.5 rounded ${isNegative ? "bg-red-100 text-red-700 dark:bg-red-900/20 dark:text-red-300" : "bg-[var(--accent)] text-[var(--foreground)]"}`}
                      >
                        {sig}
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-3 pt-2.5 border-t border-[var(--border)] text-[10px] text-[var(--muted-foreground)] leading-snug">
        This grade weighs six things that decide how well a policy actually protects you — how much it covers, how predictable your costs are, how soon you can claim, how reliably the insurer pays, and its renewal and bonus terms. Open &quot;How is this grade decided?&quot; below to see how each one was judged.
      </div>
    </div>
  );
}

function ThinkingDots({ phase }: { phase?: null | "transcribing" | "thinking" | "speaking" } = {}) {
  // KI-038 — phase-labeled status, not just an opaque blob of dots. Users
  // need to know whether the bot is hearing them ("transcribing"), thinking
  // about the answer ("thinking"), or about to speak ("speaking").
  const label = phase === "transcribing"
    ? "Hearing you…"
    : phase === "speaking"
      ? "Speaking…"
      : "Thinking…";
  return (
    <div className="flex justify-start">
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-2xl px-4 py-3 flex items-center gap-2.5">
        <div className="flex gap-1.5">
          {[0, 1, 2].map((i) => (
            <span key={i} className="w-2 h-2 rounded-full bg-[var(--primary)] opacity-70" style={{ animation: "fade-up 1.2s ease-in-out infinite", animationDelay: `${i * 0.2}s` }} />
          ))}
        </div>
        <span className="text-xs text-[var(--muted-foreground)] font-medium">{label}</span>
      </div>
    </div>
  );
}

function MicIcon() {
  return (<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </svg>);
}

function StopIcon() {
  return (<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2" /></svg>);
}

function PaperclipIcon() {
  return (<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M21.44 11.05 12.25 20.24a5 5 0 0 1-7.07-7.07l9.19-9.19a3 3 0 0 1 4.24 4.24l-9.2 9.19a1 1 0 0 1-1.41-1.41l8.49-8.49" />
  </svg>);
}

function LibraryIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="4" height="16" rx="0.5" />
      <rect x="9" y="4" width="4" height="16" rx="0.5" />
      <rect x="15" y="4" width="6" height="16" rx="0.5" transform="rotate(8 18 12)" />
    </svg>
  );
}

function RupeeIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 4h12" />
      <path d="M6 9h12" />
      <path d="M7 4c1.5 0 5 0 5 2.5S8.5 9 7 9" />
      <path d="M6 14l9 6" />
      <path d="M6 14h3c2 0 4-1 4-3" />
    </svg>
  );
}

function PdfIcon() {
  return (<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
    <polyline points="14 2 14 8 20 8"/>
    <text x="7" y="18" fontSize="6" fill="currentColor" stroke="none" fontWeight="bold">PDF</text>
  </svg>);
}

/* ============================================================ */
/* MARKETPLACE — browse all policies, filter, click to expand   */
/* ============================================================ */

const INSURER_COLOR: Record<string, string> = {
  "aditya-birla":  "bg-orange-600",
  "bajaj-allianz": "bg-blue-700",
  "care-health":   "bg-emerald-700",
  "hdfc-ergo":     "bg-rose-700",
  "icici-lombard": "bg-orange-500",
  "manipalcigna":  "bg-fuchsia-700",
  "new-india":     "bg-indigo-700",
  "niva-bupa":     "bg-cyan-700",
  "star-health":   "bg-amber-600",
  "tata-aig":      "bg-slate-700",
};

// SafeLink — renders a real <a> only when href is non-empty + not a "#"
// placeholder. Otherwise renders the children as a non-interactive span so
// the user never lands on a dead link. Closes #107 ghost-URL prevention.
function SafeLink({ href, children, className, fallbackClassName }: {
  href?: string | null;
  children: React.ReactNode;
  className?: string;
  fallbackClassName?: string;
}) {
  const ok = !!href && href !== "#" && href.startsWith("http");
  if (ok) {
    return <a href={href!} target="_blank" rel="noopener" className={className}>{children}</a>;
  }
  return <span className={fallbackClassName || `${className || ""} opacity-50 cursor-not-allowed`} title="No verified source URL available">{children}</span>;
}

// Jargon — inline component that wraps a term and shows an info popover
// on click with a plain-language explanation. Bilingual via uiLang.
// The "?" explainer is HOVER-only (and keyboard-focus for a11y): it
// appears while you hover/focus the badge and disappears when you stop.
// No click, no × close. The tooltip is width-constrained, sits ABOVE
// the badge centered, and is pointer-events-none so it never blocks or
// thrashes. Scoped group/jg so only the badge — not the whole label —
// triggers it.
function Jargon({ term, children, uiLang }: { term: keyof typeof GLOSSARY; children: React.ReactNode; uiLang: UILang }) {
  const entry = GLOSSARY[term];
  if (!entry) return <>{children}</>;
  const lang = uiLang === "hi" ? "hi" : "en";
  const { title, body } = entry[lang];
  return (
    <span className="inline-flex items-center gap-0.5">
      {children}
      <span className="relative inline-flex group/jg align-middle">
        <span
          tabIndex={0}
          role="img"
          aria-label={`Explain ${String(term)}`}
          className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-[var(--muted-foreground)] text-[8px] text-[var(--muted-foreground)] group-hover/jg:text-[var(--primary)] group-hover/jg:border-[var(--primary)] focus:text-[var(--primary)] focus:border-[var(--primary)] outline-none ml-0.5 cursor-help select-none"
        >
          ?
        </span>
        <span
          role="tooltip"
          className="pointer-events-none absolute z-[60] bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-[min(15rem,72vw)] bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-lg p-2.5 text-left opacity-0 invisible translate-y-0.5 transition-all duration-150 group-hover/jg:opacity-100 group-hover/jg:visible group-hover/jg:translate-y-0 group-focus-within/jg:opacity-100 group-focus-within/jg:visible group-focus-within/jg:translate-y-0"
        >
          <span className="block text-[11px] font-semibold text-[var(--foreground)] mb-1">{title}</span>
          <span className="block text-[10px] text-[var(--muted-foreground)] leading-snug normal-case">{body}</span>
        </span>
      </span>
    </span>
  );
}

function insurerInitials(name: string): string {
  return name.split(" ").map((w) => w[0]).filter(Boolean).join("").slice(0, 2).toUpperCase();
}

// #94 — every insurer's official domain. The previous hand-picked logo
// hotlinks broke constantly (CDN path changes, hotlink protection, 404/
// 410), so cards fell back to letter avatars. We instead render the brand
// mark via Google's favicon service for the official domain — it is
// effectively never down and always returns the real logo — with the
// letter avatar only as the absolute last resort.
const INSURER_DOMAIN: Record<string, string> = {
  "acko": "acko.com",
  "aditya-birla": "adityabirlacapital.com",
  "bajaj-allianz": "bajajallianz.com",
  "care-health": "careinsurance.com",
  "cholamandalam": "cholainsurance.com",
  "go-digit": "godigit.com",
  "hdfc-ergo": "hdfcergo.com",
  "icici-lombard": "icicilombard.com",
  "iffco-tokio": "iffcotokio.co.in",
  "indusind-general": "indusindinsurance.com",
  "manipalcigna": "manipalcigna.com",
  "national-insurance": "nationalinsurance.nic.co.in",
  "new-india": "newindia.co.in",
  "niva-bupa": "nivabupa.com",
  "oriental-insurance": "orientalinsurance.org.in",
  "reliance-general": "reliancegeneral.co.in",
  "royal-sundaram": "royalsundaram.in",
  "sbi-general": "sbigeneral.in",
  "star-health": "starhealth.in",
  "tata-aig": "tataaig.com",
};

function InsurerLogo({ slug, name, homeUrl, size = 44 }: { slug: string; name: string; homeUrl?: string; size?: number }) {
  // #94 — staged fallback: a locally-hosted real logo (durable, no external
  // dependency) → DuckDuckGo's icon service (very reliable, returns the
  // site icon, effectively never hard-404s) → colored letter avatar. The
  // previous gstatic faviconV2 hard-404'd for several insurers
  // (manipalcigna/royalsundaram/sbigeneral/national) so cards fell back to
  // initials.
  const [stage, setStage] = useState(0);
  const color = INSURER_COLOR[slug] || "bg-slate-500";
  let domain = INSURER_DOMAIN[slug];
  if (!domain && homeUrl) {
    try { domain = new URL(homeUrl).hostname.replace(/^www\./, ""); } catch { /* keep undefined */ }
  }
  const sources = [
    `/insurer-logos/${slug}.png`,
    domain ? `https://icons.duckduckgo.com/ip3/${domain}.ico` : "",
  ].filter(Boolean);
  if (stage >= sources.length) {
    return (
      <div
        className={`rounded-lg ${color} text-white flex items-center justify-center font-bold shrink-0`}
        style={{ width: size, height: size, fontSize: size * 0.32 }}
      >
        {insurerInitials(name)}
      </div>
    );
  }
  return (
    // #logo-fix — logos span 1:1 … 4.9:1 aspect ratios and the baked-white
    // backgrounds were flood-filled to transparent. Render at a FIXED HEIGHT
    // with AUTO width (aspect-preserving — never distorted), capped so a
    // very wide mark can't blow out the card header. No box, no background:
    // the transparent logo sits directly on the card.
    <div className="flex items-center shrink-0" style={{ height: size }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={sources[stage]}
        alt={name}
        onError={() => setStage((s) => s + 1)}
        style={{ height: size, width: "auto", maxWidth: size * 2.8, objectFit: "contain" }}
      />
    </div>
  );
}

function MarketplacePanel({
  data,
  onOpenPolicy,
  onClose,
  t,
  isPersonalized,
}: {
  data: MarketplaceResponse;
  onOpenPolicy: (p: MarketplacePolicy) => void;
  onClose: () => void;
  t: (k: StringKey, v?: Record<string, string | number>) => string;
  isPersonalized: boolean;
}) {
  const [search, setSearch] = useState("");
  const [insurerFilter, setInsurerFilter] = useState<string>("all");
  const [maxPED, setMaxPED] = useState(48);
  const [minSI, setMinSI] = useState(500000);
  const [requireAyush, setRequireAyush] = useState(false);
  const [requireCashless, setRequireCashless] = useState(false);
  const [grade, setGrade] = useState<string>("all");
  const [sortBy, setSortBy] = useState<"score" | "name" | "insurer">("score");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [compareOpen, setCompareOpen] = useState(false);
  const MAX_COMPARE = 4;
  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= MAX_COMPARE) return prev;
      return [...prev, id];
    });
  };

  const insurers = Array.from(new Set(data.policies.map((p) => p.insurer_slug))).sort();

  const filtered = data.policies.filter((p) => {
    if (search) {
      const q = search.toLowerCase();
      const aliasHit = (p.aliases || []).some((a) => a.toLowerCase().includes(q));
      if (!p.policy_name.toLowerCase().includes(q) && !p.insurer_name.toLowerCase().includes(q) && !aliasHit) return false;
    }
    if (insurerFilter !== "all" && p.insurer_slug !== insurerFilter) return false;
    // #85 — MIN RATING is a THRESHOLD, not an equality. "A" = A only;
    // "B" = B or better (A,B); "C" = C or better (A,B,C). The old
    // `p.grade !== grade` made "B or better" silently exclude every A.
    if (grade !== "all") {
      const RANK: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };
      const pr = RANK[p.grade] ?? -1;
      if (grade === "A" ? p.grade !== "A" : pr < (RANK[grade] ?? 99)) return false;
    }
    if (p.pre_existing_disease_waiting_months && p.pre_existing_disease_waiting_months > maxPED) return false;
    // #85 / SI RATIONALISATION (D3) — filter on the CORROBORATED SI ceiling
    // (sum_insured_max is now the source-quote-corroborated max; D3). The
    // slider FLOOR (₹5 L) means "Any" — at the lowest setting nothing is
    // filtered, so policies whose corroborated ceiling is below ₹5 L stay
    // reachable. Above the floor it filters for real; policies with NO
    // corroborated SI stay visible (don't punish unpublished data).
    if (minSI > 500000) {
      const _siTiers = p.sum_insured_tiers && p.sum_insured_tiers.length
        ? p.sum_insured_tiers
        : p.sum_insured_options;
      const siCap =
        p.sum_insured_max ??
        (_siTiers.length ? Math.max(..._siTiers) : null);
      if (siCap != null && siCap < minSI) return false;
    }
    if (requireAyush && p.ayush_coverage !== true) return false;
    if (requireCashless && p.cashless_treatment_supported !== true) return false;
    return true;
  });

  const sorted = filtered.sort((a, b) => {
    if (sortBy === "score") return b.overall_score - a.overall_score;
    if (sortBy === "name") return a.policy_name.localeCompare(b.policy_name);
    return a.insurer_name.localeCompare(b.insurer_name);
  });

  return (
    <div className="app-panel app-panel-mobile-full border-t border-[var(--border)] max-h-[80vh] overflow-y-auto scrollbar-thin">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6">
        <div className="flex items-start justify-between gap-4 mb-5">
          <div className="min-w-0">
            <div className="panel-kicker mb-1.5">
              <span className="dot" />
              {t("header.policy_library")}
            </div>
            <h2 className="panel-title text-2xl sm:text-[28px] leading-[1.1]">{t("mp.heading")}</h2>
            <p className="text-[13px] text-[var(--muted-foreground)] mt-2 max-w-2xl leading-relaxed">
              {t("mp.summary", { total: data.total, insurers: data.insurers_indexed })}
            </p>
          </div>
          <button onClick={onClose} className="shrink-0 text-xs text-[var(--muted-foreground)] hover:text-[var(--foreground)] transition">{t("mp.close")}</button>
        </div>

        {/* Filter bar */}
        <div className="filter-shell p-4 mb-5">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.search")}</label>
              <input
                type="text" value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder={t("mp.search_placeholder")}
                className="app-input text-sm py-1.5"
              />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.insurer")}</label>
              <select value={insurerFilter} onChange={(e) => setInsurerFilter(e.target.value)} className="app-input text-sm py-1.5">
                <option value="all">{t("mp.all_insurers")} ({data.insurers_indexed})</option>
                {insurers.map((s) => {
                  const name = data.policies.find((p) => p.insurer_slug === s)?.insurer_name || s;
                  const count = data.policies.filter((p) => p.insurer_slug === s).length;
                  return <option key={s} value={s}>{name} ({count})</option>;
                })}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.min_rating")}</label>
              <select value={grade} onChange={(e) => setGrade(e.target.value)} className="app-input text-sm py-1.5">
                <option value="all">{t("mp.all_grades")}</option>
                <option value="A">{t("mp.a_only")}</option>
                <option value="B">{t("mp.b_or_better")}</option>
                <option value="C">{t("mp.c_or_better")}</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.sort_by")}</label>
              <select value={sortBy} onChange={(e) => setSortBy(e.target.value as "score" | "name" | "insurer")} className="app-input text-sm py-1.5">
                <option value="score">{t("mp.sort_score")}</option>
                <option value="name">{t("mp.sort_name")}</option>
                <option value="insurer">{t("mp.sort_insurer")}</option>
              </select>
            </div>
            {/* #85 — each slider in its own bordered cell spanning half the
                row, so the two no longer collide at a column boundary and
                read as one broken double-thumb control. Value chip is
                normal-case (was being uppercased to "48 MO" / "5 L"). */}
            <div className="lg:col-span-2 rounded-xl border border-[var(--border)] bg-[var(--card)] px-3.5 py-2.5">
              <label className="flex items-center justify-between text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-2">
                <span>{t("mp.max_ped_wait")}</span>
                <span className="font-mono text-[var(--primary)] normal-case">{maxPED === 48 ? "Any" : `≤ ${maxPED} mo`}</span>
              </label>
              <input type="range" min={12} max={48} step={6} value={maxPED} onChange={(e) => setMaxPED(parseInt(e.target.value))} className="app-range" />
            </div>
            <div className="lg:col-span-2 rounded-xl border border-[var(--border)] bg-[var(--card)] px-3.5 py-2.5">
              <label className="flex items-center justify-between text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-2">
                <span>{t("mp.min_sum_insured")}</span>
                <span className="font-mono text-[var(--primary)] normal-case">{minSI === 500000 ? "Any" : minSI >= 10000000 ? `₹${minSI / 10000000} Cr+` : `₹${minSI / 100000} L+`}</span>
              </label>
              <input type="range" min={500000} max={10000000} step={500000} value={minSI} onChange={(e) => setMinSI(parseInt(e.target.value))} className="app-range" />
            </div>
            <label className="flex items-center gap-2 text-xs lg:col-span-2">
              <input type="checkbox" checked={requireAyush} onChange={(e) => setRequireAyush(e.target.checked)} className="accent-[var(--primary)]" /> {t("mp.ayush_covered")}
            </label>
            <label className="flex items-center gap-2 text-xs lg:col-span-2">
              <input type="checkbox" checked={requireCashless} onChange={(e) => setRequireCashless(e.target.checked)} className="accent-[var(--primary)]" /> {t("mp.cashless_network")}
            </label>
          </div>
          <div className="text-xs text-[var(--muted-foreground)] mt-3">
            {t("mp.showing")} <span className="font-semibold text-[var(--foreground)]">{sorted.length}</span> {t("mp.of")} {data.total} {t("mp.policies_word")}
          </div>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 pb-20">
          {sorted.map((p) => (
            <PolicyCard
              key={p.policy_id}
              policy={p}
              onOpen={() => onOpenPolicy(p)}
              selected={selectedIds.includes(p.policy_id)}
              onToggleSelect={() => toggleSelect(p.policy_id)}
              selectionDisabled={selectedIds.length >= MAX_COMPARE}
              t={t}
              isPersonalized={isPersonalized}
            />
          ))}
          {sorted.length === 0 && (
            <div className="col-span-full text-center text-sm text-[var(--muted-foreground)] py-12">
              {t("mp.no_match")}
            </div>
          )}
        </div>
      </div>

      {/* Sticky compare action bar */}
      {selectedIds.length > 0 && (
        <div className="fixed bottom-0 left-0 right-0 z-40 bg-[var(--card)] border-t border-[var(--border)] shadow-lg animate-fade-up">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold">{selectedIds.length} of {MAX_COMPARE} selected</span>
              {selectedIds.map((id) => {
                const p = data.policies.find((pp) => pp.policy_id === id);
                if (!p) return null;
                return (
                  <span
                    key={id}
                    className="inline-flex items-center gap-1 text-[11px] bg-[var(--accent)] border border-[var(--border)] rounded-md px-2 py-0.5"
                  >
                    {p.policy_name.slice(0, 28)}{p.policy_name.length > 28 ? "…" : ""}
                    <button
                      onClick={() => toggleSelect(id)}
                      className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                      aria-label="Remove from comparison"
                    >×</button>
                  </span>
                );
              })}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => setSelectedIds([])}
                className="text-xs text-[var(--muted-foreground)] hover:underline"
              >Clear</button>
              <button
                onClick={() => setCompareOpen(true)}
                disabled={selectedIds.length < 2}
                className={`text-sm font-semibold rounded-md px-3 py-1.5 ${selectedIds.length < 2 ? "bg-[var(--muted)] text-[var(--muted-foreground)] cursor-not-allowed" : "bg-[var(--primary)] text-white hover:opacity-90"}`}
              >
                Compare {selectedIds.length >= 2 ? `(${selectedIds.length})` : ""}
              </button>
            </div>
          </div>
        </div>
      )}

      {compareOpen && (
        <ComparisonModal policyIds={selectedIds} onClose={() => setCompareOpen(false)} />
      )}
    </div>
  );
}

function PerPolicyPremiumEstimator({ policy, desiredSI }: { policy: MarketplacePolicy; desiredSI?: number | null }) {
  const [age, setAge] = useState(35);
  // SI RATIONALISATION (D1/D2) — seed the slider from the policy's
  // CORROBORATED tiers (mid tier). When the policy publishes no corroborated
  // SI, fall back to the user's stated desired_sum_insured_inr, else ₹10 L
  // (D2) — the backend then returns sum_insured_disclosure for this case.
  const _siTiers = (policy.sum_insured_tiers && policy.sum_insured_tiers.length
    ? policy.sum_insured_tiers
    : policy.sum_insured_options) || [];
  const defaultSI = _siTiers.length
    ? _siTiers[Math.floor(_siTiers.length / 2)]
    : (desiredSI && desiredSI > 0 ? desiredSI : 1000000);
  const [si, setSI] = useState(defaultSI);
  const [city, setCity] = useState<"metro" | "tier1" | "tier2">("metro");
  const [smoker, setSmoker] = useState(false);
  const [fam, setFam] = useState(0);
  const [ped, setPed] = useState<"none" | "diabetes_or_hypertension" | "heart_disease" | "multiple">("none");
  const [copay, setCopay] = useState(0);
  const [est, setEst] = useState<PremiumEstimateResponse | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => {
      setBusy(true);
      postPremiumEstimate({ age, sum_insured_inr: si, city_tier: city, smoker, family_size: fam, policy_id: policy.policy_id, pre_existing_conditions: ped, copayment_pct: copay })
        .then(setEst).catch(() => setEst(null)).finally(() => setBusy(false));
    }, 150);
    return () => clearTimeout(t);
  }, [age, si, city, smoker, fam, ped, copay, policy.policy_id]);
  const fmt = (v: number) => `₹${v.toLocaleString("en-IN")}`;
  const siDisp = si >= 10000000 ? `${si / 10000000} cr` : `${si / 100000} L`;
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
      <div className="space-y-2">
        <div>
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">
            <span>Age</span><span className="font-mono">{age}</span>
          </div>
          <input type="range" min={18} max={80} value={age} onChange={(e) => setAge(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
        </div>
        <div>
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">
            <span className="inline-flex items-center gap-1">Sum insured<HelpTip id="sum_insured" /></span><span className="font-mono">{siDisp}</span>
          </div>
          <input type="range" min={300000} max={10000000} step={100000} value={si} onChange={(e) => setSI(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
        </div>
        <div>
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">
            <span>Family covered</span><span className="font-mono">{fam === 0 ? "Self only" : `Self + ${fam}`}</span>
          </div>
          <input type="range" min={0} max={6} value={fam} onChange={(e) => setFam(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold mb-0.5">Pre-existing conditions</div>
          <select
            value={ped}
            onChange={(e) => setPed(e.target.value as typeof ped)}
            className="w-full text-[11px] bg-transparent border border-[var(--border)] rounded px-1.5 py-1"
          >
            <option value="none">None</option>
            <option value="diabetes_or_hypertension">Diabetes / hypertension</option>
            <option value="heart_disease">Heart disease</option>
            <option value="multiple">Multiple</option>
          </select>
        </div>
        <div>
          <div className="flex items-center justify-between text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">
            <span className="inline-flex items-center gap-1">Your share per claim<HelpTip id="copay" /></span>
            <span className="font-mono">
              {copay === 0 ? (
                <span className="text-emerald-600">Insurer pays all</span>
              ) : (
                <span>₹{Math.round(si * copay / 100 / 100000) || "0"}L on ₹{Math.round(si / 100000)}L</span>
              )}
            </span>
          </div>
          <input type="range" min={0} max={40} step={5} value={copay} onChange={(e) => setCopay(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
        </div>
        <div className="flex items-center gap-2 flex-wrap text-[11px] pt-1">
          {(["metro", "tier1", "tier2"] as const).map((t) => (
            <button key={t} onClick={() => setCity(t)} className={`px-2 py-0.5 rounded-md border ${city === t ? "border-[var(--primary)] bg-[var(--accent)]" : "border-[var(--border)]"}`}>{t}</button>
          ))}
          <label className="flex items-center gap-1 cursor-pointer">
            <input type="checkbox" checked={smoker} onChange={(e) => setSmoker(e.target.checked)} className="accent-[var(--primary)]" /> Smoker
          </label>
        </div>
      </div>
      <div className="bg-[var(--muted)] rounded-lg p-3 flex flex-col justify-center">
        {busy && <div className="text-[var(--muted-foreground)]">Estimating…</div>}
        {!busy && est && (
          <>
            <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">Indicative annual premium</div>
            <div className="text-2xl font-bold mt-1">{fmt(est.low_inr)} <span className="text-[var(--muted-foreground)] text-sm">–</span> {fmt(est.high_inr)}</div>
            <div className="text-[10px] text-[var(--muted-foreground)] mt-1">point ≈ {fmt(est.point_estimate_inr)}</div>
            {est.sources.length > 0 && (
              <div className="text-[10px] mt-2">
                <a href={est.sources[0]} target="_blank" rel="noopener" className="text-[var(--primary)] hover:underline">verified source ↗</a>
              </div>
            )}
            {/* D2 — verbatim disclosure when the policy publishes no
                corroborated SI and the estimate used a fallback cover. */}
            {est.sum_insured_disclosure && (
              <div className="text-[10px] text-amber-700 dark:text-amber-300 mt-2 leading-tight font-medium">
                {est.sum_insured_disclosure}
              </div>
            )}
            <div className="text-[9px] text-amber-700 dark:text-amber-300 mt-2 leading-tight">Illustrative only. Final quote depends on underwriting.</div>
          </>
        )}
      </div>
    </div>
  );
}

// #82 — reputation is read from several independent sources (IRDAI claims
// data, consumer rating sites, Reddit, YouTube, news). The block makes that
// explicit: one lead "overall" synthesis, then ONE card per source, each a
// number / score / one-line verdict, every card clickable through to where
// that rating actually lives. Same editorial language (.rev-*) as the
// snapshot above — no plain-HTML island.
function InsurerReviewsBlock({ reviews }: { reviews: InsurerReviews }) {
  const cm = reviews.claim_metrics || {};
  const agg = reviews.aggregator_ratings || {};
  const score = reviews.aggregate_score || {};
  const reddit = reviews.reddit_sentiment || {};
  const yt = reviews.youtube_coverage || {};
  const news = reviews.in_news || [];

  // One entry per DISTINCT reviewer (the source legitimately lists the same
  // outlet across an article + videos; raw rendering made one creator
  // appear 3× and read like a bug).
  const normName = (s?: string | null) =>
    (s || "").toLowerCase().replace(/\(.*?\)/g, "").replace(/\s+/g, " ").trim();
  const ytByCreator = new Map<string, { creator?: string; video_url?: string; verdict?: string }>();
  for (const c of yt.top_creators_who_reviewed || []) {
    const k = normName(c.creator);
    if (k && !ytByCreator.has(k)) ytByCreator.set(k, c);
  }
  const ytCreators = [...ytByCreator.values()];

  const aggList = Object.entries(agg).filter(([, v]) => v?.avg_star != null);

  // Plain-language count of how many independent sources fed the grade —
  // so it's obvious this is a synthesis, not a single site's number.
  const sources: string[] = [];
  if (cm.claim_settlement_ratio_pct != null || cm.complaints_per_10k_policies != null)
    sources.push("IRDAI claims data");
  if (aggList.length) sources.push(`${aggList.length} consumer rating ${aggList.length === 1 ? "site" : "sites"}`);
  if ((reddit.notable_themes || []).length || reddit.sentiment_overall) sources.push("Reddit");
  if (ytCreators.length) sources.push("YouTube reviewers");
  if (news.length) sources.push("news coverage");

  // Whole-card link wrapper — the card IS the click target when a source
  // URL exists, a plain tile when it doesn't (never a dead link).
  const Card = ({
    src, value, sub, href,
  }: { src: string; value: React.ReactNode; sub?: string; href?: string | null }) => {
    const inner = (
      <>
        <div className="rev-src">{src}{href && <span className="rev-go" aria-hidden> ↗</span>}</div>
        <div className="rev-val">{value}</div>
        {sub && <div className="rev-sub">{sub}</div>}
      </>
    );
    return href
      ? <SafeLink href={href} className="rev-card rev-card--link">{inner}</SafeLink>
      : <div className="rev-card">{inner}</div>;
  };

  const redditHref = `https://www.reddit.com/search/?q=${encodeURIComponent(reviews.insurer_name + " health insurance")}`;
  // #99 — WEIGHTED by each site's review_count: insuredekho 4.6★ over 386
  // reviews must dominate mouthshut 1.39★ with little/no volume. A flat
  // mean ((4.6+1.39)/2 = 3.0) is meaningless. Fall back to a flat mean
  // ONLY when no site reports a count.
  const _aggW = (() => {
    if (aggList.length === 0) return { avg: null as number | null, weighted: false };
    const withCount = aggList.filter(([, v]) => (v?.review_count ?? 0) > 0);
    if (withCount.length > 0) {
      const num = withCount.reduce((s, [, v]) => s + (v!.avg_star! * (v!.review_count || 0)), 0);
      const den = withCount.reduce((s, [, v]) => s + (v!.review_count || 0), 0);
      return { avg: den > 0 ? num / den : null, weighted: true };
    }
    return {
      avg: aggList.reduce((s, [, v]) => s + (v?.avg_star || 0), 0) / aggList.length,
      weighted: false,
    };
  })();
  const aggAvg = _aggW.avg;
  const cap = (s?: string | null) =>
    s ? s.charAt(0).toUpperCase() + s.slice(1) : "";

  // #90 — EXACTLY six fixed, uniform buckets, always rendered (honest
  // "Not published" when an insurer's source has no value) so the grid is
  // a clean even 3×2, never a ragged auto-fit. Scalar buckets are
  // whole-card links; list buckets carry per-row links.
  type Line = { name?: string | null; meta?: string | null; href?: string | null };
  type Bucket = {
    key: string;
    label: string;
    value: React.ReactNode;
    sub?: string;
    href?: string | null;
    lines?: Line[];
  };
  const buckets: Bucket[] = [
    {
      key: "claims",
      label: `Claims settled · IRDAI ${cm.claim_settlement_ratio_year || ""}`.trim(),
      value:
        cm.claim_settlement_ratio_pct != null
          ? `${cm.claim_settlement_ratio_pct}%`
          : "Not published",
      sub: "Share of claims the insurer actually settled",
      href: cm.claim_settlement_ratio_pct != null ? cm.source_irdai_url : null,
    },
    {
      key: "complaints",
      label: `Complaints · IRDAI ${cm.complaints_year || ""}`.trim(),
      value:
        cm.complaints_per_10k_policies != null
          ? `${cm.complaints_per_10k_policies}`
          : "Not published",
      sub: "Per 10,000 policies — lower is better",
      href: cm.complaints_per_10k_policies != null ? cm.source_irdai_url : null,
    },
    {
      key: "ratings",
      label: "Consumer ratings",
      value:
        aggAvg != null ? (
          <>
            {aggAvg.toFixed(1)}
            <span className="rev-star">★</span>
          </>
        ) : (
          "Not rated"
        ),
      sub:
        aggList.length > 0
          ? `${_aggW.weighted ? "Weighted by review volume across" : "Average across"} ${aggList.length} rating ${aggList.length === 1 ? "site" : "sites"}`
          : "No public aggregator rating",
      lines: aggList.map(([portal, v]) => ({
        name: portal,
        meta: `${v?.avg_star}★${v?.review_count != null ? ` (${v.review_count.toLocaleString()})` : ""}`,
        href: v?.url,
      })),
    },
    {
      key: "reddit",
      label: "Customer voice · Reddit",
      value: (
        <span className="rev-verdict">
          {cap(reddit.sentiment_overall) || "Discussed"}
        </span>
      ),
      sub:
        (reddit.notable_themes || []).slice(0, 2).join(" · ") ||
        "Community discussion on Reddit",
      href: redditHref,
    },
    {
      key: "youtube",
      label: "Reviewer voice · YouTube",
      value: (
        <span className="rev-verdict">
          {cap(yt.overall_youtube_sentiment) ||
            (ytCreators.length ? "Reviewed" : "No coverage")}
        </span>
      ),
      sub:
        ytCreators.length === 0
          ? "No independent video reviews found"
          : undefined,
      lines: ytCreators.slice(0, 3).map((c) => ({
        name: c.creator,
        meta: c.verdict,
        href: c.video_url,
      })),
    },
    {
      key: "news",
      label: "In the news",
      value: (
        <span className="rev-verdict">
          {news.length
            ? `${news.length} mention${news.length > 1 ? "s" : ""}`
            : "No recent coverage"}
        </span>
      ),
      sub: news.length === 0 ? "No recent press coverage found" : undefined,
      lines: news.slice(0, 3).map((n) => ({
        name: n.headline,
        meta: [n.publication, n.date].filter(Boolean).join(", "),
        href: n.url,
      })),
    },
  ];

  const BucketCard = ({ b }: { b: Bucket }) => {
    const hasLines = (b.lines || []).length > 0;
    const linkable = !hasLines && !!b.href;
    const body = (
      <>
        <div className="rev-src">
          {b.label}
          {linkable && <span className="rev-go" aria-hidden> ↗</span>}
        </div>
        <div className="rev-val">{b.value}</div>
        {b.sub && <div className="rev-sub">{b.sub}</div>}
        {hasLines && (
          <div className="rev-lines">
            {b.lines!.map((ln, i) => (
              <SafeLink key={i} href={ln.href || undefined} className="rev-line">
                <span className="rev-line-name">{ln.name}</span>
                {ln.meta && (
                  <span className="rev-line-verdict"> — {ln.meta}</span>
                )}
                {ln.href && <span className="rev-go" aria-hidden> ↗</span>}
              </SafeLink>
            ))}
          </div>
        )}
      </>
    );
    return linkable ? (
      <SafeLink href={b.href!} className="rev-card rev-card--link">
        {body}
      </SafeLink>
    ) : (
      <div className="rev-card">{body}</div>
    );
  };

  return (
    <div className="space-y-3">
      {(score.headline || score.value_0_100 != null) && (
        <div className="rev-lead">
          {score.value_0_100 != null && (
            <span className={`rev-grade ${gradeColor(score.letter_grade || "C")}`}>
              {score.value_0_100}<small>{score.letter_grade}</small>
            </span>
          )}
          <div className="min-w-0">
            <div className="rev-lead-head">{score.headline}</div>
            {sources.length > 0 && (
              <div className="rev-lead-sub">Synthesised from {sources.join(", ")}.</div>
            )}
          </div>
        </div>
      )}

      <div className="rev-grid">
        {buckets.map((b) => (
          <BucketCard key={b.key} b={b} />
        ))}
      </div>
    </div>
  );
}

function PolicyCard({
  policy,
  onOpen,
  selected,
  onToggleSelect,
  selectionDisabled,
  t,
  isPersonalized = false,
}: {
  policy: MarketplacePolicy;
  onOpen: () => void;
  selected: boolean;
  onToggleSelect: () => void;
  selectionDisabled: boolean;
  t: (k: StringKey, v?: Record<string, string | number>) => string;
  isPersonalized?: boolean;
}) {
  // SI RATIONALISATION (D1) — IDENTICAL helper as the compare card /
  // SnapshotView "Cover amount" (single shared fmtSumInsured) so the same
  // policy never reads differently across the marketplace card, the detail
  // snapshot and the compare card. Continuous band → "₹X – ₹Y"; discrete
  // plans → tier list; no corroborated SI → "As per policy schedule".
  const siDisplay = fmtSumInsured(policy);
  // Translate the grade one-liner — backend produces fixed English strings;
  // we map them to i18n keys to flip with the UI language.
  const oneLinerKey = ({ A: "grade.a", B: "grade.b", C: "grade.c", D: "grade.d", F: "grade.f" } as Record<string, StringKey>)[policy.grade] || "grade.c";
  const oneLiner = t(oneLinerKey);
  return (
    <div className="policy-card relative text-left p-4 group" data-selected={selected}>
      <label
        className={`absolute top-2 right-2 z-10 flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border ${selected ? "border-[var(--primary)] bg-[var(--accent)] text-[var(--primary)]" : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)]"} ${selectionDisabled && !selected ? "opacity-40 cursor-not-allowed" : "cursor-pointer hover:border-[var(--primary)]"} transition`}
        onClick={(e) => e.stopPropagation()}
      >
        <input
          type="checkbox"
          checked={selected}
          disabled={selectionDisabled && !selected}
          onChange={onToggleSelect}
          className="accent-[var(--primary)] w-3 h-3"
        />
        {selected ? t("mp.selected") : t("mp.compare")}
      </label>
      {/* Card body is a div+role=button (NOT <button>) because it contains
          jargon "?" icons and a "src" pill which are themselves <button>s.
          HTML disallows button-in-button → hydration error. */}
      <div
        role="button"
        tabIndex={0}
        onClick={onOpen}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); } }}
        className="w-full text-left cursor-pointer"
      >
        {/* #93 — the insurer + policy name get the FULL row width. The old
            inline "SEE SCORE / build profile" pill stole ~90px and crushed
            names into "SBI General… / Super Health…". For un-scored
            policies the "Complete your profile…" line right below already
            says it, so no pill here; when scored we show a COMPACT grade
            chip that doesn't squeeze the title. */}
        <div className="flex items-start gap-3 mb-3 pr-16">
          <InsurerLogo slug={policy.insurer_slug} name={policy.insurer_name} homeUrl={policy.insurer_home_url} size={44} />
          <div className="flex-1 min-w-0">
            <div className="text-xs text-[var(--muted-foreground)] leading-snug line-clamp-2 break-words">{policy.insurer_name}</div>
            <div className="font-semibold text-sm leading-snug line-clamp-3 break-words group-hover:text-[var(--primary)] transition">{policy.policy_name}</div>
            {policy.aliases && policy.aliases.length > 0 && (
              <div className="text-xs text-slate-500 italic mt-0.5 line-clamp-2 break-words">
                Also marketed as: {policy.aliases.join(", ")}
              </div>
            )}
          </div>
          {isPersonalized && (
            <div className={`shrink-0 flex flex-col items-center rounded-lg overflow-hidden ${gradeColor(policy.grade)}`}>
              <div className="px-2 pt-0.5 text-[10px] font-semibold opacity-90 uppercase tracking-wide">{policy.grade}</div>
              <div className="px-2 pb-0.5 text-base font-bold leading-none">{policy.overall_score}<span className="text-[10px] font-normal opacity-80">/100</span></div>
            </div>
          )}
        </div>
        {/* Task #31 — when the card is personalised AND the deterministic
            profile_summary produced real strengths, surface them at the
            top of the card; otherwise keep the existing behaviour (generic
            grade one-liner when personalised, locked-score msg when not). */}
        {isPersonalized && (policy.profile_summary?.strengths?.length ?? 0) > 0 ? (
          <div className="mb-3">
            <ProfileSummaryBlock
              summary={policy.profile_summary}
              max={3}
              compact
            />
          </div>
        ) : (
          <p className="text-xs text-[var(--muted-foreground)] mb-3 line-clamp-2">{isPersonalized ? oneLiner : t("card.score_locked_msg")}</p>
        )}
        {/* #97/#98 — DYNAMIC tiles: cover (always) + the next 3 facts that
            are actually KNOWN for THIS policy, in decision-priority order.
            A card never shows a blank "Not stated" slot while richer info
            exists (the modal-vs-card mismatch). "?" is the SHARED hover
            GlossaryTip — identical component, copy and style as the
            detail-modal snapshot. #47/#89: equal-height tiles, label
            reserves 2 lines, value pinned to the bottom. */}
        <div className="grid grid-cols-2 gap-2">
          {(() => {
            const cand: { term: "cover" | "copay" | "ped" | "cashless" | "ncb" | "entry" | "initwait" | "room"; label: string; value: string | null }[] = [
              { term: "cover", label: "Sum insured", value: siDisplay },
              { term: "copay", label: "Mandatory co-pay", value: policy.copayment_pct == null ? null : policy.copayment_pct === 0 ? "None" : `${policy.copayment_pct}%` },
              { term: "ped", label: "Pre-existing wait", value: policy.pre_existing_disease_waiting_months == null ? null : policy.pre_existing_disease_waiting_months === 0 ? "None" : `${policy.pre_existing_disease_waiting_months} mo` },
              { term: "cashless", label: "Cashless", value: policy.cashless_treatment_supported === true ? (policy.network_count_official ? `Yes · ${(policy.network_count_official / 1000).toFixed(0)}K+` : "Yes") : policy.cashless_treatment_supported === false ? "No" : null },
              { term: "ncb", label: "No-claim bonus", value: policy.no_claim_bonus_pct == null ? null : policy.no_claim_bonus_pct === 0 ? "None" : `+${policy.no_claim_bonus_pct}%` },
              { term: "entry", label: "Entry age", value: (policy.min_entry_age != null || policy.max_entry_age != null) ? `${policy.min_entry_age ?? "?"}–${policy.max_entry_age ?? "?"} yrs` : null },
              { term: "initwait", label: "Initial wait", value: policy.initial_waiting_period_days == null ? null : policy.initial_waiting_period_days === 0 ? "None" : `${policy.initial_waiting_period_days} d` },
              { term: "room", label: "Room cap", value: policy.room_rent_capping ? policy.room_rent_capping : null },
            ];
            const tiles = cand.filter((c) => c.value != null).slice(0, 4);
            return tiles.map((c) => (
              <div key={c.term} className="cs-tile">
                <div className="cs-label">{c.label}<GlossaryTip term={c.term} /></div>
                <div className="cs-value">{c.value}</div>
              </div>
            ));
          })()}
        </div>
      </div>
    </div>
  );
}

type MethodologyResponse = {
  weights: Record<string, number>;
  scored_fields_count: number;
  total_schema_fields: number;
  criteria: Array<{
    name: string;
    weight_pct: number;
    consumer_question: string;
    why_it_matters: string;
    fields_driving_score: Array<{ field: string; rule: string }>;
    anchors: string[];
  }>;
  grade_thresholds: Record<string, string>;
  scoring_approach: string;
};

function MethodologyExpander() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<MethodologyResponse | null>(null);
  useEffect(() => {
    if (open && !data) {
      fetch(`${BACKEND_URL}/api/scorecard/methodology`)
        .then((r) => r.json())
        .then(setData)
        .catch(() => setData(null));
    }
  }, [open, data]);
  return (
    <div className="mt-3 border border-[var(--border)] rounded-lg bg-[var(--card)]">
      <button
        onClick={() => setOpen(!open)}
        className="w-full text-left px-3 py-2 text-xs font-semibold flex items-center justify-between hover:bg-[var(--muted)]"
      >
        <span>How is this grade decided? <span className="text-[var(--muted-foreground)] font-normal">(six things that matter, weighted)</span></span>
        <span className="text-[var(--muted-foreground)]">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3 text-xs border-t border-[var(--border)] pt-3">
          {!data && <div className="text-[var(--muted-foreground)] py-2">Loading methodology…</div>}
          {data && (
            <>
              <p className="text-[var(--muted-foreground)] leading-snug">
                Every policy is judged the same way on the things that decide whether health cover actually protects you. Each is rated out of 100 and combined by how much it matters for someone in your situation — here is what each one means and why it carries the weight it does.
              </p>
              {data.criteria.map((c) => (
                <div key={c.name} className="border border-[var(--border)] rounded-md p-2.5 bg-[var(--muted)]">
                  <div className="flex items-baseline justify-between mb-1">
                    <span className="text-xs font-bold">{c.name}</span>
                    <span className="text-[10px] font-semibold text-[var(--primary)]">{c.weight_pct}% of the grade</span>
                  </div>
                  <div className="text-[11px] text-[var(--foreground)] italic mb-1">&ldquo;{c.consumer_question}&rdquo;</div>
                  <div className="text-[11px] text-[var(--muted-foreground)] leading-snug">{c.why_it_matters}</div>
                </div>
              ))}
              <div className="text-[10px] text-[var(--muted-foreground)] pt-1 border-t border-[var(--border)]">
                What the grades mean: {Object.entries(data.grade_thresholds).map(([g, d], i) => (
                  <span key={g}>{i > 0 ? " · " : ""}<strong>{g}</strong> — {String(d).split("—").slice(1).join("—").trim() || String(d).trim()}</span>
                ))}.
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, jargon, uiLang, sourceQuote }: { label: React.ReactNode; value: string; jargon?: keyof typeof GLOSSARY; uiLang?: UILang; sourceQuote?: string }) {
  const [showSrc, setShowSrc] = useState(false);
  return (
    <div className="relative">
      <div className="text-[10px] text-[var(--muted-foreground)] uppercase tracking-wide flex items-center gap-1">
        {jargon && uiLang ? <Jargon term={jargon} uiLang={uiLang}>{label}</Jargon> : <span>{label}</span>}
        {sourceQuote && (
          <button
            onClick={(e) => { e.stopPropagation(); setShowSrc(!showSrc); }}
            className="text-[8px] px-1 py-0.5 rounded border border-[var(--border)] text-[var(--muted-foreground)] hover:text-[var(--primary)] hover:border-[var(--primary)]"
            type="button"
            title={uiLang === "hi" ? "स्रोत देखें" : "View source"}
          >
            src
          </button>
        )}
      </div>
      <div className="text-xs font-semibold">{value}</div>
      {showSrc && sourceQuote && (
        <div className="absolute z-50 top-full mt-1 left-0 w-72 bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-lg p-2.5 animate-fade-up">
          <div className="text-[10px] font-semibold text-[var(--muted-foreground)] mb-1 uppercase tracking-wide">{uiLang === "hi" ? "स्रोत (PDF से उद्धरण)" : "Source (PDF excerpt)"}</div>
          <div className="text-[11px] text-[var(--foreground)] leading-snug italic">&ldquo;{sourceQuote}&rdquo;</div>
          <button onClick={() => setShowSrc(false)} className="absolute top-1 right-1.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)] text-xs">×</button>
        </div>
      )}
    </div>
  );
}

function FIELD_LABEL(field: string): string {
  const map: Record<string, string> = {
    policy_type: "Policy type",
    uin_code: "UIN code",
    min_entry_age: "Min entry age",
    max_entry_age: "Max entry age",
    sum_insured_options: "Sum insured options",
    initial_waiting_period_days: "Initial waiting period",
    pre_existing_disease_waiting_months: "Pre-existing waiting",
    maternity_waiting_months: "Maternity waiting",
    pre_hospitalization_days: "Pre-hospitalisation cover",
    post_hospitalization_days: "Post-hospitalisation cover",
    day_care_treatments_count: "Day-care treatments",
    ayush_coverage: "AYUSH covered",
    maternity_coverage: "Maternity covered",
    newborn_coverage: "Newborn covered",
    organ_donor_expenses: "Organ donor expenses",
    no_claim_bonus_pct: "No-claim bonus",
    restoration_benefit: "Restoration benefit",
    room_rent_capping: "Room rent capping",
    copayment_pct: "Co-payment",
    deductible_amount: "Deductible",
    network_hospital_count: "Network hospitals",
    cashless_treatment_supported: "Cashless supported",
    claim_settlement_ratio: "Claim settlement ratio",
    tat_cashless_authorization_hours: "Cashless TAT",
  };
  return map[field] || field.replace(/_/g, " ");
}

function renderFieldValue(value: unknown, field: string): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    if (field === "sum_insured_options" && value.every((v) => typeof v === "number")) {
      const nums = value as number[];
      const fmt = (n: number) => (n >= 10000000 ? `${n / 10000000} cr` : `${n / 100000} L`);
      return nums.map(fmt).join(", ");
    }
    return value.map((v) => String(v)).join(", ");
  }
  if (typeof value === "number") {
    if (field.endsWith("_pct") || field === "claim_settlement_ratio") return `${value}%`;
    if (field === "network_hospital_count" && value >= 1000) return `${value.toLocaleString("en-IN")}`;
    if (field === "initial_waiting_period_days") return `${value} days`;
    if (field.endsWith("_months")) return `${value} months`;
    if (field.endsWith("_days") || field === "pre_hospitalization_days" || field === "post_hospitalization_days") return `${value} days`;
    if (field === "tat_cashless_authorization_hours") return `${value} hours`;
    if (field === "deductible_amount") return `₹${value.toLocaleString("en-IN")}`;
    return String(value);
  }
  return String(value);
}

// LiveBetaGateModal — the styled risk-confirmation gate for Live (BETA)
// always-on voice. Editorial-fintech system: Fraunces serif title, a
// brand kicker pill, hairline border + soft long shadow, an amber WARNING
// rail for the real failure modes, explicit Confirm / Cancel. Shown every
// time the user enables Live; the live session starts only on Confirm.
// EN/HI parity via an inline `hindi ?` ternary, matching the app pattern.
function LiveBetaGateModal({
  hindi,
  onConfirm,
  onCancel,
}: {
  hindi: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const L = (en: string, hi: string) => (hindi ? hi : en);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  // Escape cancels (treated as "did not confirm"); move focus into the
  // dialog on open so keyboard users land on the safe default (Cancel).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      }
    };
    window.addEventListener("keydown", onKey);
    const tid = window.setTimeout(() => {
      dialogRef.current
        ?.querySelector<HTMLButtonElement>("[data-autofocus]")
        ?.focus();
    }, 0);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(tid);
    };
  }, [onCancel]);

  // Each risk: a one-line headline + a short clause. Real failure modes,
  // not marketing softening (mirrors lib/useLiveConversation realities).
  const risks: { head: string; body: string }[] = [
    {
      head: L("BETA & unstable", "BETA · अस्थिर"),
      body: L(
        "Still experimental — behaviour can change or break without notice.",
        "अभी प्रयोगात्मक — व्यवहार बिना सूचना बदल या टूट सकता है।"
      ),
    },
    {
      head: L("May cut you off or echo", "बीच में काट सकता है / echo"),
      body: L(
        "Can clip you mid-sentence or hear the bot's own voice and reply to itself.",
        "आपको बीच वाक्य में काट सकता है, या bot की अपनी आवाज़ सुनकर खुद को जवाब दे सकता है।"
      ),
    },
    {
      head: L("Mic capture can silently fail", "Mic चुपचाप fail हो सकता है"),
      body: L(
        "AudioWorklet / AudioContext / VAD / codec issues may leave it listening but capturing nothing.",
        "AudioWorklet / AudioContext / VAD / codec की दिक्कत से यह सुनता दिखे पर कुछ रिकॉर्ड न हो।"
      ),
    },
    {
      head: L("Speech is streamed continuously", "आपकी आवाज़ लगातार stream होती है"),
      body: L(
        "Your microphone stays open and audio is sent continuously while Live is on.",
        "Live चालू रहते आपका माइक खुला रहता है और audio लगातार भेजा जाता है।"
      ),
    },
  ];

  return (
    <div
      className="modal-shell-mobile fixed inset-0 z-[70] bg-black/50 flex items-center justify-center p-4 animate-fade-up"
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="live-gate-title"
        aria-describedby="live-gate-desc"
        onClick={(e) => e.stopPropagation()}
        className="modal-card-mobile section-card w-full max-w-md max-h-[92vh] overflow-y-auto scrollbar-thin"
      >
        <div className="px-6 sm:px-7 py-7">
          {/* Kicker — reuses the landing's brand pill, recoloured amber to
              read as a warning rather than a feature badge. */}
          <span
            className="kicker mb-5"
            style={{
              color: "var(--error)",
              background:
                "color-mix(in srgb, var(--error) 9%, var(--card))",
              borderColor:
                "color-mix(in srgb, var(--error) 26%, var(--border))",
            }}
          >
            <span
              className="dot"
              aria-hidden="true"
              style={{ background: "var(--error)" }}
            />
            {L("Heads up · BETA", "ध्यान दें · BETA")}
          </span>

          <h2
            id="live-gate-title"
            className="font-display text-[1.55rem] sm:text-[1.75rem] leading-[1.12] font-semibold text-[var(--foreground)]"
          >
            {L(
              "Turn on Live always-on voice?",
              "Live always-on आवाज़ चालू करें?"
            )}
          </h2>

          <p
            id="live-gate-desc"
            className="mt-2.5 text-[13.5px] leading-relaxed text-[var(--muted-foreground)]"
          >
            {L(
              "Live keeps your mic open and listens continuously. It is genuinely useful, but it is unstable — please read these before continuing.",
              "Live आपका माइक खुला रखता है और लगातार सुनता है। यह सच में उपयोगी है, पर अस्थिर — आगे बढ़ने से पहले ये ज़रूर पढ़ें।"
            )}
          </p>

          {/* WARNING rail — amber-tinted card, same callout language as the
              landing's amber trust card; one-line risk per row. */}
          <ul className="mt-5 flex flex-col gap-px rounded-2xl border border-[color-mix(in_srgb,var(--error)_28%,var(--border))] bg-[color-mix(in_srgb,var(--error)_5%,var(--card))] overflow-hidden">
            {risks.map((r, i) => (
              <li
                key={i}
                className="flex items-start gap-3 px-4 py-3.5 bg-[var(--card)]/40"
              >
                <span
                  className="shrink-0 mt-0.5 text-[var(--error)]"
                  aria-hidden="true"
                >
                  <svg
                    width="17"
                    height="17"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
                    <path d="M12 9v4" />
                    <path d="M12 17h.01" />
                  </svg>
                </span>
                <span className="min-w-0">
                  <span className="block text-[13px] font-semibold text-[var(--foreground)] leading-tight">
                    {r.head}
                  </span>
                  <span className="block text-[12.5px] text-[var(--muted-foreground)] leading-snug mt-0.5">
                    {r.body}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          <p className="mt-4 text-[12.5px] leading-relaxed text-[var(--muted-foreground)]">
            {L(
              "Prefer Push-to-talk — it's stable and uses Sarvam STT (handles Hindi / Indic correctly).",
              "Push-to-talk बेहतर है — यह स्थिर है और Sarvam STT वापरता है (हिन्दी / Indic सही संभालता है)।"
            )}
          </p>

          {/* Confirm / Cancel. Cancel is the safe default (autofocused). */}
          <div className="mt-7 flex flex-col-reverse sm:flex-row sm:justify-end gap-2.5">
            <button
              type="button"
              data-autofocus
              onClick={onCancel}
              className="px-5 py-2.5 rounded-xl text-[13.5px] font-semibold border border-[var(--border)] text-[var(--foreground)] bg-[var(--card)] hover:border-[var(--primary)] hover:text-[var(--primary)] transition cursor-pointer"
            >
              {L("Cancel", "रद्द करें")}
            </button>
            <button
              type="button"
              onClick={onConfirm}
              className="btn-primary px-5 py-2.5 text-[13.5px] cursor-pointer"
            >
              {L("Confirm — turn on Live", "पुष्टि करें — Live चालू करें")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function ComparisonModal({ policyIds, onClose }: { policyIds: string[]; onClose: () => void }) {
  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setError(null);
    getCompare(policyIds)
      .then(setData)
      .catch((e: Error) => setError(e.message));
  }, [policyIds]);

  return (
    <div className="modal-shell-mobile fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-3 animate-fade-up" onClick={onClose}>
      <div
        className="modal-card-mobile bg-[var(--card)] rounded-2xl shadow-xl w-full max-w-6xl max-h-[92vh] overflow-y-auto scrollbar-thin"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 bg-[var(--card)] border-b border-[var(--border)] px-5 py-4 flex items-center justify-between">
          <div>
            <h3 className="text-base font-bold">Side-by-side comparison</h3>
            <p className="text-[11px] text-[var(--muted-foreground)] mt-0.5">
              Cells with differences are highlighted. Values come directly from each policy&apos;s wording PDF.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-xs text-[var(--muted-foreground)] hover:underline"
          >close</button>
        </div>

        <div className="p-5">
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md p-3">
              Failed to load comparison: {error}
            </div>
          )}
          {!data && !error && (
            <div className="text-sm text-[var(--muted-foreground)] py-12 text-center">
              Loading comparison…
            </div>
          )}
          {data && (
            <div className="table-scroll-x overflow-x-auto -mx-5 px-5">
              <table className="w-full border-collapse text-xs">
                <thead>
                  <tr>
                    <th className="text-left text-[11px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold pb-3 pr-3 sticky left-0 bg-[var(--card)]">
                      Field
                    </th>
                    {data.policies.map((p) => {
                      const color = INSURER_COLOR[p.insurer_slug] || "bg-slate-500";
                      return (
                        <th key={p.policy_id} className="text-left pb-3 px-2 align-bottom min-w-[180px]">
                          <div className="flex items-start gap-2">
                            <div className={`w-8 h-8 rounded-md ${color} text-white flex items-center justify-center font-bold text-[11px] shrink-0`}>
                              {insurerInitials(p.fields.insurer_name as string || p.insurer_slug)}
                            </div>
                            <div className="min-w-0">
                              <div className="text-[10px] text-[var(--muted-foreground)] truncate">{p.insurer_slug}</div>
                              <div className="font-semibold text-xs leading-tight truncate">{p.policy_name}</div>
                              {p.scorecard && (
                                <div className={`inline-block mt-1 text-[10px] font-semibold px-1.5 py-0.5 rounded ${gradeColor(p.scorecard.grade)}`}>
                                  {p.scorecard.grade} · {p.scorecard.overall_score}/100
                                </div>
                              )}
                            </div>
                          </div>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {data.field_order.map((field, fieldIdx) => {
                    const values = data.policies.map((p) => renderFieldValue(p.fields[field], field));
                    const allSame = values.every((v) => v === values[0]);
                    return (
                      <tr key={field} className={fieldIdx % 2 === 0 ? "bg-[var(--muted)]" : ""}>
                        <td className="text-[11px] font-semibold pr-3 py-2 sticky left-0 bg-inherit text-[var(--foreground)] align-top">
                          {FIELD_LABEL(field)}
                        </td>
                        {values.map((v, i) => (
                          <td
                            key={i}
                            className={`px-2 py-2 align-top ${allSame ? "text-[var(--muted-foreground)]" : "text-[var(--foreground)] font-medium"}`}
                          >
                            {v}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="text-[10px] text-[var(--muted-foreground)] mt-3">
                Fields shown in bold differ across the selected policies; greyed values are identical.
                For premium estimates use the calculator on each policy&apos;s detail page.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// #64 — section eyebrow that matches the front-page kicker-pill pattern
// (teal uppercase, dot) so every policy panel reads in the same editorial
// system as the landing page rather than a generic bold label.
function PanelEyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div className="panel-kicker mb-3">
      <span className="dot" />
      {children}
    </div>
  );
}

function PolicyDetailModal({ policy, onClose }: { policy: MarketplacePolicy; onClose: () => void }) {
  // Tri-state: undefined = still loading, null = fetch failed (show Retry),
  // value = loaded. Distinguishing loading-vs-failed is what stops the whole
  // Score section from silently vanishing on a slow/failed scorecard fetch.
  const [sc, setSc] = useState<ScorecardResponse | null | undefined>(undefined);
  const [reviews, setReviews] = useState<InsurerReviews | null>(null);
  const [completeness, setCompleteness] = useState<ProfileCompletenessResponse | null>(null);
  const [method, setMethod] = useState<MethodologyResponse | null>(null);
  useEffect(() => {
    setSc(undefined);
    // Task #31 — pass the session_id so the scorecard (grade +
    // profile_summary) is computed against THIS user's profile, identical
    // to the marketplace card for the same canonical id.
    const sid = typeof window !== "undefined" ? sessionStorage.getItem("insurance_session_id") || undefined : undefined;
    getScorecard(policy.policy_id, sid).then(setSc).catch(() => setSc(null));
    if (policy.insurer_slug) {
      getInsurerReviews(policy.insurer_slug).then(setReviews).catch(() => setReviews(null));
    }
    // Profile completeness gates whether we render the per-user grade.
    // Below threshold: show universal grade only (insurer-quality-led) with a
    // CTA to complete the profile.
    getProfileCompleteness(sid).then(setCompleteness).catch(() => setCompleteness(null));
    // Server-derived methodology counts so the footer never states an
    // invented "24 of 48" — it renders the real "{scored} of {total}".
    fetch(`${BACKEND_URL}/api/scorecard/methodology`).then((r) => r.json()).then(setMethod).catch(() => setMethod(null));
  }, [policy.policy_id, policy.insurer_slug]);

  // Standalone retry so a slow/failed scorecard fetch shows a Retry
  // affordance instead of removing the entire Score section without a trace.
  const retryScorecard = () => {
    setSc(undefined);
    const sid = typeof window !== "undefined" ? sessionStorage.getItem("insurance_session_id") || undefined : undefined;
    getScorecard(policy.policy_id, sid).then(setSc).catch(() => setSc(null));
  };
  const isPersonalized = completeness?.is_personalized === true;

  // #102 — logo + colour now handled inside <InsurerLogo>; the old
  // initials/color medallion consts are gone.
  // #75 + #64 — the "what this covers" grid is now the shared, decision-
  // ordered SnapshotView (What you get · Who qualifies & when · Your share
  // & limits, + a profile-aware Situational disclosure). scFacts recovers
  // any value the flat marketplace row is missing from the SAME data the
  // scorecard reads, so a policy with a full scorecard never shows blanks.
  const scFacts = parseScorecardFacts(sc || null);

  // #87 — every policy has a real source PDF (the doc the corpus was built
  // from). The backend now always emits source_pdf_url: a public origin URL
  // or a backend-served local-corpus path (/api/policy-pdf/...). Resolve the
  // latter against BACKEND_URL so it works in dev and on the Space.
  const _spu = policy.source_pdf_url;
  const pdfHref = _spu
    ? (_spu.startsWith("/api/") ? `${BACKEND_URL}${_spu}` : _spu)
    : `https://www.google.com/search?q=site:${(new URL(policy.insurer_home_url || "https://www.google.com")).hostname}+${encodeURIComponent(policy.policy_name + " policy wording PDF")}`;
  const hasRealPdf = Boolean(_spu);

  return (
    <div className="modal-shell-mobile fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4 animate-fade-up" onClick={onClose}>
      <div className="modal-card-mobile bg-[var(--card)] rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto scrollbar-thin" onClick={(e) => e.stopPropagation()}>
        {/* #64 — header re-grounded on the front-page editorial system:
            kicker pill, Fraunces (.panel-title) policy name, aligned
            insurer / name / PDF row, rounded brand PDF button. */}
        <div className="px-5 sm:px-6 pt-5 pb-4 border-b border-[var(--border)] sticky top-0 bg-[var(--card)] z-10">
          <div className="flex items-start gap-3.5">
            {/* #102 — real insurer logo (was a hardcoded initials
                medallion that never showed the brand mark). */}
            <InsurerLogo slug={policy.insurer_slug} name={policy.insurer_name} homeUrl={policy.insurer_home_url} size={48} />
            <div className="flex-1 min-w-0">
              <div className="panel-kicker mb-1">
                <span className="dot" />
                <a href={policy.insurer_home_url} target="_blank" rel="noopener" className="hover:text-[var(--primary)] transition">
                  {policy.insurer_name}
                </a>
              </div>
              <h3 className="panel-title text-xl sm:text-2xl leading-[1.15] break-words">{policy.policy_name}</h3>
              {policy.aliases && policy.aliases.length > 0 && (
                <div className="text-[11.5px] text-[var(--muted-foreground)] italic mt-1 leading-snug break-words">
                  Also marketed as: {policy.aliases.join(", ")}
                </div>
              )}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              {/* #86 — the insurer's official cashless/network hospital list,
                  alongside the policy PDF. Sourced from the insurer's own
                  domain (a downloadable PDF where one exists, else the
                  official locator). */}
              {policy.network_list_url && (
                <a
                  href={policy.network_list_url}
                  target="_blank"
                  rel="noopener"
                  className="inline-flex items-center gap-1 text-xs px-3 py-2 rounded-lg border border-[var(--border)] text-[var(--foreground)] hover:border-[var(--primary)] hover:text-[var(--primary)] transition"
                  title={`Open ${policy.insurer_name}'s official ${policy.network_list_is_pdf ? "network hospital list (PDF)" : "cashless hospital locator"}`}
                >
                  Hospitals list ↗
                </a>
              )}
              <a
                href={pdfHref}
                target="_blank"
                rel="noopener"
                className="btn-primary inline-flex items-center gap-1.5 text-xs px-3.5 py-2"
                title={hasRealPdf ? "Open the source policy PDF" : "Search the insurer's site for the policy PDF (we don't have a direct link for this policy yet)"}
              >
                <PdfIcon /> {hasRealPdf ? "Policy PDF" : "Find PDF"}
              </a>
              <button onClick={onClose} aria-label="Close" className="shrink-0 w-10 h-10 flex items-center justify-center rounded-full text-[var(--muted-foreground)] hover:text-[var(--foreground)] hover:bg-[var(--muted)] text-2xl leading-none transition">×</button>
            </div>
          </div>
        </div>

        <div className="p-5 space-y-5">
          {/* 1 — DETAILS (#75 + #64): the decision-ordered snapshot —
              What you get · Who qualifies & when cover starts · Your share
              & limits — with a profile-aware Situational disclosure.
              Identical shared component (SnapshotView) as the in-chat
              compare card, so the two surfaces never diverge. */}
          <div>
            <PanelEyebrow>What this policy covers, in plain words</PanelEyebrow>
            <SnapshotView
              policy={policy}
              facts={scFacts}
              profile={completeness?.profile ?? null}
            />
          </div>

          {/* 2 — SCORE (always rendered in this position; tri-state so a
              slow/failed scorecard fetch shows loading/Retry, never a
              silent gap). */}
          <div className="pt-5 border-t border-[var(--border)]">
            <PanelEyebrow>How this policy scores {isPersonalized ? "for you" : "(generic buyer)"}</PanelEyebrow>
            {sc === undefined && (
              <div className="py-8 flex flex-col items-center justify-center text-xs text-[var(--muted-foreground)]">
                <span className="inline-block w-5 h-5 border-2 border-[var(--primary)] border-t-transparent rounded-full animate-spin mb-2" />
                Calculating the detailed scorecard…
              </div>
            )}
            {sc === null && (
              <div className="py-6 text-center text-xs">
                <p className="text-[var(--muted-foreground)] mb-2">Couldn&apos;t load the scorecard for this policy.</p>
                <button
                  onClick={retryScorecard}
                  className="px-3 py-1.5 rounded-md border border-[var(--primary)] text-[var(--primary)] font-semibold hover:bg-[var(--accent)] transition"
                >
                  Retry
                </button>
              </div>
            )}
            {sc && (
              <div>
                {!isPersonalized && (
                  <div className="mb-3 bg-[var(--accent)] border border-[var(--primary)] rounded-lg p-3 text-xs">
                    <div className="font-semibold text-[var(--primary)] mb-1">This is the generic grade for an average buyer.</div>
                    <p className="text-[var(--muted-foreground)] leading-snug">
                      Tell me your age, dependents, health conditions and budget, and I&apos;ll re-score this plan for <strong className="text-[var(--foreground)]">your</strong> situation. The same plan can grade B for a healthy 30-year-old but D for a 60-year-old with diabetes.
                      {completeness && completeness.completeness_pct > 0 && (
                        <span className="block mt-1">Your profile is {completeness.completeness_pct}% complete. {completeness.next_question_hint && <em className="not-italic">Next: {completeness.next_question_hint.slice(0, 80)}…</em>}</span>
                      )}
                    </p>
                  </div>
                )}
                {isPersonalized && completeness && (
                  <div className="mb-3 text-[10px] text-[var(--primary)] font-semibold flex items-center gap-1">
                    ✓ Personalized for you · profile {completeness.completeness_pct}% complete
                  </div>
                )}
                <div className="flex items-center gap-3 mb-3">
                  <span className={`inline-flex items-center justify-center w-12 h-12 rounded-lg font-bold ${gradeColor(sc.grade)}`}>{sc.grade}</span>
                  <div className="flex-1">
                    <div className="text-2xl font-bold">{sc.overall_score}<span className="text-[var(--muted-foreground)] text-base font-normal">/100</span></div>
                  </div>
                </div>
                {/* Task #31 — structured, profile-aware summary at the TOP
                    of the score section (falls back to the generic
                    one_liner when empty / insufficient data). */}
                <div className="mb-3">
                  <ProfileSummaryBlock
                    summary={sc.profile_summary}
                    fallback={sc.one_liner}
                    max={5}
                  />
                </div>
                <ScorecardCard sc={sc} />
                <MethodologyExpander />
              </div>
            )}
          </div>

          {/* 3 — PRICING (per-policy premium estimator) */}
          <div className="pt-5 border-t border-[var(--border)]">
            <PanelEyebrow>Estimate premium for this policy</PanelEyebrow>
            <PerPolicyPremiumEstimator policy={policy} desiredSI={completeness?.profile?.desired_sum_insured_inr ?? null} />
          </div>

          {/* 4 — REVIEWS (insurer reputation + IRDAI claim metrics). #76 —
              ALWAYS render the section; it must NEVER silently vanish when
              the reviews fetch is null/slow or insurer_slug is missing
              (the old scorecard-vanish bug class). */}
          <div className="pt-5 border-t border-[var(--border)]">
            <PanelEyebrow>{`${reviews ? reviews.insurer_name : policy.insurer_name} — reputation & claim metrics`}</PanelEyebrow>
            {reviews
              ? <InsurerReviewsBlock reviews={reviews} />
              : <p className="text-xs text-[var(--muted-foreground)] mt-1 leading-snug">Independent reputation &amp; claim-settlement data for this insurer is being compiled.</p>}
          </div>

          <div className="text-[10px] text-[var(--muted-foreground)] pt-3 border-t border-[var(--border)]">
            This grade reflects how much the policy covers, how predictable your costs are, how soon you can claim, how reliably the insurer settles claims, and its renewal and bonus terms — read from the official policy document and the insurer&apos;s publicly disclosed claim record. Premium figures are indicative, not a quote.
          </div>
        </div>
      </div>
    </div>
  );
}
