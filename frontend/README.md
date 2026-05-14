# `frontend/` — Next.js 14 (App Router) UI

The chat UI, profile builder, scorecard, and admin panel. Single-page-ish — almost everything lives in `src/app/page.tsx` with `src/lib/` carrying the API client, the EN ↔ हिं i18n table, and the live-conversation hook.

For Claude / agent-specific rules (e.g. the "this is NOT the Next.js you know" note) see `AGENTS.md` in this folder — `CLAUDE.md` is an alias of it.

## Entry points

| File | Role |
| --- | --- |
| `src/app/page.tsx` | The chat surface. Hosts the `Message` component (which mounts the in-DOM `<audio>` element for TTS), the toolbar (Live toggle + push-to-talk), the tab switcher (Chat / Profile Builder / Scorecard / Admin), and every view's render. |
| `src/app/layout.tsx` | Root layout + font wiring (Geist via `next/font`). |
| `src/app/globals.css` | Tailwind v4 entrypoint + the shadcn/ui token layer ([ADR-013](../70-docs/60-decisions/ADR-013-tailwind-shadcn-ui.md)). |
| `src/lib/api.ts` | Typed API client. Generated types via `openapi-typescript` from the FastAPI OpenAPI schema ([ADR-015](../70-docs/60-decisions/ADR-015-openapi-typescript-codegen.md)). |
| `src/lib/useLiveConversation.ts` | The continuously-open-mic VAD hook that powers Live mode + barge-in. State persists in `localStorage.insurance_live_pref` ([ADR-028](../70-docs/60-decisions/ADR-028-voice-ux-single-default-mode.md)). |
| `src/lib/i18n.ts` | EN ↔ हिं strings + the 13-term `GLOSSARY` mirrored to [`kb/methodology/glossary.json`](../kb/methodology/glossary.json). |

## Voice UX invariants ([ADR-028](../70-docs/60-decisions/ADR-028-voice-ux-single-default-mode.md))

- **One default voice mode + one fallback.** Live mode is the default; the toolbar pill toggles it (green = on, red = off). The 🎤 push-to-talk button suspends Live for one turn → captures with VAD silence-cutoff → resumes Live if the user preference is still on.
- **Hands-free mode was removed in KI-027.** Any reference to it in code or docs is stale.
- **Bot TTS plays via the in-DOM `<audio>` element inside `Message`** (autoplay-on-mount via ref'd `useEffect`). **Never use `new Audio(url).play()`** — those detached instances are invisible to the `document.querySelectorAll("audio").pause()` call in the barge-in handler, so they keep playing under the user's speech.

## Tooling

| Concern | Where |
| --- | --- |
| Build / dev / lint | `package.json` scripts (`dev`, `build`, `lint`). |
| Lint config | `eslint.config.mjs`. |
| TS config | `tsconfig.json`. |
| Postcss / Tailwind | `postcss.config.mjs`. |
| Next config | `next.config.ts`. |
| Static assets | `public/`. |

## Develop locally

```bash
cd frontend
npm install        # or pnpm / bun
npm run dev        # localhost:3000 — points at the FastAPI on :8000 by default
```

Point at a different backend with `NEXT_PUBLIC_BACKEND_URL=...` (see `src/lib/api.ts`).

## Related

- `AGENTS.md` (this folder) — required reading for AI agents touching this code
- Root `CLAUDE.md` § Voice UX (ADR-028) — the canonical voice-UX cheat-sheet
- [ADR-005](../70-docs/60-decisions/ADR-005-nextjs-fastapi-frontend.md) · [ADR-013](../70-docs/60-decisions/ADR-013-tailwind-shadcn-ui.md) · [ADR-015](../70-docs/60-decisions/ADR-015-openapi-typescript-codegen.md) · [ADR-021](../70-docs/60-decisions/ADR-021-view-aware-system-prompt.md) · [ADR-028](../70-docs/60-decisions/ADR-028-voice-ux-single-default-mode.md)
- [`backend/main.py`](../backend/main.py) — the API the frontend talks to
