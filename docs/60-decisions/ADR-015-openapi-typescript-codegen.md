# ADR-015: REST API + `openapi-typescript` codegen

**Status:** Locked
**Date:** 2026-05-13

## Context

The Next.js frontend talks to the FastAPI backend over HTTP. Request/response types need to stay in sync as the backend evolves — manual TypeScript type maintenance always rots within weeks.

## Decision

- **REST** transport layer (not GraphQL, not gRPC, not tRPC).
- FastAPI auto-generates OpenAPI 3.x schema (`/openapi.json`).
- `openapi-typescript` codegens TypeScript types into `frontend/src/lib/api-types.ts` on every build.
- `frontend/src/lib/api.ts` wraps `fetch` with typed helpers (`postChat`, `postProfile`, `getCoverage`, etc.).

## Alternatives considered

| Approach | Why rejected |
|---|---|
| Manual TypeScript types | Rot pattern is universal; types drift from backend reality within weeks. |
| GraphQL | Overkill for our REST-shaped data; adds resolver layer + N+1 risks; Sarvam ecosystem doesn't expect it. |
| tRPC | Node-only; doesn't fit our Python backend. |
| gRPC | Overkill; HTTP/2 dependencies + protobuf tooling adds friction. |

## Implementation

```
FastAPI (auto OpenAPI) → openapi-typescript → api-types.ts → typed fetch wrappers
```

Build hook: `frontend/scripts/codegen.sh` runs `openapi-typescript http://localhost:7860/openapi.json -o src/lib/api-types.ts` after every backend deploy.

## Consequences

**Positive:**

- Single source of truth for API shape (the FastAPI Pydantic models).
- Type drift caught at TypeScript compile time.
- Refactors on the backend immediately surface as TypeScript errors on the frontend.
- No GraphQL N+1 trap.

**Negative:**

- Streaming responses require a parallel path (Server-Sent Events or WebSocket; not codegen-friendly).

**Mitigations:**

- For the streaming case (voice STT live), we keep a hand-written WebSocket handler.

## Revisit at scale

v2: if real-time streaming (full-duplex voice) becomes the dominant pattern, add a WebSocket route alongside REST. Codegen for the REST half stays.
