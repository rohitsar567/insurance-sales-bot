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
  // V3 #4 — backend MAY echo the actual mime it produced (e.g. "audio/mp4")
  // when the client requested a codec other than the wav default. The
  // frontend uses this when constructing the playback Blob URL so Safari
  // doesn't refuse to play an mp4 payload labelled as wav.
  audio_mime?: string | null;
  faithfulness_passed?: boolean;
  faithfulness_reasons?: string[];
  blocked?: boolean;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type ViewContext = {
  // Which top-level panel the user is currently focused on. The chat treats
  // this as the "screen" the copilot can see — answers can reference what the
  // user is looking at without them having to re-state it.
  active_view: "chat" | "marketplace" | "profile" | "premium" | "policy_detail";
  // Policy ID currently open in a detail modal, if any.
  active_policy_id?: string;
  // Optional marketplace filters (forwarded for personalization signals).
  filters?: Record<string, unknown>;
};

/** HF Space's first request after ~15min idle takes ~50s for cold-start.
 *  Add retry-with-backoff so a single "Load failed" doesn't surface as an
 *  error in the chat — instead we wait and try again silently. AbortError
 *  is NOT retried (it's intentional cancellation from Live mode's barge-in).
 */
async function _fetchWithRetry(
  url: string,
  init: RequestInit,
  signal: AbortSignal | undefined,
  onRetry?: (attempt: number) => void,
): Promise<Response> {
  const retryDelaysMs = [1500, 3500, 7000];
  let lastErr: unknown = null;
  for (let attempt = 0; attempt <= retryDelaysMs.length; attempt++) {
    if (signal?.aborted) throw new DOMException("aborted", "AbortError");
    try {
      const resp = await fetch(url, { ...init, signal });
      if (resp.status >= 500 && attempt < retryDelaysMs.length) {
        // 502/503 commonly means HF Space cold-start; retry
        await new Promise((r) => setTimeout(r, retryDelaysMs[attempt]));
        onRetry?.(attempt + 1);
        continue;
      }
      return resp;
    } catch (e) {
      const name = (e as { name?: string })?.name;
      if (name === "AbortError") throw e;
      lastErr = e;
      if (attempt < retryDelaysMs.length) {
        await new Promise((r) => setTimeout(r, retryDelaysMs[attempt]));
        onRetry?.(attempt + 1);
        continue;
      }
    }
  }
  throw lastErr ?? new Error("network failed after retries");
}

export async function postChat(args: {
  user_text: string;
  session_id?: string;
  chat_history?: ChatMessage[];
  profile?: Record<string, unknown>;
  policy_filter_ids?: string[];
  return_audio?: boolean;
  tts_language_code?: string;
  view_context?: ViewContext;
  // V3 #4 — Safari has no webm/opus support. Caller passes its preferred
  // codec ("audio/webm; codecs=opus" or "audio/mp4") and the backend SHOULD
  // honour it on the TTS payload. Sent as a header (`X-Preferred-Codec`)
  // AND included in the body for backends that ignore custom headers.
  preferred_codec?: string;
  signal?: AbortSignal;
  onRetry?: (attempt: number) => void;
}): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (args.preferred_codec) headers["X-Preferred-Codec"] = args.preferred_codec;
  const resp = await _fetchWithRetry(
    `${BACKEND_URL}/api/chat`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({
        user_text: args.user_text,
        session_id: args.session_id,
        chat_history: args.chat_history ?? [],
        profile: args.profile ?? {},
        policy_filter_ids: args.policy_filter_ids,
        return_audio: args.return_audio ?? false,
        tts_language_code: args.tts_language_code ?? "en-IN",
        view_context: args.view_context,
        preferred_codec: args.preferred_codec,
      }),
    },
    args.signal,
    args.onRetry,
  );
  if (!resp.ok) {
    const t = await resp.text();
    throw new Error(`chat failed: ${resp.status} ${t}`);
  }
  return resp.json();
}

export async function postTranscribe(
  blob: Blob,
  language_code?: string,
  signal?: AbortSignal,
): Promise<{ text: string; language_code?: string; latency_ms: number }> {
  const fd = new FormData();
  // Use blob's mime to derive extension; default to wav
  const mime = blob.type || "audio/wav";
  // KI-134 (2026-05-15) — iOS Safari MediaRecorder produces audio/mp4 (no
  // webm support). Without explicit mapping the file was sent as audio.wav
  // with mp4 bytes inside, breaking the backend's mime/ext whitelist.
  const ext = mime.includes("webm")
    ? "webm"
    : mime.includes("mp4") || mime.includes("m4a")
      ? "m4a"
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
    signal,
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

export type PreExistingCondition =
  | "none"
  | "diabetes_or_hypertension"
  | "heart_disease"
  | "multiple";

export type PremiumEstimateRequest = {
  age: number;
  sum_insured_inr: number;
  city_tier?: "metro" | "tier1" | "tier2";
  smoker?: boolean;
  family_size?: number;
  policy_id?: string | null;
  pre_existing_conditions?: PreExistingCondition;
  copayment_pct?: number;
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

export type ComparePolicyEntry = {
  policy_id: string;
  policy_name: string;
  insurer_slug: string;
  fields: Record<string, unknown>;
  scorecard?: ScorecardResponse;
};

export type CompareResponse = {
  policies: ComparePolicyEntry[];
  field_order: string[];
};

export type MarketplacePolicy = {
  policy_id: string;
  policy_name: string;
  aliases?: string[];
  insurer_slug: string;
  insurer_name: string;
  insurer_home_url: string;
  source_pdf_url: string;
  grade: string;
  overall_score: number;
  one_liner: string;
  data_completeness_pct: number;
  min_entry_age?: number | null;
  max_entry_age?: number | null;
  max_renewal_age?: number | null;
  sum_insured_options: number[];
  pre_existing_disease_waiting_months?: number | null;
  initial_waiting_period_days?: number | null;
  maternity_waiting_months?: number | null;
  copayment_pct?: number | null;
  network_hospital_count?: number | null;
  no_claim_bonus_pct?: number | null;
  ayush_coverage?: boolean | null;
  maternity_coverage?: boolean | null;
  cashless_treatment_supported?: boolean | null;
  room_rent_capping?: string | null;
};

export type MarketplaceResponse = {
  policies: MarketplacePolicy[];
  total: number;
  insurers_indexed: number;
};

export type InsurerReviews = {
  insurer_slug: string;
  insurer_name: string;
  aggregate_score: { value_0_100?: number; letter_grade?: string; headline?: string };
  claim_metrics: {
    claim_settlement_ratio_pct?: number;
    claim_settlement_ratio_year?: string;
    complaints_per_10k_policies?: number;
    complaints_year?: string;
    source_irdai_url?: string;
  };
  aggregator_ratings: Record<string, { avg_star?: number; review_count?: number; url?: string }>;
  reddit_sentiment: { sentiment_overall?: string; notable_themes?: string[] };
  youtube_coverage: { overall_youtube_sentiment?: string; top_creators_who_reviewed?: Array<{ creator?: string; video_url?: string; verdict?: string }> };
  in_news?: Array<{ headline?: string; url?: string; publication?: string; date?: string; tone?: string }>;
};

export async function getInsurerReviews(slug: string): Promise<InsurerReviews> {
  const resp = await fetch(`${BACKEND_URL}/api/insurers/${slug}/reviews`);
  if (!resp.ok) throw new Error(`reviews failed: ${resp.status}`);
  return resp.json();
}

export async function getMarketplace(session_id?: string): Promise<MarketplaceResponse> {
  // When session_id is passed AND its profile is complete enough, the backend
  // re-scores every policy with the user's profile — cards reveal personalised
  // grades. Without session_id, grades use the generic baseline.
  const qs = session_id ? `?session_id=${encodeURIComponent(session_id)}` : "";
  const resp = await fetch(`${BACKEND_URL}/api/policies/all${qs}`);
  if (!resp.ok) throw new Error(`marketplace failed: ${resp.status}`);
  return resp.json();
}

export type UserProfile = {
  name?: string | null;  // KI-077 — captured from chat or entered in profile panel
  age?: number | null;
  dependents?: string | null;
  income_band?: string | null;
  existing_cover_inr?: number | null;
  primary_goal?: string | null;
  location_tier?: string | null;
  parents_to_insure?: boolean | null;
  parents_age_max?: number | null;
  parents_has_ped?: boolean | null;
  health_conditions?: string[] | null;
  budget_band?: string | null;
};

export type ProfileCompletenessResponse = {
  completeness: number;
  completeness_pct: number;
  fields_collected: string[];
  fields_missing: string[];
  is_personalized: boolean;
  gate_threshold: number;
  next_question_hint?: string | null;
  profile?: UserProfile;
  session_id?: string | null;
};

export async function getProfileCompleteness(session_id?: string): Promise<ProfileCompletenessResponse> {
  const qs = session_id ? `?session_id=${encodeURIComponent(session_id)}` : "";
  const resp = await fetch(`${BACKEND_URL}/api/profile/completeness${qs}`);
  if (!resp.ok) throw new Error(`profile completeness failed: ${resp.status}`);
  return resp.json();
}

export async function postProfileUpdate(req: UserProfile & { session_id: string }): Promise<ProfileCompletenessResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/profile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!resp.ok) throw new Error(`profile update failed: ${resp.status}`);
  return resp.json();
}

export async function getCompare(policy_ids: string[]): Promise<CompareResponse> {
  // Build query string manually — URL constructor requires an absolute URL,
  // but in production BACKEND_URL is "" (same-origin) which makes the path
  // relative. Constructing `new URL("/api/...")` throws "Invalid URL".
  const params = policy_ids.map((id) => `policy_ids=${encodeURIComponent(id)}`).join("&");
  const resp = await fetch(`${BACKEND_URL}/api/policies/compare?${params}`);
  if (!resp.ok) throw new Error(`compare failed: ${resp.status}`);
  return resp.json();
}

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


// KI-020 — User-facing chat clear / session restart.
export interface SessionResetResponse {
  ok: boolean;
  session_id?: string | null;  // new session_id returned when drop_profile=true
  cleared_state: boolean;
}

export async function postSessionReset(
  args: { session_id: string; drop_profile?: boolean }
): Promise<SessionResetResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/session/reset`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: args.session_id,
      drop_profile: args.drop_profile ?? false,
    }),
  });
  if (!resp.ok) throw new Error(`session reset failed: ${resp.status}`);
  return resp.json();
}


// KI-196 (ADR-041) — Clean Clear-chat semantic. Wipes in-memory session
// state for the supplied session_id and ALWAYS returns a fresh UUID the
// caller must adopt going forward. The on-disk profile JSON is preserved.
export interface SessionClearResponse {
  cleared: boolean;
  new_session_id: string;
}

export async function postSessionClear(
  args: { session_id: string }
): Promise<SessionClearResponse> {
  const resp = await fetch(`${BACKEND_URL}/api/session/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: args.session_id }),
  });
  if (!resp.ok) throw new Error(`session clear failed: ${resp.status}`);
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
