"use client";

/**
 * PolicyPremiumWidget — per-policy slider-driven premium calculator.
 *
 * Embedded inside PolicyCompareModal (B1). Fetches an initial estimate from
 * /api/premium/estimate using the user's profile defaults, then re-fetches
 * (debounced 300ms) whenever the user moves the SI / tenure / deductible
 * sliders. When the backend reports `base_sample_used: false` (no curated
 * actuarial sample for this policy) the widget shows an "Estimate" badge so
 * the user understands the number is heuristic, not a quote.
 *
 * KI-bugfix (2026-05-15): switched from /api/premium/bulk → /api/premium/estimate
 * so per-policy pricing shares the curated-anchored math used by the standalone
 * PremiumCalculatorPanel. The bulk endpoint's flat ₹500/lakh fallback was
 * producing wildly low numbers (~₹6K) versus the panel's curated number
 * (~₹18-25K) for the same profile. Tenure + deductible are still honoured —
 * the estimate endpoint now applies the bulk multipliers post-anchor.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  postPremiumEstimate,
  type PremiumBulkProfile,
  type PreExistingCondition,
  type PremiumEstimateResponse,
} from "@/lib/api";

export type PolicyPremiumWidgetProps = {
  policyId: string;
  policyName: string;
  // Kept the bulk-profile shape because PolicyCompareModal still hands us this
  // exact object — it doubles as the predicted-premium-band profile. We map
  // its fields onto the estimate-endpoint contract internally.
  profile?: PremiumBulkProfile;
  initialSumInsured?: number;
  initialTenureYears?: 1 | 2 | 3;
  initialDeductibleInr?: 0 | 25000 | 50000 | 100000;
  onCalculated?: (premium: number) => void;
  // User's profile-level predicted premium band (the same number rendered in
  // the header chip / `getPredictedPremiumBand`). Surfaced as the indicative
  // reference when the backend reports `base_sample_used: false` (i.e. no
  // curated quote sample for this specific policy). Threaded down from
  // PolicyCompareModal so we don't refetch per-column.
  aggregateBand?: {
    min_inr: number;
    max_inr: number;
    median_inr: number;
    sample_size?: number;
    assumed?: boolean;
  } | null;
};

const SUM_INSURED_MIN = 500_000;
const SUM_INSURED_MAX = 10_000_000;
const SUM_INSURED_STEP = 500_000;
const TENURE_CHOICES = [1, 2, 3] as const;
const DEDUCTIBLE_CHOICES = [0, 25_000, 50_000, 100_000] as const;

function formatInr(value: number): string {
  // Indian-style 1,23,456 grouping.
  return value.toLocaleString("en-IN");
}

function formatSiLabel(inr: number): string {
  if (inr >= 10_000_000) return `${(inr / 10_000_000).toFixed(inr % 10_000_000 === 0 ? 0 : 1)}Cr`;
  return `${Math.round(inr / 100_000)}L`;
}

function formatDeductibleLabel(inr: number): string {
  if (inr === 0) return "₹0";
  if (inr >= 100_000) return `₹${(inr / 100_000).toFixed(0)}L`;
  return `₹${Math.round(inr / 1_000)}K`;
}

function summariseProfile(profile?: PremiumBulkProfile): string | null {
  if (!profile) return null;
  const parts: string[] = [];
  if (typeof profile.age === "number") parts.push(`age ${profile.age}`);
  if (typeof profile.family_size === "number" && profile.family_size > 1) {
    parts.push(`family of ${profile.family_size}`);
  } else if (profile.dependents) {
    parts.push(profile.dependents);
  }
  if (profile.location_tier) parts.push(String(profile.location_tier).toLowerCase());
  return parts.length ? parts.join(", ") : null;
}

/**
 * Map the free-text `location_tier` we ship in PremiumBulkProfile onto the
 * strict {metro|tier1|tier2} enum the /api/premium/estimate endpoint expects.
 * Same normalization rule used inside premium_calculator.bulk_estimate.
 */
function normaliseCityTier(tier: string | null | undefined): "metro" | "tier1" | "tier2" {
  if (!tier) return "metro";
  const t = String(tier).toLowerCase().replace(/[-_\s]/g, "");
  if (t === "metro") return "metro";
  if (t.includes("1")) return "tier1";
  if (t.includes("2")) return "tier2";
  // tier3 / unknown — fall back to tier2 (closest cheaper bucket the estimate
  // endpoint accepts; matches the standalone panel's default).
  return "tier2";
}

/**
 * Coerce the typed `pre_existing_conditions` profile slot onto the
 * estimate-endpoint's PreExistingCondition union, defaulting to "none".
 */
function normalisePed(ped: string | null | undefined): PreExistingCondition {
  if (!ped) return "none";
  const allowed: PreExistingCondition[] = [
    "none",
    "diabetes_or_hypertension",
    "heart_disease",
    "multiple",
  ];
  return (allowed as string[]).includes(ped) ? (ped as PreExistingCondition) : "none";
}

export default function PolicyPremiumWidget({
  policyId,
  policyName,
  profile,
  initialSumInsured = 1_000_000,
  initialTenureYears = 1,
  initialDeductibleInr = 0,
  onCalculated,
  aggregateBand,
}: PolicyPremiumWidgetProps) {
  const [sumInsured, setSumInsured] = useState<number>(initialSumInsured);
  const [tenureYears, setTenureYears] = useState<1 | 2 | 3>(initialTenureYears);
  const [deductibleInr, setDeductibleInr] = useState<0 | 25000 | 50000 | 100000>(
    initialDeductibleInr,
  );
  const [resp, setResp] = useState<PremiumEstimateResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Stable string key so we can put `profile` in the effect dep list without
  // triggering refetches on every parent re-render (object identity changes).
  const profileKey = useMemo(() => JSON.stringify(profile ?? {}), [profile]);

  const onCalculatedRef = useRef(onCalculated);
  useEffect(() => {
    onCalculatedRef.current = onCalculated;
  }, [onCalculated]);

  const fetchPremium = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        // Defaults match the standalone PremiumCalculatorPanel so the two
        // surfaces converge on the same number for the same profile.
        const age = typeof profile?.age === "number" ? profile.age : 35;
        const familySize =
          typeof profile?.family_size === "number" ? profile.family_size : 1;
        const r = await postPremiumEstimate({
          age,
          sum_insured_inr: sumInsured,
          city_tier: normaliseCityTier(profile?.location_tier),
          smoker: profile?.smoker === true,
          family_size: familySize,
          policy_id: policyId,
          pre_existing_conditions: normalisePed(profile?.pre_existing_conditions),
          copayment_pct: 0,
          tenure_years: tenureYears,
          deductible_inr: deductibleInr,
        });
        if (signal.aborted) return;
        setResp(r);
        onCalculatedRef.current?.(r.point_estimate_inr);
      } catch (e) {
        if (signal.aborted) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    [policyId, profileKey, sumInsured, tenureYears, deductibleInr], // eslint-disable-line react-hooks/exhaustive-deps
  );

  // 300ms debounce on slider drags; immediate fetch on mount / policy switch.
  useEffect(() => {
    const ctrl = new AbortController();
    const handle = window.setTimeout(() => {
      void fetchPremium(ctrl.signal);
    }, 300);
    return () => {
      window.clearTimeout(handle);
      ctrl.abort();
    };
  }, [fetchPremium]);

  const profileSummary = summariseProfile(profile);

  // Option B+: when the backend has no curated quote sample for this policy,
  // hide the slider widget entirely and surface the user's aggregate band
  // (same number rendered in the header chip) as the indicative reference.
  // We wait for the first response before deciding so we don't flash the
  // notice while loading. Loading + error states fall through to the slider
  // widget below (which renders its own loading/error UI).
  if (resp && resp.base_sample_used === false) {
    return (
      <NonCuratedPricingNotice
        policyName={policyName}
        aggregateBand={aggregateBand ?? null}
      />
    );
  }

  // Compact, profile-only breakdown — the estimate endpoint doesn't expose
  // a multiplicative chain so we surface the active overrides + methodology
  // instead. This keeps the widget transparent without faking precision.
  const bullets: string[] = [];
  if (resp) {
    bullets.push(
      `Range: ₹${formatInr(resp.low_inr)} – ₹${formatInr(resp.high_inr)}/year (±15% band)`,
    );
    if (resp.tenure_years && resp.tenure_years !== 1) {
      bullets.push(`Multi-year discount applied (${resp.tenure_years}-year policy)`);
    }
    if (resp.deductible_inr && resp.deductible_inr > 0) {
      bullets.push(
        `Voluntary deductible discount applied (${formatDeductibleLabel(resp.deductible_inr)})`,
      );
    }
  }

  return (
    <div className="policy-premium-widget" style={widgetStyle}>
      <header style={headerStyle}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{policyName}</div>
        {/* Curated-only branch: by the time we render this widget,
            base_sample_used is guaranteed not false (the !== false branch
            short-circuits to NonCuratedPricingNotice above). No "Estimate"
            badge needed here — the number is anchored to a real quote sample. */}
      </header>

      {profileSummary && (
        <div style={profileLineStyle}>
          Your profile defaults: {profileSummary}
        </div>
      )}

      <div style={sliderGroupStyle}>
        <label style={labelStyle}>
          <span style={labelHeadStyle}>
            Sum insured
            <strong>₹{formatSiLabel(sumInsured)}</strong>
          </span>
          <input
            type="range"
            min={SUM_INSURED_MIN}
            max={SUM_INSURED_MAX}
            step={SUM_INSURED_STEP}
            value={sumInsured}
            onChange={(e) => setSumInsured(Number(e.target.value))}
            aria-label="Sum insured"
            style={{ width: "100%" }}
          />
          <div style={tickRowStyle}>
            <span>₹5L</span>
            <span>₹1Cr</span>
          </div>
        </label>

        <label style={labelStyle}>
          <span style={labelHeadStyle}>
            Tenure
            <strong>{tenureYears} {tenureYears === 1 ? "year" : "years"}</strong>
          </span>
          <div role="radiogroup" aria-label="Tenure" style={pillRowStyle}>
            {TENURE_CHOICES.map((y) => (
              <button
                key={y}
                type="button"
                role="radio"
                aria-checked={tenureYears === y}
                onClick={() => setTenureYears(y)}
                style={pillStyle(tenureYears === y)}
              >
                {y}y
              </button>
            ))}
          </div>
        </label>

        <label style={labelStyle}>
          <span style={labelHeadStyle}>
            Deductible
            <strong>{formatDeductibleLabel(deductibleInr)}</strong>
          </span>
          <div role="radiogroup" aria-label="Deductible" style={pillRowStyle}>
            {DEDUCTIBLE_CHOICES.map((d) => (
              <button
                key={d}
                type="button"
                role="radio"
                aria-checked={deductibleInr === d}
                onClick={() => setDeductibleInr(d)}
                style={pillStyle(deductibleInr === d)}
              >
                {formatDeductibleLabel(d)}
              </button>
            ))}
          </div>
        </label>
      </div>

      <div style={resultBoxStyle} aria-live="polite">
        {error ? (
          <div style={{ color: "#b00020" }}>Failed: {error}</div>
        ) : loading && !resp ? (
          <div style={{ color: "#666" }}>Calculating estimate…</div>
        ) : resp ? (
          <>
            <div style={resultHeadlineStyle}>
              Estimated premium:&nbsp;
              <strong>₹{formatInr(resp.point_estimate_inr)}</strong>
              <span style={resultSuffixStyle}>/year</span>
              {loading && <span style={spinnerHintStyle}> updating…</span>}
            </div>
            {bullets.length > 0 && (
              <ul style={breakdownListStyle}>
                {bullets.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            )}
            {resp.methodology && (
              <div style={noteStyle}>{resp.methodology}</div>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* NonCuratedPricingNotice — rendered in place of the slider widget   */
/* when /api/premium/estimate reports base_sample_used: false. Shows  */
/* the user's aggregate predicted-premium band (threaded down from    */
/* PolicyCompareModal) as the indicative reference + a clear pricing- */
/* note explaining we don't have a policy-specific quote.             */
/* ------------------------------------------------------------------ */

function NonCuratedPricingNotice({
  policyName,
  aggregateBand,
}: {
  policyName: string;
  aggregateBand: {
    min_inr: number;
    max_inr: number;
    median_inr: number;
    sample_size?: number;
    assumed?: boolean;
  } | null;
}) {
  const hasBand = !!aggregateBand;
  return (
    <div style={noticeWidgetStyle} role="note" aria-label="Pricing note">
      <header style={noticeHeaderStyle}>
        <span style={noticeIconStyle} aria-hidden="true">
          {/* info icon — pure SVG so we don't pull a new dependency */}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="8" />
            <line x1="12" y1="12" x2="12" y2="16" />
          </svg>
        </span>
        <span style={noticeBadgeStyle}>Pricing note</span>
      </header>

      <p style={noticeBodyStyle}>
        We don&apos;t have a verified quote for <strong>{policyName}</strong> yet,
        so we can&apos;t show a plan-specific estimate. Based on your profile,
        plans in this category typically cost:
      </p>

      <div style={noticeBandBoxStyle}>
        {hasBand ? (
          <>
            <div style={noticeBandHeadlineStyle}>
              ₹{formatInr(aggregateBand!.min_inr)}–₹{formatInr(aggregateBand!.max_inr)}
              <span style={noticeBandSuffixStyle}>&nbsp;/ year</span>
            </div>
            <div style={noticeBandMedianStyle}>
              Median ₹{formatInr(aggregateBand!.median_inr)}/year
              {typeof aggregateBand!.sample_size === "number" &&
                aggregateBand!.sample_size > 0 && (
                  <> · across {aggregateBand!.sample_size} similar profiles</>
                )}
            </div>
          </>
        ) : (
          <div style={noticeBandFallbackStyle}>
            Profile-level band not available yet — complete your profile to see
            an indicative range.
          </div>
        )}
      </div>

      <p style={noticeFootnoteStyle}>
        This is an indicative range — the actual premium depends on the
        insurer&apos;s underwriting + your final disclosures. To get an exact
        quote, request one from the insurer directly or via
        PolicyBazaar / InsuranceDekho.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Inline styles — kept local so the widget drops into any modal      */
/* without a CSS-module dependency.                                   */
/* ------------------------------------------------------------------ */

const widgetStyle: React.CSSProperties = {
  border: "1px solid #e5e7eb",
  borderRadius: 12,
  padding: 16,
  background: "#fff",
  display: "flex",
  flexDirection: "column",
  gap: 12,
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const profileLineStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#666",
};

const sliderGroupStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 14,
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  fontSize: 12,
  color: "#374151",
};

const labelHeadStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
  fontWeight: 500,
};

const tickRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  fontSize: 11,
  color: "#9ca3af",
};

const pillRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 6,
  flexWrap: "wrap",
};

const pillStyle = (active: boolean): React.CSSProperties => ({
  border: `1px solid ${active ? "#2563eb" : "#d1d5db"}`,
  background: active ? "#2563eb" : "#fff",
  color: active ? "#fff" : "#374151",
  padding: "4px 10px",
  borderRadius: 999,
  fontSize: 12,
  cursor: "pointer",
});

const resultBoxStyle: React.CSSProperties = {
  borderTop: "1px solid #f1f5f9",
  paddingTop: 12,
  display: "flex",
  flexDirection: "column",
  gap: 8,
};

const resultHeadlineStyle: React.CSSProperties = {
  fontSize: 14,
};

const resultSuffixStyle: React.CSSProperties = {
  color: "#6b7280",
  fontWeight: 400,
};

const spinnerHintStyle: React.CSSProperties = {
  marginLeft: 8,
  fontSize: 11,
  color: "#9ca3af",
  fontStyle: "italic",
};

const breakdownListStyle: React.CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  color: "#4b5563",
  fontSize: 12,
  lineHeight: 1.5,
};

const noteStyle: React.CSSProperties = {
  fontSize: 11,
  color: "#6b7280",
  fontStyle: "italic",
};

/* ---------------- NonCuratedPricingNotice styles ------------------- */
/* Same outer card framing as the slider widget (border + radius + bg) */
/* but with an amber/blue informational accent so users immediately    */
/* distinguish "indicative range" from "calculated estimate".          */

const noticeWidgetStyle: React.CSSProperties = {
  border: "1px solid #bfdbfe", // soft blue
  borderLeft: "4px solid #2563eb", // strong blue accent strip
  borderRadius: 12,
  padding: 16,
  background: "#f8fbff",
  display: "flex",
  flexDirection: "column",
  gap: 10,
  fontFamily: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
};

const noticeHeaderStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const noticeIconStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 20,
  height: 20,
  borderRadius: 999,
  color: "#2563eb",
};

const noticeBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "#1d4ed8",
};

const noticeBodyStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 12.5,
  lineHeight: 1.55,
  color: "#374151",
};

const noticeBandBoxStyle: React.CSSProperties = {
  background: "#fff",
  border: "1px solid #dbeafe",
  borderRadius: 10,
  padding: "10px 12px",
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const noticeBandHeadlineStyle: React.CSSProperties = {
  fontSize: 16,
  fontWeight: 700,
  color: "#0f172a",
};

const noticeBandSuffixStyle: React.CSSProperties = {
  fontSize: 12,
  fontWeight: 400,
  color: "#6b7280",
};

const noticeBandMedianStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: "#475569",
};

const noticeBandFallbackStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#92400e",
  fontStyle: "italic",
};

const noticeFootnoteStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 11,
  lineHeight: 1.5,
  color: "#6b7280",
  fontStyle: "italic",
};
