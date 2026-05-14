# ADR-024: Triple-mirror — HuggingFace + GitHub + local for both code and data

**Status:** Locked
**Date:** 2026-05-14

## Context

The project's working state must be resilient against:

1. **HF Space outage** — production deploy down.
2. **HF Dataset corruption / accidental deletion** — entire RAG corpus lost.
3. **Local Mac failure** — work-in-progress code lost.
4. **GitHub repo nuked** — public mirror lost.

Single-point-of-failure on any one layer is unacceptable.

## Decision

**Mirror both code and data across three layers.** Two GitHub repos parallel two HuggingFace artifacts.

| Layer | Code | Data |
|---|---|---|
| **HuggingFace** | Space `rohitsar567/InsuranceBot` (Docker) | Dataset `rohitsar567/insurance-bot-data` |
| **GitHub** | Repo `rohitsar567/insurance-sales-bot` | Repo `rohitsar567/insurance-sales-bot-data` (Git LFS) |
| **Local Mac** | `~/Developer/Insurance Sales Bot/` working tree | `~/Developer/Insurance Sales Bot/rag/_hf_dataset_backup/` (gitignored) |

## Alternatives considered

| Mirror count | Why rejected |
|---|---|
| Single layer (HF only) | HF Space outage = product down + no backup of code state. |
| Two layers (HF + local) | Local Mac failure or theft = irrecoverable. |
| Two layers (HF + GitHub) | GitHub LFS bandwidth cap (1 GB/month free) is exposed to every external reviewer clone. |

## Push fan-out pattern

The code repo has two remotes:

```
origin → huggingface.co/spaces/rohitsar567/InsuranceBot (HF Space)
github → github.com/rohitsar567/insurance-sales-bot
```

`git push origin main && git push github main` after every commit. Sync verification: `git rev-list --count main...origin/main` and same for `github/main`; both must equal 0.

The data repo (`insurance-sales-bot-data`) on GitHub uses Git LFS for files >50 MB (the 157 MB chroma.sqlite3 and the 87 MB IRDAI master circular PDF).

## End-user runtime path (important)

External users hitting the live bot **never touch GitHub**. The runtime path is:

```
End user → HF Space (Docker container with data pre-baked at build time
                     via snapshot_download from HF Dataset)
```

GitHub is for code mirror + offline backup. GitHub LFS bandwidth is only consumed by manual clones (operator on a new Mac, reviewer inspecting the repo).

## Consequences

**Positive:**

- Three independent failure domains; any one outage doesn't lose the project.
- Sarvam reviewers can inspect code on GitHub (the polished read-only surface).
- HF reviewers can run the live demo.
- Operator can rebuild the entire dataset from local backup if HF Dataset is ever lost.

**Negative:**

- Push fan-out is two commands instead of one.
- Three sets of credentials (HF token, GitHub PAT, local file system).

**Mitigations:**

- A shell alias / git hook can collapse the dual push to one command if it becomes friction.
- Tokens stored in `.git/config` (one-time setup per machine).

## Revisit at scale

- Add `git push origin main github main` as a single composite remote (`git remote add all` with two pushurls) so one command pushes to both.
- For data, automate "HF Dataset commit → GitHub LFS sync" via a GitHub Action triggered by HF webhook.
