"use client";

import { useEffect, useRef, useState } from "react";
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
  getProfileCompleteness,
  getScorecard,
  InsurerReviews,
  MarketplacePolicy,
  MarketplaceResponse,
  postChat,
  postPremiumEstimate,
  postProfileUpdate,
  postTranscribe,
  PremiumEstimateResponse,
  ProfileCompletenessResponse,
  ScorecardResponse,
  uploadPolicy,
  UserProfile,
} from "@/lib/api";
import { translate, UILang, StringKey, GLOSSARY } from "@/lib/i18n";
import { useLiveConversation } from "@/lib/useLiveConversation";

type DisplayMessage = ChatMessage & {
  id: string;
  citations?: Citation[];
  audioUrl?: string;
  brain?: string;
  latencyMs?: number;
  blocked?: boolean;
};

const SUGGESTED_QUESTIONS = [
  "I'm looking for a new health insurance policy.",
  "What is the waiting period for pre-existing diseases?",
  "Does HDFC ERGO Optima Secure cover AYUSH?",
  "What's the room rent cap on Care Supreme?",
];

export default function Page() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [returnAudio, setReturnAudio] = useState(true);
  const [ttsLang, setTtsLang] = useState<"en-IN" | "hi-IN">("en-IN");
  // Visual UI language — same source as ttsLang so the toggle controls both
  const uiLang: UILang = ttsLang === "hi-IN" ? "hi" : "en";
  const t = (key: StringKey, vars?: Record<string, string | number>) => translate(uiLang, key, vars);
  const [health, setHealth] = useState<{ status: string; missing: string[] } | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [showCoverage, setShowCoverage] = useState(false);
  const [showPremium, setShowPremium] = useState(false);
  const [showMarketplace, setShowMarketplace] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  // Admin panel: iframe-embedded LLM control surface. Backend admin API is
  // IP-gated (ADMIN_IP_ALLOWLIST), so the panel itself silently renders the
  // dashboard's "not authorized" view for non-allowlisted IPs.
  const [showAdmin, setShowAdmin] = useState(false);
  const [marketplace, setMarketplace] = useState<MarketplaceResponse | null>(null);
  const [openPolicy, setOpenPolicy] = useState<MarketplacePolicy | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [profileCompleteness, setProfileCompleteness] = useState<ProfileCompletenessResponse | null>(null);

  // Re-fetch profile completeness whenever sessionId changes (after first chat
  // turn) — drives the score-gate on marketplace cards + detail modal.
  useEffect(() => {
    if (typeof window !== "undefined" && sessionId) {
      localStorage.setItem("insurance_session_id", sessionId);
      getProfileCompleteness(sessionId)
        .then(setProfileCompleteness)
        .catch(() => setProfileCompleteness(null));
    }
  }, [sessionId]);

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
    const savedSession = localStorage.getItem("insurance_session_id");
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
  const [handsFree, setHandsFree] = useState(false);  // VAD auto-cutoff mode
  // Live ref of handsFree so async TTS-ended callbacks read the latest value
  // (closure captured at audio.play() time would otherwise be stale)
  const handsFreeRef = useRef(false);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadFrameRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Live-conversation mode (full-duplex VAD barge-in). Defined further down
  // in the component body so it can reference `sessionId`, `messages`, etc.
  // via closure when the onUtterance handler fires. See useLiveConversation.ts.
  const liveOnUtteranceRef = useRef<((blob: Blob, abort: AbortController) => Promise<void>) | null>(null);
  const live = useLiveConversation({
    onUtterance: async (blob, abort) => {
      const fn = liveOnUtteranceRef.current;
      if (fn) await fn(blob, abort);
    },
  });

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

  // Hands-free continuous loop: when toggled ON, immediately open mic. When
  // toggled OFF, close any in-progress recording. The send() function takes
  // care of re-opening the mic after each assistant TTS finishes.
  useEffect(() => {
    handsFreeRef.current = handsFree;
    if (handsFree && !recording && !busy) {
      startRecording();
    } else if (!handsFree && recording) {
      stopRecording();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handsFree]);

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

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
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
      const res = await postChat({
        user_text: actualText,
        session_id: sessionId,
        chat_history: history,
        return_audio: returnAudio,
        tts_language_code: ttsLang,
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
      const audioUrl = res.audio_base64 ? audioBlobURLFromBase64(res.audio_base64) : undefined;
      pushAssistant(res.reply_text, {
        citations: res.citations,
        audioUrl,
        brain: res.brain_used,
        latencyMs: res.latency_ms,
        blocked: res.blocked,
      });
      if (audioUrl) {
        const audio = new Audio(audioUrl);
        // In hands-free mode, re-open the mic AFTER the assistant's TTS reply
        // finishes playing — that's how a Siri/Alexa loop feels natural.
        audio.addEventListener("ended", () => {
          if (handsFreeRef.current) {
            setTimeout(() => { if (handsFreeRef.current) startRecording(); }, 250);
          }
        });
        audio.play().catch(() => {
          // Audio playback failed (e.g. browser blocked autoplay) — still
          // continue the hands-free loop if enabled.
          if (handsFreeRef.current) {
            setTimeout(() => { if (handsFreeRef.current) startRecording(); }, 250);
          }
        });
      } else if (handsFreeRef.current) {
        // No audio reply (voice toggle off) — restart mic after a short pause
        setTimeout(() => { if (handsFreeRef.current) startRecording(); }, 500);
      }
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
    }
  }

  // Live-conversation onUtterance binding. Rebinds when relevant state changes
  // so the latest sessionId / history / view are captured in the closure.
  useEffect(() => {
    liveOnUtteranceRef.current = async (blob, abort) => {
      try {
        const transcribed = await postTranscribe(blob, ttsLang, abort.signal);
        const text = (transcribed.text || "").trim();
        if (text.length < 2) return;

        pushUser(text);
        const history: ChatMessage[] = messages.map((m) => ({ role: m.role, content: m.content }));
        const active_view: "chat" | "marketplace" | "profile" | "premium" | "policy_detail" =
          openPolicy ? "policy_detail" :
          showMarketplace ? "marketplace" :
          showProfile ? "profile" :
          showPremium ? "premium" :
          "chat";

        const res = await postChat({
          user_text: text,
          session_id: sessionId,
          chat_history: history,
          return_audio: true,
          tts_language_code: ttsLang,
          view_context: { active_view, active_policy_id: openPolicy?.policy_id },
          signal: abort.signal,
        });
        setSessionId(res.session_id);
        getProfileCompleteness(res.session_id)
          .then(setProfileCompleteness)
          .catch(() => {});
        const audioUrl = res.audio_base64 ? audioBlobURLFromBase64(res.audio_base64) : undefined;
        pushAssistant(res.reply_text, {
          citations: res.citations,
          audioUrl,
          brain: res.brain_used,
          latencyMs: res.latency_ms,
          blocked: res.blocked,
        });
        if (audioUrl) {
          const audio = new Audio(audioUrl);
          audio.play().catch(() => {});
        }
      } catch (e: unknown) {
        const name = (e as { name?: string })?.name;
        if (name === "AbortError") return; // user barged in; intentional
        // eslint-disable-next-line no-console
        console.error("[live mode] turn failed:", e);
      }
    };
  }, [messages, sessionId, ttsLang, openPolicy, showMarketplace, showProfile, showPremium]);

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];
      recorder.ondataavailable = (ev) => { if (ev.data.size > 0) audioChunksRef.current.push(ev.data); };
      recorder.onstop = async () => {
        stopVAD();
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, { type: recorder.mimeType || "audio/webm" });
        setRecording(false);
        if (blob.size < 1000) return;
        setBusy(true);
        try {
          const { text } = await postTranscribe(blob, ttsLang);
          if (text && text.trim()) await send(text);
          else pushAssistant("Sorry, I couldn't hear that clearly. Please try again.");
        } catch (e: unknown) {
          pushAssistant(`Sorry — transcribe error: ${e instanceof Error ? e.message : String(e)}`);
        } finally { setBusy(false); }
      };
      recorder.start();
      setRecording(true);

      // Hands-free / VAD auto-cutoff mode: listen for ~1.5s of silence
      // (RMS level below threshold) and auto-stop the recording. Falls back
      // gracefully if AudioContext unsupported.
      if (handsFree) {
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
    setUploadStatus(`Indexing ${f.name}…`);
    try {
      const r = await uploadPolicy(f);
      setUploadStatus(`✓ Indexed "${r.policy_name}" — ${r.chunks_added} chunks from ${r.pages_indexed} pages (${(r.elapsed_ms / 1000).toFixed(1)}s). Ask me about it.`);
      // Refresh coverage so the uploaded doc shows up
      getCoverage().then(setCoverage).catch(() => {});
    } catch (e: unknown) {
      setUploadStatus(`✗ Upload failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
      setTimeout(() => setUploadStatus(null), 8000);
    }
  }

  return (
    <div className="min-h-screen flex flex-col bg-[var(--background)] text-[var(--foreground)]">
      <header className="border-b border-[var(--border)] bg-[var(--card)]">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-bold text-sm">IA</div>
            <div>
              <h1 className="font-semibold text-base sm:text-lg leading-tight">{t("header.title")}</h1>
              <p className="text-xs text-[var(--muted-foreground)]">{t("header.subtitle")}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <button
              onClick={() => { setShowMarketplace(!showMarketplace); setShowPremium(false); setShowCoverage(false); setShowAdmin(false); }}
              className={`group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
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
                  <div className="text-[10px] uppercase tracking-wider opacity-85 leading-none">{t("header.policy_library_kicker")}</div>
                  <div className="text-xs font-bold leading-tight whitespace-nowrap">{t("header.policy_library")}</div>
                </div>
                {marketplace && (
                  <div className="flex flex-col items-center justify-center px-3 py-1 bg-white/15 border-l border-white/20">
                    <div className="text-sm font-bold leading-none">{marketplace.total}</div>
                    <div className="text-[9px] uppercase tracking-wider opacity-90 leading-none mt-0.5">{t("header.policies_label")}</div>
                  </div>
                )}
                {marketplace && (
                  <div className="hidden sm:flex flex-col items-center justify-center px-3 py-1 bg-black/10">
                    <div className="text-sm font-bold leading-none">{marketplace.insurers_indexed}</div>
                    <div className="text-[9px] uppercase tracking-wider opacity-90 leading-none mt-0.5">{t("header.insurers_label")}</div>
                  </div>
                )}
              </div>
            </button>
            <button
              onClick={() => { setShowPremium(!showPremium); setShowMarketplace(false); setShowCoverage(false); setShowProfile(false); setShowAdmin(false); }}
              className={`group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
                showPremium ? "ring-2 ring-[var(--primary)]" : ""
              }`}
              title={t("header.annual_premium")}
            >
              <div className="absolute inset-0 bg-gradient-to-br from-amber-500 via-orange-500 to-rose-500" />
              <div className="relative flex items-stretch text-white">
                <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                  <RupeeIcon />
                </div>
                <div className="px-3 py-2 text-left">
                  <div className="text-[10px] uppercase tracking-wider opacity-85 leading-none">{t("header.annual_premium_kicker")}</div>
                  <div className="text-xs font-bold leading-tight whitespace-nowrap">{t("header.annual_premium")}</div>
                </div>
              </div>
            </button>
            <button
              onClick={() => { setShowProfile(!showProfile); setShowMarketplace(false); setShowPremium(false); setShowCoverage(false); setShowAdmin(false); }}
              className={`group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
                showProfile ? "ring-2 ring-[var(--primary)]" : ""
              }`}
              title={uiLang === "hi" ? "अपनी profile बनाएं — हर policy को आपके लिए score करेंगे" : "Build your profile — every policy gets a personal score"}
            >
              <div className="absolute inset-0 bg-gradient-to-br from-violet-600 via-purple-600 to-fuchsia-600" />
              <div className="relative flex items-stretch text-white">
                <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="8" r="4" /><path d="M4 21v-2a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v2" /></svg>
                </div>
                <div className="px-3 py-2 text-left">
                  <div className="text-[10px] uppercase tracking-wider opacity-85 leading-none">{uiLang === "hi" ? "आप" : "You"}</div>
                  <div className="text-xs font-bold leading-tight whitespace-nowrap">{uiLang === "hi" ? "आपकी profile" : "Your profile"}</div>
                </div>
                {profileCompleteness && (
                  <div className="flex flex-col items-center justify-center px-3 py-1 bg-white/15 border-l border-white/20">
                    <div className="text-sm font-bold leading-none">{profileCompleteness.completeness_pct}%</div>
                    <div className="text-[9px] uppercase tracking-wider opacity-90 leading-none mt-0.5">{uiLang === "hi" ? "पूर्ण" : "DONE"}</div>
                  </div>
                )}
              </div>
            </button>
            {/* Admin access — opens the LLM control panel in an embedded view.
                Backend admin API is IP-gated, so the panel works only from the
                allowlisted home IP; from other networks it shows "not authorized". */}
            <button
              onClick={() => { setShowAdmin(!showAdmin); setShowMarketplace(false); setShowPremium(false); setShowProfile(false); setShowCoverage(false); }}
              className={`group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
                showAdmin ? "ring-2 ring-[var(--primary)]" : ""
              }`}
              title="LLM control panel — health, chain order, usage (admin-only, IP-gated)"
            >
              <div className="absolute inset-0 bg-gradient-to-br from-slate-700 via-slate-600 to-zinc-700" />
              <div className="relative flex items-stretch text-white">
                <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z" /><path d="M9 12l2 2 4-4" /></svg>
                </div>
                <div className="px-3 py-2 text-left">
                  <div className="text-[10px] uppercase tracking-wider opacity-85 leading-none">Admin</div>
                  <div className="text-xs font-bold leading-tight whitespace-nowrap">Access panel</div>
                </div>
              </div>
            </button>
            {/* UI language toggle — flips visual chrome + voice TTS together */}
            <button
              onClick={() => setTtsLang(ttsLang === "en-IN" ? "hi-IN" : "en-IN")}
              className="text-xs font-semibold px-2 py-1 rounded-md border border-[var(--border)] bg-[var(--card)] hover:border-[var(--primary)] hover:text-[var(--primary)]"
              title={ttsLang === "en-IN" ? "Switch to Hindi" : "अंग्रेज़ी में बदलें"}
            >
              {ttsLang === "en-IN" ? "EN · हिं" : "हिं · EN"}
            </button>
          </div>
        </div>
      </header>
      {openPolicy && <PolicyDetailModal policy={openPolicy} onClose={() => setOpenPolicy(null)} />}

      {/* Two-column layout on desktop (chat | panel), stacked on mobile.
          When no panel is open, chat takes the full width. The chat column
          never unmounts, so messages, voice, and view-context stay live no
          matter which view the user is focused on. */}
      <div className="flex-1 flex flex-col lg:flex-row min-h-0 w-full">
        <main className={`flex flex-col min-h-0 px-4 sm:px-6 py-4 sm:py-6 ${
          (showMarketplace || showPremium || showProfile)
            ? "lg:w-2/5 lg:border-r lg:border-[var(--border)] w-full"
            : "max-w-6xl w-full mx-auto"
        }`}>
        {messages.length === 0 ? (
          <EmptyState onSuggest={(q) => send(q)} coverage={coverage} t={t} />
        ) : (
          <div ref={scrollRef} className="flex-1 overflow-y-auto scrollbar-thin space-y-4 mb-4 pr-1">
            {messages.map((m) => <Message key={m.id} m={m} />)}
            {busy && <ThinkingDots />}
          </div>
        )}

        {uploadStatus && (
          <div className="mb-3 text-xs px-3 py-2 rounded-lg bg-[var(--accent)] border border-[var(--border)] text-[var(--foreground)]">
            {uploadStatus}
          </div>
        )}

        <div className="border border-[var(--border)] rounded-2xl bg-[var(--card)] p-3 shadow-sm">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder="Ask about coverage, waiting periods, exclusions, or compare policies…"
              rows={1}
              className="flex-1 resize-none bg-transparent outline-none text-sm sm:text-base px-2 py-2 min-h-[40px] max-h-32"
              disabled={busy}
            />
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              onChange={handleFile}
              className="hidden"
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={busy || !!uploadStatus}
              title="Upload your own policy PDF"
              className="shrink-0 w-11 h-11 rounded-xl flex items-center justify-center bg-[var(--muted)] hover:bg-[var(--border)] disabled:opacity-40 transition"
            >
              <UploadIcon />
            </button>
            <button
              type="button"
              onClick={recording ? stopRecording : startRecording}
              disabled={busy && !recording}
              className={`shrink-0 w-11 h-11 rounded-xl flex items-center justify-center transition-all ${
                recording ? "bg-[var(--error)] text-white animate-record-pulse" : "bg-[var(--muted)] hover:bg-[var(--border)]"
              } disabled:opacity-40`}
              title={recording ? "Stop recording" : "Voice input"}
            >
              {recording ? <StopIcon /> : <MicIcon />}
            </button>
            <button
              type="button"
              onClick={() => send(input)}
              disabled={busy || !input.trim()}
              className="shrink-0 h-11 px-4 rounded-xl bg-[var(--primary)] text-[var(--primary-foreground)] text-sm font-medium hover:opacity-90 disabled:opacity-40"
            >
              Send
            </button>
          </div>
          <div className="flex items-center justify-between gap-3 mt-2 pt-2 px-2 text-xs text-[var(--muted-foreground)]">
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input type="checkbox" checked={returnAudio} onChange={(e) => setReturnAudio(e.target.checked)} className="w-3.5 h-3.5 accent-[var(--primary)]" /> Voice reply
              </label>
              <label className="flex items-center gap-1.5 cursor-pointer" title="Hands-free voice — auto-submits when you stop speaking">
                <input type="checkbox" checked={handsFree} onChange={(e) => setHandsFree(e.target.checked)} className="w-3.5 h-3.5 accent-[var(--primary)]" /> Hands-free
              </label>
              {/* Live conversation — full-duplex with VAD barge-in. Speak any
                  time, even while the bot is still talking, and it stops mid-
                  sentence and listens to you. No button press needed. */}
              <button
                type="button"
                onClick={() => live.setLive(!live.live)}
                className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-xs font-medium transition ${
                  live.live
                    ? "bg-red-500/15 border-red-400 text-red-600"
                    : "border-[var(--border)] hover:border-[var(--primary)] hover:text-[var(--primary)]"
                }`}
                title="Live conversation: continuous mic, interrupt the bot by speaking"
              >
                <span className={`inline-block w-2 h-2 rounded-full ${live.live ? (live.recording ? "bg-red-500 animate-pulse" : "bg-green-500 animate-pulse") : "bg-gray-400"}`} />
                {live.live ? (live.recording ? "Listening…" : "Live ✓") : "Go Live"}
              </button>
              {live.micPermissionDenied && (
                <span className="text-red-500" title="Browser blocked microphone access — check site permissions">mic blocked</span>
              )}
              <label className="flex items-center gap-1.5">
                Lang:
                <select value={ttsLang} onChange={(e) => setTtsLang(e.target.value as "en-IN" | "hi-IN")} className="bg-transparent border border-[var(--border)] rounded px-1.5 py-0.5">
                  <option value="en-IN">English</option>
                  <option value="hi-IN">हिन्दी</option>
                </select>
              </label>
            </div>
            <div className="hidden sm:block">Enter to send · 📎 to upload your own PDF</div>
          </div>
        </div>
      </main>

        {/* Panel column — sits beside the chat on desktop, takes over on
            mobile. Stays mounted as long as a panel is open; chat in the
            other column remains fully interactive (real-time copilot). */}
        {(showMarketplace || showPremium || showProfile || showAdmin) && (
          <aside className="lg:w-3/5 w-full overflow-y-auto bg-[var(--background)]">
            {showMarketplace && marketplace && (
              <MarketplacePanel
                data={marketplace}
                onOpenPolicy={(p) => setOpenPolicy(p)}
                onClose={() => setShowMarketplace(false)}
                t={t}
                isPersonalized={profileCompleteness?.is_personalized === true}
              />
            )}
            {showPremium && <PremiumCalculatorPanel onClose={() => setShowPremium(false)} />}
            {showProfile && (
              <ProfileBuilderPanel
                sessionId={sessionId}
                setSessionId={setSessionId}
                initialProfile={profileCompleteness?.profile || {}}
                onSaved={(resp) => { setProfileCompleteness(resp); }}
                onClose={() => setShowProfile(false)}
                uiLang={uiLang}
              />
            )}
            {showAdmin && (
              <div className="flex flex-col h-full">
                <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)] bg-[var(--card)]">
                  <div>
                    <h2 className="text-sm font-semibold">Admin · LLM Control Panel</h2>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      IP-gated. Only the home network IP can interact with chain reordering / probes.
                    </p>
                  </div>
                  <button
                    onClick={() => setShowAdmin(false)}
                    className="text-xs text-[var(--muted-foreground)] hover:underline"
                  >
                    close
                  </button>
                </div>
                <iframe
                  src="/admin/llm-control.html"
                  title="LLM Control Panel"
                  className="flex-1 w-full border-0 bg-white"
                  sandbox="allow-scripts allow-same-origin allow-forms"
                />
              </div>
            )}
          </aside>
        )}
      </div>

      <footer className="border-t border-[var(--border)] py-3 px-6 text-center text-xs text-[var(--muted-foreground)]">
        Advisory only. Information based on policy documents; verify with the insurer before purchase. All policy ratings are illustrative and based on publicly disclosed data.
      </footer>
    </div>
  );
}

function ProfileBuilderPanel({
  sessionId,
  setSessionId,
  initialProfile,
  onSaved,
  onClose,
  uiLang,
}: {
  sessionId: string | undefined;
  setSessionId: (id: string) => void;
  initialProfile: UserProfile;
  onSaved: (r: ProfileCompletenessResponse) => void;
  onClose: () => void;
  uiLang: UILang;
}) {
  const [age, setAge] = useState<number | null>(initialProfile.age ?? null);
  const [dependents, setDependents] = useState<string>(initialProfile.dependents ?? "self");
  const [budget, setBudget] = useState<string>(initialProfile.budget_band ?? "");
  const [income, setIncome] = useState<string>(initialProfile.income_band ?? "");
  const [city, setCity] = useState<string>(initialProfile.location_tier ?? "");
  const [conditions, setConditions] = useState<string[]>(initialProfile.health_conditions ?? []);
  const [existingCover, setExistingCover] = useState<number | null>(initialProfile.existing_cover_inr ?? null);
  const [primaryGoal, setPrimaryGoal] = useState<string>(initialProfile.primary_goal ?? "");
  const [parentsHasPed, setParentsHasPed] = useState<boolean | null>(initialProfile.parents_has_ped ?? null);
  const [parentsAgeMax, setParentsAgeMax] = useState<number | null>(initialProfile.parents_age_max ?? null);
  const [busy, setBusy] = useState(false);

  const hindi = uiLang === "hi";

  const toggleCondition = (c: string) => {
    setConditions((prev) => prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]);
  };

  const handleSave = async () => {
    if (busy) return;
    setBusy(true);
    let sid = sessionId;
    if (!sid) {
      sid = `s_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      setSessionId(sid);
      if (typeof window !== "undefined") localStorage.setItem("insurance_session_id", sid);
    }
    try {
      const resp = await postProfileUpdate({
        session_id: sid,
        age: age ?? undefined,
        dependents: dependents || undefined,
        budget_band: budget || undefined,
        income_band: income || undefined,
        location_tier: city || undefined,
        health_conditions: conditions.length ? conditions : undefined,
        existing_cover_inr: existingCover ?? undefined,
        primary_goal: primaryGoal || undefined,
        parents_to_insure: dependents.includes("parent") ? true : null,
        parents_has_ped: parentsHasPed,
        parents_age_max: parentsAgeMax ?? undefined,
      });
      onSaved(resp);
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  };

  // chip helper styles
  const chipBase = "px-2.5 py-1 rounded-full border text-[11px] cursor-pointer transition";
  const chipOn = "border-[var(--primary)] bg-[var(--primary)] text-white";
  const chipOff = "border-[var(--border)] hover:border-[var(--primary)]";

  const conditionOptions = hindi
    ? [["diabetes", "मधुमेह"], ["hypertension", "BP"], ["thyroid", "थायरॉइड"], ["heart", "हृदय रोग"], ["asthma", "अस्थमा"], ["cancer", "कैंसर इतिहास"]]
    : [["diabetes", "Diabetes"], ["hypertension", "BP / Hypertension"], ["thyroid", "Thyroid"], ["heart", "Heart"], ["asthma", "Asthma"], ["cancer", "Cancer history"]];

  return (
    <div className="border-t border-[var(--border)] bg-[var(--muted)] animate-fade-up max-h-[80vh] overflow-y-auto scrollbar-thin">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-5">
        <div className="flex items-baseline justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">{hindi ? "आपकी profile बनाएं" : "Build your profile"}</h2>
            <p className="text-xs text-[var(--muted-foreground)] mt-1 max-w-2xl">
              {hindi
                ? "ये जवाब इसी chat में रहते हैं। ईमानदारी से बताइए — आपकी सेहत का सच बताना आपकी claim बचाता है, premium बढ़ाने का बहाना नहीं।"
                : "Your answers stay in this chat. Be honest — the truth protects your claim later, not just my recommendation. We don't share with any insurer until you choose to buy."}
            </p>
          </div>
          <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:underline">{hindi ? "बंद करें" : "close"}</button>
        </div>

        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-5 space-y-5">
          {/* Age */}
          <div>
            <label className="flex items-baseline justify-between text-xs mb-1.5">
              <span className="font-semibold">{hindi ? "आपकी उम्र" : "Your age"}</span>
              <span className="font-mono text-sm">{age ?? (hindi ? "—" : "—")}</span>
            </label>
            <input type="range" min={18} max={80} value={age ?? 35} onChange={(e) => setAge(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
            <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">{hindi ? "Premium + eligibility + renewal age इसी पर निर्भर।" : "Premium, eligibility, and how long you can renew all hinge on this."}</p>
          </div>

          {/* Dependents */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "किसको cover करना है" : "Who needs cover"}</label>
            <div className="flex flex-wrap gap-2">
              {[
                ["self", hindi ? "सिर्फ मैं" : "Just me"],
                ["self+spouse", hindi ? "मैं + पति/पत्नी" : "Self + spouse"],
                ["self+spouse+kids", hindi ? "मैं + पति/पत्नी + बच्चे" : "Self + spouse + kids"],
                ["self+parents", hindi ? "मैं + माता-पिता" : "Self + parents"],
                ["self+spouse+kids+parents", hindi ? "पूरा परिवार" : "Whole family"],
              ].map(([key, label]) => (
                <button key={key} onClick={() => setDependents(key)} className={`${chipBase} ${dependents === key ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
          </div>

          {/* Parents detail — conditional */}
          {dependents.includes("parent") && (
            <div className="border-l-2 border-[var(--primary)] pl-3 space-y-3">
              <div>
                <label className="flex items-baseline justify-between text-xs mb-1.5">
                  <span className="font-semibold">{hindi ? "सबसे बड़े parent की उम्र" : "Older parent's age"}</span>
                  <span className="font-mono text-sm">{parentsAgeMax ?? "—"}</span>
                </label>
                <input type="range" min={45} max={85} value={parentsAgeMax ?? 65} onChange={(e) => setParentsAgeMax(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
              </div>
              <div>
                <label className="block text-xs font-semibold mb-1.5">{hindi ? "क्या उन्हें diabetes / BP / heart है?" : "Any pre-existing conditions (diabetes / BP / heart)?"}</label>
                <div className="flex gap-2">
                  <button onClick={() => setParentsHasPed(true)} className={`${chipBase} ${parentsHasPed === true ? chipOn : chipOff}`}>{hindi ? "हाँ" : "Yes"}</button>
                  <button onClick={() => setParentsHasPed(false)} className={`${chipBase} ${parentsHasPed === false ? chipOn : chipOff}`}>{hindi ? "नहीं" : "No"}</button>
                </div>
              </div>
            </div>
          )}

          {/* Your conditions */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "आपकी pre-existing conditions" : "Your pre-existing conditions"}</label>
            <p className="text-[10px] text-amber-700 dark:text-amber-400 mb-2">{hindi ? "सच बताइए। बीमाकर्ता claim time पर hospital records check करते हैं। आज की बचत बाद में ₹8L का denied claim बन जाती है।" : "Be honest. Insurers cross-check at claim time. ₹500 saved today = ₹8L denied claim tomorrow."}</p>
            <div className="flex flex-wrap gap-2">
              <button onClick={() => setConditions([])} className={`${chipBase} ${conditions.length === 0 ? chipOn : chipOff}`}>{hindi ? "कुछ नहीं" : "None"}</button>
              {conditionOptions.map(([key, label]) => (
                <button key={key} onClick={() => toggleCondition(key)} className={`${chipBase} ${conditions.includes(key) ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
          </div>

          {/* Existing cover */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "पहले से कोई health insurance?" : "Already have any health insurance?"}</label>
            <div className="flex flex-wrap gap-2">
              {[[0, hindi ? "नहीं" : "None"], [300000, "₹3L"], [500000, "₹5L"], [1000000, "₹10L"], [2500000, "₹25L+"]].map(([v, label]) => (
                <button key={String(v)} onClick={() => setExistingCover(v as number)} className={`${chipBase} ${existingCover === v ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
          </div>

          {/* City tier */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "आपका शहर" : "Your city"}</label>
            <div className="flex gap-2">
              {[["metro", hindi ? "Metro (Mumbai/Delhi/Bangalore/...)" : "Metro"], ["tier1", hindi ? "Tier 1" : "Tier 1"], ["tier2", hindi ? "छोटा शहर" : "Tier 2 / smaller"]].map(([key, label]) => (
                <button key={key} onClick={() => setCity(key)} className={`${chipBase} ${city === key ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
            <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">{hindi ? "Cashless network आपके शहर में कितना deep है — यह बड़ा फर्क डालता है।" : "How many cashless hospitals exist in your city makes a huge difference."}</p>
          </div>

          {/* Budget */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "सालाना premium budget" : "Annual premium budget"}</label>
            <div className="flex flex-wrap gap-2">
              {[["under_15k", hindi ? "₹15k से कम" : "Under ₹15k"], ["15k_30k", "₹15-30k"], ["30k_60k", "₹30-60k"], ["60k+", "₹60k+"]].map(([key, label]) => (
                <button key={key} onClick={() => setBudget(key)} className={`${chipBase} ${budget === key ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
          </div>

          {/* Income */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "सालाना आय" : "Annual income"}</label>
            <div className="flex flex-wrap gap-2">
              {[["under_5L", hindi ? "₹5L से कम" : "Under ₹5L"], ["5L-10L", "₹5-10L"], ["10L-25L", "₹10-25L"], ["25L+", "₹25L+"]].map(([key, label]) => (
                <button key={key} onClick={() => setIncome(key)} className={`${chipBase} ${income === key ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
          </div>

          {/* Primary goal */}
          <div>
            <label className="block text-xs font-semibold mb-1.5">{hindi ? "आज यहाँ क्यों?" : "What brought you here today?"}</label>
            <div className="flex flex-wrap gap-2">
              {[["first_buy", hindi ? "पहली policy" : "First policy"], ["upgrade", hindi ? "Cover बढ़ानी है" : "Upgrade"], ["compare_specific", hindi ? "Specific policies compare करनी हैं" : "Compare specific policies"], ["tax_planning", "Tax 80D"]].map(([key, label]) => (
                <button key={key} onClick={() => setPrimaryGoal(key)} className={`${chipBase} ${primaryGoal === key ? chipOn : chipOff}`}>{label}</button>
              ))}
            </div>
          </div>
        </div>

        <div className="sticky bottom-0 mt-4 pb-2 bg-[var(--muted)] flex items-center justify-end gap-2">
          <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:underline">{hindi ? "रद्द करें" : "Cancel"}</button>
          <button
            onClick={handleSave}
            disabled={busy}
            className={`text-sm font-semibold rounded-md px-4 py-2 ${busy ? "bg-[var(--muted)] text-[var(--muted-foreground)]" : "bg-[var(--primary)] text-white hover:opacity-90"}`}
          >
            {busy ? (hindi ? "Save हो रहा है…" : "Saving…") : (hindi ? "Save & Score करें" : "Save & Score")}
          </button>
        </div>
      </div>
    </div>
  );
}

function PremiumCalculatorPanel({ onClose }: { onClose: () => void }) {
  const [age, setAge] = useState(35);
  const [sumInsured, setSumInsured] = useState(1000000);
  const [cityTier, setCityTier] = useState<"metro" | "tier1" | "tier2">("metro");
  const [smoker, setSmoker] = useState(false);
  const [familySize, setFamilySize] = useState(0);
  const [ped, setPed] = useState<"none" | "diabetes_or_hypertension" | "heart_disease" | "multiple">("none");
  const [copay, setCopay] = useState(0);
  const [estimate, setEstimate] = useState<PremiumEstimateResponse | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const handler = setTimeout(() => {
      setBusy(true);
      postPremiumEstimate({ age, sum_insured_inr: sumInsured, city_tier: cityTier, smoker, family_size: familySize, pre_existing_conditions: ped, copayment_pct: copay })
        .then(setEstimate)
        .catch(() => setEstimate(null))
        .finally(() => setBusy(false));
    }, 200); // debounce
    return () => clearTimeout(handler);
  }, [age, sumInsured, cityTier, smoker, familySize, ped, copay]);

  const fmtINR = (v: number) => `₹${v.toLocaleString("en-IN")}`;
  const siDisplay = sumInsured >= 10000000 ? `${sumInsured / 10000000} cr` : `${sumInsured / 100000} L`;

  return (
    <div className="border-t border-[var(--border)] bg-[var(--muted)] animate-fade-up">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-5">
        <div className="flex items-baseline justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold">Illustrative premium calculator</h2>
            <p className="text-xs text-[var(--muted-foreground)]">
              Indicative annual premium range from public quote data. Not a binding quote — actual depends on underwriting.
            </p>
          </div>
          <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:underline">close</button>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-4">
          <div className="space-y-4">
            <div>
              <label className="flex items-center justify-between text-xs mb-1">
                <span className="font-medium">Age</span>
                <span className="font-mono">{age}</span>
              </label>
              <input
                type="range" min={18} max={80} step={1}
                value={age}
                onChange={(e) => setAge(parseInt(e.target.value))}
                className="w-full accent-[var(--primary)]"
              />
            </div>
            <div>
              <label className="flex items-center justify-between text-xs mb-1">
                <span className="font-medium">Sum insured</span>
                <span className="font-mono">{siDisplay}</span>
              </label>
              <input
                type="range" min={300000} max={20000000} step={100000}
                value={sumInsured}
                onChange={(e) => setSumInsured(parseInt(e.target.value))}
                className="w-full accent-[var(--primary)]"
              />
              <div className="flex gap-1 mt-1 text-[10px] text-[var(--muted-foreground)]">
                {[500000, 1000000, 2500000, 5000000, 10000000].map((s) => (
                  <button key={s} onClick={() => setSumInsured(s)} className="hover:text-[var(--primary)]">
                    {s >= 10000000 ? `${s/10000000} cr` : `${s/100000} L`}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="flex items-center justify-between text-xs mb-1">
                <span className="font-medium">Family covered</span>
                <span className="font-mono">{familySize === 0 ? "Self only" : `Self + ${familySize} dependent${familySize === 1 ? "" : "s"}`}</span>
              </label>
              <input
                type="range" min={0} max={6} step={1}
                value={familySize}
                onChange={(e) => setFamilySize(parseInt(e.target.value))}
                className="w-full accent-[var(--primary)]"
              />
            </div>
            <div>
              <label className="block text-xs mb-1 font-medium">Pre-existing conditions</label>
              <select
                value={ped}
                onChange={(e) => setPed(e.target.value as typeof ped)}
                className="w-full text-xs bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5 outline-none focus:border-[var(--primary)]"
              >
                <option value="none">None</option>
                <option value="diabetes_or_hypertension">Diabetes or hypertension</option>
                <option value="heart_disease">Heart disease</option>
                <option value="multiple">Multiple conditions</option>
              </select>
            </div>
            <div>
              <label className="flex items-center justify-between text-xs mb-1">
                <span className="font-medium">Your share of every claim</span>
                <span className="font-mono">
                  {copay === 0 ? (
                    <span className="text-emerald-600 font-semibold">Insurer pays it all</span>
                  ) : (
                    <span>You pay ~₹{Math.round(sumInsured * copay / 100 / 100000)}L on a ₹{Math.round(sumInsured / 100000)}L claim</span>
                  )}
                </span>
              </label>
              <input
                type="range" min={0} max={40} step={5}
                value={copay}
                onChange={(e) => setCopay(parseInt(e.target.value))}
                className="w-full accent-[var(--primary)]"
              />
              <p className="text-[10px] text-[var(--muted-foreground)] mt-0.5">
                {copay === 0
                  ? "No share. Highest premium."
                  : `Your premium drops ~${Math.round(copay * 0.7)}%. In exchange you pay ₹${Math.round(sumInsured * copay / 100 / 1000)}k on a ₹${Math.round(sumInsured / 100000)}L hospital bill.`}
              </p>
            </div>
            <div className="flex items-center gap-3 flex-wrap text-xs">
              <span className="font-medium">City tier:</span>
              {(["metro", "tier1", "tier2"] as const).map((t) => (
                <button key={t} onClick={() => setCityTier(t)}
                  className={`px-2 py-1 rounded-md border text-[11px] ${cityTier === t ? "border-[var(--primary)] bg-[var(--accent)]" : "border-[var(--border)]"}`}>
                  {t}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={smoker} onChange={(e) => setSmoker(e.target.checked)} className="w-3.5 h-3.5 accent-[var(--primary)]" />
              <span>Smoker / tobacco user</span>
            </label>
          </div>
          <div className="bg-[var(--card)] rounded-xl border border-[var(--border)] p-5 flex flex-col justify-center">
            {busy && <div className="text-xs text-[var(--muted-foreground)]">Estimating…</div>}
            {!busy && estimate && (
              <>
                <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">Indicative annual premium</div>
                <div className="text-3xl font-bold mt-1">
                  {fmtINR(estimate.low_inr)} <span className="text-[var(--muted-foreground)] text-base font-normal">–</span> {fmtINR(estimate.high_inr)}
                </div>
                <div className="text-xs text-[var(--muted-foreground)] mt-1">point estimate {fmtINR(estimate.point_estimate_inr)}</div>
                <div className="mt-3 pt-3 border-t border-[var(--border)] text-[10px] text-[var(--muted-foreground)]">
                  {estimate.methodology}
                </div>
                {estimate.sources.length > 0 && (
                  <div className="mt-1 text-[10px] text-[var(--muted-foreground)]">
                    Source anchor: <a href={estimate.sources[0]} target="_blank" rel="noopener" className="hover:text-[var(--primary)] underline">verified URL</a>
                  </div>
                )}
                <div className="mt-2 text-[10px] text-amber-700 dark:text-amber-300">
                  ⚠ {estimate.disclaimer}
                </div>
              </>
            )}
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

function EmptyState({ onSuggest, coverage, t }: { onSuggest: (q: string) => void; coverage: CoverageResponse | null; t: (k: StringKey, v?: Record<string, string | number>) => string }) {
  const suggested: StringKey[] = ["suggested.q1", "suggested.q2", "suggested.q3", "suggested.q4"];
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center px-4 py-6">
      <div className="w-16 h-16 rounded-2xl bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center text-2xl font-bold mb-5">IA</div>
      <h2 className="text-xl sm:text-2xl font-semibold mb-2">{t("welcome.heading_a")}<em className="not-italic text-[var(--primary)]">{t("welcome.heading_b")}</em>{t("welcome.heading_c")}</h2>
      <p className="text-sm text-[var(--muted-foreground)] max-w-xl mb-4">
        {t("welcome.subtitle")} <strong className="text-[var(--foreground)]">{t("welcome.no_commissions")}</strong> {t("welcome.source_link")}
      </p>
      {coverage && (
        <p className="text-xs text-[var(--muted-foreground)] mb-5">
          {t("welcome.coverage_template", { policies: coverage.total_policies, insurers: coverage.total_insurers })}
        </p>
      )}
      <div className="bg-[var(--accent)] border border-[var(--primary)] rounded-xl px-4 py-3 max-w-xl mb-6 text-left">
        <div className="text-xs font-semibold text-[var(--primary)] mb-1">{t("welcome.trust_title")}</div>
        <p className="text-xs text-[var(--muted-foreground)] leading-snug">{t("welcome.trust_body")}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-2xl">
        {suggested.map((key, i) => {
          const q = t(key);
          return (
            <button
              key={i}
              onClick={() => onSuggest(q)}
              className="text-left text-sm px-4 py-3 rounded-xl border border-[var(--border)] bg-[var(--card)] hover:border-[var(--primary)] transition"
            >
              <span className="opacity-50 text-xs">→</span> {q}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function stripInlineCitations(text: string): string {
  // Customer-facing: hide inline [Source: ...] tags from prose; the citation list below the message already shows them.
  return text.replace(/\s*\[(?:Source|Regulation):[^\]]+\]/gi, "").replace(/\s{2,}/g, " ").trim();
}

function Message({ m }: { m: DisplayMessage }) {
  const isUser = m.role === "user";
  const displayContent = isUser ? m.content : stripInlineCitations(m.content);
  return (
    <div className={`flex animate-fade-up ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3 ${
        isUser ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--card)] border border-[var(--border)]"
      }`}>
        <div className="text-sm sm:text-base whitespace-pre-wrap leading-relaxed">{displayContent}</div>
        {m.audioUrl && <audio controls src={m.audioUrl} className="mt-2 w-full max-w-xs" style={{ height: 32 }} />}
        {!isUser && m.citations && m.citations.length > 0 && (
          <PolicyChipsFromCitations citations={m.citations} />
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

// Customer-facing chip: shows the cited policies as clickable pills with their
// rating, no internal jargon (no "score X.YZ" or chunk metadata).
function PolicyChipsFromCitations({ citations }: { citations: Citation[] }) {
  const [cards, setCards] = useState<Record<string, ScorecardResponse | null>>({});
  const [openId, setOpenId] = useState<string | null>(null);
  const seen = new Set<string>();
  const topPolicies = citations.filter((c) => {
    if (seen.has(c.policy_id)) return false;
    seen.add(c.policy_id);
    return true;
  }).slice(0, 3);

  useEffect(() => {
    for (const c of topPolicies) {
      if (cards[c.policy_id] !== undefined) continue;
      getScorecard(c.policy_id)
        .then((s) => setCards((p) => ({ ...p, [c.policy_id]: s })))
        .catch(() => setCards((p) => ({ ...p, [c.policy_id]: null })));
    }
  }, [citations.map((c) => c.policy_id).join("|")]);

  return (
    <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">Cited policies</div>
      <div className="flex flex-wrap gap-1.5">
        {topPolicies.map((c) => {
          const sc = cards[c.policy_id];
          const isOpen = openId === c.policy_id;
          return (
            <div key={c.policy_id} className="inline-flex items-center gap-1.5">
              <button
                onClick={() => setOpenId(isOpen ? null : c.policy_id)}
                className={`text-xs px-2.5 py-1 rounded-lg border transition flex items-center gap-2 ${
                  isOpen ? "border-[var(--primary)] bg-[var(--accent)]" : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--primary)]"
                }`}
                title={sc?.one_liner || c.policy_name}
              >
                {sc && <span className={`inline-flex items-center justify-center w-5 h-5 rounded font-bold text-[11px] ${gradeColor(sc.grade)}`}>{sc.grade}</span>}
                <span className="flex flex-col items-start leading-tight">
                  {/* Bug B — show the insurer label above the policy name so
                      "Sarvah Param" reads as "ManipalCigna · Sarvah Param"
                      instead of an unattributed policy fragment. */}
                  {c.insurer_slug && (
                    <span className="text-[9px] uppercase tracking-wider text-[var(--muted-foreground)]">
                      {c.insurer_slug.replace(/-/g, " ")}
                    </span>
                  )}
                  <span className="font-medium truncate max-w-[160px]">{c.policy_name}</span>
                </span>
              </button>
              {c.source_url && (
                <a
                  href={c.source_url}
                  target="_blank"
                  rel="noopener"
                  title="Open policy PDF"
                  className="text-xs text-[var(--muted-foreground)] hover:text-[var(--primary)]"
                >
                  <PdfIcon />
                </a>
              )}
            </div>
          );
        })}
      </div>
      {openId && cards[openId] && <ScorecardCard sc={cards[openId]!} />}
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
    for (const c of topPolicies) {
      if (cards[c.policy_id] !== undefined) continue;
      getScorecard(c.policy_id)
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
              {lowData && <span title="extraction was incomplete" className="opacity-50">⚠</span>}
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
          <div className="text-[10px] text-[var(--muted-foreground)] mt-0.5">data {sc.data_completeness_pct.toFixed(0)}% complete</div>
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
        Weighted average across 6 criteria. Rules-based — no LLM in the scoring loop. Expand &quot;How is this score computed?&quot; below to see which of 48 schema fields feed each criterion.
      </div>
    </div>
  );
}

function ThinkingDots() {
  return (
    <div className="flex justify-start">
      <div className="bg-[var(--card)] border border-[var(--border)] rounded-2xl px-4 py-3">
        <div className="flex gap-1.5">
          {[0, 1, 2].map((i) => (
            <span key={i} className="w-2 h-2 rounded-full bg-[var(--muted-foreground)] opacity-50" style={{ animation: "fade-up 1.2s ease-in-out infinite", animationDelay: `${i * 0.2}s` }} />
          ))}
        </div>
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

function UploadIcon() {
  return (<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
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
function Jargon({ term, children, uiLang }: { term: keyof typeof GLOSSARY; children: React.ReactNode; uiLang: UILang }) {
  const [open, setOpen] = useState(false);
  const entry = GLOSSARY[term];
  if (!entry) return <>{children}</>;
  const lang = uiLang === "hi" ? "hi" : "en";
  const { title, body } = entry[lang];
  return (
    <span className="inline-flex items-center gap-0.5 relative">
      {children}
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open); }}
        className="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-[var(--muted-foreground)] text-[8px] text-[var(--muted-foreground)] hover:text-[var(--primary)] hover:border-[var(--primary)] ml-0.5"
        aria-label={`Explain ${String(term)}`}
        type="button"
      >
        ?
      </button>
      {open && (
        <span className="absolute z-50 top-full mt-1 left-0 w-64 bg-[var(--card)] border border-[var(--border)] rounded-lg shadow-lg p-2.5 text-left animate-fade-up" onClick={(e) => e.stopPropagation()}>
          <span className="block text-[11px] font-semibold text-[var(--foreground)] mb-1">{title}</span>
          <span className="block text-[10px] text-[var(--muted-foreground)] leading-snug">{body}</span>
          <button onClick={() => setOpen(false)} className="absolute top-1 right-1.5 text-[var(--muted-foreground)] hover:text-[var(--foreground)] text-xs">×</button>
        </span>
      )}
    </span>
  );
}

function insurerInitials(name: string): string {
  return name.split(" ").map((w) => w[0]).filter(Boolean).join("").slice(0, 2).toUpperCase();
}

// Real insurer logos sourced from each insurer's official site (favicons /
// media-kit assets). Fall back to colored letter avatar when the URL fails
// to load (handled by onError swap in InsurerLogo component).
const INSURER_LOGO_URL: Record<string, string> = {
  "aditya-birla":  "https://www.adityabirlacapital.com/healthinsurance/static/assets/images/abhi-logo.svg",
  "bajaj-allianz": "https://www.bajajallianz.com/content/dam/bagic/header/logo.png",
  "care-health":   "https://www.careinsurance.com/upload_master/images/logo.png",
  "hdfc-ergo":     "https://www.hdfcergo.com/etc.clientlibs/hdfcergo/clientlibs/clientlib-site/resources/images/HDFC-ERGO-Logo.png",
  "icici-lombard": "https://www.icicilombard.com/content/dam/ilom-website/icon/icici-lombard-logo-new.svg",
  "manipalcigna":  "https://www.manipalcigna.com/o/manipal-cigna-theme/images/manipal-cigna-logo.svg",
  "new-india":     "https://www.newindia.co.in/portal/readWriteData/NIAImages/NewLogo.png",
  "niva-bupa":     "https://transactions.nivabupa.com/_next/static/media/niva-bupa-logo.7b6e7f4e.svg",
  "star-health":   "https://www.starhealth.in/sites/default/files/star-logo-revised.png",
  "tata-aig":      "https://www.tataaig.com/etc/designs/tataaig/clientlibs/responsive/images/tataaig-logo.svg",
};

function InsurerLogo({ slug, name, size = 44 }: { slug: string; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const url = INSURER_LOGO_URL[slug];
  const color = INSURER_COLOR[slug] || "bg-slate-500";
  if (!url || failed) {
    const initials = insurerInitials(name);
    return (
      <div
        className={`rounded-lg ${color} text-white flex items-center justify-center font-bold shrink-0`}
        style={{ width: size, height: size, fontSize: size * 0.32 }}
      >
        {initials}
      </div>
    );
  }
  return (
    <div
      className="rounded-lg bg-white border border-[var(--border)] flex items-center justify-center shrink-0 overflow-hidden p-1"
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={name}
        onError={() => setFailed(true)}
        className="max-w-full max-h-full object-contain"
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
    if (search && !p.policy_name.toLowerCase().includes(search.toLowerCase()) && !p.insurer_name.toLowerCase().includes(search.toLowerCase())) return false;
    if (insurerFilter !== "all" && p.insurer_slug !== insurerFilter) return false;
    if (grade !== "all" && p.grade !== grade) return false;
    if (p.pre_existing_disease_waiting_months && p.pre_existing_disease_waiting_months > maxPED) return false;
    const maxAvailable = p.sum_insured_options.length ? Math.max(...p.sum_insured_options) : minSI;
    if (maxAvailable < minSI) return false;
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
    <div className="border-t border-[var(--border)] bg-[var(--muted)] animate-fade-up max-h-[80vh] overflow-y-auto scrollbar-thin">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-5">
        <div className="flex items-baseline justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold">{t("mp.heading")}</h2>
            <p className="text-xs text-[var(--muted-foreground)]">
              {t("mp.summary", { total: data.total, insurers: data.insurers_indexed })}
            </p>
          </div>
          <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:underline">{t("mp.close")}</button>
        </div>

        {/* Filter bar */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 mb-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.search")}</label>
              <input
                type="text" value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder={t("mp.search_placeholder")}
                className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5 outline-none focus:border-[var(--primary)]"
              />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.insurer")}</label>
              <select value={insurerFilter} onChange={(e) => setInsurerFilter(e.target.value)} className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5">
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
              <select value={grade} onChange={(e) => setGrade(e.target.value)} className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5">
                <option value="all">{t("mp.all_grades")}</option>
                <option value="A">{t("mp.a_only")}</option>
                <option value="B">{t("mp.b_or_better")}</option>
                <option value="C">{t("mp.c_or_better")}</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.sort_by")}</label>
              <select value={sortBy} onChange={(e) => setSortBy(e.target.value as "score" | "name" | "insurer")} className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5">
                <option value="score">{t("mp.sort_score")}</option>
                <option value="name">{t("mp.sort_name")}</option>
                <option value="insurer">{t("mp.sort_insurer")}</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.max_ped_wait")} <span className="font-mono">{maxPED} mo</span></label>
              <input type="range" min={12} max={48} step={6} value={maxPED} onChange={(e) => setMaxPED(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">{t("mp.min_sum_insured")} <span className="font-mono">{minSI >= 10000000 ? (minSI/10000000) + " cr" : (minSI/100000) + " L"}</span></label>
              <input type="range" min={500000} max={10000000} step={500000} value={minSI} onChange={(e) => setMinSI(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={requireAyush} onChange={(e) => setRequireAyush(e.target.checked)} className="accent-[var(--primary)]" /> {t("mp.ayush_covered")}
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={requireCashless} onChange={(e) => setRequireCashless(e.target.checked)} className="accent-[var(--primary)]" /> {t("mp.cashless_network")}
            </label>
          </div>
          <div className="text-xs text-[var(--muted-foreground)] mt-3">
            {t("mp.showing")} <span className="font-semibold text-[var(--foreground)]">{sorted.length}</span> {t("mp.of")} {data.total} {t("mp.policies_word")}
          </div>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 pb-20">
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

function PerPolicyPremiumEstimator({ policy }: { policy: MarketplacePolicy }) {
  const [age, setAge] = useState(35);
  const defaultSI = policy.sum_insured_options.length ? policy.sum_insured_options[Math.floor(policy.sum_insured_options.length / 2)] : 1000000;
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
            <span>Sum insured</span><span className="font-mono">{siDisp}</span>
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
            <span>Your share per claim</span>
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
            <div className="text-[9px] text-amber-700 dark:text-amber-300 mt-2 leading-tight">Illustrative only. Final quote depends on underwriting.</div>
          </>
        )}
      </div>
    </div>
  );
}

function InsurerReviewsBlock({ reviews }: { reviews: InsurerReviews }) {
  const cm = reviews.claim_metrics || {};
  const agg = reviews.aggregator_ratings || {};
  const score = reviews.aggregate_score || {};
  return (
    <div className="space-y-3 text-xs">
      <div className="flex items-center gap-3">
        {score.value_0_100 != null && (
          <div className={`flex flex-col items-center justify-center px-3 py-1.5 rounded-lg ${gradeColor(score.letter_grade || "C")}`}>
            <div className="text-base font-bold leading-none">{score.value_0_100}</div>
            <div className="text-[9px] uppercase tracking-wide opacity-90">{score.letter_grade}</div>
          </div>
        )}
        <div className="flex-1 text-[var(--muted-foreground)]">{score.headline}</div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        {cm.claim_settlement_ratio_pct != null && (
          <SafeLink href={cm.source_irdai_url} className="rounded-lg border border-[var(--border)] p-2 hover:border-[var(--primary)] transition block">
            <div className="text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">Claim ratio (IRDAI {cm.claim_settlement_ratio_year})</div>
            <div className="font-semibold text-sm">{cm.claim_settlement_ratio_pct}%</div>
          </SafeLink>
        )}
        {cm.complaints_per_10k_policies != null && (
          <div className="rounded-lg border border-[var(--border)] p-2">
            <div className="text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">Complaints / 10K policies</div>
            <div className="font-semibold text-sm">{cm.complaints_per_10k_policies}</div>
          </div>
        )}
        {Object.entries(agg).filter(([, v]) => v?.avg_star != null).slice(0, 2).map(([portal, v]) => (
          <SafeLink key={portal} href={v?.url} className="rounded-lg border border-[var(--border)] p-2 hover:border-[var(--primary)] transition block">
            <div className="text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">{portal}</div>
            <div className="font-semibold text-sm">{v?.avg_star}★ {v?.review_count != null && <span className="opacity-60 font-normal">({v?.review_count.toLocaleString()})</span>}</div>
          </SafeLink>
        ))}
      </div>
      {reviews.reddit_sentiment?.notable_themes && reviews.reddit_sentiment.notable_themes.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold mb-1">What customers say (Reddit)</div>
          <div className="flex flex-wrap gap-1">
            {reviews.reddit_sentiment.notable_themes.slice(0, 6).map((t, i) => (
              <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--muted)] text-[var(--muted-foreground)]">{t}</span>
            ))}
          </div>
        </div>
      )}
      {reviews.youtube_coverage?.top_creators_who_reviewed && reviews.youtube_coverage.top_creators_who_reviewed.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold mb-1">Reviewed by</div>
          <div className="space-y-0.5">
            {reviews.youtube_coverage.top_creators_who_reviewed.slice(0, 3).map((c, i) => (
              <SafeLink key={i} href={c.video_url} className="block text-xs hover:text-[var(--primary)]">
                <span className="font-medium">{c.creator}</span> — <span className="text-[var(--muted-foreground)]">{c.verdict}</span>
              </SafeLink>
            ))}
          </div>
        </div>
      )}
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
  const maxSI = policy.sum_insured_options.length ? Math.max(...policy.sum_insured_options) : null;
  const siDisplay = maxSI ? (maxSI >= 10000000 ? `${maxSI/10000000} cr` : `${maxSI/100000} L`) : "—";
  // Translate the grade one-liner — backend produces fixed English strings;
  // we map them to i18n keys to flip with the UI language.
  const oneLinerKey = ({ A: "grade.a", B: "grade.b", C: "grade.c", D: "grade.d", F: "grade.f" } as Record<string, StringKey>)[policy.grade] || "grade.c";
  const oneLiner = t(oneLinerKey);
  return (
    <div className={`relative text-left bg-[var(--card)] border ${selected ? "border-[var(--primary)] shadow-md" : "border-[var(--border)]"} rounded-xl p-4 hover:border-[var(--primary)] hover:shadow-md transition group`}>
      <label
        className={`absolute top-2 right-2 z-10 flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded-md border ${selected ? "border-[var(--primary)] bg-[var(--accent)] text-[var(--primary)]" : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)]"} ${selectionDisabled && !selected ? "opacity-40 cursor-not-allowed" : "cursor-pointer hover:border-[var(--primary)]"}`}
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
        <div className="flex items-start gap-3 mb-3 pr-16">
          <InsurerLogo slug={policy.insurer_slug} name={policy.insurer_name} size={44} />
          <div className="flex-1 min-w-0">
            <div className="text-xs text-[var(--muted-foreground)] truncate">{policy.insurer_name}</div>
            <div className="font-semibold text-sm truncate group-hover:text-[var(--primary)] transition">{policy.policy_name}</div>
          </div>
          {/* Score badge ONLY when we have a profile — otherwise CTA pill */}
          {isPersonalized ? (
            <div className={`shrink-0 flex flex-col items-center rounded-lg overflow-hidden ${gradeColor(policy.grade)}`}>
              <div className="px-2 pt-0.5 text-[10px] font-semibold opacity-90 uppercase tracking-wide">{policy.grade}</div>
              <div className="px-2 pb-0.5 text-base font-bold leading-none">{policy.overall_score}<span className="text-[10px] font-normal opacity-80">/100</span></div>
            </div>
          ) : (
            <div className="shrink-0 flex flex-col items-center justify-center rounded-lg overflow-hidden bg-[var(--muted)] border border-dashed border-[var(--border)] px-2 py-1.5 text-center" style={{ minWidth: 64 }}>
              <div className="text-[9px] font-semibold uppercase tracking-wide text-[var(--muted-foreground)] leading-tight">{t("card.see_score_pill")}</div>
              <div className="text-[8px] text-[var(--muted-foreground)] leading-tight mt-0.5">{t("card.see_score_sub")}</div>
            </div>
          )}
        </div>
        <p className="text-xs text-[var(--muted-foreground)] mb-3 line-clamp-2">{isPersonalized ? oneLiner : t("card.score_locked_msg")}</p>
        <div className="grid grid-cols-2 gap-2 text-xs">
          <Stat label={<Jargon term="SI" uiLang={t("header.title").includes("स्व") ? "hi" : "en"}>{t("stat.sum_insured_up_to")}</Jargon>} value={siDisplay} />
          <Stat label={<Jargon term="PED" uiLang={t("header.title").includes("स्व") ? "hi" : "en"}>{t("stat.ped_waiting")}</Jargon>} value={policy.pre_existing_disease_waiting_months ? `${policy.pre_existing_disease_waiting_months} mo` : "—"} />
          <Stat label={<Jargon term="AYUSH" uiLang={t("header.title").includes("स्व") ? "hi" : "en"}>{t("stat.ayush")}</Jargon>} value={policy.ayush_coverage === true ? "Yes" : policy.ayush_coverage === false ? "No" : "—"} />
          <Stat label={t("stat.network")} value={policy.network_hospital_count ? `${(policy.network_hospital_count / 1000).toFixed(0)}K+` : "—"} />
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
        <span>How is this score computed? <span className="text-[var(--muted-foreground)] font-normal">(48 fields → 6 criteria, with weights)</span></span>
        <span className="text-[var(--muted-foreground)]">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3 text-xs border-t border-[var(--border)] pt-3">
          {!data && <div className="text-[var(--muted-foreground)] py-2">Loading methodology…</div>}
          {data && (
            <>
              <p className="text-[var(--muted-foreground)] leading-snug">
                {data.scoring_approach} The blueprint below shows which fields drive each criterion and what regulatory or buyer-research source justifies the weight.
              </p>
              {data.criteria.map((c) => (
                <div key={c.name} className="border border-[var(--border)] rounded-md p-2.5 bg-[var(--muted)]">
                  <div className="flex items-baseline justify-between mb-1">
                    <span className="text-xs font-bold">{c.name}</span>
                    <span className="text-[10px] font-mono text-[var(--primary)]">{c.weight_pct}% of overall</span>
                  </div>
                  <div className="text-[11px] text-[var(--foreground)] italic mb-1">"{c.consumer_question}"</div>
                  <div className="text-[11px] text-[var(--muted-foreground)] mb-2 leading-snug">{c.why_it_matters}</div>
                  <details className="text-[11px]">
                    <summary className="cursor-pointer text-[var(--primary)] hover:underline mb-1">
                      {c.fields_driving_score.length} fields drive this score
                    </summary>
                    <ul className="mt-1 space-y-0.5 pl-2">
                      {c.fields_driving_score.map((f, i) => (
                        <li key={i} className="text-[10px]">
                          <code className="text-[var(--primary)]">{f.field}</code>
                          <span className="text-[var(--muted-foreground)]"> — {f.rule}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                  {c.anchors.length > 0 && (
                    <details className="text-[11px] mt-1">
                      <summary className="cursor-pointer text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
                        Why this weight? {c.anchors.length} source{c.anchors.length === 1 ? "" : "s"}
                      </summary>
                      <ul className="mt-1 space-y-0.5 pl-2 text-[10px] text-[var(--muted-foreground)]">
                        {c.anchors.map((a, i) => <li key={i}>· {a}</li>)}
                      </ul>
                    </details>
                  )}
                </div>
              ))}
              <div className="text-[10px] text-[var(--muted-foreground)] pt-1 border-t border-[var(--border)]">
                Grade bands: A ≥85, B 70–84, C 55–69, D 40–54, F &lt;40. Overall = weighted average of the 6 sub-scores (weights re-tuned to buyer profile when known).
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
    max_renewal_age: "Max renewal age",
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
    <div className="fixed inset-0 z-[60] bg-black/50 flex items-center justify-center p-3 animate-fade-up" onClick={onClose}>
      <div
        className="bg-[var(--card)] rounded-2xl shadow-xl w-full max-w-6xl max-h-[92vh] overflow-y-auto scrollbar-thin"
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
            <div className="overflow-x-auto -mx-5 px-5">
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

function PolicyDetailModal({ policy, onClose }: { policy: MarketplacePolicy; onClose: () => void }) {
  const [sc, setSc] = useState<ScorecardResponse | null>(null);
  const [reviews, setReviews] = useState<InsurerReviews | null>(null);
  const [completeness, setCompleteness] = useState<ProfileCompletenessResponse | null>(null);
  useEffect(() => {
    getScorecard(policy.policy_id).then(setSc).catch(() => setSc(null));
    if (policy.insurer_slug) {
      getInsurerReviews(policy.insurer_slug).then(setReviews).catch(() => setReviews(null));
    }
    // Profile completeness gates whether we render the per-user grade.
    // Below threshold: show universal grade only (insurer-quality-led) with a
    // CTA to complete the profile.
    const sid = typeof window !== "undefined" ? localStorage.getItem("insurance_session_id") || undefined : undefined;
    getProfileCompleteness(sid).then(setCompleteness).catch(() => setCompleteness(null));
  }, [policy.policy_id, policy.insurer_slug]);
  const isPersonalized = completeness?.is_personalized === true;

  const initials = insurerInitials(policy.insurer_name);
  const color = INSURER_COLOR[policy.insurer_slug] || "bg-slate-500";
  const maxSI = policy.sum_insured_options.length ? Math.max(...policy.sum_insured_options) : null;
  const siDisplay = maxSI ? (maxSI >= 10000000 ? `${maxSI/10000000} cr` : `${maxSI/100000} L`) : "—";

  const pdfHref = policy.source_pdf_url ||
    `https://www.google.com/search?q=site:${(new URL(policy.insurer_home_url || "https://www.google.com")).hostname}+${encodeURIComponent(policy.policy_name + " policy wording PDF")}`;
  const hasRealPdf = Boolean(policy.source_pdf_url);

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4 animate-fade-up" onClick={onClose}>
      <div className="bg-[var(--card)] rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto scrollbar-thin" onClick={(e) => e.stopPropagation()}>
        <div className="p-5 border-b border-[var(--border)] sticky top-0 bg-[var(--card)] z-10">
          <div className="flex items-start gap-3">
            <div className={`w-12 h-12 rounded-lg ${color} text-white flex items-center justify-center font-bold shrink-0`}>{initials}</div>
            <div className="flex-1 min-w-0">
              <div className="text-xs text-[var(--muted-foreground)]">
                <a href={policy.insurer_home_url} target="_blank" rel="noopener" className="hover:text-[var(--primary)] underline-offset-2 hover:underline">
                  {policy.insurer_name}
                </a>
              </div>
              <h3 className="text-lg font-bold">{policy.policy_name}</h3>
            </div>
            <a
              href={pdfHref}
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-1.5 text-xs font-semibold bg-[var(--primary)] text-white hover:opacity-90 px-3 py-2 rounded-md shrink-0"
              title={hasRealPdf ? "Open the source policy PDF" : "Search the insurer's site for the policy PDF (we don't have a direct link for this policy yet)"}
            >
              <PdfIcon /> {hasRealPdf ? "Policy PDF" : "Find PDF"}
            </a>
            <button onClick={onClose} className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] text-2xl leading-none ml-1">×</button>
          </div>
        </div>

        <div className="p-5 space-y-5">
          {sc && (
            <div>
              {!isPersonalized && (
                <div className="mb-3 bg-[var(--accent)] border border-[var(--primary)] rounded-lg p-3 text-xs">
                  <div className="font-semibold text-[var(--primary)] mb-1">This is the generic grade for an average buyer.</div>
                  <p className="text-[var(--muted-foreground)] leading-snug">
                    Tell me about yourself (age, dependents, conditions, budget) and I&apos;ll re-score this policy for <strong className="text-[var(--foreground)]">your</strong> situation. The same policy can be a B for a 30-year-old and a D for a 60-year-old with diabetes — context changes everything.
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
                  <div className="text-xs text-[var(--muted-foreground)]">{sc.one_liner}</div>
                </div>
              </div>
              <ScorecardCard sc={sc} />
              <MethodologyExpander />
            </div>
          )}

          <div>
            <h4 className="text-sm font-semibold mb-3">What this policy covers, in plain words</h4>
            <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
              <Stat label={<Jargon term="SI" uiLang="en">Cover up to</Jargon>} value={siDisplay} />
              <Stat label="Who can buy + renew" value={(() => {
                const min = policy.min_entry_age;
                const max = policy.max_entry_age;
                const renew = policy.max_renewal_age;
                const minStr = min ? (min >= 30 && min <= 365 ? `${min} days` : `${min} yrs`) : null;
                const maxStr = max ? `${max} yrs` : null;
                const range = minStr && maxStr ? `${minStr} – ${maxStr}` : (minStr || maxStr || "Not stated");
                const renewStr = renew ? (renew >= 99 ? " · lifelong renewal" : ` · renews up to ${renew}`) : "";
                return range + renewStr;
              })()} />
              <Stat label="Wait before any claim" value={policy.initial_waiting_period_days ? `${policy.initial_waiting_period_days} days from start` : "Not stated"} />
              <Stat label={<Jargon term="PED" uiLang="en">Wait if you already had a condition</Jargon>} value={policy.pre_existing_disease_waiting_months ? `${policy.pre_existing_disease_waiting_months} months` : "Not stated"} />
              <Stat label="Maternity" value={policy.maternity_coverage === true ? (policy.maternity_waiting_months ? `Covered after ${policy.maternity_waiting_months}-month wait` : "Covered") : policy.maternity_coverage === false ? "Not covered" : "Check the wording"} />
              <Stat label={<Jargon term="CoPay" uiLang="en">Your share per claim</Jargon>} value={policy.copayment_pct != null ? (policy.copayment_pct === 0 ? "Insurer pays it all" : `You pay ${policy.copayment_pct}% of every bill`) : "Not stated"} />
              <Stat label={<Jargon term="NCB" uiLang="en">Reward for staying claim-free</Jargon>} value={policy.no_claim_bonus_pct ? `+${policy.no_claim_bonus_pct}% cover each claim-free year` : "Not stated"} />
              <Stat label={<Jargon term="Cashless" uiLang="en">Cashless at hospital</Jargon>} value={policy.cashless_treatment_supported === true ? `Yes · ${policy.network_hospital_count ? policy.network_hospital_count.toLocaleString() + "+ network hospitals" : "network published by insurer"}` : "Not supported"} />
              <Stat label={<Jargon term="AYUSH" uiLang="en">AYUSH (Ayurveda, Yoga…)</Jargon>} value={policy.ayush_coverage === true ? "Covered" : policy.ayush_coverage === false ? "Not covered" : "Check the wording"} />
              {policy.room_rent_capping && (
                <div className="col-span-2 pt-1 border-t border-[var(--border)]">
                  <Stat label={<Jargon term="RoomRent" uiLang="en">Hospital room category</Jargon>} value={policy.room_rent_capping} />
                </div>
              )}
            </div>
          </div>

          {/* Per-policy premium estimator */}
          <div className="pt-5 border-t border-[var(--border)]">
            <h4 className="text-sm font-semibold mb-2">Estimate premium for this policy</h4>
            <PerPolicyPremiumEstimator policy={policy} />
          </div>

          {/* Insurer reviews + IRDAI metrics */}
          {reviews && (
            <div className="pt-5 border-t border-[var(--border)]">
              <h4 className="text-sm font-semibold mb-2">{reviews.insurer_name} — reputation & claim metrics</h4>
              <InsurerReviewsBlock reviews={reviews} />
            </div>
          )}

          <div className="text-[10px] text-[var(--muted-foreground)] pt-3 border-t border-[var(--border)]">
            Rating methodology weighs 24 of 48 policy fields across coverage breadth, cost predictability, waiting periods, claim experience, renewal protection, and bonus benefits. Data extracted from the policy wording PDF and combined with publicly disclosed IRDAI claim metrics. Premium ranges are illustrative.
          </div>
        </div>
      </div>
    </div>
  );
}
