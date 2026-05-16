"use client";

// PolicyCompareModal — side-by-side comparison modal opened from the chat
// reply. Renders one column per cited policy with three slots:
//   1. Header  — insurer logo + policy name + insurer + scorecard chip
//   2. Premium — pluggable widget (B2) via props.renderPremiumFor(policyId)
//   3. Scorecard — pluggable widget (B3) via props.renderScorecardFor(policyId)
//   4. Policy details — expandable section with source URL
//
// ── Visual system ─────────────────────────────────────────────────────
// Re-grounded on the premium editorial-fintech landing (app/globals.css):
// Fraunces display serif via `.font-display` for the modal title, the teal
// --primary token, color-mix soft depth shadows, the kicker-pill pattern
// for section eyebrows, and tight one-line fact bullets. Everything reads
// from CSS variables so the modal tracks the page's light/dark scheme. The
// grade chip uses the same calm A→F ramp as PolicyScorecardWidget.

import { useEffect, useState, type ReactNode } from "react";
import {
  Citation,
  MarketplacePolicy,
  ScorecardResponse,
  getScorecard,
} from "@/lib/api";

// ----- local visual helpers (forked from page.tsx so this file is
// self-contained; the originals stay in page.tsx untouched). -----

const INSURER_COLOR: Record<string, string> = {
  "aditya-birla":  "bg-orange-600",
  "bajaj-allianz": "bg-blue-700",
  "care-health":   "bg-emerald-700",
  "hdfc-ergo":     "bg-rose-700",
  "icici-lombard": "bg-orange-500",
  "manipalcigna":  "bg-fuchsia-700",
  "new-india":     "bg-indigo-700",
  "niva-bupa":     "bg-cyan-700",
  "star-health":   "bg-amber-600",
  "tata-aig":      "bg-slate-700",
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
  return name.split(/[\s-]+/).map((w) => w[0]).filter(Boolean).join("").slice(0, 2).toUpperCase();
}

function InsurerLogo({ slug, name, size = 40 }: { slug: string; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const url = INSURER_LOGO_URL[slug];
  const color = INSURER_COLOR[slug] || "bg-slate-500";
  if (!url || failed) {
    return (
      <div
        className={`rounded-xl ${color} text-white flex items-center justify-center font-bold shrink-0`}
        style={{ width: size, height: size, fontSize: size * 0.32 }}
      >
        {insurerInitials(name)}
      </div>
    );
  }
  return (
    <div
      className="rounded-xl bg-white border border-[var(--border)] flex items-center justify-center shrink-0 overflow-hidden p-1.5"
      style={{
        width: size,
        height: size,
        boxShadow:
          "0 1px 2px color-mix(in srgb, var(--foreground) 6%, transparent)",
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt={name}
        onError={() => setFailed(true)}
        className="max-w-full max-h-full object-contain"
      />
    </div>
  );
}

// Calm A→F ramp — the same tonal family as PolicyScorecardWidget so the
// chip in the column header matches the full scorecard below it. "A" is the
// brand teal; the rest walk a quiet green→amber→red gradient.
function gradeChip(grade: string): { fg: string; bg: string; ring: string } {
  const head = (grade || "").charAt(0).toUpperCase();
  switch (head) {
    case "A":
      return {
        fg: "color-mix(in srgb, var(--primary) 82%, #042f2a)",
        bg: "color-mix(in srgb, var(--primary) 14%, var(--card))",
        ring: "color-mix(in srgb, var(--primary) 55%, var(--border))",
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

// Dedupe citations by policy_id, preserving order.
function uniquePolicies(citations: Citation[]): Citation[] {
  const seen = new Set<string>();
  const out: Citation[] = [];
  for (const c of citations) {
    if (seen.has(c.policy_id)) continue;
    seen.add(c.policy_id);
    out.push(c);
  }
  return out;
}

export type PolicyCompareModalProps = {
  policies: Citation[];
  onClose: () => void;
  // B2 + B3 plug points. Both are optional; safe fallbacks render below.
  renderPremiumFor?: (policyId: string, policyName: string) => ReactNode;
  renderScorecardFor?: (policyId: string, policyName: string) => ReactNode;
  // Profile hint for downstream personalized widgets (unused by the shell
  // itself; pass-through so widgets opened via renderXxxFor can read it).
  // Typed loosely (any) by contract; harness should narrow when wiring.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  profile?: any;
  // Optional resolver returning the marketplace MarketplacePolicy row for a
  // citation. Powers the new "POLICY HIGHLIGHTS" section (4-stat grid +
  // bullets). When undefined, the highlights section is skipped.
  policyDataFor?: (policyId: string) => MarketplacePolicy | undefined;
  // Hook for "Open in full marketplace" — defaults to no-op + closes modal.
  onOpenMarketplace?: () => void;
  // User's profile-level predicted premium band (same number rendered in
  // the chat header chip). Threaded down to PolicyPremiumWidget so that
  // non-curated policies (base_sample_used: false) can surface the band
  // as their indicative reference instead of a heuristic slider estimate.
  // Optional: when omitted, non-curated widgets fall back to a "band not
  // available" hint. The parent (page.tsx) typically closes over the same
  // value inside renderPremiumFor too — this prop is the declarative
  // contract for future callers.
  aggregateBand?: {
    min_inr: number;
    max_inr: number;
    median_inr: number;
    sample_size?: number;
    assumed?: boolean;
  } | null;
};

export default function PolicyCompareModal({
  policies,
  onClose,
  renderPremiumFor,
  renderScorecardFor,
  profile: _profile,
  policyDataFor,
  onOpenMarketplace,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  aggregateBand: _aggregateBand,
}: PolicyCompareModalProps) {
  const uniq = uniquePolicies(policies).slice(0, 4);
  const n = uniq.length;

  // Close on Escape — keyboard parity with the click-outside backdrop.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[70] flex items-stretch sm:items-center justify-center p-0 sm:p-4 animate-fade-up"
      style={{
        background:
          "color-mix(in srgb, var(--foreground) 48%, transparent)",
        backdropFilter: "blur(3px)",
        WebkitBackdropFilter: "blur(3px)",
      }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Compare ${n} polic${n === 1 ? "y" : "ies"}`}
    >
      <div
        className="bg-[var(--card)] sm:rounded-3xl w-full sm:max-w-7xl sm:w-[92vw] max-h-screen sm:max-h-[92vh] overflow-y-auto scrollbar-thin"
        style={{
          border: "1px solid var(--border)",
          boxShadow:
            "0 1px 2px color-mix(in srgb, var(--foreground) 6%, transparent), 0 40px 90px -40px color-mix(in srgb, var(--foreground) 55%, transparent)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header — Fraunces display title + brand-tinted eyebrow */}
        <div
          className="sticky top-0 z-10 px-5 sm:px-6 py-4 flex items-start justify-between gap-4"
          style={{
            background:
              "linear-gradient(180deg, color-mix(in srgb, var(--primary) 6%, var(--card)) 0%, var(--card) 100%)",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div className="min-w-0">
            <div
              className="inline-flex items-center gap-2 mb-2"
              style={{
                padding: "4px 10px 4px 9px",
                borderRadius: 999,
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "var(--primary)",
                background:
                  "color-mix(in srgb, var(--primary) 9%, var(--card))",
                border:
                  "1px solid color-mix(in srgb, var(--primary) 22%, var(--border))",
              }}
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
              Side-by-side
            </div>
            <h3
              className="font-display"
              style={{
                fontSize: 22,
                fontWeight: 600,
                color: "var(--foreground)",
                lineHeight: 1.15,
                letterSpacing: "-0.012em",
              }}
            >
              Compare {n} polic{n === 1 ? "y" : "ies"}
            </h3>
            <p
              style={{
                fontSize: 12,
                color: "var(--muted-foreground)",
                marginTop: 4,
                lineHeight: 1.4,
              }}
            >
              Premiums, fit scores and policy details — aligned side-by-side.
            </p>
          </div>
          <button
            onClick={onClose}
            className="shrink-0"
            style={{
              width: 34,
              height: 34,
              borderRadius: 999,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 20,
              lineHeight: 1,
              color: "var(--muted-foreground)",
              background: "var(--card)",
              border: "1px solid var(--border)",
              cursor: "pointer",
              transition: "color .15s ease, border-color .15s ease",
            }}
            aria-label="Close comparison"
          >
            ×
          </button>
        </div>

        {/* Body: 1 col on mobile, n cols on desktop */}
        <div className="p-4 sm:p-6">
          <div
            className="grid gap-4 sm:gap-5 grid-cols-1"
            style={{
              gridTemplateColumns:
                n > 1 ? `repeat(${n}, minmax(0, 1fr))` : undefined,
            }}
          >
            {uniq.map((c) => (
              <CompareColumn
                key={c.policy_id}
                citation={c}
                premiumSlot={renderPremiumFor?.(c.policy_id, c.policy_name)}
                scorecardSlot={renderScorecardFor?.(c.policy_id, c.policy_name)}
                marketplacePolicy={policyDataFor?.(c.policy_id)}
              />
            ))}
          </div>
        </div>

        {/* Footer */}
        <div
          className="sticky bottom-0 px-5 sm:px-6 py-3.5 flex flex-wrap items-center justify-between gap-2"
          style={{
            background:
              "linear-gradient(0deg, color-mix(in srgb, var(--primary) 5%, var(--card)) 0%, var(--card) 100%)",
            borderTop: "1px solid var(--border)",
          }}
        >
          <span
            style={{
              fontSize: 11.5,
              color: "var(--muted-foreground)",
              lineHeight: 1.4,
            }}
          >
            Comparing the policies cited in this reply. Open the full
            marketplace for filters and 30+ more options.
          </span>
          <button
            onClick={() => {
              onOpenMarketplace?.();
              onClose();
            }}
            className="hover:underline"
            style={{
              fontSize: 12.5,
              fontWeight: 600,
              color: "var(--primary)",
              cursor: "pointer",
            }}
          >
            Open in full marketplace →
          </button>
        </div>
      </div>
    </div>
  );
}

// One vertical card per cited policy.
function CompareColumn({
  citation,
  premiumSlot,
  scorecardSlot,
  marketplacePolicy,
}: {
  citation: Citation;
  premiumSlot?: ReactNode;
  scorecardSlot?: ReactNode;
  marketplacePolicy?: MarketplacePolicy;
}) {
  // Prefer the canonical insurer name from the marketplace row when we
  // have it (e.g. "HDFC ERGO General Insurance" vs the slug "hdfc-ergo"
  // un-prettified). Falls back to the slug humanised.
  const insurerName =
    marketplacePolicy?.insurer_name ?? citation.insurer_slug.replace(/-/g, " ");
  return (
    <div
      className="flex flex-col gap-4 min-w-0"
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 18,
        padding: 18,
        boxShadow:
          "0 1px 2px color-mix(in srgb, var(--foreground) 4%, transparent), 0 18px 44px -34px color-mix(in srgb, var(--foreground) 30%, transparent)",
      }}
    >
      {/* Header — logo + insurer + policy name */}
      <div className="flex items-start gap-3 pb-4" style={{ borderBottom: "1px solid var(--border)" }}>
        <InsurerLogo slug={citation.insurer_slug} name={insurerName} size={44} />
        <div className="flex-1 min-w-0">
          <div
            className="truncate"
            style={{
              fontSize: 10,
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              color: "var(--muted-foreground)",
              fontWeight: 600,
            }}
          >
            {insurerName}
          </div>
          <div
            className="break-words"
            style={{
              fontFamily: "var(--font-serif)",
              fontOpticalSizing: "auto",
              fontSize: 16,
              fontWeight: 600,
              color: "var(--foreground)",
              lineHeight: 1.25,
              letterSpacing: "-0.01em",
              marginTop: 3,
            }}
          >
            {citation.policy_name}
          </div>
          {marketplacePolicy?.aliases && marketplacePolicy.aliases.length > 0 && (
            <div
              className="break-words"
              style={{
                fontSize: 11,
                color: "var(--muted-foreground)",
                fontStyle: "italic",
                marginTop: 4,
                lineHeight: 1.4,
              }}
            >
              Also marketed as: {marketplacePolicy.aliases.join(", ")}
            </div>
          )}
        </div>
      </div>

      {/* POLICY HIGHLIGHTS — 4-stat grid + bullets. Mirrors the marketplace
          PolicyCard so a chat-side compare looks the same as a marketplace-
          side compare. Skipped when no marketplace row is available. */}
      {marketplacePolicy && (
        <PolicyHighlights policy={marketplacePolicy} />
      )}

      {/* PREMIUM ESTIMATE slot (B2). Render the slot directly — the parent's
          PolicyPremiumWidget already owns its own bordered card chrome, so
          double-wrapping it produces nested boxes. Only fall back to a
          placeholder when the parent didn't wire renderPremiumFor at all. */}
      <Section title="Premium estimate">
        {premiumSlot ?? <PlaceholderWidget label="Premium calculator coming soon" />}
      </Section>

      {/* FIT SCORECARD slot (B3). Same direct-render rule. */}
      <Section title="Your fit scorecard">
        {scorecardSlot ?? <ScorecardFallback policyId={citation.policy_id} />}
      </Section>

      {/* POLICY DETAILS expandable */}
      <PolicyDetails citation={citation} />
    </div>
  );
}

// 4-stat grid + bullets sourced from the MarketplacePolicy row. Matches
// PolicyCard's body in MarketplacePanel so the user sees the same numbers
// in both surfaces.
function PolicyHighlights({ policy }: { policy: MarketplacePolicy }) {
  const maxSI = policy.sum_insured_options.length
    ? Math.max(...policy.sum_insured_options)
    : null;
  const siDisplay = maxSI
    ? maxSI >= 10_000_000
      ? `${maxSI / 10_000_000} Cr`
      : `${maxSI / 100_000} L`
    : "—";
  const network = policy.network_hospital_count
    ? `${(policy.network_hospital_count / 1000).toFixed(0)}K+`
    : "—";
  const pedWait = policy.pre_existing_disease_waiting_months
    ? `${policy.pre_existing_disease_waiting_months} mo`
    : "—";
  const cashless =
    policy.cashless_treatment_supported === true
      ? "Yes"
      : policy.cashless_treatment_supported === false
        ? "No"
        : "—";

  const bullets: string[] = [];
  if (policy.no_claim_bonus_pct) {
    bullets.push(`No-claim bonus up to ${policy.no_claim_bonus_pct}%`);
  }
  if (policy.room_rent_capping && policy.room_rent_capping.trim() !== "") {
    bullets.push(`Room rent: ${policy.room_rent_capping}`);
  }
  if (policy.ayush_coverage === true) {
    bullets.push("AYUSH treatments covered");
  }
  if (policy.maternity_coverage === true && policy.maternity_waiting_months) {
    bullets.push(
      `Maternity covered (${policy.maternity_waiting_months}-mo wait)`,
    );
  } else if (policy.maternity_coverage === true) {
    bullets.push("Maternity covered");
  }
  if (policy.copayment_pct != null && policy.copayment_pct > 0) {
    bullets.push(`Co-payment: ${policy.copayment_pct}%`);
  }

  return (
    <Section title="Policy highlights">
      <div className="grid grid-cols-2 gap-2.5">
        <MiniStat label="Sum insured" value={siDisplay} />
        <MiniStat label="PED waiting" value={pedWait} />
        <MiniStat label="Network" value={network} />
        <MiniStat label="Cashless" value={cashless} />
      </div>
      {bullets.length > 0 && (
        <ul
          className="mt-3"
          style={{
            margin: "12px 0 0",
            padding: 0,
            listStyle: "none",
            display: "flex",
            flexDirection: "column",
            gap: 7,
          }}
        >
          {bullets.slice(0, 4).map((b, i) => (
            <li
              key={i}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 8,
                fontSize: 11.5,
                color: "var(--foreground)",
                lineHeight: 1.4,
              }}
            >
              <span
                aria-hidden
                style={{
                  flex: "none",
                  marginTop: 6,
                  width: 5,
                  height: 5,
                  borderRadius: 999,
                  background: "var(--primary)",
                }}
              />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "color-mix(in srgb, var(--primary) 4%, var(--muted))",
        border: "1px solid var(--border)",
        borderRadius: 12,
        padding: "9px 11px",
      }}
    >
      <div
        style={{
          fontSize: 9.5,
          color: "var(--muted-foreground)",
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          fontWeight: 600,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 14,
          fontWeight: 700,
          color: "var(--foreground)",
          marginTop: 3,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
    </div>
  );
}

// Section header — a brand-tinted eyebrow with a hairline rule that grows
// from the label, echoing the landing's "titled chapter" section pattern.
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginBottom: 10,
        }}
      >
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            color:
              "color-mix(in srgb, var(--primary) 70%, var(--muted-foreground))",
            fontWeight: 700,
            whiteSpace: "nowrap",
          }}
        >
          {title}
        </span>
        <span
          aria-hidden
          style={{
            flex: 1,
            height: 1,
            background:
              "linear-gradient(90deg, color-mix(in srgb, var(--primary) 28%, var(--border)) 0%, var(--border) 35%, transparent 100%)",
          }}
        />
      </div>
      {children}
    </div>
  );
}

function PlaceholderWidget({ label }: { label: string }) {
  return (
    <div
      style={{
        background: "var(--muted)",
        border: "1px dashed var(--border)",
        borderRadius: 12,
        padding: "16px 12px",
        textAlign: "center",
        fontSize: 11.5,
        color: "var(--muted-foreground)",
      }}
    >
      {label}
    </div>
  );
}

// Default scorecard preview — fetches `/api/policies/:id/scorecard` and
// renders a compact grade + one-liner card. Used when the parent doesn't
// pass a renderScorecardFor() prop.
function ScorecardFallback({ policyId }: { policyId: string }) {
  const [sc, setSc] = useState<ScorecardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    getScorecard(policyId)
      .then((r) => {
        if (!cancelled) setSc(r);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [policyId]);
  if (loading) {
    return <PlaceholderWidget label="Loading scorecard…" />;
  }
  if (error || !sc) {
    return <PlaceholderWidget label="Scorecard unavailable" />;
  }
  const chip = gradeChip(sc.grade);
  return (
    <div
      style={{
        background: "color-mix(in srgb, var(--primary) 3%, var(--muted))",
        border: "1px solid var(--border)",
        borderRadius: 14,
        padding: 14,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 11 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 42,
            height: 42,
            borderRadius: 12,
            fontFamily: "var(--font-serif)",
            fontOpticalSizing: "auto",
            fontSize: sc.grade.length > 1 ? 17 : 21,
            fontWeight: 600,
            letterSpacing: "-0.02em",
            color: chip.fg,
            background: chip.bg,
            border: `1px solid ${chip.ring}`,
            flexShrink: 0,
          }}
        >
          {sc.grade}
        </span>
        <div style={{ minWidth: 0 }}>
          <div
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: "var(--foreground)",
              lineHeight: 1.2,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {sc.overall_score}
            <span
              style={{
                color: "var(--muted-foreground)",
                fontSize: 10,
                fontWeight: 500,
              }}
            >
              {" "}
              / 100
            </span>
          </div>
          <div
            className="truncate"
            style={{
              fontSize: 10.5,
              color: "var(--muted-foreground)",
              marginTop: 2,
            }}
          >
            {sc.one_liner}
          </div>
        </div>
      </div>
    </div>
  );
}

function PolicyDetails({ citation }: { citation: Citation }) {
  const [open, setOpen] = useState(false);
  const hasSource = !!citation.source_url && citation.source_url.startsWith("http");
  const pageRange =
    citation.page_start && citation.page_end
      ? citation.page_start === citation.page_end
        ? `p. ${citation.page_start}`
        : `pp. ${citation.page_start}–${citation.page_end}`
      : null;
  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: 14,
        background: "var(--card)",
        overflow: "hidden",
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left"
        style={{
          padding: "11px 13px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          background: open
            ? "color-mix(in srgb, var(--primary) 5%, var(--card))"
            : "var(--card)",
          cursor: "pointer",
          transition: "background .15s ease",
        }}
        aria-expanded={open}
      >
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            color:
              "color-mix(in srgb, var(--primary) 70%, var(--muted-foreground))",
            fontWeight: 700,
          }}
        >
          Policy details
        </span>
        <span
          aria-hidden
          style={{
            fontSize: 16,
            lineHeight: 1,
            color: "var(--primary)",
            fontWeight: 500,
          }}
        >
          {open ? "−" : "+"}
        </span>
      </button>
      {open && (
        <div
          style={{
            padding: "12px 13px 13px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            flexDirection: "column",
            gap: 9,
          }}
        >
          <DetailRow label="Policy" value={citation.policy_name} />
          <DetailRow
            label="Insurer"
            value={citation.insurer_slug.replace(/-/g, " ")}
          />
          {pageRange && <DetailRow label="Cited" value={pageRange} />}
          {hasSource ? (
            <a
              href={citation.source_url}
              target="_blank"
              rel="noopener"
              className="hover:underline"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                fontSize: 11.5,
                fontWeight: 600,
                color: "var(--primary)",
                marginTop: 2,
              }}
            >
              Open policy PDF →
            </a>
          ) : (
            <span
              style={{
                fontSize: 11,
                color: "var(--muted-foreground)",
                fontStyle: "italic",
              }}
            >
              No source PDF link available
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: "flex", gap: 10 }}>
      <span
        style={{
          fontSize: 9.5,
          textTransform: "uppercase",
          letterSpacing: "0.07em",
          color: "var(--muted-foreground)",
          fontWeight: 600,
          width: 60,
          flexShrink: 0,
          paddingTop: 1,
        }}
      >
        {label}
      </span>
      <span
        className="break-words"
        style={{
          fontSize: 12,
          color: "var(--foreground)",
        }}
      >
        {value}
      </span>
    </div>
  );
}
