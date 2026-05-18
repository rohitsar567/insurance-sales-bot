"use client";

// PolicyCompareModal — side-by-side comparison modal opened from the chat
// reply. EVERY column renders the SAME ordered sections so the columns are
// row-aligned and equal-height (a missing field renders a graceful "—",
// never a collapsed/absent block):
//   1. Header   — insurer logo + policy name + a top-right "Policy PDF"
//                 link (mirrors page.tsx's PdfIcon "Open policy PDF").
//   2. Highlights — full marketplace-parity coverage grid (sum insured,
//                 PED wait, network, cashless, room rent, co-pay, NCB,
//                 restoration/AYUSH, pre/post-hosp, waiting periods).
//   3. Premium  — pluggable widget (B2) via props.renderPremiumFor.
//   4. Scorecard — pluggable widget (B3) via props.renderScorecardFor.
//   5. Policy details — expandable section with the source URL.
//
// On < 640px the columns become a single horizontal CSS scroll-snap
// carousel (one card per viewport, swipeable, no page-level horizontal
// overflow) with full-size tappable targets; ≥ 640px they are an
// equal-fraction grid.
//
// ── Visual system ─────────────────────────────────────────────────────
// Re-grounded on the premium editorial-fintech landing (app/globals.css):
// Fraunces display serif via `.font-display` for the modal title, the teal
// --primary token, color-mix soft depth shadows, the kicker-pill pattern
// for section eyebrows, and tight one-line fact bullets. Everything reads
// from CSS variables so the modal tracks the page's light/dark scheme. The
// grade chip uses the same calm A→F ramp as PolicyScorecardWidget.

import { useEffect, useState, type ReactNode, type CSSProperties } from "react";
import {
  Citation,
  MarketplacePolicy,
  ScorecardResponse,
  getScorecard,
  BACKEND_URL,
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

// #102 — official insurer domains for the DDG icon fallback (the old
// hotlink map was dead). Kept in sync with page.tsx INSURER_DOMAIN.
const INSURER_DOMAIN: Record<string, string> = {
  "acko": "acko.com",
  "aditya-birla": "adityabirlacapital.com",
  "bajaj-allianz": "bajajallianz.com",
  "care-health": "careinsurance.com",
  "cholamandalam": "cholainsurance.com",
  "go-digit": "godigit.com",
  "hdfc-ergo": "hdfcergo.com",
  "icici-lombard": "icicilombard.com",
  "iffco-tokio": "iffcotokio.co.in",
  "indusind-general": "indusindinsurance.com",
  "manipalcigna": "manipalcigna.com",
  "national-insurance": "nationalinsurance.nic.co.in",
  "new-india": "newindia.co.in",
  "niva-bupa": "nivabupa.com",
  "oriental-insurance": "orientalinsurance.org.in",
  "reliance-general": "reliancegeneral.co.in",
  "royal-sundaram": "royalsundaram.in",
  "sbi-general": "sbigeneral.in",
  "star-health": "starhealth.in",
  "tata-aig": "tataaig.com",
};

function insurerInitials(name: string): string {
  return name.split(/[\s-]+/).map((w) => w[0]).filter(Boolean).join("").slice(0, 2).toUpperCase();
}

// #102/#103 — same reliable, unboxed logo as page.tsx: locally-hosted real
// PNG → DuckDuckGo icon → colored letter avatar. No white box; the mark
// sits directly on the surface.
function InsurerLogo({ slug, name, size = 40 }: { slug: string; name: string; size?: number }) {
  const [stage, setStage] = useState(0);
  const color = INSURER_COLOR[slug] || "bg-slate-500";
  const domain = INSURER_DOMAIN[slug];
  const sources = [
    `/insurer-logos/${slug}.png`,
    domain ? `https://icons.duckduckgo.com/ip3/${domain}.ico` : "",
  ].filter(Boolean);
  if (stage >= sources.length) {
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
      className="flex items-center justify-center shrink-0"
      style={{ width: size, height: size }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={sources[stage]}
        alt={name}
        onError={() => setStage((s) => s + 1)}
        className="w-full h-full object-contain"
      />
    </div>
  );
}

// Inline PDF glyph — forked verbatim from page.tsx's PdfIcon so the
// compare-column source link reads identically to the one on the chat
// citation chips ("Open policy PDF").
function PdfIcon({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      aria-hidden
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <text
        x="7"
        y="18"
        fontSize="6"
        fill="currentColor"
        stroke="none"
        fontWeight="bold"
      >
        PDF
      </text>
    </svg>
  );
}

// Resolve the best policy-PDF URL for a column. Prefers the marketplace
// row's curated `source_pdf_url`, then the citation's `source_url`, then a
// scoped Google fallback (same precedence/UX as the marketplace detail
// modal in page.tsx). Always returns *something* so every column gets a
// uniform top-right link — `isReal` flags whether it's a direct PDF.
function resolvePdfHref(
  citation: Citation,
  marketplacePolicy?: MarketplacePolicy,
): { href: string; isReal: boolean } {
  // #87 — accept both a public origin URL and the backend-served local
  // corpus path (/api/policy-pdf/...), resolving the latter to an absolute
  // BACKEND_URL so every column links the real document.
  const curated = marketplacePolicy?.source_pdf_url;
  if (curated && (curated.startsWith("http") || curated.startsWith("/api/"))) {
    return {
      href: curated.startsWith("/api/") ? `${BACKEND_URL}${curated}` : curated,
      isReal: true,
    };
  }
  if (citation.source_url && citation.source_url.startsWith("http")) {
    return { href: citation.source_url, isReal: true };
  }
  let host = "www.google.com";
  try {
    if (marketplacePolicy?.insurer_home_url) {
      host = new URL(marketplacePolicy.insurer_home_url).hostname;
    }
  } catch {
    /* keep google host */
  }
  const q = encodeURIComponent(
    `${citation.policy_name} policy wording PDF`,
  );
  return {
    href: `https://www.google.com/search?q=site:${host}+${q}`,
    isReal: false,
  };
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
  // Resolve by cited policy_id; policyName is a canonical fallback so a
  // doctype/variant/alias id (e.g. recommended `hdfc-ergo__optima-restore`
  // vs marketplace card `..__brochure`) still resolves to its card —
  // otherwise the card silently degrades (no Hospitals link, SI falls back
  // to "As per policy schedule", fewer fields → asymmetric). #57/#58/#59.
  policyDataFor?: (
    policyId: string,
    policyName?: string,
  ) => MarketplacePolicy | undefined;
  // Hook for "Open in full marketplace" — defaults to no-op + closes modal.
  onOpenMarketplace?: () => void;
};

export default function PolicyCompareModal({
  policies,
  onClose,
  renderPremiumFor,
  renderScorecardFor,
  profile: _profile,
  policyDataFor,
  onOpenMarketplace,
}: PolicyCompareModalProps) {
  const uniq = uniquePolicies(policies).slice(0, 3);
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

        {/* Body.
            • < 640px: a horizontal scroll-snap carousel. Each card is
              ~86vw so a sliver of the next card peeks (affordance that it
              swipes), `overflow-x-auto` is scoped to THIS strip so the page
              never gets a horizontal scrollbar, and `touch-pan-x` keeps
              vertical scroll of the modal working.
            • ≥ 640px: an equal-fraction grid. `align-items: stretch` (grid
              default) + each column being a flex-col makes every column the
              SAME height regardless of content. */}
        <div className="p-4 sm:p-6">
          {n > 1 && (
            <div
              className="sm:hidden mb-3 flex items-center gap-1.5"
              style={{
                fontSize: 11,
                color: "var(--muted-foreground)",
              }}
              aria-hidden
            >
              <span>Swipe to compare all {n}</span>
              <span style={{ color: "var(--primary)" }}>→</span>
            </div>
          )}
          <div
            className={
              n > 1
                ? "flex sm:grid gap-4 sm:gap-5 overflow-x-auto sm:overflow-visible snap-x snap-mandatory sm:snap-none -mx-4 px-4 sm:mx-0 sm:px-0 scrollbar-thin touch-pan-x"
                : "grid grid-cols-1"
            }
            style={
              n > 1
                ? {
                    gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))`,
                    WebkitOverflowScrolling: "touch",
                  }
                : undefined
            }
          >
            {uniq.map((c) => (
              <div
                key={c.policy_id}
                className={
                  n > 1
                    ? "snap-center shrink-0 sm:shrink basis-[86%] sm:basis-auto min-w-0"
                    : "min-w-0"
                }
              >
                <CompareColumn
                  citation={c}
                  premiumSlot={renderPremiumFor?.(c.policy_id, c.policy_name)}
                  scorecardSlot={renderScorecardFor?.(
                    c.policy_id,
                    c.policy_name,
                  )}
                  marketplacePolicy={policyDataFor?.(c.policy_id, c.policy_name)}
                  profile={_profile as SnapProfile}
                />
              </div>
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
  profile,
}: {
  citation: Citation;
  premiumSlot?: ReactNode;
  scorecardSlot?: ReactNode;
  marketplacePolicy?: MarketplacePolicy;
  profile?: SnapProfile;
}) {
  // Prefer the canonical insurer name from the marketplace row when we
  // have it (e.g. "HDFC ERGO General Insurance" vs the slug "hdfc-ergo"
  // un-prettified). Falls back to the slug humanised.
  const insurerName =
    marketplacePolicy?.insurer_name ?? citation.insurer_slug.replace(/-/g, " ");
  const pdf = resolvePdfHref(citation, marketplacePolicy);

  // #4 — fetch the SAME scorecard the widget uses and recover structured
  // facts from its signals. PolicyHighlights then shows real coverage even
  // when the flat marketplace row is missing values for this policy.
  const [scFacts, setScFacts] = useState<ScorecardFacts>({});
  useEffect(() => {
    let alive = true;
    getScorecard(citation.policy_id)
      .then((sc) => {
        if (alive) setScFacts(parseScorecardFacts(sc));
      })
      .catch(() => {
        /* no scorecard → highlights fall back to marketplace fields only */
      });
    return () => {
      alive = false;
    };
  }, [citation.policy_id]);
  return (
    <div
      // h-full + flex-col makes every column the SAME height in the desktop
      // grid (grid stretches each cell; the card fills it). Sections inside
      // are uniform across columns so rows line up.
      className="flex flex-col gap-4 min-w-0 h-full"
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: 18,
        padding: 18,
        boxShadow:
          "0 1px 2px color-mix(in srgb, var(--foreground) 4%, transparent), 0 18px 44px -34px color-mix(in srgb, var(--foreground) 30%, transparent)",
      }}
    >
      {/* Header — logo + insurer + policy name, with a top-right PDF link */}
      <div
        className="flex items-start gap-3 pb-4"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
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
          {marketplacePolicy?.aliases &&
            marketplacePolicy.aliases.length > 0 && (
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
        {/* Top-right policy-PDF link — uniform on every column. ~36px tall
            so it's a comfortable tap target on mobile. */}
        <a
          href={pdf.href}
          target="_blank"
          rel="noopener"
          className="shrink-0 hover:opacity-90 active:opacity-80"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 5,
            fontSize: 11,
            fontWeight: 600,
            lineHeight: 1,
            color: pdf.isReal ? "#fff" : "var(--primary)",
            background: pdf.isReal
              ? "var(--primary)"
              : "color-mix(in srgb, var(--primary) 9%, var(--card))",
            border: pdf.isReal
              ? "1px solid var(--primary)"
              : "1px solid color-mix(in srgb, var(--primary) 30%, var(--border))",
            borderRadius: 999,
            padding: "8px 12px",
            textDecoration: "none",
            whiteSpace: "nowrap",
          }}
          title={
            pdf.isReal
              ? "Open the source policy PDF"
              : "Search the insurer's site for the policy PDF (no direct link indexed yet)"
          }
          aria-label={
            pdf.isReal ? "Open policy PDF" : "Find policy PDF"
          }
        >
          <PdfIcon size={13} />
          {pdf.isReal ? "Policy PDF" : "Find PDF"}
        </a>
        {/* #86 — insurer's official network hospital list, beside the PDF */}
        {marketplacePolicy?.network_list_url && (
          <a
            href={marketplacePolicy.network_list_url}
            target="_blank"
            rel="noopener"
            className="shrink-0 hover:opacity-90 active:opacity-80"
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 5,
              fontSize: 11,
              fontWeight: 600,
              lineHeight: 1,
              color: "var(--primary)",
              background: "color-mix(in srgb, var(--primary) 9%, var(--card))",
              border: "1px solid color-mix(in srgb, var(--primary) 30%, var(--border))",
              borderRadius: 999,
              padding: "8px 12px",
              textDecoration: "none",
              whiteSpace: "nowrap",
            }}
            title={`Open ${marketplacePolicy.insurer_name}'s official ${marketplacePolicy.network_list_is_pdf ? "network hospital list (PDF)" : "cashless hospital locator"}`}
          >
            Hospitals list ↗
          </a>
        )}
      </div>

      {/* POLICY SNAPSHOT — the shared decision-ordered lens (#75 + #64),
          identical to the marketplace detail modal. Always rendered so
          every column carries the SAME section in the SAME position. */}
      <PolicyHighlights
        policy={marketplacePolicy}
        facts={scFacts}
        profile={profile}
      />

      {/* FIT SCORECARD slot (B3). Order is Details → Score → Pricing to match
          the marketplace PolicyDetailModal exactly (one consistent reading
          order everywhere a policy is shown). */}
      <Section title="Your fit scorecard">
        {scorecardSlot ?? <ScorecardFallback policyId={citation.policy_id} />}
      </Section>

      {/* PREMIUM ESTIMATE slot (B2). Render the slot directly — the parent's
          PolicyPremiumWidget already owns its own bordered card chrome, so
          double-wrapping it produces nested boxes. Only fall back to a
          placeholder when the parent didn't wire renderPremiumFor at all. */}
      <Section title="Premium estimate">
        {premiumSlot ?? <PlaceholderWidget label="Premium calculator coming soon" />}
      </Section>

      {/* POLICY DETAILS expandable */}
      <PolicyDetails citation={citation} policy={marketplacePolicy} />
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────
// Scorecard-derived facts. The scorecard the widget renders is computed from
// the SAME canonical coverage data the highlights grid wants — it just
// exposes it as human-readable "signals" (e.g. "− 20% copayment", "540
// day-care procedures", "14,000+ network hospitals", "− 36mo PED waiting").
// When the flat marketplace fields are missing for a policy but the
// scorecard clearly HAS the detail, we recover the structured values from
// those signals so the highlights / "what this covers" grids show the real
// numbers instead of a wall of "—". This is the single shared data source
// the task requires: the grids read what the scorecard read.
// ────────────────────────────────────────────────────────────────────────
export type ScorecardFacts = {
  copaymentPct?: number;
  pedWaitingMonths?: number;
  networkHospitals?: number;
  cashless?: boolean;
  ayush?: boolean;
  dayCareCount?: number;
  maternity?: boolean;
  noRoomRentCap?: boolean;
  roomRentCapText?: string;
  csrPct?: number;
  maxEntryAge?: number;
};

export function parseScorecardFacts(
  sc?: ScorecardResponse | null,
): ScorecardFacts {
  const f: ScorecardFacts = {};
  if (!sc || !sc.sub_scores) return f;
  const all: string[] = [];
  for (const s of sc.sub_scores) for (const sig of s.signals || []) all.push(sig);

  for (const raw of all) {
    const sig = raw.trim();
    const low = sig.toLowerCase();

    // Copayment — "− 20% copayment" / "0% copayment"
    let m = sig.match(/(\d+(?:\.\d+)?)%\s*copay/i);
    if (m) f.copaymentPct = parseFloat(m[1]);
    else if (/0% copayment/i.test(sig)) f.copaymentPct = 0;

    // PED waiting — "− 36mo PED waiting" / "12mo PED waiting (short)"
    m = sig.match(/(\d+)\s*mo\s*PED\s*waiting/i);
    if (m) f.pedWaitingMonths = parseInt(m[1], 10);

    // Network hospitals — "14,000+ network hospitals" / "only 1500 network hospitals"
    m = sig.match(/([\d,]+)\+?\s*network hospitals/i);
    if (m) f.networkHospitals = parseInt(m[1].replace(/,/g, ""), 10);

    // Cashless
    if (/cashless supported/i.test(low)) f.cashless = true;
    else if (/no cashless/i.test(low)) f.cashless = false;

    // AYUSH
    if (/ayush covered/i.test(low)) f.ayush = true;
    else if (/no ayush/i.test(low)) f.ayush = false;

    // Day-care — "540 day-care procedures" / "only 80 day-care procedures"
    m = sig.match(/(\d+)\s*day-care procedures/i);
    if (m) f.dayCareCount = parseInt(m[1], 10);

    // Maternity
    if (/maternity covered/i.test(low)) f.maternity = true;

    // Room rent
    if (/no room rent cap/i.test(low)) f.noRoomRentCap = true;
    else {
      const rr = sig.match(/room rent capped:\s*(.+)$/i);
      if (rr) f.roomRentCapText = rr[1].trim();
    }

    // CSR — "82.3% CSR" / "95.0% CSR (IRDAI 2023-24)"
    m = sig.match(/(\d+(?:\.\d+)?)%\s*CSR/i);
    if (m) f.csrPct = parseFloat(m[1]);

    // Entry age — "entry up to 65"
    m = sig.match(/entry up to\s*(\d+)/i);
    if (m) f.maxEntryAge = parseInt(m[1], 10);
  }
  return f;
}

// Coalesce: prefer the explicit marketplace field, fall back to the
// scorecard-recovered value. Returns undefined only when BOTH are absent
// (so the caller can omit the row gracefully instead of showing "—").
function coalesceNum(
  primary: number | null | undefined,
  fallback: number | undefined,
): number | undefined {
  if (primary != null) return primary;
  if (fallback != null) return fallback;
  return undefined;
}
function coalesceBool(
  primary: boolean | null | undefined,
  fallback: boolean | undefined,
): boolean | undefined {
  if (primary != null) return primary;
  if (fallback != null) return fallback;
  return undefined;
}

// SI RATIONALISATION (D1) — Sum Insured shows as a continuous range
// "₹X – ₹Y" ONLY when the policy genuinely offers a continuous band
// (sum_insured_is_band, decided server-side from the field's own
// source_quote in backend/sum_insured.py). Otherwise list the discrete,
// source-corroborated plan tiers ("₹25 L / ₹50 L / ₹1 Cr"; >4 tiers →
// "₹{min} … ₹{max} · N plans"). "As per policy schedule" only when there
// is no corroborated SI at all — never a synthesized floor/ceiling.
export function fmtSumInsured(p?: MarketplacePolicy): string {
  const f = (v: number) =>
    v >= 10_000_000 ? `${+(v / 10_000_000).toFixed(1)} Cr` : `${+(v / 100_000).toFixed(1)} L`;
  const tiers = (p?.sum_insured_tiers && p.sum_insured_tiers.length
    ? p.sum_insured_tiers
    : p?.sum_insured_options) || [];
  const mn = p?.sum_insured_min ?? (tiers.length ? Math.min(...tiers) : null);
  const mx = p?.sum_insured_max ?? (tiers.length ? Math.max(...tiers) : null);
  if (!tiers.length || mn == null || mx == null) return "As per policy schedule";
  // Genuine continuous band → single range.
  if (p?.sum_insured_is_band && mn !== mx) return `₹${f(mn)} – ₹${f(mx)}`;
  // Discrete tiers.
  const uniq = Array.from(new Set(tiers)).sort((a, b) => a - b);
  if (uniq.length === 1) return `₹${f(uniq[0])}`;
  if (uniq.length > 4) return `₹${f(uniq[0])} … ₹${f(uniq[uniq.length - 1])} · ${uniq.length} plans`;
  return uniq.map((v) => `₹${f(v)}`).join(" / ");
}

// ────────────────────────────────────────────────────────────────────────
// POLICY SNAPSHOT (#75 + #64) — the single shared decision lens used by
// BOTH the marketplace PolicyDetailModal (page.tsx) and the in-chat compare
// card (PolicyHighlights). It answers, in plain words and decision order:
//
//   1. WHAT YOU GET            — cover, no-claim bonus, cashless reach
//   2. WHO QUALIFIES & WHEN    — entry/renewal, the waits before you claim,
//      COVER STARTS              and how reliably the insurer actually pays
//   3. YOUR SHARE & THE LIMITS — your out-of-pocket and the caps
//
// CONDITIONAL facts (maternity / AYUSH / day-care) are never headline —
// they live in a "Situational coverage" disclosure and only auto-surface
// (with a "for you" tag + the panel pre-opened) when the user's profile
// makes them relevant (e.g. maternity for a couple/family profile).
//
// Sum Insured is rendered via the existing fmtSumInsured (a RANGE / honest
// "As per policy schedule", never a synthesized floor/ceiling). Its deeper
// data semantics are tracked in the separate data exercise — NOT here.
//
// Rows whose value is genuinely absent are OMITTED (no "—" wall); the
// three group shells always render so the structure is identical across
// every policy and every surface.
// ────────────────────────────────────────────────────────────────────────
export type SnapRow = { label: string; value: string; relevant?: boolean; term?: GlossaryKey };

// #64/#65/#98 — ONE canonical plain-language glossary, used identically by
// the snapshot rows AND the marketplace card tiles via <GlossaryTip>. Copy
// is deliberately layperson-simple ("explain it to someone who knows
// nothing about insurance"). Hover/focus only — never click.
export type GlossaryKey =
  | "cover" | "ncb" | "cashless" | "entry" | "initwait" | "ped"
  | "csr" | "copay" | "room" | "maternity" | "ayush" | "daycare";
export const GLOSSARY_TIPS: Record<GlossaryKey, { title: string; body: string }> = {
  cover: { title: "Cover amount (sum insured)", body: "The most this policy will pay for your hospital bills in one year. ₹10 L means up to ₹10 lakh of covered treatment per year." },
  ncb: { title: "No-claim bonus", body: "If you make no claim in a year, your cover increases for free the next year — your premium does not go up for it." },
  cashless: { title: "Cashless treatment", body: "At a network hospital you don't pay and wait for a refund — the insurer settles the bill directly. Outside the network you pay first, then claim it back." },
  entry: { title: "Who can buy + renew", body: "The age at which you can first take this policy, and whether you can keep renewing it for the rest of your life." },
  initwait: { title: "Wait before any claim", body: "A short period right after you buy when only accident claims are paid. Claims for normal illness start once this is over." },
  ped: { title: "Wait for a pre-existing condition", body: "If you already have an illness (diabetes, BP, thyroid, anything ongoing) when you buy, claims for THAT illness are only paid after this waiting period." },
  csr: { title: "Claims actually paid", body: "Out of every 100 claims people made to this insurer, how many they actually paid (official IRDAI data). Higher is better." },
  copay: { title: "Mandatory co-pay", body: "A fixed share of every hospital bill you must pay yourself, always. 'None' means the insurer cannot force you to share any bill." },
  room: { title: "Hospital room category", body: "The room type the policy will pay for. If it's capped and you take a costlier room, you pay the difference — and sometimes a bigger share of the whole bill." },
  maternity: { title: "Maternity & newborn", body: "Whether childbirth and newborn-baby expenses are covered, and how long you must wait before you can claim maternity." },
  ayush: { title: "AYUSH treatment", body: "Treatment under Ayurveda, Yoga, Unani, Siddha or Homeopathy at a recognised hospital — covered or not." },
  daycare: { title: "Day-care procedures", body: "Treatments that need hospital admission but finish in under 24 hours (e.g. cataract, dialysis, chemotherapy) — how many this policy covers." },
};

// The ONE explainer affordance used everywhere (snapshot rows + card
// tiles). Hover or keyboard-focus shows it; moving away hides it. No
// click, no close button. Width-constrained + above the badge so it never
// spills across cards. Styling via .gtip-* in globals.css to match the
// site's editorial system.
export function GlossaryTip({ term }: { term?: GlossaryKey }) {
  if (!term || !GLOSSARY_TIPS[term]) return null;
  const { title, body } = GLOSSARY_TIPS[term];
  return (
    <span className="gtip">
      <span tabIndex={0} role="img" aria-label={`Explain: ${title}`} className="gtip-badge">?</span>
      <span role="tooltip" className="gtip-pop">
        <span className="gtip-title">{title}</span>
        <span className="gtip-body">{body}</span>
      </span>
    </span>
  );
}
export type SnapGroup = {
  key: "get" | "eligible" | "limits";
  title: string;
  sub: string;
  rows: SnapRow[];
};
export type SnapProfile = {
  dependents?: string | null;
  primary_goal?: string | null;
  age?: number | null;
  health_conditions?: string[] | null;
  // #76 — the customer's stated/selected cover. When a plan doesn't
  // publish fixed SI tiers we price at THIS and must show it, not the
  // dismissive "As per policy schedule" (which looks like their input
  // was ignored).
  desired_sum_insured_inr?: number | null;
} | null;

export function buildSnapshot(
  policy: MarketplacePolicy | undefined,
  facts: ScorecardFacts,
  profile?: SnapProfile,
): { groups: SnapGroup[]; situational: SnapRow[]; anyRelevant: boolean } {
  const cPed = coalesceNum(
    policy?.pre_existing_disease_waiting_months,
    facts.pedWaitingMonths,
  );
  const cCopay = coalesceNum(policy?.copayment_pct, facts.copaymentPct);
  // #86 — prefer the sourced insurer-level official count over the
  // web-backfilled per-policy figure. #88 — ONLY the insurer's officially
  // published total is ever shown as a number; when the insurer publishes
  // none we say "cashless network — see official list" (the link sits in
  // the panel header) rather than assert the unsourced backfilled figure.
  const cNetwork = policy?.network_count_official ?? null;
  const cCashless = coalesceBool(
    policy?.cashless_treatment_supported,
    facts.cashless,
  );
  const cAyush = coalesceBool(policy?.ayush_coverage, facts.ayush);
  const cMaternity = coalesceBool(policy?.maternity_coverage, facts.maternity);
  const initWait = policy?.initial_waiting_period_days ?? null;
  const ncb = policy?.no_claim_bonus_pct ?? null;
  const csr = facts.csrPct ?? null;
  const dayCare = facts.dayCareCount ?? null;
  const minEntry = policy?.min_entry_age ?? null;
  const maxEntry = policy?.max_entry_age ?? facts.maxEntryAge ?? null;
  const roomRent =
    (policy?.room_rent_capping && policy.room_rent_capping.trim()) ||
    (facts.noRoomRentCap
      ? "No room rent cap"
      : facts.roomRentCapText || "");

  const fmtNet = (nh: number) =>
    nh >= 1000 ? `${Math.round(nh / 1000)}K+` : `${nh}`;
  const push = (
    arr: SnapRow[],
    label: string,
    value: string | null,
    term?: GlossaryKey,
  ) => {
    if (value != null && value !== "") arr.push({ label, value, term });
  };

  // 1 — WHAT YOU GET
  const get: SnapRow[] = [];
  // #76 — when the plan publishes no fixed SI ("As per policy schedule")
  // BUT the customer stated/selected a cover, the premium IS priced at
  // their cover — so show it, with a clear label, instead of the
  // dismissive placeholder that reads as if their input was discarded.
  let _coverVal = fmtSumInsured(policy);
  const _custSI = profile?.desired_sum_insured_inr;
  if (_coverVal === "As per policy schedule" && _custSI && _custSI > 0) {
    const _c =
      _custSI >= 10_000_000
        ? `${+(_custSI / 10_000_000).toFixed(1)} Cr`
        : `${+(_custSI / 100_000).toFixed(1)} L`;
    _coverVal = `₹${_c} (your chosen cover)`;
  }
  get.push({ label: "Cover amount", value: _coverVal, term: "cover" });
  push(
    get,
    "No-claim bonus",
    ncb == null
      ? null
      : ncb === 0
        ? "None"
        : `+${ncb}% cover for each claim-free year`,
    "ncb",
  );
  push(
    get,
    "Cashless treatment",
    cCashless === true
      ? cNetwork && cNetwork > 0
        ? `Yes · ${fmtNet(cNetwork)}+ network hospitals`
        : "Yes · cashless network — see official list"
      : cCashless === false
        ? "Not available"
        : null,
    "cashless",
  );

  // 2 — WHO QUALIFIES & WHEN COVER STARTS
  const eligible: SnapRow[] = [];
  const minStr =
    minEntry != null
      ? minEntry >= 30 && minEntry <= 365
        ? `${minEntry} days`
        : `${minEntry} yrs`
      : null;
  const maxStr = maxEntry != null ? `${maxEntry} yrs` : null;
  const ageRange =
    minStr && maxStr ? `${minStr} – ${maxStr}` : minStr || maxStr || "";
  push(
    eligible,
    "Who can buy + renew",
    ageRange ? `${ageRange} · lifelong renewal` : null,
    "entry",
  );
  push(
    eligible,
    "Wait before any claim",
    initWait == null
      ? null
      : initWait === 0
        ? "None"
        : `${initWait} days from start`,
    "initwait",
  );
  push(
    eligible,
    "Wait if you already had a condition",
    cPed == null ? null : cPed === 0 ? "None" : `${cPed} months`,
    "ped",
  );
  push(
    eligible,
    "Claims actually paid",
    csr == null ? null : `${csr}% of claims settled`,
    "csr",
  );

  // 3 — YOUR SHARE & THE LIMITS
  const limits: SnapRow[] = [];
  // #84 — the only decision-critical co-pay question is whether the policy
  // FORCES a share on every claim. The exact % the user opts into is set
  // later on the pricing slider; what matters here is mandatory-or-not (a
  // hard minimum is a real consideration). So: binary first, figure second.
  // #30 — both rows render on EVERY card with an explicit "Not specified"
  // fallback (never omitted), so this section is consistent across
  // policies instead of showing whichever single field happened to be
  // non-null (which read as random to the user).
  push(
    limits,
    "Mandatory co-pay",
    cCopay == null
      ? "Not specified"
      : cCopay === 0
        ? "None — no forced co-pay"
        : `Yes · ${cCopay}% minimum on every claim`,
    "copay",
  );
  push(
    limits,
    "Hospital room category",
    roomRent ? roomRent : "Not specified",
    "room",
  );

  // CONDITIONAL — profile-aware, never headline
  const ctx = `${(profile?.dependents || "").toLowerCase()} ${(
    profile?.primary_goal || ""
  ).toLowerCase()}`;
  const familyCtx =
    /spouse|wife|husband|partner|couple|family|kid|child|son|daughter|matern|newborn|pregnan/.test(
      ctx,
    );
  const situational: SnapRow[] = [];
  if (cMaternity != null)
    situational.push({
      label: "Maternity & newborn",
      value: cMaternity
        ? policy?.maternity_waiting_months
          ? `Covered after ${policy.maternity_waiting_months}-month wait`
          : "Covered"
        : "Not covered",
      relevant: familyCtx,
      term: "maternity",
    });
  if (cAyush != null)
    situational.push({
      label: "AYUSH (Ayurveda, Yoga, Unani, Siddha, Homeopathy)",
      value: cAyush ? "Covered" : "Not covered",
      term: "ayush",
    });
  if (dayCare != null)
    situational.push({
      label: "Day-care procedures",
      value: `${dayCare} covered`,
      term: "daycare",
    });

  return {
    groups: [
      {
        key: "get",
        title: "What you get",
        sub: "The cover, bonus and cashless reach this policy gives you.",
        rows: get,
      },
      {
        key: "eligible",
        title: "Who qualifies & when cover starts",
        sub: "Entry age, renewal, and the waits before you can actually claim.",
        rows: eligible,
      },
      {
        key: "limits",
        title: "Your share & the limits",
        sub: "What you pay out of pocket and the caps that apply.",
        rows: limits,
      },
    ],
    situational,
    anyRelevant: situational.some((s) => s.relevant),
  };
}

// Shared presentational layer for the snapshot. Pure CSS classes (.snap-*
// in globals.css) so the SAME elevated editorial chrome renders in the
// detail modal and the compare card — single source of truth for both the
// grouping logic AND the look.
const SNAP_ACCENT: Record<SnapGroup["key"], string> = {
  get: "var(--primary)",
  eligible: "#5b6bb5",
  limits: "#c98a2b",
};

export function SnapshotView({
  policy,
  facts,
  profile,
}: {
  policy?: MarketplacePolicy;
  facts: ScorecardFacts;
  profile?: SnapProfile;
}) {
  const { groups, situational, anyRelevant } = buildSnapshot(
    policy,
    facts,
    profile,
  );
  // Auto-open the situational disclosure once the profile resolves and a
  // conditional fact turns relevant. useState's initializer alone is stale
  // here: on first render `completeness` is still loading → anyRelevant is
  // false → the panel would stay collapsed even after the profile arrives
  // and the "Relevant to you" pill appears. The effect re-opens it when
  // relevance flips true; the user can still collapse it manually after.
  const [openSit, setOpenSit] = useState(anyRelevant);
  useEffect(() => {
    if (anyRelevant) setOpenSit(true);
  }, [anyRelevant]);
  return (
    <div className="snap-stack">
      {groups.map((g) =>
        g.rows.length === 0 ? null : (
          <div
            key={g.key}
            className="snap-group"
            style={
              { ["--snap-accent" as string]: SNAP_ACCENT[g.key] } as CSSProperties
            }
          >
            <div className="snap-head">
              <span className="snap-dot" />
              <span className="snap-title">{g.title}</span>
            </div>
            <p className="snap-sub">{g.sub}</p>
            <dl className="snap-rows">
              {g.rows.map((r, i) => (
                <div key={i} className="snap-row">
                  <dt>
                    {r.label}
                    <GlossaryTip term={r.term} />
                  </dt>
                  <dd>{r.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        ),
      )}
      {situational.length > 0 && (
        <div className="snap-group snap-group--sit">
          <button
            type="button"
            onClick={() => setOpenSit((o) => !o)}
            className="snap-sit-toggle"
            aria-expanded={openSit}
          >
            <span className="snap-head" style={{ marginBottom: 0 }}>
              <span className="snap-dot" />
              <span className="snap-title">Situational coverage</span>
            </span>
            {anyRelevant && (
              <span className="snap-rel-pill">Relevant to you</span>
            )}
            <span className="snap-chevron" data-open={openSit} aria-hidden>
              ⌄
            </span>
          </button>
          {openSit && (
            <dl className="snap-rows" style={{ marginTop: 10 }}>
              {situational.map((r, i) => (
                <div
                  key={i}
                  className="snap-row"
                  data-rel={r.relevant ? "true" : "false"}
                >
                  <dt>
                    {r.label}
                    <GlossaryTip term={r.term} />
                    {r.relevant && <span className="snap-rel-tag">for you</span>}
                  </dt>
                  <dd>{r.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}
    </div>
  );
}

// Full marketplace-parity coverage block. ALWAYS rendered (the section
// shell, the 4 headline stats AND the detailed spec list) so every column
// has the identical structure — missing values degrade to "—". When no
// marketplace row is wired at all, a thin context line explains it but the
// structure (and the citation-derived facts) still render.
// #75 + #64 — the compare card's snapshot is now the SAME decision-ordered
// editorial lens as the marketplace detail modal (one shared SnapshotView).
// Profile-aware: situational facts (maternity/AYUSH/day-care) only
// auto-surface when the user's profile makes them relevant.
function PolicyHighlights({
  policy,
  facts,
  profile,
}: {
  policy?: MarketplacePolicy;
  // Scorecard-recovered facts — the SAME data the scorecard read, so a
  // policy whose scorecard has full detail never renders an empty snapshot.
  facts: ScorecardFacts;
  profile?: SnapProfile;
}) {
  return (
    <Section title="Policy snapshot">
      <SnapshotView policy={policy} facts={facts} profile={profile} />
    </Section>
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
          aria-hidden
          style={{
            width: 5,
            height: 5,
            borderRadius: 999,
            background: "var(--primary)",
            flexShrink: 0,
          }}
        />
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            color:
              "color-mix(in srgb, var(--primary) 78%, var(--muted-foreground))",
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

function PolicyDetails({
  citation,
  policy,
}: {
  citation: Citation;
  policy?: MarketplacePolicy;
}) {
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
          {/* #66 — enrich the sparse expander with the key decision facts
              already on the resolved marketplace row (was just name +
              insurer + PDF, which the user flagged as pointless). */}
          {policy &&
            (() => {
              const rows: [string, string][] = [];
              const cov = fmtSumInsured(policy);
              if (cov) rows.push(["Cover", cov]);
              if (policy.grade)
                rows.push([
                  "Grade",
                  `${policy.grade} · ${policy.overall_score}/100`,
                ]);
              if (policy.no_claim_bonus_pct != null)
                rows.push([
                  "No-claim bonus",
                  `+${policy.no_claim_bonus_pct}% per claim-free year`,
                ]);
              if (policy.pre_existing_disease_waiting_months != null)
                rows.push([
                  "Pre-existing wait",
                  `${policy.pre_existing_disease_waiting_months} months`,
                ]);
              if (policy.initial_waiting_period_days != null)
                rows.push([
                  "Initial wait",
                  `${policy.initial_waiting_period_days} days`,
                ]);
              if (policy.room_rent_capping)
                rows.push(["Room rent", policy.room_rent_capping]);
              if (policy.copayment_pct != null)
                rows.push([
                  "Co-pay",
                  policy.copayment_pct ? `${policy.copayment_pct}%` : "None",
                ]);
              if (policy.network_count_official != null)
                rows.push([
                  "Network",
                  `${policy.network_count_official.toLocaleString(
                    "en-IN",
                  )}+ cashless hospitals`,
                ]);
              if (policy.max_entry_age != null)
                rows.push(["Max entry age", `${policy.max_entry_age} yrs`]);
              return rows.map(([l, v]) => (
                <DetailRow key={l} label={l} value={v} />
              ));
            })()}
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
