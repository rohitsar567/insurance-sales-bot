// PolicyScorecardWidget — profile-aware A/B+/etc grade card for a single
// policy. Mounted inside PolicyCompareModal once per policy.
//
// Why a per-policy widget (rather than a single multi-policy table):
//   - PolicyCompareModal renders 2-4 of these side-by-side in a flex row.
//   - Each card is self-contained: insurer logo strip, header grade +
//     overall score, sub-score bars with their *drivers* (the concrete
//     policy fields that moved each bar), a prominent "Why this fits you"
//     personalisation panel, and a data-completeness warning.
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
//
// ── Visual system ──────────────────────────────────────────────────────
// Re-grounded on the premium editorial-fintech landing (see app/globals.css):
// Fraunces display serif for the grade + score numerals, Plus Jakarta for
// UI chrome, the teal --primary token, color-mix soft depth, the kicker
// pill pattern for the "Personalised" tag, and tabular-nums everywhere a
// number must align. All chrome reads from CSS variables so the card shifts
// with the page's light/dark scheme. Reduced-motion is honoured.

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
  { key: "coverage_breadth", label: "Coverage breadth" },
  { key: "cost_predictability", label: "Cost predictability" },
  { key: "waiting_period_friction", label: "Waiting periods" },
  { key: "claim_experience", label: "Claim experience" },
  { key: "renewal_protection", label: "Renewal protection" },
  { key: "bonus_and_loyalty", label: "Bonuses" },
];

// Insurer brand assets — forked from PolicyCompareModal so the scorecard
// block can carry the same logo top-left without importing from a sibling
// (keeps each widget self-contained; both maps stay in lock-step).
const INSURER_COLOR: Record<string, string> = {
  "aditya-birla":  "#ea580c",
  "bajaj-allianz": "#1d4ed8",
  "care-health":   "#047857",
  "hdfc-ergo":     "#be123c",
  "icici-lombard": "#f97316",
  "manipalcigna":  "#a21caf",
  "new-india":     "#4338ca",
  "niva-bupa":     "#0e7490",
  "star-health":   "#d97706",
  "tata-aig":      "#334155",
};

const INSURER_LOGO_URL: Record<string, string> = {
  "aditya-birla":  "https://www.adityabirlacapital.com/healthinsurance/static/assets/images/abhi-logo.svg",
  "bajaj-allianz": "https://www.bajajallianz.com/content/dam/bagic/header/logo.png",
  "care-health":   "https://www.careinsurance.com/upload_master/images/logo.png",
  "hdfc-ergo":     "https://www.hdfcergo.com/etc.clientlibs/hdfcergo/clientlibs/clientlib-site/resources/images/HDFC-ERGO-Logo.png",
  "icici-lombard": "https://www.icicilombard.com/content/dam/ilom-website/icon/icici-lombard-logo-new.svg",
  "manipalcigna":  "https://www.manipalcigna.com/o/manipal-cigna-theme/images/manipal-cigna-logo.svg",
  "new-india":     "https://www.newindia.co.in/portal/readWriteData/NIAImages/NewLogo.png",
  "niva-bupa":     "https://transactions.nivabupa.com/_next/static/media/niva-bupa-logo.7b6e7f4e.svg",
  "star-health":   "https://www.starhealth.in/sites/default/files/star-logo-revised.png",
  "tata-aig":      "https://www.tataaig.com/etc/designs/tataaig/clientlibs/responsive/images/tataaig-logo.svg",
};

function insurerInitials(name: string): string {
  return name
    .split(/[\s-]+/)
    .map((w) => w[0])
    .filter(Boolean)
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

// Small brand chip — image with a typeset-initials fallback. Used top-left
// of the scorecard so the card is identifiable at a glance inside a row of
// 2-4 side-by-side scorecards.
function InsurerLogo({
  slug,
  name,
  size = 36,
}: {
  slug: string;
  name: string;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const url = INSURER_LOGO_URL[slug];
  const color = INSURER_COLOR[slug] || "#64748b";
  if (!url || failed) {
    return (
      <div
        style={{
          width: size,
          height: size,
          borderRadius: 10,
          background: color,
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 700,
          fontSize: size * 0.32,
          flexShrink: 0,
        }}
        aria-hidden
      >
        {insurerInitials(name)}
      </div>
    );
  }
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: 10,
        background: "#fff",
        border: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        overflow: "hidden",
        padding: 5,
        flexShrink: 0,
        boxShadow:
          "0 1px 2px color-mix(in srgb, var(--foreground) 6%, transparent)",
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={name}
        onError={() => setFailed(true)}
        style={{ maxWidth: "100%", maxHeight: "100%", objectFit: "contain" }}
      />
    </div>
  );
}

// Humanise an insurer slug for the fallback initials / label.
function humaniseSlug(slug: string): string {
  return slug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

// Pretty label for a sub-score key when the backend hands us one we don't
// have a curated label for.
function prettyKey(k: string): string {
  return k.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

// Serif display face + sans UI face, pulled from the landing's CSS vars so
// the widget shares the exact type system as the rest of the app.
const SERIF = "var(--font-serif)";
const SANS = "var(--font-sans)";

// A→F grade ramp. Kept inside one tonal family per letter so a B+ reads as
// a stronger sibling of a B-. The "A" tier is the brand teal; the rest walk
// a calm green→amber→red gradient that still feels editorial, not alarmist.
function gradeColor(grade: string): { fg: string; bg: string; ring: string } {
  const head = grade.charAt(0).toUpperCase();
  switch (head) {
    case "A":
      return {
        fg: "color-mix(in srgb, var(--primary) 82%, #042f2a)",
        bg: "color-mix(in srgb, var(--primary) 13%, var(--card))",
        ring: "color-mix(in srgb, var(--primary) 60%, var(--border))",
      };
    case "B":
      return { fg: "#155e63", bg: "#e3f4f3", ring: "#3d9c98" };
    case "C":
      return { fg: "#855316", bg: "#fbeed2", ring: "#cf9b3f" };
    case "D":
      return { fg: "#8a3c12", bg: "#fae0cd", ring: "#d4793b" };
    case "F":
      return { fg: "#8a2020", bg: "#f8d9d9", ring: "#cf4b4b" };
    default:
      return {
        fg: "var(--muted-foreground)",
        bg: "var(--muted)",
        ring: "var(--border)",
      };
  }
}

// Sub-score bar fill — same calm ramp as the grade so the card never has
// two competing color stories.
function barColor(score: number): string {
  if (score >= 80) return "var(--primary)";
  if (score >= 65) return "#3d9c98";
  if (score >= 50) return "#cf9b3f";
  if (score >= 35) return "#d4793b";
  return "#cf4b4b";
}

function rationaleTone(bullet: string): "pos" | "neg" | "neutral" {
  const lower = bullet.toLowerCase();
  if (lower.startsWith("strong fit") || lower.startsWith("strongest")) return "pos";
  if (lower.startsWith("weak fit") || lower.startsWith("watch out")) return "neg";
  return "neutral";
}

// Shared shell so loading / error / loaded states share identical framing
// (no layout jump between states inside the compare grid).
const shellStyle: React.CSSProperties = {
  borderRadius: 18,
  border: "1px solid var(--border)",
  background: "var(--card)",
  padding: 18,
  fontFamily: SANS,
  boxShadow:
    "0 1px 2px color-mix(in srgb, var(--foreground) 4%, transparent), 0 16px 40px -32px color-mix(in srgb, var(--foreground) 28%, transparent)",
};

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
          ...shellStyle,
          minHeight: 240,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
          color: "var(--muted-foreground)",
        }}
        aria-busy="true"
        aria-live="polite"
      >
        <div
          aria-hidden
          style={{
            width: 26,
            height: 26,
            borderRadius: 999,
            border: "2px solid color-mix(in srgb, var(--primary) 22%, var(--border))",
            borderTopColor: "var(--primary)",
            animation: "scw-spin 0.8s linear infinite",
          }}
        />
        <span style={{ fontSize: 12.5, letterSpacing: "0.01em" }}>
          Scoring {policyName}…
        </span>
        <style>{
          "@keyframes scw-spin{to{transform:rotate(360deg)}}" +
          "@media (prefers-reduced-motion: reduce){[style*='scw-spin']{animation:none!important}}"
        }</style>
      </div>
    );
  }

  if (error || !entry) {
    return (
      <div
        className={className}
        style={{
          ...shellStyle,
          border: "1px solid color-mix(in srgb, var(--error) 38%, var(--border))",
          background: "color-mix(in srgb, var(--error) 6%, var(--card))",
          color: "color-mix(in srgb, var(--error) 75%, var(--foreground))",
          fontSize: 12.5,
          lineHeight: 1.5,
        }}
        role="alert"
      >
        <span style={{ fontWeight: 600 }}>Couldn’t score this policy.</span>{" "}
        {error ?? "Unknown error"}
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
        ...shellStyle,
        display: "flex",
        flexDirection: "column",
        gap: 16,
      }}
      data-policy-id={policyId}
    >
      {/* Insurer identity strip — logo top-left so a row of side-by-side
          scorecards is scannable without reading the policy name. */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 9,
          paddingBottom: 12,
          borderBottom: "1px solid var(--border)",
        }}
      >
        <InsurerLogo
          slug={entry.insurer_slug || ""}
          name={
            entry.insurer_slug
              ? humaniseSlug(entry.insurer_slug)
              : entry.policy_name || policyName
          }
          size={34}
        />
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            color: "var(--muted-foreground)",
            fontWeight: 700,
            whiteSpace: "nowrap",
            overflow: "hidden",
            textOverflow: "ellipsis",
          }}
        >
          {entry.insurer_slug
            ? humaniseSlug(entry.insurer_slug)
            : "Fit scorecard"}
        </span>
      </div>

      {/* Header: grade medallion + overall score */}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div
          style={{
            width: 62,
            height: 62,
            borderRadius: 16,
            background: colors.bg,
            color: colors.fg,
            border: `1px solid ${colors.ring}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: SERIF,
            fontOpticalSizing: "auto",
            fontSize: entry.overall_grade.length > 1 ? 26 : 32,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            flexShrink: 0,
            boxShadow:
              "inset 0 1px 0 color-mix(in srgb, #fff 50%, transparent), 0 2px 6px color-mix(in srgb, var(--foreground) 8%, transparent)",
          }}
          aria-label={`Grade ${entry.overall_grade}`}
        >
          {entry.overall_grade}
        </div>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div
            style={{
              fontSize: 13.5,
              fontWeight: 600,
              color: "var(--foreground)",
              lineHeight: 1.35,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              letterSpacing: "-0.005em",
            }}
            title={entry.policy_name || policyName}
          >
            {entry.policy_name || policyName}
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "baseline",
              gap: 5,
              marginTop: 4,
            }}
          >
            <span
              style={{
                fontFamily: SERIF,
                fontOpticalSizing: "auto",
                fontSize: 26,
                fontWeight: 600,
                color: "var(--foreground)",
                letterSpacing: "-0.02em",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {isNA ? "—" : `${entry.overall_score}`}
            </span>
            {!isNA && (
              <span
                style={{
                  fontSize: 12,
                  color: "var(--muted-foreground)",
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                / 100
              </span>
            )}
          </div>
          {/* Task #31 — the deterministic profile_summary replaces the
              generic one_liner here. The first strength reads as the
              headline takeaway; the full list + caveat live in the
              "Why this fits you" panel below. Fall back to one_liner only
              when the structured summary is empty / insufficient. */}
          {entry.profile_summary && entry.profile_summary.strengths.length > 0 ? (
            <div
              style={{
                fontSize: 11.5,
                color: "var(--foreground)",
                marginTop: 3,
                lineHeight: 1.4,
              }}
            >
              {entry.profile_summary.strengths[0]}
            </div>
          ) : (
            entry.one_liner && (
              <div
                style={{
                  fontSize: 11.5,
                  color: "var(--muted-foreground)",
                  marginTop: 3,
                  lineHeight: 1.4,
                }}
              >
                {entry.one_liner}
              </div>
            )
          )}
        </div>
      </div>

      {/* "Personalised" tag — kicker-pill pattern from the landing. Full
          width so it never collides with a long policy name. */}
      {profile && !isNA && (
        <div
          style={{
            display: "inline-flex",
            alignSelf: "flex-start",
            alignItems: "center",
            gap: 7,
            padding: "5px 11px 5px 9px",
            borderRadius: 999,
            fontSize: 10.5,
            fontWeight: 600,
            letterSpacing: "0.1em",
            textTransform: "uppercase",
            color: "var(--primary)",
            background: "color-mix(in srgb, var(--primary) 9%, var(--card))",
            border: "1px solid color-mix(in srgb, var(--primary) 22%, var(--border))",
          }}
          title="Score weights adjusted for your profile"
        >
          <span
            aria-hidden
            style={{
              width: 5,
              height: 5,
              borderRadius: 999,
              background: "var(--primary)",
            }}
          />
          Personalised for you
        </div>
      )}

      {/* Sub-scores — labelled rows with tabular numerals + a calm fill */}
      {renderable.length > 0 && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 10,
            paddingTop: 14,
            borderTop: "1px solid var(--border)",
          }}
        >
          {renderable.map(({ key, label }) => {
            const v = entry.sub_scores[key] ?? 0;
            const pct = Math.max(0, Math.min(100, v));
            // What drove THIS bar — the backend returns a per-sub-score
            // signal list (the concrete policy fields / rules that moved
            // the number). Surfacing it makes each bar auditable instead
            // of a mystery fill.
            const drivers = (entry.signals?.[key] ?? [])
              .map((s) => s.trim())
              .filter(Boolean)
              .slice(0, 3);
            return (
              <div
                key={key}
                style={{ display: "flex", flexDirection: "column", gap: 5 }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                    gap: 10,
                    fontSize: 11.5,
                  }}
                >
                  <span
                    style={{
                      color: "var(--foreground)",
                      fontWeight: 500,
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {label}
                  </span>
                  <span
                    style={{
                      color: "var(--muted-foreground)",
                      fontVariantNumeric: "tabular-nums",
                      fontWeight: 600,
                      flexShrink: 0,
                    }}
                  >
                    {v}
                  </span>
                </div>
                <div
                  style={{
                    height: 6,
                    borderRadius: 999,
                    background: "var(--muted)",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${pct}%`,
                      height: "100%",
                      borderRadius: 999,
                      background: barColor(v),
                      transition: "width 260ms cubic-bezier(.2,.7,.3,1)",
                    }}
                  />
                </div>
                {drivers.length > 0 && (
                  <ul
                    style={{
                      margin: "3px 0 0",
                      padding: 0,
                      listStyle: "none",
                      display: "flex",
                      flexDirection: "column",
                      gap: 3,
                    }}
                  >
                    {drivers.map((d, di) => (
                      <li
                        key={di}
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 6,
                          fontSize: 10.5,
                          lineHeight: 1.4,
                          color: "var(--muted-foreground)",
                        }}
                      >
                        <span
                          aria-hidden
                          style={{
                            flex: "none",
                            marginTop: 5,
                            width: 4,
                            height: 4,
                            borderRadius: 999,
                            background: barColor(v),
                          }}
                        />
                        <span>{d}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          <div
            style={{
              fontSize: 9.5,
              color: "var(--muted-foreground)",
              letterSpacing: "0.04em",
              marginTop: 1,
            }}
          >
            Bars show what each criterion contributed — drivers under a bar
            are the exact policy fields that moved it.
          </div>
        </div>
      )}

      {/* "Why this fits you" — the headline personalisation payoff. Given
          its own emphatic teal-tinted panel so it reads as the answer to
          the user's real question, not a footnote under the bars.

          Task #31 — prefer the deterministic profile_summary: strengths
          render in a positive tone (teal tick), the single caveat in a
          negative tone (red tick). When the structured summary is empty /
          insufficient, fall back to the legacy profile_rationale list
          (tone inferred via the existing rationaleTone()). */}
      {(() => {
        const ps = entry.profile_summary;
        const bullets: { text: string; tone: "pos" | "neg" | "neutral" }[] =
          ps && ps.strengths.length > 0
            ? [
                ...ps.strengths.map(
                  (s) => ({ text: s, tone: "pos" as const }),
                ),
                ...(ps.caveat
                  ? [{ text: ps.caveat, tone: "neg" as const }]
                  : []),
              ]
            : entry.profile_rationale.map((b) => ({
                text: b,
                tone: rationaleTone(b),
              }));
        if (bullets.length === 0) return null;
        return (
          <div
            style={{
              background:
                "linear-gradient(180deg, color-mix(in srgb, var(--primary) 9%, var(--card)) 0%, color-mix(in srgb, var(--primary) 4%, var(--card)) 100%)",
              border:
                "1px solid color-mix(in srgb, var(--primary) 28%, var(--border))",
              borderRadius: 14,
              padding: "14px 15px",
              boxShadow:
                "0 1px 2px color-mix(in srgb, var(--primary) 10%, transparent)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 10,
              }}
            >
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 999,
                  background: "var(--primary)",
                  flexShrink: 0,
                }}
              />
              <span
                style={{
                  fontFamily: SERIF,
                  fontOpticalSizing: "auto",
                  fontSize: 14,
                  fontWeight: 600,
                  letterSpacing: "-0.01em",
                  color:
                    "color-mix(in srgb, var(--primary) 80%, var(--foreground))",
                }}
              >
                Why this fits you
              </span>
            </div>
            <ul
              style={{
                margin: 0,
                padding: 0,
                listStyle: "none",
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              {bullets.map((b, i) => {
                const tickColor =
                  b.tone === "pos"
                    ? "var(--primary)"
                    : b.tone === "neg"
                      ? "#cf4b4b"
                      : "var(--muted-foreground)";
                return (
                  <li
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "flex-start",
                      gap: 9,
                      fontSize: 12.5,
                      color: "var(--foreground)",
                      lineHeight: 1.5,
                    }}
                  >
                    <span
                      aria-hidden
                      style={{
                        flex: "none",
                        marginTop: 6,
                        width: 6,
                        height: 6,
                        borderRadius: 999,
                        background: tickColor,
                      }}
                    />
                    <span>{b.text}</span>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })()}

      {/* Limited-data warning — warm amber, single tidy row.
          Copy is branched per source: catalogued insurer cards reflect
          gaps in the insurer's own filings, but USER-UPLOADED PDFs are
          missing fields because OUR extractor couldn't pull them from
          the PDF text. Saying "the insurer hasn't published…" on an
          uploaded doc is just wrong — flip to honest copy there. */}
      {showLimitedWarning && (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            fontSize: 11,
            lineHeight: 1.45,
            color: "#855316",
            background: "color-mix(in srgb, var(--accent) 60%, var(--card))",
            border: "1px solid color-mix(in srgb, #cf9b3f 40%, var(--border))",
            borderRadius: 10,
            padding: "8px 11px",
          }}
          role="status"
        >
          <span style={{ fontWeight: 700, flexShrink: 0 }}>Partial information ·</span>
          <span>
            {policyId.startsWith("user-upload__") ? (
              <>
                Some fields couldn&apos;t be pulled from this PDF
                automatically — open the document for the full wording
                before you decide. (Re-grading in the background as more
                fields are extracted.)
              </>
            ) : (
              <>
                The insurer hasn&apos;t published every term for this policy
                yet, so this grade is an early read — open the policy PDF
                for the full wording before you decide.
              </>
            )}
          </span>
        </div>
      )}
    </div>
  );
}
