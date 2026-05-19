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
 * KI-278 (2026-05-16) — header≠panel reconciliation. The widget now seeds
 * its SI slider via resolveProfileSumInsured(profile) — the byte-identical
 * client mirror of backend/premium_calculator.py::resolve_profile_sum_insured,
 * the SAME precedence the header "Premium range" chip
 * (estimate_premium_band) prices its basket at: desired_sum_insured_inr →
 * existing_cover_inr → ₹10L default. Because the chip band aggregates this
 * exact policy (one basket member) at the SAME profile-resolved SI, this
 * widget's number is guaranteed to fall inside the header band the user saw
 * — they can no longer contradict each other. `smoker` is forwarded to
 * /api/premium/estimate (`profile?.smoker === true`) and applied by the
 * backend estimate() loading chain; family-history is applied on the header
 * band path (it consumes the full SLOT_UNION profile).
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
  // When omitted, the widget resolves its starting SI from the profile with
  // the SAME precedence the header-chip backend uses
  // (resolveProfileSumInsured ↔ premium_calculator.resolve_profile_sum_insured)
  // so the per-policy number reconciles with the header band. Pass an explicit
  // value only to force a specific starting SI (overrides profile resolution).
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

/**
 * Client mirror of backend/premium_calculator.py::resolve_profile_sum_insured.
 *
 * KI-278 (2026-05-16) — header≠panel reconciliation. The header "Premium
 * range" chip is produced by estimate_premium_band(), which now prices the
 * basket at the profile-resolved SI (desired_sum_insured_inr →
 * existing_cover_inr → ₹10L default). The per-policy widget MUST seed its SI
 * slider with the SAME precedence, or it re-introduces the original bug
 * (chip priced at one SI, widget at another → contradictory numbers for one
 * profile). Precedence + clamp + ₹50k snap are byte-identical to the Python
 * resolver so the two surfaces agree by construction. The user can still
 * drag the slider to explore other SIs afterward.
 */
function resolveProfileSumInsured(
  profile: PremiumBulkProfile | undefined,
  fallbackDefault = 1_000_000,
): number {
  const coerce = (v: unknown): number | null => {
    if (v === null || v === undefined || v === "") return null;
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Math.trunc(n);
  };
  let si =
    coerce(profile?.desired_sum_insured_inr) ??
    coerce(profile?.existing_cover_inr) ??
    fallbackDefault;
  // Clamp to the slider domain, then snap to the nearest ₹50k slider stop.
  si = Math.max(SUM_INSURED_MIN, Math.min(SUM_INSURED_MAX, si));
  return Math.round(si / 50_000) * 50_000;
}

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
  initialSumInsured,
  initialTenureYears = 1,
  initialDeductibleInr = 0,
  onCalculated,
}: PolicyPremiumWidgetProps) {
  // KI-278 — seed the SI from the profile (same precedence as the header
  // chip) unless the caller forced an explicit initialSumInsured. This is
  // what makes the per-policy number land inside the header band.
  const resolvedInitialSI = useMemo(
    () =>
      typeof initialSumInsured === "number"
        ? initialSumInsured
        : resolveProfileSumInsured(profile),
    [initialSumInsured, profile],
  );
  const [sumInsured, setSumInsured] = useState<number>(resolvedInitialSI);
  const [tenureYears, setTenureYears] = useState<1 | 2 | 3>(initialTenureYears);
  const [deductibleInr, setDeductibleInr] = useState<0 | 25000 | 50000 | 100000>(
    initialDeductibleInr,
  );
  const [resp, setResp] = useState<PremiumEstimateResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // BUG #29 — only the ~2 of 148 policies that genuinely offer a
  // user-selectable voluntary deductible expose the selector. The backend
  // is authoritative; default to "unsupported" until the estimate arrives.
  // Declared here (before fetchPremium / the JSX) so both the request
  // builder and the render read the same value without a TDZ hazard.
  // Stale-selection reset is handled in the fetch callback (an async event
  // handler — the React-recommended place to react to a response), not an
  // effect, so we don't trigger cascading-render lint.
  const supportsDeductible = resp?.supports_voluntary_deductible === true;
  const deductibleChoices: readonly number[] =
    resp?.allowed_deductibles && resp.allowed_deductibles.length
      ? resp.allowed_deductibles
      : DEDUCTIBLE_CHOICES;

  // Stable string key so we can put `profile` in the effect dep list without
  // triggering refetches on every parent re-render (object identity changes).
  const profileKey = useMemo(() => JSON.stringify(profile ?? {}), [profile]);

  // KI-278 — async-profile re-sync. The profile often arrives AFTER the
  // widget mounts (the compare modal opens the instant data finishes
  // fetching). Without this, the SI slider stays on the ₹10L fallback while
  // the header chip already re-priced at the user's stated SI → the exact
  // header≠panel contradiction we're fixing. We re-seed the SI from the
  // resolved profile value ONLY while the user hasn't touched the slider
  // (slider still equals the prior resolved value). Once the user drags it,
  // their choice is sticky and profile updates no longer override it. Mirror
  // of PremiumCalculatorPanel's snapshot-guard in page.tsx.
  const lastResolvedSIRef = useRef<number>(resolvedInitialSI);
  useEffect(() => {
    if (sumInsured === lastResolvedSIRef.current && resolvedInitialSI !== sumInsured) {
      setSumInsured(resolvedInitialSI);
    }
    lastResolvedSIRef.current = resolvedInitialSI;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resolvedInitialSI]);

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
          deductible_inr: supportsDeductible ? deductibleInr : 0,
        });
        if (signal.aborted) return;
        setResp(r);
        // BUG #29 — if this policy does NOT support a voluntary deductible
        // but a stale non-zero selection carried over from a previously
        // compared policy, clear it so it can't persist or be re-sent.
        if (r.supports_voluntary_deductible === false && deductibleInr !== 0) {
          setDeductibleInr(0);
        }
        onCalculatedRef.current?.(r.point_estimate_inr);
      } catch (e) {
        if (signal.aborted) return;
        console.error("Premium estimate failed:", e);
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

  // Every recommended policy renders the SAME per-policy estimate block
  // below (point + ±15% band + methodology). When the backend has no
  // curated quote sample, the `methodology` string itself states it is a
  // rules-based estimate — we no longer substitute the wide profile-level
  // basket band for some policies (that produced an identical, 4-5x-wide
  // "no verified quote" panel on every non-curated card).

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
        {/* The methodology line under the estimate states whether the
            number is anchored to a curated quote sample or a rules-based
            formula — so no separate badge is needed here. */}
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

        {supportsDeductible && (
          <label style={labelStyle}>
            <span style={labelHeadStyle}>
              <span style={labelTextStyle}>Deductible</span>
              <strong style={labelValueStyle}>
                {formatDeductibleLabel(deductibleInr)}
              </strong>
            </span>
            <div role="radiogroup" aria-label="Deductible" style={pillRowStyle}>
              {deductibleChoices.map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={deductibleInr === d}
                  onClick={() =>
                    setDeductibleInr(d as 0 | 25000 | 50000 | 100000)
                  }
                  style={pillStyle(deductibleInr === d)}
                >
                  {formatDeductibleLabel(d)}
                </button>
              ))}
            </div>
          </label>
        )}
      </div>

      <div style={resultBoxStyle} aria-live="polite">
        {error ? (
          <div style={errorTextStyle}>Couldn&apos;t calculate this estimate. Try again in a moment.</div>
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

/* (NonCuratedPricingNotice + its styles removed — every policy now
   renders the unified per-policy estimate block; the methodology line
   states when the number is rules-based vs curated-sample anchored.) */
