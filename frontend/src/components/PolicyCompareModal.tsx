"use client";

// PolicyCompareModal — side-by-side comparison modal opened from the chat
// reply. Renders one column per cited policy with three slots:
//   1. Header  — insurer logo + policy name + insurer + scorecard chip
//   2. Premium — pluggable widget (B2) via props.renderPremiumFor(policyId)
//   3. Scorecard — pluggable widget (B3) via props.renderScorecardFor(policyId)
//   4. Policy details — expandable section with source URL
// Visual style copied from MarketplacePanel's PolicyCard so the chat-side
// compare matches the marketplace-side compare (same fonts, borders, radii).

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
        className={`rounded-lg ${color} text-white flex items-center justify-center font-bold shrink-0`}
        style={{ width: size, height: size, fontSize: size * 0.32 }}
      >
        {insurerInitials(name)}
      </div>
    );
  }
  return (
    <div
      className="rounded-lg bg-white border border-[var(--border)] flex items-center justify-center shrink-0 overflow-hidden p-1"
      style={{ width: size, height: size }}
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

function gradeColor(grade: string): string {
  const map: Record<string, string> = {
    A: "bg-emerald-500 text-white",
    B: "bg-teal-500 text-white",
    C: "bg-amber-500 text-white",
    D: "bg-orange-500 text-white",
    F: "bg-red-500 text-white",
  };
  return map[grade] || "bg-stone-400 text-white";
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

  return (
    <div
      className="fixed inset-0 z-[70] bg-black/50 flex items-stretch sm:items-center justify-center p-0 sm:p-3 animate-fade-up"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`Compare ${n} polic${n === 1 ? "y" : "ies"}`}
    >
      <div
        className="bg-[var(--card)] sm:rounded-2xl shadow-xl w-full sm:max-w-7xl sm:w-[92vw] max-h-screen sm:max-h-[92vh] overflow-y-auto scrollbar-thin"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 bg-[var(--card)] border-b border-[var(--border)] px-5 py-4 flex items-center justify-between">
          <div>
            <h3 className="text-base font-bold">
              Compare {n} polic{n === 1 ? "y" : "ies"}
            </h3>
            <p className="text-[11px] text-[var(--muted-foreground)] mt-0.5">
              Premiums, fit scores and policy details — side-by-side.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-[var(--muted-foreground)] hover:text-[var(--foreground)] text-2xl leading-none ml-2"
            aria-label="Close comparison"
          >
            ×
          </button>
        </div>

        {/* Body: 1 col on mobile, n cols on desktop */}
        <div className="p-4 sm:p-5">
          <div
            className="grid gap-4 grid-cols-1"
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
        <div className="sticky bottom-0 bg-[var(--card)] border-t border-[var(--border)] px-5 py-3 flex items-center justify-between text-xs">
          <span className="text-[var(--muted-foreground)]">
            Comparing the policies cited in this reply. Open the full
            marketplace for filters and 30+ more options.
          </span>
          <button
            onClick={() => {
              onOpenMarketplace?.();
              onClose();
            }}
            className="font-semibold text-[var(--primary)] hover:underline"
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
    <div className="bg-[var(--card)] border border-[var(--border)] rounded-xl p-4 flex flex-col gap-4 min-w-0">
      {/* Header — logo + insurer + policy name */}
      <div className="flex items-start gap-3">
        <InsurerLogo slug={citation.insurer_slug} name={insurerName} size={40} />
        <div className="flex-1 min-w-0">
          <div className="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)] truncate">
            {insurerName}
          </div>
          <div className="font-semibold text-sm leading-tight break-words">
            {citation.policy_name}
          </div>
          {marketplacePolicy?.aliases && marketplacePolicy.aliases.length > 0 && (
            <div className="text-[11px] text-slate-500 italic mt-0.5 break-words">
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
      ? `${maxSI / 10_000_000} cr`
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
      <div className="grid grid-cols-2 gap-2 text-xs">
        <MiniStat label="Sum insured" value={siDisplay} />
        <MiniStat label="PED waiting" value={pedWait} />
        <MiniStat label="Network" value={network} />
        <MiniStat label="Cashless" value={cashless} />
      </div>
      {bullets.length > 0 && (
        <ul className="mt-2.5 space-y-1 text-[11px] text-[var(--foreground)] leading-snug">
          {bullets.slice(0, 4).map((b, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <span className="text-[var(--primary)] mt-[1px]">•</span>
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
    <div>
      <div className="text-[10px] text-[var(--muted-foreground)] uppercase tracking-wide">
        {label}
      </div>
      <div className="text-xs font-semibold">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--muted-foreground)] font-semibold mb-1.5">
        {title}
      </div>
      {children}
    </div>
  );
}

function PlaceholderWidget({ label }: { label: string }) {
  return (
    <div className="bg-[var(--muted)] border border-dashed border-[var(--border)] rounded-lg px-3 py-4 text-center text-[11px] text-[var(--muted-foreground)]">
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
  return (
    <div className="bg-[var(--muted)] border border-[var(--border)] rounded-lg p-3">
      <div className="flex items-center gap-2">
        <span
          className={`inline-flex items-center justify-center w-9 h-9 rounded-md font-bold text-sm ${gradeColor(
            sc.grade,
          )}`}
        >
          {sc.grade}
        </span>
        <div className="min-w-0">
          <div className="text-sm font-semibold leading-tight">
            {sc.overall_score}
            <span className="text-[var(--muted-foreground)] text-[10px] font-normal">
              /100
            </span>
          </div>
          <div className="text-[10px] text-[var(--muted-foreground)] truncate">
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
    <div className="border border-[var(--border)] rounded-lg bg-[var(--card)]">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-3 py-2 text-[11px] font-semibold flex items-center justify-between hover:bg-[var(--muted)] rounded-lg"
        aria-expanded={open}
      >
        <span className="uppercase tracking-wider text-[var(--muted-foreground)]">
          Policy details
        </span>
        <span className="text-[var(--muted-foreground)]">{open ? "−" : "+"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-[var(--border)] space-y-2 text-[11px]">
          <DetailRow label="Policy" value={citation.policy_name} />
          <DetailRow label="Insurer" value={citation.insurer_slug.replace(/-/g, " ")} />
          {pageRange && <DetailRow label="Cited" value={pageRange} />}
          {hasSource ? (
            <a
              href={citation.source_url}
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-1 text-[var(--primary)] hover:underline font-semibold"
            >
              Open policy PDF →
            </a>
          ) : (
            <span className="text-[var(--muted-foreground)] italic">
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
    <div className="flex gap-2">
      <span className="text-[var(--muted-foreground)] uppercase tracking-wide text-[10px] w-16 shrink-0">
        {label}
      </span>
      <span className="text-[var(--foreground)] break-words">{value}</span>
    </div>
  );
}
