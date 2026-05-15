// PolicyScorecardWidget — profile-aware A/B+/etc grade card for a single
// policy. Mounted inside PolicyCompareModal once per policy.
//
// Why a per-policy widget (rather than a single multi-policy table):
//   - PolicyCompareModal renders 2-4 of these side-by-side in a flex row.
//   - Each card is self-contained: header grade + overall score, sub-score
//     bars, profile rationale bullets, data-completeness warning.
//   - The fetch is one bulk POST per widget mount (single-policy), but the
//     SAME endpoint can be batched by a parent that wants to issue one call
//     for all N policies — we expose `precomputed` for that case.
//
// Ranking is UNIQUE TO THE USER's PROFILE. The backend's
// _profile_tuned_weights() re-balances the 6 sub-score weights based on:
//   - diabetes / BP / hyper  -> heavier waiting-period + claim-experience
//   - age >= 50              -> heavier renewal-protection
//   - dependents = spouse    -> heavier coverage-breadth + cost-predictability
//   - existing_cover_inr=0   -> heavier cost-predictability (first-time buyer)
// So the same policy will literally score differently for different users.

"use client";

import { useEffect, useMemo, useState } from "react";
import {
  postScorecardBulk,
  type BulkScorecardEntry,
  type BulkScorecardProfile,
} from "@/lib/api";

export type PolicyScorecardWidgetProps = {
  policyId: string;
  policyName: string;
  profile?: BulkScorecardProfile;
  // When the parent has already fetched a bulk response, pass the entry
  // directly — avoids a second network call per widget.
  precomputed?: BulkScorecardEntry;
  // Optional callback so the parent can collect entries for analytics /
  // a ranking row above the cards.
  onLoaded?: (entry: BulkScorecardEntry) => void;
  className?: string;
};

// Sub-score keys we know about — controls render order. Anything else the
// backend returns gets appended after these in arrival order.
const SUBSCORE_ORDER: { key: string; label: string }[] = [
  { key: "coverage_breadth", label: "Coverage Breadth" },
  { key: "cost_predictability", label: "Cost Predictability" },
  { key: "waiting_period_friction", label: "Waiting Periods" },
  { key: "claim_experience", label: "Claim Experience" },
  { key: "renewal_protection", label: "Renewal Protection" },
  { key: "bonus_and_loyalty", label: "Bonuses" },
];

function gradeColor(grade: string): { fg: string; bg: string; ring: string } {
  // Same palette family for every variant of a letter so a B+ looks like a B-
  // but slightly stronger.
  const head = grade.charAt(0).toUpperCase();
  switch (head) {
    case "A":
      return { fg: "#0f5132", bg: "#d1fadf", ring: "#16a34a" };
    case "B":
      return { fg: "#0c4a6e", bg: "#dbeafe", ring: "#2563eb" };
    case "C":
      return { fg: "#854d0e", bg: "#fef3c7", ring: "#d97706" };
    case "D":
      return { fg: "#7c2d12", bg: "#fed7aa", ring: "#ea580c" };
    case "F":
      return { fg: "#7f1d1d", bg: "#fecaca", ring: "#dc2626" };
    default:
      return { fg: "#374151", bg: "#f3f4f6", ring: "#9ca3af" };
  }
}

function barColor(score: number): string {
  if (score >= 80) return "#16a34a"; // green
  if (score >= 65) return "#2563eb"; // blue
  if (score >= 50) return "#d97706"; // amber
  if (score >= 35) return "#ea580c"; // orange
  return "#dc2626";                   // red
}

function rationaleTone(bullet: string): "pos" | "neg" | "neutral" {
  const lower = bullet.toLowerCase();
  if (lower.startsWith("strong fit") || lower.startsWith("strongest")) return "pos";
  if (lower.startsWith("weak fit") || lower.startsWith("watch out")) return "neg";
  return "neutral";
}

export default function PolicyScorecardWidget({
  policyId,
  policyName,
  profile,
  precomputed,
  onLoaded,
  className,
}: PolicyScorecardWidgetProps) {
  const [entry, setEntry] = useState<BulkScorecardEntry | null>(precomputed ?? null);
  const [loading, setLoading] = useState(!precomputed);
  const [error, setError] = useState<string | null>(null);

  // Stabilise profile dependency: callers usually rebuild the object each
  // render but the values rarely change. Stringify-key the effect so we don't
  // re-fetch on identity churn.
  const profileKey = useMemo(
    () => (profile ? JSON.stringify(profile) : ""),
    [profile],
  );

  useEffect(() => {
    if (precomputed) {
      setEntry(precomputed);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    postScorecardBulk({
      policy_ids: [policyId],
      profile: profile ?? undefined,
    })
      .then((resp) => {
        if (cancelled) return;
        const e = resp.per_policy?.[policyId];
        if (!e) {
          setError("No scorecard returned for this policy.");
          setEntry(null);
        } else {
          setEntry(e);
          onLoaded?.(e);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load scorecard.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // policyId + profileKey + precomputed identity are the real deps; onLoaded
    // is intentionally excluded to avoid re-fetch loops if the parent passes
    // an inline arrow.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [policyId, profileKey, precomputed]);

  if (loading) {
    return (
      <div
        className={className}
        style={{
          border: "1px solid #e5e7eb",
          borderRadius: 12,
          padding: 16,
          background: "#fff",
          minHeight: 220,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: "#6b7280",
          fontSize: 13,
        }}
        aria-busy="true"
        aria-live="polite"
      >
        Scoring {policyName}…
      </div>
    );
  }

  if (error || !entry) {
    return (
      <div
        className={className}
        style={{
          border: "1px solid #fecaca",
          borderRadius: 12,
          padding: 16,
          background: "#fef2f2",
          color: "#991b1b",
          fontSize: 13,
        }}
        role="alert"
      >
        Couldn’t score this policy: {error ?? "unknown error"}
      </div>
    );
  }

  const isNA = entry.overall_grade === "N/A";
  const colors = gradeColor(entry.overall_grade);
  const completeness = entry.data_completeness_pct;
  const showLimitedWarning = completeness < 50 && !isNA;

  // Render sub-scores in the canonical order first, then any extras.
  const knownKeys = new Set(SUBSCORE_ORDER.map((s) => s.key));
  const extras = Object.keys(entry.sub_scores).filter((k) => !knownKeys.has(k));
  const renderable = [
    ...SUBSCORE_ORDER.filter((s) => entry.sub_scores[s.key] !== undefined),
    ...extras.map((k) => ({ key: k, label: k.replace(/_/g, " ") })),
  ];

  return (
    <div
      className={className}
      style={{
        border: "1px solid #e5e7eb",
        borderRadius: 12,
        padding: 16,
        background: "#fff",
        display: "flex",
        flexDirection: "column",
        gap: 14,
      }}
      data-policy-id={policyId}
    >
      {/* Header: grade + overall score */}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div
          style={{
            width: 64,
            height: 64,
            borderRadius: 14,
            background: colors.bg,
            color: colors.fg,
            border: `2px solid ${colors.ring}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: entry.overall_grade.length > 1 ? 26 : 32,
            fontWeight: 800,
            letterSpacing: "-0.04em",
            flexShrink: 0,
          }}
          aria-label={`Grade ${entry.overall_grade}`}
        >
          {entry.overall_grade}
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "#111827",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
            title={entry.policy_name || policyName}
          >
            {entry.policy_name || policyName}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginTop: 2 }}>
            <span style={{ fontSize: 22, fontWeight: 700, color: "#111827" }}>
              {isNA ? "—" : `${entry.overall_score}`}
            </span>
            {!isNA && (
              <span style={{ fontSize: 12, color: "#6b7280" }}>/ 100</span>
            )}
            {profile && !isNA && (
              <span
                style={{
                  marginLeft: "auto",
                  fontSize: 10,
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  color: "#0c4a6e",
                  background: "#e0f2fe",
                  padding: "2px 6px",
                  borderRadius: 4,
                  fontWeight: 600,
                }}
                title="Score weights adjusted for your profile"
              >
                Personalised
              </span>
            )}
          </div>
          {entry.one_liner && (
            <div style={{ fontSize: 11, color: "#4b5563", marginTop: 2 }}>
              {entry.one_liner}
            </div>
          )}
        </div>
      </div>

      {/* Sub-scores */}
      {renderable.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {renderable.map(({ key, label }) => {
            const v = entry.sub_scores[key] ?? 0;
            return (
              <div key={key} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11 }}>
                  <span style={{ color: "#374151", textTransform: "capitalize" }}>
                    {label}
                  </span>
                  <span style={{ color: "#6b7280", fontVariantNumeric: "tabular-nums" }}>
                    {v}
                  </span>
                </div>
                <div
                  style={{
                    height: 6,
                    borderRadius: 999,
                    background: "#f3f4f6",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${Math.max(0, Math.min(100, v))}%`,
                      height: "100%",
                      background: barColor(v),
                      transition: "width 200ms ease-out",
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Profile rationale */}
      {entry.profile_rationale.length > 0 && (
        <div
          style={{
            background: "#f9fafb",
            border: "1px solid #f3f4f6",
            borderRadius: 8,
            padding: 10,
          }}
        >
          <div
            style={{
              fontSize: 10,
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              color: "#6b7280",
              fontWeight: 600,
              marginBottom: 6,
            }}
          >
            Why this score for you
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 4 }}>
            {entry.profile_rationale.map((b, i) => {
              const tone = rationaleTone(b);
              const color =
                tone === "pos" ? "#0f5132" : tone === "neg" ? "#991b1b" : "#374151";
              return (
                <li key={i} style={{ fontSize: 12, color, lineHeight: 1.4 }}>
                  {b}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {/* Limited-data warning */}
      {showLimitedWarning && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 11,
            color: "#854d0e",
            background: "#fef3c7",
            border: "1px solid #fde68a",
            borderRadius: 8,
            padding: "6px 10px",
          }}
          role="status"
        >
          <span style={{ fontWeight: 700 }}>Limited data:</span>
          <span>
            Only {completeness.toFixed(0)}% of scoring fields are filled for this policy —
            grade may shift once more details are indexed.
          </span>
        </div>
      )}
    </div>
  );
}
