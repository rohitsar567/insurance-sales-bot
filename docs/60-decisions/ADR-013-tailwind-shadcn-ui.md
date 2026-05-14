# ADR-013: Tailwind CSS + shadcn/ui

**Status:** Locked
**Date:** 2026-05-13

## Context

The frontend needs to look polished in <1 day of build. Three constraints: accessibility, mobile-responsive, no time for bespoke design system.

## Decision

**Tailwind CSS for utility styling** + **shadcn/ui for primitive components** (Button, Dialog, Tabs, Form, etc.).

## Alternatives considered

| Library | Why rejected |
|---|---|
| Material UI (MUI) | Heavier component model; "Material" aesthetic doesn't fit consumer-fintech feel. |
| Chakra UI | Similar to MUI in weight; ecosystem smaller than shadcn since 2024. |
| Mantine | Good but smaller pool of community examples for chat / voice interfaces. |
| Plain CSS | Too slow at 1-day pace; reinvents wheels. |

## Why shadcn/ui specifically

- Components are **copy-pasted** into the repo, not imported as a dependency — full control over styling and behavior.
- Built on Radix primitives → accessibility built in (focus management, ARIA).
- Tailwind classes mean theming is one CSS variable change.
- Dark mode comes free.
- Active community → lots of examples for chat interfaces, marketplaces, settings panels.

## Consequences

**Positive:**

- Mobile-first responsive design in hours, not days.
- Accessibility passes (keyboard navigation, screen reader labels) by default.
- Theming via CSS variables — single source of truth in `frontend/src/app/globals.css`.
- Custom design tokens (`--primary`, `--secondary`, etc.) reused across all components.

**Negative:**

- Tailwind class soup in JSX can be visually noisy.
- `cn()` utility (clsx + tailwind-merge) needed for conditional classes.

**Mitigations:**

- shadcn components encapsulate their own class logic; page-level components stay readable.
- Component-level styles extracted to small CSS modules when class lists exceed ~5 utilities.

## Revisit at scale

Same stack. Production-pattern in 2026; no compelling reason to switch.
