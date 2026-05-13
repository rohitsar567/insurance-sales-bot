"use client";

import { useEffect, useRef, useState } from "react";
import {
  audioBlobURLFromBase64,
  Citation,
  ChatMessage,
  CoverageResponse,
  getCoverage,
  getHealth,
  getInsurerReviews,
  getMarketplace,
  getScorecard,
  InsurerReviews,
  MarketplacePolicy,
  MarketplaceResponse,
  postChat,
  postPremiumEstimate,
  postTranscribe,
  PremiumEstimateResponse,
  ScorecardResponse,
  uploadPolicy,
} from "@/lib/api";

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
  const [health, setHealth] = useState<{ status: string; missing: string[] } | null>(null);
  const [coverage, setCoverage] = useState<CoverageResponse | null>(null);
  const [showCoverage, setShowCoverage] = useState(false);
  const [showPremium, setShowPremium] = useState(false);
  const [showMarketplace, setShowMarketplace] = useState(false);
  const [marketplace, setMarketplace] = useState<MarketplaceResponse | null>(null);
  const [openPolicy, setOpenPolicy] = useState<MarketplacePolicy | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [handsFree, setHandsFree] = useState(false);  // VAD auto-cutoff mode

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const vadFrameRef = useRef<number | null>(null);
  const silenceStartRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setHealth({ status: h.status, missing: h.missing_keys }))
      .catch(() => setHealth({ status: "unreachable", missing: [] }));
    getCoverage()
      .then(setCoverage)
      .catch(() => setCoverage(null));
    getMarketplace()
      .then(setMarketplace)
      .catch(() => setMarketplace(null));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function pushUser(text: string) {
    setMessages((m) => [...m, { id: `u_${Date.now()}`, role: "user", content: text }]);
  }
  function pushAssistant(content: string, extras: Partial<DisplayMessage> = {}) {
    setMessages((m) => [...m, { id: `a_${Date.now()}`, role: "assistant", content, ...extras }]);
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setBusy(true);
    setInput("");
    pushUser(text);
    try {
      const history: ChatMessage[] = messages.map((m) => ({ role: m.role, content: m.content }));
      const res = await postChat({
        user_text: text,
        session_id: sessionId,
        chat_history: history,
        return_audio: returnAudio,
        tts_language_code: ttsLang,
      });
      setSessionId(res.session_id);
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
      pushAssistant(`Sorry — backend error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

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
              <h1 className="font-semibold text-base sm:text-lg leading-tight">Insurance Sales Portfolio Expert</h1>
              <p className="text-xs text-[var(--muted-foreground)]">Voice-first AI advisor · Indian health insurance · Sarvam AI</p>
            </div>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <button
              onClick={() => { setShowMarketplace(!showMarketplace); setShowPremium(false); setShowCoverage(false); }}
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
                  <div className="text-[10px] uppercase tracking-wider opacity-85 leading-none">Interactive</div>
                  <div className="text-xs font-bold leading-tight whitespace-nowrap">Policy Library</div>
                </div>
                {marketplace && (
                  <div className="flex flex-col items-center justify-center px-3 py-1 bg-white/15 border-l border-white/20">
                    <div className="text-sm font-bold leading-none">{marketplace.total}</div>
                    <div className="text-[9px] uppercase tracking-wider opacity-90 leading-none mt-0.5">policies</div>
                  </div>
                )}
                {marketplace && (
                  <div className="hidden sm:flex flex-col items-center justify-center px-3 py-1 bg-black/10">
                    <div className="text-sm font-bold leading-none">{marketplace.insurers_indexed}</div>
                    <div className="text-[9px] uppercase tracking-wider opacity-90 leading-none mt-0.5">insurers</div>
                  </div>
                )}
              </div>
            </button>
            <button
              onClick={() => { setShowPremium(!showPremium); setShowMarketplace(false); setShowCoverage(false); }}
              className={`group relative overflow-hidden rounded-xl transition-all shadow-sm hover:shadow-md ${
                showPremium ? "ring-2 ring-[var(--primary)]" : ""
              }`}
              title="Estimate annual premium"
            >
              <div className="absolute inset-0 bg-gradient-to-br from-amber-500 via-orange-500 to-rose-500" />
              <div className="relative flex items-stretch text-white">
                <div className="flex items-center justify-center px-3 py-2 bg-black/15">
                  <RupeeIcon />
                </div>
                <div className="px-3 py-2 text-left">
                  <div className="text-[10px] uppercase tracking-wider opacity-85 leading-none">Estimate</div>
                  <div className="text-xs font-bold leading-tight whitespace-nowrap">Annual premium</div>
                </div>
              </div>
            </button>
          </div>
        </div>
        {showMarketplace && marketplace && (
          <MarketplacePanel
            data={marketplace}
            onOpenPolicy={(p) => setOpenPolicy(p)}
            onClose={() => setShowMarketplace(false)}
          />
        )}
        {showPremium && <PremiumCalculatorPanel onClose={() => setShowPremium(false)} />}
      </header>
      {openPolicy && <PolicyDetailModal policy={openPolicy} onClose={() => setOpenPolicy(null)} />}

      <main className="flex-1 max-w-6xl w-full mx-auto px-4 sm:px-6 py-4 sm:py-6 flex flex-col">
        {messages.length === 0 ? (
          <EmptyState onSuggest={(q) => send(q)} coverage={coverage} />
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

      <footer className="border-t border-[var(--border)] py-3 px-6 text-center text-xs text-[var(--muted-foreground)]">
        Advisory only. Information based on policy documents; verify with the insurer before purchase. All policy ratings are illustrative and based on publicly disclosed data.
      </footer>
    </div>
  );
}

function PremiumCalculatorPanel({ onClose }: { onClose: () => void }) {
  const [age, setAge] = useState(35);
  const [sumInsured, setSumInsured] = useState(1000000);
  const [cityTier, setCityTier] = useState<"metro" | "tier1" | "tier2">("metro");
  const [smoker, setSmoker] = useState(false);
  const [familySize, setFamilySize] = useState(2);
  const [estimate, setEstimate] = useState<PremiumEstimateResponse | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const handler = setTimeout(() => {
      setBusy(true);
      postPremiumEstimate({ age, sum_insured_inr: sumInsured, city_tier: cityTier, smoker, family_size: familySize })
        .then(setEstimate)
        .catch(() => setEstimate(null))
        .finally(() => setBusy(false));
    }, 200); // debounce
    return () => clearTimeout(handler);
  }, [age, sumInsured, cityTier, smoker, familySize]);

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
                <span className="font-mono">{familySize} {familySize === 1 ? "person" : "people"}</span>
              </label>
              <input
                type="range" min={1} max={6} step={1}
                value={familySize}
                onChange={(e) => setFamilySize(parseInt(e.target.value))}
                className="w-full accent-[var(--primary)]"
              />
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

function EmptyState({ onSuggest, coverage }: { onSuggest: (q: string) => void; coverage: CoverageResponse | null }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center px-4">
      <div className="w-16 h-16 rounded-2xl bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center text-2xl font-bold mb-6">IA</div>
      <h2 className="text-xl sm:text-2xl font-semibold mb-2">Hi, I&apos;m your AI insurance advisor.</h2>
      <p className="text-sm text-[var(--muted-foreground)] max-w-md mb-3">
        Ask me about Indian health insurance — coverage, waiting periods, exclusions, side-by-side comparisons. Speak or type, English or हिन्दी. Every fact comes with a citation.
      </p>
      {coverage && (
        <p className="text-xs text-[var(--muted-foreground)] mb-6">
          Currently covering <span className="font-semibold text-[var(--foreground)]">{coverage.total_policies} policies</span> from <span className="font-semibold text-[var(--foreground)]">{coverage.total_insurers} insurers</span>. Have a different PDF? <span className="font-semibold">Click the 📎 icon to upload.</span>
        </p>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-2xl">
        {SUGGESTED_QUESTIONS.map((q, i) => (
          <button
            key={i}
            onClick={() => onSuggest(q)}
            className="text-left text-sm px-4 py-3 rounded-xl border border-[var(--border)] bg-[var(--card)] hover:border-[var(--primary)] transition"
          >
            <span className="opacity-50 text-xs">→</span> {q}
          </button>
        ))}
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
                <span className="font-medium truncate max-w-[140px]">{c.policy_name}</span>
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

function ScorecardCard({ sc }: { sc: ScorecardResponse }) {
  return (
    <div className="mt-2 rounded-xl border border-[var(--border)] bg-[var(--card)] p-3 text-xs animate-fade-up">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center justify-center w-7 h-7 rounded-lg font-bold ${gradeColor(sc.grade)}`}>
            {sc.grade}
          </span>
          <div>
            <div className="font-semibold text-sm">{sc.policy_name}</div>
            <div className="text-[var(--muted-foreground)] text-[11px]">{sc.one_liner}</div>
          </div>
        </div>
        <div className="text-right">
          <div className="text-lg font-semibold">{sc.overall_score}<span className="text-[var(--muted-foreground)] text-xs">/100</span></div>
          <div className="text-[10px] text-[var(--muted-foreground)]">data {sc.data_completeness_pct.toFixed(0)}% complete</div>
        </div>
      </div>
      <div className="space-y-1.5 mt-3">
        {sc.sub_scores.map((s) => (
          <div key={s.name}>
            <div className="flex items-center justify-between text-[11px]">
              <span className="font-medium">{s.name}</span>
              <span className="text-[var(--muted-foreground)]">{s.score} · {s.summary}</span>
            </div>
            <div className="h-1.5 rounded-full bg-[var(--muted)] overflow-hidden">
              <div
                className={`h-full ${s.score >= 70 ? "bg-emerald-500" : s.score >= 55 ? "bg-amber-500" : "bg-red-400"}`}
                style={{ width: `${Math.max(2, s.score)}%` }}
              />
            </div>
            {s.signals && s.signals.length > 0 && (
              <ul className="mt-1 ml-1 space-y-0.5">
                {s.signals.slice(0, 4).map((sig, i) => (
                  <li key={i} className="text-[10px] text-[var(--muted-foreground)]">
                    · {sig}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      <div className="mt-2 pt-2 border-t border-[var(--border)] text-[10px] text-[var(--muted-foreground)]">
        Methodology: 24 of 48 schema fields drive this grade. Rules-based, no LLM-in-the-loop.
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

function insurerInitials(name: string): string {
  return name.split(" ").map((w) => w[0]).filter(Boolean).join("").slice(0, 2).toUpperCase();
}

function MarketplacePanel({
  data,
  onOpenPolicy,
  onClose,
}: {
  data: MarketplaceResponse;
  onOpenPolicy: (p: MarketplacePolicy) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [insurerFilter, setInsurerFilter] = useState<string>("all");
  const [maxPED, setMaxPED] = useState(48);
  const [minSI, setMinSI] = useState(500000);
  const [requireAyush, setRequireAyush] = useState(false);
  const [requireCashless, setRequireCashless] = useState(false);
  const [grade, setGrade] = useState<string>("all");
  const [sortBy, setSortBy] = useState<"score" | "name" | "insurer">("score");

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
            <h2 className="text-lg font-semibold">Health insurance marketplace</h2>
            <p className="text-xs text-[var(--muted-foreground)]">
              {data.total} policies from {data.insurers_indexed} leading Indian health insurers. Click any policy for the full rating, key terms, and the source document.
            </p>
          </div>
          <button onClick={onClose} className="text-xs text-[var(--muted-foreground)] hover:underline">close</button>
        </div>

        {/* Filter bar */}
        <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 mb-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">Search</label>
              <input
                type="text" value={search} onChange={(e) => setSearch(e.target.value)}
                placeholder="Policy or insurer name…"
                className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5 outline-none focus:border-[var(--primary)]"
              />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">Insurer</label>
              <select value={insurerFilter} onChange={(e) => setInsurerFilter(e.target.value)} className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5">
                <option value="all">All ({data.insurers_indexed})</option>
                {insurers.map((s) => {
                  const name = data.policies.find((p) => p.insurer_slug === s)?.insurer_name || s;
                  const count = data.policies.filter((p) => p.insurer_slug === s).length;
                  return <option key={s} value={s}>{name} ({count})</option>;
                })}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">Min rating</label>
              <select value={grade} onChange={(e) => setGrade(e.target.value)} className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5">
                <option value="all">All grades</option>
                <option value="A">A only</option>
                <option value="B">B or better</option>
                <option value="C">C or better</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">Sort by</label>
              <select value={sortBy} onChange={(e) => setSortBy(e.target.value as "score" | "name" | "insurer")} className="w-full text-sm bg-transparent border border-[var(--border)] rounded-md px-2 py-1.5">
                <option value="score">Highest rated</option>
                <option value="name">Policy name (A–Z)</option>
                <option value="insurer">Insurer (A–Z)</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">Max pre-existing wait: <span className="font-mono">{maxPED} mo</span></label>
              <input type="range" min={12} max={48} step={6} value={maxPED} onChange={(e) => setMaxPED(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-[var(--muted-foreground)] uppercase tracking-wide mb-1">Min sum insured: <span className="font-mono">{minSI >= 10000000 ? (minSI/10000000) + " cr" : (minSI/100000) + " L"}</span></label>
              <input type="range" min={500000} max={10000000} step={500000} value={minSI} onChange={(e) => setMinSI(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
            </div>
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={requireAyush} onChange={(e) => setRequireAyush(e.target.checked)} className="accent-[var(--primary)]" /> AYUSH covered
            </label>
            <label className="flex items-center gap-2 text-xs">
              <input type="checkbox" checked={requireCashless} onChange={(e) => setRequireCashless(e.target.checked)} className="accent-[var(--primary)]" /> Cashless network
            </label>
          </div>
          <div className="text-xs text-[var(--muted-foreground)] mt-3">
            Showing <span className="font-semibold text-[var(--foreground)]">{sorted.length}</span> of {data.total} policies
          </div>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {sorted.map((p) => (
            <PolicyCard key={p.policy_id} policy={p} onOpen={() => onOpenPolicy(p)} />
          ))}
          {sorted.length === 0 && (
            <div className="col-span-full text-center text-sm text-[var(--muted-foreground)] py-12">
              No policies match these filters. Try widening the criteria.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PerPolicyPremiumEstimator({ policy }: { policy: MarketplacePolicy }) {
  const [age, setAge] = useState(35);
  const defaultSI = policy.sum_insured_options.length ? policy.sum_insured_options[Math.floor(policy.sum_insured_options.length / 2)] : 1000000;
  const [si, setSI] = useState(defaultSI);
  const [city, setCity] = useState<"metro" | "tier1" | "tier2">("metro");
  const [smoker, setSmoker] = useState(false);
  const [fam, setFam] = useState(1);
  const [est, setEst] = useState<PremiumEstimateResponse | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => {
      setBusy(true);
      postPremiumEstimate({ age, sum_insured_inr: si, city_tier: city, smoker, family_size: fam, policy_id: policy.policy_id })
        .then(setEst).catch(() => setEst(null)).finally(() => setBusy(false));
    }, 150);
    return () => clearTimeout(t);
  }, [age, si, city, smoker, fam, policy.policy_id]);
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
            <span>Family size</span><span className="font-mono">{fam}</span>
          </div>
          <input type="range" min={1} max={6} value={fam} onChange={(e) => setFam(parseInt(e.target.value))} className="w-full accent-[var(--primary)]" />
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
          <a href={cm.source_irdai_url || "#"} target="_blank" rel="noopener" className="rounded-lg border border-[var(--border)] p-2 hover:border-[var(--primary)] transition">
            <div className="text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">Claim ratio (IRDAI {cm.claim_settlement_ratio_year})</div>
            <div className="font-semibold text-sm">{cm.claim_settlement_ratio_pct}%</div>
          </a>
        )}
        {cm.complaints_per_10k_policies != null && (
          <div className="rounded-lg border border-[var(--border)] p-2">
            <div className="text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">Complaints / 10K policies</div>
            <div className="font-semibold text-sm">{cm.complaints_per_10k_policies}</div>
          </div>
        )}
        {Object.entries(agg).filter(([, v]) => v?.avg_star != null).slice(0, 2).map(([portal, v]) => (
          <a key={portal} href={v?.url || "#"} target="_blank" rel="noopener" className="rounded-lg border border-[var(--border)] p-2 hover:border-[var(--primary)] transition">
            <div className="text-[9px] uppercase tracking-wide text-[var(--muted-foreground)]">{portal}</div>
            <div className="font-semibold text-sm">{v?.avg_star}★ {v?.review_count != null && <span className="opacity-60 font-normal">({v?.review_count.toLocaleString()})</span>}</div>
          </a>
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
              <a key={i} href={c.video_url || "#"} target="_blank" rel="noopener" className="block text-xs hover:text-[var(--primary)]">
                <span className="font-medium">{c.creator}</span> — <span className="text-[var(--muted-foreground)]">{c.verdict}</span>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PolicyCard({ policy, onOpen }: { policy: MarketplacePolicy; onOpen: () => void }) {
  const initials = insurerInitials(policy.insurer_name);
  const color = INSURER_COLOR[policy.insurer_slug] || "bg-slate-500";
  const maxSI = policy.sum_insured_options.length ? Math.max(...policy.sum_insured_options) : null;
  const siDisplay = maxSI ? (maxSI >= 10000000 ? `${maxSI/10000000} cr` : `${maxSI/100000} L`) : "—";
  return (
    <button
      onClick={onOpen}
      className="text-left bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 hover:border-[var(--primary)] hover:shadow-md transition group"
    >
      <div className="flex items-start gap-3 mb-3">
        <div className={`w-11 h-11 rounded-lg ${color} text-white flex items-center justify-center font-bold text-sm shrink-0`}>{initials}</div>
        <div className="flex-1 min-w-0">
          <div className="text-xs text-[var(--muted-foreground)] truncate">{policy.insurer_name}</div>
          <div className="font-semibold text-sm truncate group-hover:text-[var(--primary)] transition">{policy.policy_name}</div>
        </div>
        <div className={`shrink-0 flex flex-col items-center rounded-lg overflow-hidden ${gradeColor(policy.grade)}`}>
          <div className="px-2 pt-0.5 text-[10px] font-semibold opacity-90 uppercase tracking-wide">{policy.grade}</div>
          <div className="px-2 pb-0.5 text-base font-bold leading-none">{policy.overall_score}<span className="text-[10px] font-normal opacity-80">/100</span></div>
        </div>
      </div>
      <p className="text-xs text-[var(--muted-foreground)] mb-3 line-clamp-2">{policy.one_liner}</p>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Stat label="Sum insured up to" value={siDisplay} />
        <Stat label="PED waiting" value={policy.pre_existing_disease_waiting_months ? `${policy.pre_existing_disease_waiting_months} mo` : "—"} />
        <Stat label="AYUSH" value={policy.ayush_coverage === true ? "Yes" : policy.ayush_coverage === false ? "No" : "—"} />
        <Stat label="Network" value={policy.network_hospital_count ? `${(policy.network_hospital_count / 1000).toFixed(0)}K+` : "—"} />
      </div>
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] text-[var(--muted-foreground)] uppercase tracking-wide">{label}</div>
      <div className="text-xs font-semibold">{value}</div>
    </div>
  );
}

function PolicyDetailModal({ policy, onClose }: { policy: MarketplacePolicy; onClose: () => void }) {
  const [sc, setSc] = useState<ScorecardResponse | null>(null);
  const [reviews, setReviews] = useState<InsurerReviews | null>(null);
  useEffect(() => {
    getScorecard(policy.policy_id).then(setSc).catch(() => setSc(null));
    if (policy.insurer_slug) {
      getInsurerReviews(policy.insurer_slug).then(setReviews).catch(() => setReviews(null));
    }
  }, [policy.policy_id, policy.insurer_slug]);

  const initials = insurerInitials(policy.insurer_name);
  const color = INSURER_COLOR[policy.insurer_slug] || "bg-slate-500";
  const maxSI = policy.sum_insured_options.length ? Math.max(...policy.sum_insured_options) : null;
  const siDisplay = maxSI ? (maxSI >= 10000000 ? `${maxSI/10000000} cr` : `${maxSI/100000} L`) : "—";

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
              {policy.source_pdf_url && (
                <a href={policy.source_pdf_url} target="_blank" rel="noopener" className="inline-flex items-center gap-1.5 text-xs text-[var(--primary)] hover:underline mt-1">
                  <PdfIcon /> Open policy document
                </a>
              )}
            </div>
            <button onClick={onClose} className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] text-2xl leading-none">×</button>
          </div>
        </div>

        <div className="p-5 space-y-5">
          {sc && (
            <div>
              <div className="flex items-center gap-3 mb-3">
                <span className={`inline-flex items-center justify-center w-12 h-12 rounded-lg font-bold ${gradeColor(sc.grade)}`}>{sc.grade}</span>
                <div className="flex-1">
                  <div className="text-2xl font-bold">{sc.overall_score}<span className="text-[var(--muted-foreground)] text-base font-normal">/100</span></div>
                  <div className="text-xs text-[var(--muted-foreground)]">{sc.one_liner}</div>
                </div>
              </div>
              <ScorecardCard sc={sc} />
            </div>
          )}

          <div>
            <h4 className="text-sm font-semibold mb-2">Key terms</h4>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <Stat label="Sum insured up to" value={siDisplay} />
              <Stat label="Entry age" value={policy.min_entry_age && policy.max_entry_age ? `${policy.min_entry_age}-${policy.max_entry_age}` : "—"} />
              <Stat label="Renewal up to" value={policy.max_renewal_age ? (policy.max_renewal_age >= 99 ? "Lifelong" : `${policy.max_renewal_age}`) : "—"} />
              <Stat label="Initial waiting" value={policy.initial_waiting_period_days ? `${policy.initial_waiting_period_days} days` : "—"} />
              <Stat label="Pre-existing waiting" value={policy.pre_existing_disease_waiting_months ? `${policy.pre_existing_disease_waiting_months} months` : "—"} />
              <Stat label="Maternity waiting" value={policy.maternity_waiting_months ? `${policy.maternity_waiting_months} months` : "—"} />
              <Stat label="Copayment" value={policy.copayment_pct != null ? `${policy.copayment_pct}%` : "None"} />
              <Stat label="No-claim bonus" value={policy.no_claim_bonus_pct ? `${policy.no_claim_bonus_pct}%` : "—"} />
              <Stat label="Network hospitals" value={policy.network_hospital_count ? `${policy.network_hospital_count.toLocaleString()}+` : "—"} />
              <Stat label="AYUSH covered" value={policy.ayush_coverage === true ? "Yes" : policy.ayush_coverage === false ? "No" : "—"} />
              <Stat label="Maternity" value={policy.maternity_coverage === true ? "Covered" : policy.maternity_coverage === false ? "Not covered" : "—"} />
              <Stat label="Cashless" value={policy.cashless_treatment_supported === true ? "Supported" : "—"} />
              {policy.room_rent_capping && (
                <div className="col-span-2">
                  <div className="text-[10px] text-[var(--muted-foreground)] uppercase tracking-wide">Room rent</div>
                  <div className="text-xs">{policy.room_rent_capping}</div>
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
