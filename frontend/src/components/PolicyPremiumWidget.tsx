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
 *
 * ── Visual system ─────────────────────────────────────────────────────
 * Re-grounded on the premium editorial-fintech landing (app/globals.css):
 * Fraunces display serif for the headline premium numeral, Plus Jakarta for
 * UI chrome, the teal --primary token for the active pills + slider track,
 * color-mix soft depth, and tight one-line fact bullets. All chrome reads
 * from CSS variables so the widget tracks the page's light/dark scheme.
 * Reduced-motion is honoured via a local media query.
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

// Display serif + sans UI face, pulled from the landing's CSS vars so the
// widget shares the exact type system as the rest of the app.
const SERIF = "var(--font-serif)";
const SANS = "var(--font-sans)";

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
      `Range ₹${formatInr(resp.low_inr)} – ₹${formatInr(resp.high_inr)}/year (±15% band)`,
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
      {/* Local accent-color + reduced-motion scoping for the native range
          input. Scoped to .policy-premium-widget so it never leaks. */}
      <style>{PREMIUM_WIDGET_CSS}</style>

      <header style={headerStyle}>
        <div
          style={{
            fontWeight: 600,
            fontSize: 13.5,
            color: "var(--foreground)",
            lineHeight: 1.35,
            letterSpacing: "-0.005em",
          }}
        >
          {policyName}
        </div>
        {/* Curated-only branch: by the time we render this widget,
            base_sample_used is guaranteed not false (the !== false branch
            short-circuits to NonCuratedPricingNotice above). No "Estimate"
            badge needed here — the number is anchored to a real quote sample. */}
      </header>

      {profileSummary && (
        <div style={profileLineStyle}>
          Your profile defaults · {profileSummary}
        </div>
      )}

      <div style={sliderGroupStyle}>
        <label style={labelStyle}>
          <span style={labelHeadStyle}>
            <span style={labelTextStyle}>Sum insured</span>
            <strong style={labelValueStyle}>₹{formatSiLabel(sumInsured)}</strong>
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
            <span style={labelTextStyle}>Tenure</span>
            <strong style={labelValueStyle}>
              {tenureYears} {tenureYears === 1 ? "year" : "years"}
            </strong>
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
            <span style={labelTextStyle}>Deductible</span>
            <strong style={labelValueStyle}>
              {formatDeductibleLabel(deductibleInr)}
            </strong>
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
          <div style={errorTextStyle}>Failed: {error}</div>
        ) : loading && !resp ? (
          <div style={calculatingStyle}>
            <span aria-hidden style={dotPulseStyle} />
            Calculating estimate…
          </div>
        ) : resp ? (
          <>
            <div style={resultHeadlineRowStyle}>
              <span style={resultLabelStyle}>Estimated premium</span>
              <span style={resultValueWrapStyle}>
                <strong style={resultValueStyle}>
                  ₹{formatInr(resp.point_estimate_inr)}
                </strong>
                <span style={resultSuffixStyle}>/year</span>
                {loading && (
                  <span style={spinnerHintStyle}>updating…</span>
                )}
              </span>
            </div>
            {bullets.length > 0 && (
              <ul style={breakdownListStyle}>
                {bullets.map((b) => (
                  <li key={b} style={breakdownItemStyle}>
                    <span aria-hidden style={breakdownTickStyle} />
                    <span>{b}</span>
                  </li>
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
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
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
/* without a CSS-module dependency. All colors read from the landing's */
/* CSS variables (--primary teal, --card, --border, --muted, etc.) so  */
/* the widget belongs to the premium editorial-fintech design system   */
/* and tracks the page's light/dark scheme automatically.              */
/* ------------------------------------------------------------------ */

// Scoped native-range-input theming + reduced-motion guard. Kept as a tiny
// string so the file stays CSS-module-free and self-contained.
const PREMIUM_WIDGET_CSS = `
.policy-premium-widget input[type="range"]{
  -webkit-appearance:none;appearance:none;height:6px;border-radius:999px;
  background:linear-gradient(90deg,
    color-mix(in srgb, var(--primary) 55%, var(--border)) 0%,
    var(--muted) 100%);
  outline:none;cursor:pointer;margin:2px 0;
}
.policy-premium-widget input[type="range"]:focus-visible{
  outline:2px solid var(--primary);outline-offset:3px;
}
.policy-premium-widget input[type="range"]::-webkit-slider-thumb{
  -webkit-appearance:none;appearance:none;width:17px;height:17px;border-radius:999px;
  background:var(--card);border:2px solid var(--primary);cursor:pointer;
  box-shadow:0 1px 3px color-mix(in srgb, var(--foreground) 22%, transparent);
  transition:transform .15s ease;
}
.policy-premium-widget input[type="range"]::-webkit-slider-thumb:hover{transform:scale(1.12);}
.policy-premium-widget input[type="range"]::-moz-range-thumb{
  width:17px;height:17px;border-radius:999px;background:var(--card);
  border:2px solid var(--primary);cursor:pointer;
  box-shadow:0 1px 3px color-mix(in srgb, var(--foreground) 22%, transparent);
}
@keyframes ppw-dot{0%,100%{opacity:.35}50%{opacity:1}}
@media (prefers-reduced-motion: reduce){
  .policy-premium-widget input[type="range"]::-webkit-slider-thumb{transition:none!important}
  .policy-premium-widget *{animation:none!important}
}
`;

const widgetStyle: React.CSSProperties = {
  border: "1px solid var(--border)",
  borderRadius: 18,
  padding: 18,
  background: "var(--card)",
  display: "flex",
  flexDirection: "column",
  gap: 14,
  fontFamily: SANS,
  boxShadow:
    "0 1px 2px color-mix(in srgb, var(--foreground) 4%, transparent), 0 16px 40px -32px color-mix(in srgb, var(--foreground) 28%, transparent)",
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const profileLineStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: "var(--muted-foreground)",
  letterSpacing: "0.005em",
};

const sliderGroupStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 16,
};

const labelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  fontSize: 12,
  color: "var(--foreground)",
};

const labelHeadStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  justifyContent: "space-between",
  gap: 10,
};

const labelTextStyle: React.CSSProperties = {
  fontWeight: 500,
  color: "var(--muted-foreground)",
  textTransform: "uppercase",
  letterSpacing: "0.07em",
  fontSize: 10.5,
};

const labelValueStyle: React.CSSProperties = {
  fontWeight: 700,
  color: "var(--foreground)",
  fontSize: 13,
  fontVariantNumeric: "tabular-nums",
};

const tickRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  fontSize: 10.5,
  color: "var(--muted-foreground)",
  fontVariantNumeric: "tabular-nums",
};

const pillRowStyle: React.CSSProperties = {
  display: "flex",
  gap: 7,
  flexWrap: "wrap",
};

const pillStyle = (active: boolean): React.CSSProperties => ({
  border: active
    ? "1px solid var(--primary)"
    : "1px solid var(--border)",
  background: active
    ? "var(--primary)"
    : "var(--card)",
  color: active ? "var(--primary-foreground)" : "var(--muted-foreground)",
  padding: "5px 13px",
  borderRadius: 999,
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
  fontVariantNumeric: "tabular-nums",
  transition: "background .16s ease, color .16s ease, border-color .16s ease",
  boxShadow: active
    ? "0 2px 8px -2px color-mix(in srgb, var(--primary) 45%, transparent)"
    : "none",
});

const resultBoxStyle: React.CSSProperties = {
  borderTop: "1px solid var(--border)",
  paddingTop: 14,
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const resultHeadlineRowStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
};

const resultLabelStyle: React.CSSProperties = {
  fontSize: 10.5,
  textTransform: "uppercase",
  letterSpacing: "0.1em",
  fontWeight: 700,
  color: "color-mix(in srgb, var(--primary) 70%, var(--muted-foreground))",
};

const resultValueWrapStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "baseline",
  gap: 6,
  flexWrap: "wrap",
};

const resultValueStyle: React.CSSProperties = {
  fontFamily: SERIF,
  fontOpticalSizing: "auto",
  fontSize: 28,
  fontWeight: 600,
  color: "var(--foreground)",
  letterSpacing: "-0.02em",
  fontVariantNumeric: "tabular-nums",
};

const resultSuffixStyle: React.CSSProperties = {
  color: "var(--muted-foreground)",
  fontWeight: 500,
  fontSize: 12.5,
};

const spinnerHintStyle: React.CSSProperties = {
  marginLeft: 4,
  fontSize: 11,
  color: "var(--muted-foreground)",
  fontStyle: "italic",
};

const errorTextStyle: React.CSSProperties = {
  color: "color-mix(in srgb, var(--error) 78%, var(--foreground))",
  fontSize: 12.5,
  fontWeight: 500,
};

const calculatingStyle: React.CSSProperties = {
  color: "var(--muted-foreground)",
  fontSize: 12.5,
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const dotPulseStyle: React.CSSProperties = {
  width: 7,
  height: 7,
  borderRadius: 999,
  background: "var(--primary)",
  animation: "ppw-dot 1.2s ease-in-out infinite",
};

const breakdownListStyle: React.CSSProperties = {
  margin: 0,
  padding: 0,
  listStyle: "none",
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const breakdownItemStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 8,
  color: "var(--muted-foreground)",
  fontSize: 12,
  lineHeight: 1.45,
};

const breakdownTickStyle: React.CSSProperties = {
  flex: "none",
  marginTop: 6,
  width: 5,
  height: 5,
  borderRadius: 999,
  background: "color-mix(in srgb, var(--primary) 70%, transparent)",
};

const noteStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--muted-foreground)",
  fontStyle: "italic",
  lineHeight: 1.5,
  paddingTop: 2,
};

/* ---------------- NonCuratedPricingNotice styles ------------------- */
/* Same outer card framing as the slider widget (border + radius + bg) */
/* but with a brand-teal informational accent rail so users instantly  */
/* distinguish "indicative range" from a "calculated estimate".        */

const noticeWidgetStyle: React.CSSProperties = {
  border: "1px solid color-mix(in srgb, var(--primary) 22%, var(--border))",
  borderLeft: "3px solid var(--primary)",
  borderRadius: 18,
  padding: 18,
  background: "color-mix(in srgb, var(--primary) 4%, var(--card))",
  display: "flex",
  flexDirection: "column",
  gap: 11,
  fontFamily: SANS,
  boxShadow:
    "0 1px 2px color-mix(in srgb, var(--foreground) 4%, transparent), 0 16px 40px -32px color-mix(in srgb, var(--foreground) 28%, transparent)",
};

const noticeHeaderStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const noticeIconStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 22,
  height: 22,
  borderRadius: 999,
  color: "var(--primary)",
  background: "color-mix(in srgb, var(--primary) 12%, var(--card))",
  border: "1px solid color-mix(in srgb, var(--primary) 22%, var(--border))",
};

const noticeBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: "0.12em",
  textTransform: "uppercase",
  color: "color-mix(in srgb, var(--primary) 78%, var(--foreground))",
};

const noticeBodyStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 12.5,
  lineHeight: 1.55,
  color: "var(--foreground)",
};

const noticeBandBoxStyle: React.CSSProperties = {
  background: "var(--card)",
  border: "1px solid color-mix(in srgb, var(--primary) 16%, var(--border))",
  borderRadius: 12,
  padding: "12px 14px",
  display: "flex",
  flexDirection: "column",
  gap: 3,
};

const noticeBandHeadlineStyle: React.CSSProperties = {
  fontFamily: SERIF,
  fontOpticalSizing: "auto",
  fontSize: 21,
  fontWeight: 600,
  color: "var(--foreground)",
  letterSpacing: "-0.02em",
  fontVariantNumeric: "tabular-nums",
};

const noticeBandSuffixStyle: React.CSSProperties = {
  fontFamily: SANS,
  fontSize: 12,
  fontWeight: 500,
  color: "var(--muted-foreground)",
};

const noticeBandMedianStyle: React.CSSProperties = {
  fontSize: 11.5,
  color: "var(--muted-foreground)",
  fontVariantNumeric: "tabular-nums",
};

const noticeBandFallbackStyle: React.CSSProperties = {
  fontSize: 12,
  color: "#855316",
  fontStyle: "italic",
  lineHeight: 1.5,
};

const noticeFootnoteStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 11,
  lineHeight: 1.5,
  color: "var(--muted-foreground)",
  fontStyle: "italic",
};
