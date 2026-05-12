"use client";

import { useEffect, useRef, useState } from "react";
import {
  audioBlobURLFromBase64,
  Citation,
  ChatMessage,
  getHealth,
  postChat,
  postTranscribe,
} from "@/lib/api";

type DisplayMessage = ChatMessage & {
  id: string;
  citations?: Citation[];
  audioUrl?: string;
  brain?: string;
  latencyMs?: number;
};

const SUGGESTED_QUESTIONS = [
  "What is the waiting period for pre-existing diseases?",
  "Does HDFC ERGO Optima Secure cover ayurveda?",
  "Compare maternity coverage across Star and Niva Bupa",
  "What's the room rent cap on Care Health policies?",
];

export default function Page() {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [recording, setRecording] = useState(false);
  const [returnAudio, setReturnAudio] = useState(true);
  const [ttsLang, setTtsLang] = useState<"en-IN" | "hi-IN">("en-IN");
  const [health, setHealth] = useState<{ status: string; missing: string[] } | null>(null);
  const [sessionId, setSessionId] = useState<string | undefined>();

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getHealth()
      .then((h) => setHealth({ status: h.status, missing: h.missing_keys }))
      .catch(() => setHealth({ status: "unreachable", missing: [] }));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  function pushUser(text: string) {
    const id = `u_${Date.now()}`;
    setMessages((m) => [...m, { id, role: "user", content: text }]);
    return id;
  }

  function pushAssistant(content: string, extras: Partial<DisplayMessage> = {}) {
    const id = `a_${Date.now()}`;
    setMessages((m) => [...m, { id, role: "assistant", content, ...extras }]);
    return id;
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
      });
      if (audioUrl) {
        const audio = new Audio(audioUrl);
        audio.play().catch(() => {});
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      pushAssistant(`Sorry — backend error: ${msg}`);
    } finally {
      setBusy(false);
    }
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "";
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream);
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) audioChunksRef.current.push(ev.data);
      };

      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        setRecording(false);
        if (blob.size < 1000) return;
        setBusy(true);
        try {
          const { text } = await postTranscribe(blob, ttsLang);
          if (text && text.trim()) {
            await send(text);
          } else {
            pushAssistant("Sorry, I couldn't hear that clearly. Please try again.");
          }
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : String(e);
          pushAssistant(`Sorry — transcribe error: ${msg}`);
        } finally {
          setBusy(false);
        }
      };

      recorder.start();
      setRecording(true);
    } catch (e) {
      console.error(e);
      pushAssistant(`Sorry — mic permission denied or unavailable.`);
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
  }

  return (
    <div className="min-h-screen flex flex-col bg-[var(--background)] text-[var(--foreground)]">
      {/* Header */}
      <header className="border-b border-[var(--border)] bg-[var(--card)]">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center font-bold text-sm">
              IA
            </div>
            <div>
              <h1 className="font-semibold text-base sm:text-lg leading-tight">
                Insurance Sales Portfolio Expert
              </h1>
              <p className="text-xs text-[var(--muted-foreground)]">
                Voice-first AI advisor • Indian health insurance • Sarvam AI
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <HealthBadge health={health} />
          </div>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 sm:px-6 py-4 sm:py-6 flex flex-col">
        {messages.length === 0 ? (
          <EmptyState onSuggest={(q) => send(q)} />
        ) : (
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto scrollbar-thin space-y-4 mb-4 pr-1"
          >
            {messages.map((m) => (
              <Message key={m.id} m={m} />
            ))}
            {busy && <ThinkingDots />}
          </div>
        )}

        {/* Input bar */}
        <div className="border border-[var(--border)] rounded-2xl bg-[var(--card)] p-3 shadow-sm">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
              placeholder="Ask about coverage, waiting periods, exclusions, or compare policies…"
              rows={1}
              className="flex-1 resize-none bg-transparent outline-none text-sm sm:text-base px-2 py-2 min-h-[40px] max-h-32"
              disabled={busy}
            />
            <button
              type="button"
              onClick={recording ? stopRecording : startRecording}
              disabled={busy && !recording}
              className={`shrink-0 w-11 h-11 rounded-xl flex items-center justify-center transition-all ${
                recording
                  ? "bg-[var(--error)] text-white animate-record-pulse"
                  : "bg-[var(--muted)] hover:bg-[var(--border)] text-[var(--foreground)]"
              } disabled:opacity-40`}
              title={recording ? "Stop recording" : "Click to record (push-to-talk)"}
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

          {/* Settings row */}
          <div className="flex items-center justify-between gap-3 mt-2 pt-2 px-2 text-xs text-[var(--muted-foreground)]">
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={returnAudio}
                  onChange={(e) => setReturnAudio(e.target.checked)}
                  className="w-3.5 h-3.5 accent-[var(--primary)]"
                />
                Voice reply
              </label>
              <label className="flex items-center gap-1.5">
                Lang:
                <select
                  value={ttsLang}
                  onChange={(e) => setTtsLang(e.target.value as "en-IN" | "hi-IN")}
                  className="bg-transparent border border-[var(--border)] rounded px-1.5 py-0.5"
                >
                  <option value="en-IN">English</option>
                  <option value="hi-IN">हिन्दी</option>
                </select>
              </label>
            </div>
            <div className="hidden sm:block">Enter to send · Shift+Enter for newline</div>
          </div>
        </div>
      </main>

      <footer className="border-t border-[var(--border)] py-3 px-6 text-center text-xs text-[var(--muted-foreground)]">
        Sarvam-M · Sarvam Saarika STT · Sarvam Bulbul TTS · Voyage embeddings · Llama-3.3-70B grader · DeepSeek-V3 fallback brain. Advisory only — verify with the insurer before purchase.
      </footer>
    </div>
  );
}

function HealthBadge({ health }: { health: { status: string; missing: string[] } | null }) {
  if (!health) return <span className="text-xs text-[var(--muted-foreground)]">checking…</span>;
  const ok = health.status === "ok";
  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span
        className={`w-2 h-2 rounded-full ${
          ok ? "bg-emerald-500" : health.status === "unreachable" ? "bg-red-500" : "bg-amber-500"
        }`}
      />
      <span className="text-[var(--muted-foreground)]">
        {ok ? "all systems healthy" : health.status === "unreachable" ? "backend unreachable" : `degraded (${health.missing.join(", ")})`}
      </span>
    </div>
  );
}

function EmptyState({ onSuggest }: { onSuggest: (q: string) => void }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center text-center px-4">
      <div className="w-16 h-16 rounded-2xl bg-[var(--primary)] text-[var(--primary-foreground)] flex items-center justify-center text-2xl font-bold mb-6">
        IA
      </div>
      <h2 className="text-xl sm:text-2xl font-semibold mb-2">
        Hi, I&apos;m your AI insurance advisor.
      </h2>
      <p className="text-sm text-[var(--muted-foreground)] max-w-md mb-6">
        Ask me about Indian health insurance policies — coverage, waiting periods, exclusions, side-by-side comparisons. Speak or type, English or हिन्दी. Every fact comes with a citation.
      </p>
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
      <div
        className={`max-w-[85%] sm:max-w-[75%] rounded-2xl px-4 py-3 ${
          isUser
            ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
            : "bg-[var(--card)] border border-[var(--border)]"
        }`}
      >
        <div className="text-sm sm:text-base whitespace-pre-wrap leading-relaxed">{m.content}</div>
        {m.audioUrl && (
          <audio
            controls
            src={m.audioUrl}
            className="mt-2 w-full max-w-xs"
            style={{ height: 32 }}
          />
        )}
        {m.citations && m.citations.length > 0 && (
          <div className="mt-3 pt-3 border-t border-[var(--border)] space-y-1.5">
            <div className="text-[10px] uppercase tracking-wide text-[var(--muted-foreground)] font-semibold">
              Sources
            </div>
            {m.citations.slice(0, 5).map((c, i) => (
              <a
                key={i}
                href={c.source_url || "#"}
                target="_blank"
                rel="noopener"
                className="block text-xs text-[var(--muted-foreground)] hover:text-[var(--primary)] transition"
              >
                <span className="font-medium">{c.policy_name}</span>
                <span className="opacity-60"> · {c.insurer_slug} · p.{c.page_start}</span>
                <span className="opacity-50"> · score {c.score.toFixed(2)}</span>
              </a>
            ))}
          </div>
        )}
        {m.brain && (
          <div className="mt-2 text-[10px] text-[var(--muted-foreground)] opacity-60">
            {m.brain} · {m.latencyMs}ms
          </div>
        )}
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
            <span
              key={i}
              className="w-2 h-2 rounded-full bg-[var(--muted-foreground)] opacity-50"
              style={{
                animation: "fade-up 1.2s ease-in-out infinite",
                animationDelay: `${i * 0.2}s`,
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" y1="19" x2="12" y2="23" />
      <line x1="8" y1="23" x2="16" y2="23" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}
