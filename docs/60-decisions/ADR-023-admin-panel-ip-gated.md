# ADR-023: Admin panel IP-gated; surfaced as in-app tab

**Status:** Locked
**Date:** 2026-05-14

## Context

The bot needs an admin surface for:

- Real-time LLM health snapshot (5 healthy / 9 degraded / 0 down).
- Force-fresh probes of every model.
- Reorder model chains live (promote a model to primary brain/fast/judge).
- Per-role usage stats.

This surface must:

1. Be inaccessible to ordinary users.
2. Be discoverable (and usable) for the operator without re-sharing a URL.
3. Not require a heavy OAuth / SSO stack for v1.

## Decision

**Dual-gate auth** (IP allowlist + password header) **plus in-app tab** in the main UI.

### Auth

`backend/admin.py` exposes all `/api/admin/*` endpoints behind `_check_admin(request, password)`:

```python
ADMIN_IP_ALLOWLIST    # comma-separated CIDRs or single IPs
ADMIN_PASSWORD        # set via HF Space secret
X-Admin-Password      # header on every admin request
```

Unauthorized callers get **HTTP 404 Not Found** (not 401) — the endpoints don't exist for them. This hides the admin surface from drive-by scanning.

### UI surfacing

The admin HTML (`frontend/public/admin/llm-control.html`) is iframe-embedded inside a new **"Admin · Access panel"** tab in the main app header, matching the existing tab pattern (Marketplace / Premium / Profile / Admin / Lang toggle). Same iframe, same backend; just a more discoverable entry point than typing the URL.

## Alternatives considered

| Auth method | Why rejected |
|---|---|
| Full OAuth (Google / GitHub) | Overkill for one operator; adds infra dependency. |
| JWT with rotating keys | Doesn't add real security at this scale; rotation flow burdens operator. |
| Single password, no IP gate | Password leak = anyone in the world owns the panel. |
| IP gate only, no password | A neighbor on the same home network could probe. |

| UI surfacing | Why rejected |
|---|---|
| Bookmark the URL | Loses on machine switches; no in-app discoverability. |
| Native React rewrite of llm-control.html | Heavy lift; iframe gives full functionality immediately. |
| Hidden keyboard shortcut | Operators forget shortcuts; worse than a visible button. |

## Consequences

**Positive:**

- IP allowlist + password is a real security gate.
- 404 (not 401) hides the endpoint's existence from non-allowlisted callers.
- In-app tab is one-click for the operator; matches the other panel patterns.
- iframe sandbox (`allow-scripts allow-same-origin allow-forms`) lets the panel call `/api/admin/*` cleanly.

**Negative:**

- Operator's IP changing (mobile network, café Wi-Fi, VPN) breaks access.
- iframe pattern doesn't feel as native as a hand-coded React panel.

**Mitigations:**

- `ADMIN_IP_ALLOWLIST` is comma-separated → operator can add new IPs without code change.
- Rotation is one command: `tools/set_hf_secrets.py` re-pushes `.env` to HF Space.
- v2 can replace the iframe with a React panel using the same `/api/admin/*` endpoints — no backend change needed.

## Revisit at scale

- Replace IP gate with mutual-TLS or a real auth provider (Cloudflare Access, Tailscale ACL).
- Native React admin panel for richer UX (live charts, alerting).
