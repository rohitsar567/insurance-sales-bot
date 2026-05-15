"use client";

/**
 * PolicyPremiumWidget — per-policy slider-driven premium calculator.
 *
 * Embedded inside PolicyCompareModal (B1). Fetches an initial estimate from
 * /api/premium/bulk using the user's profile defaults, then re-fetches
 * (debounced 300ms) whenever the user moves the SI / tenure / deductible
 * sliders. When the backend marks the row `assumed: true` (no curated
 * actuarial data for this policy) the widget shows an "Estimate" badge so
 * the user understands the number is heuristic, not a quote.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  postPremiumBulk,
  type PremiumBulkProfile,
  type PremiumBulkRow,
} from "@/lib/api";

export type PolicyPremiumWidgetProps = {
  policyId: string;
  policyName: string;
  profile?: PremiumBulkProfile;
  initialSumInsured?: number;
  initialTenureYears?: 1 | 2 | 3;
  initialDeductibleInr?: 0 | 25000 | 50000 | 100000;
  onCalculated?: (premium: number) => void;
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

const BREAKDOWN_LABELS: Record<string, string> = {
  base_inr: "Base",
  age_loading_x: "Age loading",
  location_loading_x: "Location loading",
  family_loading_x: "Family loading",
  tenure_discount_x: "Tenure discount",
  deductible_discount_x: "Deductible discount",
};

function renderBreakdownBullets(breakdown: Record<string, number | string>): string[] {
  const bullets: string[] = [];
  const base = breakdown.base_inr;
  if (typeof base === "number") bullets.push(`Base: ₹${formatInr(base)}`);
  for (const key of [
    "age_loading_x",
    "location_loading_x",
    "family_loading_x",
    "tenure_discount_x",
    "deductible_discount_x",
  ]) {
    const v = breakdown[key];
    if (typeof v === "number" && Math.abs(v - 1.0) > 0.001) {
      const label = BREAKDOWN_LABELS[key];
      bullets.push(`${label}: ${v.toFixed(2)}×`);
    }
  }
  return bullets;
}

export default function PolicyPremiumWidget({
  policyId,
  policyName,
  profile,
  initialSumInsured = 1_000_000,
  initialTenureYears = 1,
  initialDeductibleInr = 0,
  onCalculated,
}: PolicyPremiumWidgetProps) {
  const [sumInsured, setSumInsured] = useState<number>(initialSumInsured);
  const [tenureYears, setTenureYears] = useState<number>(initialTenureYears);
  const [deductibleInr, setDeductibleInr] = useState<number>(initialDeductibleInr);
  const [row, setRow] = useState<PremiumBulkRow | null>(null);
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
        const resp = await postPremiumBulk({
          policy_ids: [policyId],
          profile: profile ?? {},
          overrides: {
            [policyId]: {
              sum_insured_inr: sumInsured,
              tenure_years: tenureYears,
              deductible_inr: deductibleInr,
            },
          },
        });
        if (signal.aborted) return;
        const r = resp.per_policy[policyId];
        if (!r) {
          setError("No estimate returned for this policy.");
          return;
        }
        setRow(r);
        onCalculatedRef.current?.(r.premium_inr_annual);
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
  const bullets = row ? renderBreakdownBullets(row.breakdown) : [];

  return (
    <div className="policy-premium-widget" style={widgetStyle}>
      <header style={headerStyle}>
        <div style={{ fontWeight: 600, fontSize: 14 }}>{policyName}</div>
        {row?.assumed && (
          <span style={badgeStyle} title="Heuristic — no exact actuarial data for this policy.">
            Estimate
          </span>
        )}
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
        ) : loading && !row ? (
          <div style={{ color: "#666" }}>Calculating estimate…</div>
        ) : row ? (
          <>
            <div style={resultHeadlineStyle}>
              Estimated premium:&nbsp;
              <strong>₹{formatInr(row.premium_inr_annual)}</strong>
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
            {row.notes && row.notes.length > 0 && (
              <div style={noteStyle}>{row.notes.join(" ")}</div>
            )}
          </>
        ) : null}
      </div>
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

const badgeStyle: React.CSSProperties = {
  fontSize: 11,
  fontWeight: 600,
  padding: "2px 8px",
  borderRadius: 999,
  background: "#fff8e1",
  color: "#8a6d00",
  border: "1px solid #f1d680",
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
