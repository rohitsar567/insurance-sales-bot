// Typed client for the FastAPI backend.
// Backend URL is configurable via NEXT_PUBLIC_BACKEND_URL so we can point at
// localhost in dev and at Render in production.

// In production (HF Spaces deploy): empty -> same-origin requests, no CORS.
// In dev: set NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 in frontend/.env.local
export const BACKEND_URL =
  process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

export type Citation = {
  policy_id: string;
  policy_name: string;
  insurer_slug: string;
  page_start: number;
  page_end: number;
  source_url: string;
  score: number;
};

export type ChatResponse = {
  reply_text: string;
  citations: Citation[];
  brain_used: string;
  intent: string;
  language: string;
  latency_ms: number;
  session_id: string;
  audio_base64?: string | null;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export async function postChat(args: {
  user_text: string;
  session_id?: string;
  chat_history?: ChatMessage[];
  profile?: Record<string, unknown>;
  policy_filter_ids?: string[];
  return_audio?: boolean;
  tts_language_code?: string;
}): Promise<ChatResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      user_text: args.user_text,
      session_id: args.session_id,
      chat_history: args.chat_history ?? [],
      profile: args.profile ?? {},
      policy_filter_ids: args.policy_filter_ids,
      return_audio: args.return_audio ?? false,
      tts_language_code: args.tts_language_code ?? "en-IN",
    }),
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`chat failed: ${resp.status} ${t}`);
  }
  return resp.json();
}

export async function postTranscribe(
  blob: Blob,
  language_code?: string,
): Promise<{ text: string; language_code?: string; latency_ms: number }> {
  const fd = new FormData();
  // Use blob's mime to derive extension; default to wav
  const mime = blob.type || "audio/wav";
  const ext = mime.includes("webm")
    ? "webm"
    : mime.includes("mp3")
      ? "mp3"
      : mime.includes("ogg")
        ? "ogg"
        : "wav";
  fd.append("file", blob, `audio.${ext}`);
  if (language_code) fd.append("language_code", language_code);

  const resp = await fetch(`${BACKEND_URL}/api/transcribe`, {
    method: "POST",
    body: fd,
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`transcribe failed: ${resp.status} ${t}`);
  }
  return resp.json();
}

export async function getHealth(): Promise<{
  status: string;
  providers_ok: Record<string, boolean>;
  missing_keys: string[];
}> {
  const resp = await fetch(`${BACKEND_URL}/api/health`);
  if (!resp.ok) throw new Error(`health failed: ${resp.status}`);
  return resp.json();
}

// Decode a base64 string to a playable audio Blob URL.
export function audioBlobURLFromBase64(b64: string, mime = "audio/wav"): string {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: mime });
  return URL.createObjectURL(blob);
}
