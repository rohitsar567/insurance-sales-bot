# ADR-010: Secret handling — `.env` chmod 600, gitignored, mirrored to HF Space secrets

**Status:** Locked
**Date:** 2026-05-13

## Context

The bot needs 8 API keys / secrets (Sarvam, Voyage, NIM, HuggingFace, Groq, OpenRouter, admin password, admin IP allowlist). Leaking any of them creates real cost or security exposure.

## Decision

**Three-place handling pattern:**

| Location | Used by | Access control |
|---|---|---|
| Local `.env` | Local dev | `chmod 600`, gitignored (`.gitignore` line 1) |
| HF Space secrets | Production deploy | HF Space UI / API; encrypted at rest by HF |
| `~/.claude/.../memory/reference_insurance_bot_api_keys.md` | Recovery if `.env` is wiped | Lives only on local Mac; not synced to any remote |

The HF Space mirror is managed by `tools/set_hf_secrets.py` which reads from local `.env` and PATCHes the HF Space API.

## Alternatives considered

| Approach | Why rejected |
|---|---|
| Plain-text keys in repo | Catastrophic for any commit history. |
| Encrypted file in repo | Custom decryption flow per machine; key management problem moves around but doesn't go away. |
| External vault (1Password Connect, HashiCorp Vault) | Overkill for take-home; another infra dependency. |

## Implementation details

- `.env.example` checked in with placeholder values, makes new-machine setup trivial.
- `.gitignore` line 1: `.env`. Verified via `git ls-files | grep -E '^\.env$'` → only `.env.example` shows.
- `backend/config.py` loads via `python-dotenv`; missing keys raise `ValueError` at boot with the field name.
- Admin password rotation: `tools/set_hf_secrets.py` re-pushes any changed value to HF Space; Space restarts automatically.
- Memory file is the recovery copy of last resort. After the May 14 `.env` wipe incident, the memory file is treated as the canonical inventory.

## Consequences

**Positive:**

- Secrets exist exactly where they're needed; nowhere else.
- Rotation is one script run.
- New-machine setup needs only `.env` provisioning.

**Negative:**

- Memory file containing plaintext keys is a sensitive artifact; protect the Mac.

## Revisit at scale

v2: move HF Space secrets to a real KMS (AWS Secrets Manager, GCP Secret Manager) when the deploy moves off HF Space free tier.
