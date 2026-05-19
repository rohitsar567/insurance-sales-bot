"use client";

/**
 * HelpTip — a shared "?" affordance placed next to every policy-calculation
 * control. Hover (desktop) or tap (touch; this app is mobile-first) reveals a
 * concise, plain-language explanation of what the control does and how it
 * moves the premium. ONE component + ONE copy map is reused across all three
 * surfaces (PolicyPremiumWidget, the ProfileBuilder cover/cost sliders, and
 * the PerPolicyPremiumEstimator) so the wording is identical everywhere.
 *
 * Body-root portal rule: all three surfaces sit inside `overflow` scroll
 * containers (compare modal, profile drawer, scorecard panel) that clip an
 * absolutely-positioned popover. This is the repo's FIRST portal tooltip:
 * the popover is rendered via ReactDOM.createPortal into document.body and
 * positioned with `position:fixed` from the badge's getBoundingClientRect(),
 * which escapes every clipping ancestor. z-index sits above modals (modals
 * use z-[60]; we use 1000).
 *
 * Copy is strictly qualitative — no digits, %, or ₹ anywhere — per the
 * no-invented-numbers rule. It explains direction (raises / lowers premium),
 * never magnitude.
 *
 * SSR-safe: the portal only renders after a mounted state flips true (the
 * same pattern useIsTouch uses), so the static export emits nothing.
 */

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import ReactDOM from "react-dom";
import { useIsTouch } from "@/lib/useIsTouch";

export type CalcHelpId =
  | "sum_insured"
  | "tenure"
  | "deductible"
  | "copay"
  | "existing_cover"
  | "budget";

export const CALC_HELP_COPY: Record<CalcHelpId, { title: string; body: string }> = {
  sum_insured: {
    title: "Sum insured",
    body:
      "The maximum the insurer will pay for your covered hospital bills in one policy year. A higher sum insured raises the premium; a lower one reduces it.",
  },
  tenure: {
    title: "Policy tenure",
    body:
      "How many years of cover you buy and pay for upfront. Choosing a multi-year tenure usually lowers the per-year premium versus renewing yearly.",
  },
  deductible: {
    title: "Voluntary deductible",
    body:
      "An amount you agree to pay yourself on a claim before the insurer pays the rest. Choosing a deductible lowers your premium; zero means no deductible. Only offered on plans that support it — it won't appear for plans that don't.",
  },
  copay: {
    title: "Co-pay (your share)",
    body:
      "The share of every approved hospital bill you pay yourself, with the insurer paying the rest. A higher co-pay lowers your premium but increases what you pay at claim time.",
  },
  existing_cover: {
    title: "Existing cover you hold",
    body:
      "Health insurance you already have. We use it to suggest a top-up instead of a fresh base policy; it doesn't change this premium directly but shapes which plans are recommended.",
  },
  budget: {
    title: "Annual premium budget",
    body:
      "What you're willing to pay per year. This doesn't change any premium — it's compared against the estimate to flag plans that fit or exceed your budget.",
  },
};

// Keep the popover inside the viewport with a small breathing margin.
const VIEWPORT_MARGIN = 8;
// Matches the `.helptip-pop` max width clamp in globals.css.
const POP_MAX_WIDTH = 320;

export default function HelpTip({ id }: { id: CalcHelpId }) {
  const entry = CALC_HELP_COPY[id];

  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number; placeAbove: boolean }>({
    top: 0,
    left: 0,
    placeAbove: true,
  });

  const isTouch = useIsTouch();
  const badgeRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);
  const rid = useId();
  const popId = `helptip-pop-${rid}`;

  // SSR-safe: mirror useIsTouch's mount pattern so the static export emits
  // nothing and the portal only attaches on the client. The setState is
  // routed through a closure (same shape as useIsTouch's `update`) so it
  // synchronises React with an external fact (we are now on the client)
  // rather than being a direct in-effect state write.
  useEffect(() => {
    const markMounted = () => setMounted(true);
    markMounted();
  }, []);

  // Position the fixed popover from the badge's viewport rect. Prefer above;
  // flip below if there's no room; clamp horizontally to the viewport.
  const reposition = () => {
    const badge = badgeRef.current;
    if (typeof window === "undefined" || !badge) return;
    const r = badge.getBoundingClientRect();
    const popH = popRef.current?.offsetHeight ?? 96;
    const popW = Math.min(
      popRef.current?.offsetWidth ?? POP_MAX_WIDTH,
      POP_MAX_WIDTH,
    );

    const spaceAbove = r.top;
    const placeAbove = spaceAbove >= popH + 12;

    const top = placeAbove ? r.top - popH - 8 : r.bottom + 8;

    const badgeCenter = r.left + r.width / 2;
    let left = badgeCenter - popW / 2;
    const maxLeft = window.innerWidth - popW - VIEWPORT_MARGIN;
    if (left < VIEWPORT_MARGIN) left = VIEWPORT_MARGIN;
    if (left > maxLeft) left = Math.max(VIEWPORT_MARGIN, maxLeft);

    setCoords({ top, left, placeAbove });
  };

  // Reposition synchronously once the popover is in the DOM (before paint) so
  // it never flashes at the wrong spot, and keep it pinned on resize.
  useLayoutEffect(() => {
    if (!open) return;
    reposition();
    const onResize = () => reposition();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open]);

  // Touch/keyboard dismissal: outside pointerdown, Esc, and scroll all close.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const t = e.target as Node | null;
      if (
        t &&
        (badgeRef.current?.contains(t) || popRef.current?.contains(t))
      ) {
        return;
      }
      setOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        badgeRef.current?.focus();
      }
    };
    const onScroll = () => setOpen(false);
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  if (!entry) return null;

  const { title, body } = entry;

  // Desktop: hover/focus opens, leaving/blur closes. Touch: tap toggles
  // (dismissal handled by the document listeners above).
  const hoverHandlers = isTouch
    ? {}
    : {
        onMouseEnter: () => setOpen(true),
        onMouseLeave: () => setOpen(false),
        onFocus: () => setOpen(true),
        onBlur: () => setOpen(false),
      };

  return (
    <span style={{ display: "inline-flex", verticalAlign: "middle" }}>
      <button
        ref={badgeRef}
        type="button"
        className="helptip-badge"
        aria-label={"Explain: " + title}
        aria-describedby={open ? popId : undefined}
        aria-expanded={open}
        {...hoverHandlers}
        onClick={(e) => {
          // Don't let the badge toggle the surrounding <label> control.
          e.preventDefault();
          e.stopPropagation();
          if (isTouch) setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setOpen((v) => !v);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      >
        ?
      </button>

      {mounted && open
        ? ReactDOM.createPortal(
            <div
              ref={popRef}
              role="tooltip"
              id={popId}
              className={
                "helptip-pop" + (coords.placeAbove ? " helptip-pop--above" : " helptip-pop--below")
              }
              style={{
                position: "fixed",
                top: coords.top,
                left: coords.left,
                zIndex: 1000,
              }}
            >
              <span className="helptip-title">{title}</span>
              <span className="helptip-body">{body}</span>
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
