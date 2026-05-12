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
  faithfulness_passed?: boolean;
  faithfulness_reasons?: string[];
  blocked?: boolean;
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

export type PolicyEntry = {
  name: string;
  source_url: string;
};

export type CoverageInsurer = {
  slug: string;
  name: string;
  home_url: string;
  policy_count: number;
  sample_policies: PolicyEntry[];
};

export type CoverageResponse = {
  total_chunks: number;
  total_policies: number;
  total_insurers: number;
  insurers: CoverageInsurer[];
};

export async function getCoverage(): Promise<CoverageResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/coverage`);
  if (!resp.ok) throw new Error(`coverage failed: ${resp.status}`);
  return resp.json();
}

export type UploadResponse = {
  policy_id: string;
  policy_name: string;
  chunks_added: number;
  pages_indexed: number;
  elapsed_ms: number;
};

export type ScorecardSubScore = {
  name: string;
  score: number;
  summary: string;
  signals: string[];
};

export type ScorecardResponse = {
  policy_id: string;
  policy_name: string;
  insurer_slug: string;
  overall_score: number;
  grade: string;
  one_liner: string;
  sub_scores: ScorecardSubScore[];
  data_completeness_pct: number;
  methodology_link: string;
};

export async function getScorecard(policy_id: string): Promise<ScorecardResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/policies/${encodeURIComponent(policy_id)}/scorecard`);
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`scorecard failed: ${resp.status} ${t}`);
  }
  return resp.json();
}

export type PremiumEstimateRequest = {
  age: number;
  sum_insured_inr: number;
  city_tier?: "metro" | "tier1" | "tier2";
  smoker?: boolean;
  family_size?: number;
  policy_id?: string | null;
};

export type PremiumEstimateResponse = {
  policy_id: string;
  point_estimate_inr: number;
  low_inr: number;
  high_inr: number;
  methodology: string;
  sources: string[];
  is_illustrative: boolean;
  disclaimer: string;
};

export async function postPremiumEstimate(req: PremiumEstimateRequest): Promise<PremiumEstimateResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/premium/estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...req,
      city_tier: req.city_tier ?? "metro",
      smoker: req.smoker ?? false,
      family_size: req.family_size ?? 1,
    }),
  });
  if (!resp.ok) throw new Error(`premium estimate failed: ${resp.status}`);
  return resp.json();
}


export async function uploadPolicy(file: File): Promise<UploadResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const resp = await fetch(`${BACKEND_URL}/api/upload-policy`, {
    method: "POST",
    body: fd,
  });
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`upload failed: ${resp.status} ${t}`);
  }
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
