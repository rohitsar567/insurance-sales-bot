# ADR-012: Render-then-HF-Space deployment

**Status:** Superseded by deploy-to-HF-Space (current production)
**Date:** 2026-05-13

## Context

The backend needed a free, GitHub-auto-deploying Python host with persistent disk for DuckDB + Chroma.

## Decision (v1, since superseded)

**Render** (free tier, 750 h/mo).

## Current state (post-supersession)

Production now deploys to **HuggingFace Space** (`origin` remote). Reasons:

- HF Space's Docker SDK gives full control over the runtime (Python + Node side-by-side for the unified `backend + frontend/standalone` Docker container).
- HF Space natively integrates with HF Dataset (ADR-020) — data fetch at build time via `huggingface_hub.snapshot_download` is one-line.
- Sarvam reviewers expect Sarvam-stack demos on HF Space anyway.
- Free tier is ample for take-home traffic.

## Alternatives considered (v1)

| Host | Why not | 
|---|---|
| Fly.io | Better global routing, but more setup overhead. |
| Railway | Free tier dropped after the build started. |
| Modal | Better for ML workloads but no persistent disk model. |
| Self-hosted VPS | Operational overhead without payoff. |

## Why Render was the original pick

- GitHub auto-deploy on push.
- Python-native build.
- Persistent disk for DuckDB + Chroma.
- Env-var secrets.
- Well-documented.

## Why HF Space replaced Render

- The data-split architecture (ADR-020) made HF Space's free 50 GB dataset quota a perfect fit.
- Single platform for both code (Space) and data (Dataset).
- Docker SDK supports the unified frontend + backend container we ended up needing.
- Render's free tier sleep-after-15-min idle policy is identical to HF Space's, so no operational difference.

## Consequences

Migration from Render to HF Space was a one-commit change (Dockerfile + render.yaml retained as a fallback option, but not active).

## Revisit at scale

v2: move to dedicated cloud (AWS Fargate / GCP Cloud Run) when traffic justifies it.
