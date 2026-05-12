"use client";

import { useEffect, useRef, useState } from "react";
import {
  audioBlobURLFromBase64,
  Citation,
  ChatMessage,
  CoverageResponse,
  getCoverage,
  getHealth,
  getScorecard,
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
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setHealth({ status: h.status, missing: h.missing_keys }))
      .catch(() => setHealth({ status: "unreachable", missing: [] }));
    getCoverage()
      .then(setCoverage)
      .catch(() => setCoverage(null));
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
    } catch (e) {
      console.error(e);
      pushAssistant(`Sorry — mic permission denied or unavailable.`);
    }
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
          <div className="flex items-center gap-3">
            <button
              onClick={() => { setShowPremium(!showPremium); setShowCoverage(false); }}
              className={`text-xs px-3 py-1.5 rounded-lg border transition ${showPremium ? "border-[var(--primary)] bg-[var(--accent)]" : "border-[var(--border)] hover:border-[var(--primary)]"}`}
            >
              Premium calculator
            </button>
            {coverage && (
              <button
                onClick={() => { setShowCoverage(!showCoverage); setShowPremium(false); }}
                className={`text-xs px-3 py-1.5 rounded-lg border transition ${showCoverage ? "border-[var(--primary)] bg-[var(--accent)]" : "border-[var(--border)] hover:border-[var(--primary)]"}`}
              >
                {coverage.total_policies} policies · {coverage.total_insurers} insurers
              </button>
            )}
            <HealthBadge health={health} />
          </div>
        </div>
        {showCoverage && coverage && <CoveragePanel coverage={coverage} onClose={() => setShowCoverage(false)} />}
        {showPremium && <PremiumCalculatorPanel onClose={() => setShowPremium(false)} />}
      </header>

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
        Sarvam-M · Sarvam Saarika STT · Sarvam Bulbul TTS · Voyage-prepared embeddings · Llama-3.3-70B grader · DeepSeek-V3 fallback brain. Advisory only — verify with the insurer before purchase.
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

function Message({ m }: { m: DisplayMessage }) {
  const isUser = m.role === "user";
  return (
    <div className={`flex animate-fade-up ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3 ${
        isUser ? "bg-[var(--primary)] text-[var(--primary-foreground)]" : "bg-[var(--card)] border border-[var(--border)]"
      } ${m.blocked ? "ring-1 ring-amber-300" : ""}`}>
        <div className="text-sm sm:text-base whitespace-pre-wrap leading-relaxed">{m.content}</div>
        {m.audioUrl && <audio controls src={m.audioUrl} className="mt-2 w-full max-w-xs" style={{ height: 32 }} />}
        {m.citations && m.citations.length > 0 && !isUser && (
          <ScorecardBadgesForCitations citations={m.citations} />
        )}
        {m.citations && m.citations.length > 0 && (
          <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-1.5">
            <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">Sources</div>
            {m.citations.slice(0, 5).map((c, i) => (
              <a key={i} href={c.source_url || "#"} target="_blank" rel="noopener" className="block text-xs text-[var(--muted-foreground)] hover:text-[var(--primary)] transition">
                <span className="font-medium">{c.policy_name}</span>
                <span className="opacity-60"> · {c.insurer_slug} · p.{c.page_start}</span>
                <span className="opacity-50"> · score {c.score.toFixed(2)}</span>
              </a>
            ))}
          </div>
        )}
        {m.brain && (<div className="mt-2 text-[10px] text-[var(--muted-foreground)] opacity-60">{m.brain} · {m.latencyMs}ms</div>)}
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
