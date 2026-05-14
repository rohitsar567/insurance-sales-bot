# ADR-032 — LLM Chain Architecture Reference

**Status:** Accepted — 2026-05-15
**Type:** Architecture reference (not a decision ADR)
**Owner:** Rohit Saraf
**Consolidates:** [ADR-019](ADR-019-nim-single-provider-consolidation.md), [ADR-026](ADR-026-provider-load-balancing.md) (superseded), [ADR-030](ADR-030-llm-driven-fact-find.md), [ADR-031](ADR-031-sticky-primary-election.md)
**Related KIs:** KI-079 (`87ee522`), KI-080 (`6159c54`), KI-081 (HF Space env), KI-084 (`119e0fd`), KI-085 (`8fc7979`)

> This is **not a decision ADR.** No alternatives or trade-offs are weighed here.
> ADR-032 is the single readable spec for how the LLM chain works in production
> after the KI-080 → KI-085 sweep. New decisions still ship as their own ADRs;
> this file is updated when the spec shifts.

## 1. TL;DR

Every LLM role (`brain` / `fast_brain` / `judge`) is a **candidate pool**, not a
hardcoded model. A background probe loop in `backend/llm_health.py` scores every
candidate every 300s and elects a sticky PRIMARY + provider-diverse BACKUP per
chain. `NimChainLLM.chat()` calls the elected PRIMARY exactly once per turn with
explicit `httpx` per-phase timeouts (`connect=2s, read=12s, write=2s, pool=2s`);
on real-time failure it falls to the elected BACKUP once. Election is gated by
**liveness AND credits** — each provider's credit signal (Groq response
headers / OpenRouter `/api/v1/credits` endpoint / NIM local rate-meter)
proactively excludes quota-exhausted candidates BEFORE the user hits a 429,
while a reactive 1-hour demotion absorbs any 429 that slips through. Result:
per-turn LLM call count is 1 (happy path), 2 (PRIMARY failover), or 3 (KI-079
fast→heavy escalation), with `_canonical_fallback` (KI-072 / KI-074 greedy
slot capture) as the always-available last bite.

## 2. Data flow

```
User chat turn
    ↓
FastAPI /api/chat  →  backend/orchestrator.py
    ↓
fact_find_brain.drive_fact_find()
    ↓
asyncio.wait_for(_TIMEOUT_S=25s) wrapping NimChainLLM(FAST_BRAIN_CHAIN).chat()
    ↓
┌─────────────────────────────────────────────────────────┐
│  NimChainLLM.chat()  — KI-080 election + KI-084/085     │
│  ┌───────────────────────────────────────────────────┐  │
│  │ Election  (backend/llm_health.py)                 │  │
│  │   PRIMARY  = highest score in chain               │  │
│  │             AND is_alive (probe < 600s)           │  │
│  │             AND has_credits > low_water (KI-085)  │  │
│  │             AND NOT in 1h demote window (KI-084)  │  │
│  │   BACKUP   = next-best, cross-provider preferred  │  │
│  │             (same eligibility predicate)          │  │
│  │   score    = (1/max(50, latency_ms)) * success    │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │ _call_one(PRIMARY)  — KI-084 per-phase timeouts   │  │
│  │   httpx.Timeout(connect=2, read=12, write=2,      │  │
│  │                 pool=2)                           │  │
│  │   12s read-deadline = NimChainLLM._ELECTED_CALL_  │  │
│  │   TIMEOUT_S; outer wait_for is the hard ceiling.  │  │
│  └───────────────────────────────────────────────────┘  │
│       │                                                 │
│       ├── success → report_success(latency_ms)          │
│       │            → record_nim_call() (KI-085)         │
│       │            → return LLMResult                   │
│       │                                                 │
│       └── failure → _classify_error(e)                  │
│             │                                           │
│             ├── Status429 / "RateLimit"                 │
│             │     → report_failure → demote 1h (KI-084) │
│             │                                           │
│             ├── HTTPStatusError:5xx / ReadTimeout /     │
│             │   TimeoutException / net errors           │
│             │     → report_failure → demote 30s         │
│             │                                           │
│             └── (credit state already updated from      │
│                 response headers in groq/openrouter     │
│                 client; KI-085)                         │
│       ↓                                                 │
│  _call_one(BACKUP)  — same shape, same timeouts         │
│       ↓ fail → trigger probe_all() refresh + walk       │
│              filter_chain order (final safety net,      │
│              budget-clipped per remaining total_budget)│
│       ↓ fail → raise RuntimeError                       │
└─────────────────────────────────────────────────────────┘
    ↓ outer wait_for fires (25s) OR RuntimeError raised
    ↓
fact_find_brain.drive_fact_find() catches asyncio.TimeoutError
    ↓
[KI-079] escalation:
    asyncio.wait_for(_TIMEOUT_S_ESCALATION=15s)
        wrapping NimChainLLM(BRAIN_CHAIN).chat()
    ↓ same election + per-phase + credit gating, heavier pool
    ↓ success → reply prose to user, brain_used = fact_find_brain::continue
    ↓ fail → _canonical_fallback(session, user_text, reason="…")
            (KI-072 / KI-074 greedy slot capture; never wedges)
    ↓
user-facing reply
    brain_used = fact_find_brain::continue
               | fact_find_brain::complete
               | fact_find_brain::fallback:<reason>
```

## 3. Component reference — `llm_health._STATE`

Per-model state is a `ModelHealth` dataclass keyed by chain entry (model id with
optional `groq:` / `openrouter:` prefix). Fields the elector reads on every
call:

| Field | Type | Producer | Meaning |
|---|---|---|---|
| `status` | `'healthy' / 'degraded' / 'down' / 'unknown'` | probe loop (`_absorb_probe_result`) | Coarse health bucket; `'down'` ⇒ election-ineligible. |
| `latency_ms` | `int / None` | probe + `report_success` | Last observed latency; `None` ⇒ ineligible. Floor of 50ms in scoring. |
| `last_success_at` | ISO8601 | probe + chat | Wall-clock of last 2xx. |
| `last_failure_at` | ISO8601 | probe + chat | Wall-clock of last failure. |
| `tested_at` | ISO8601 | probe loop | Drives the `HEALTHY_PROBE_AGE_SEC = 600s` freshness gate. |
| `probe_history` | `list[dict]`, cap 5 | probe + chat | `[{ok, latency_ms, ts, src}]`. Powers `success_rate`. |
| `degraded_until_monotonic` | `float` (monotonic time) | `report_failure` | Sin-bin deadline; 30s transient, 3600s rate-limit (KI-084). |
| `credits_remaining` | `float / None` | KI-085 trackers | Tokens / USD / req-slots remaining. `None` ⇒ cold-start permissive. |
| `credits_unit` | `'tokens_day' / 'usd_balance' / 'requests_min'` | KI-085 trackers | Semantic of the number above. |
| `credits_reset_at` | `float / None` (monotonic) | KI-085 trackers | When the quota resets; past-now ⇒ signal treated as stale (permissive). |
| `credits_observed_at` | `float` (monotonic) | KI-085 trackers | Snapshot timestamp; surfaced in admin tab. |
| `credits_low_water` | `float` | KI-085 constants | Gate threshold. `credits_remaining > credits_low_water` ⇒ electable. |

Persisted snapshot: `40-data/llm_health.json` (atomic write).

## 4. Election algorithm

`backend/llm_health.py::_ranked_candidates(chain_name)` returns
election-eligible candidates sorted by score descending. Election runs every
`chat()` call (microsecond hot path; in-memory `_STATE` under a coarse lock).

```python
def is_electable(h, now_mono):
    if h.degraded_until_monotonic > now_mono: return False    # KI-084 sin-bin
    if h.status == "down":                    return False
    if age_of(h.tested_at) > 600s:            return False    # HEALTHY_PROBE_AGE_SEC
    if h.latency_ms is None:                  return False
    if not has_credits(h, now_mono):          return False    # KI-085
    return True

def has_credits(h, now_mono):
    if h.credits_reset_at is not None and now_mono >= h.credits_reset_at:
        return True                                # quota already reset
    if h.credits_remaining is None:                # cold-start permissive
        return True
    return h.credits_remaining > h.credits_low_water

def score(h):
    return (1 / max(50, h.latency_ms)) * success_rate(h.probe_history)

def get_primary(chain): return ranked[0].model
def get_backup(chain):
    primary_provider = provider_of(ranked[0])
    for h in ranked[1:]:
        if provider_of(h) != primary_provider: return h.model   # provider-diverse
    return ranked[1].model                                      # graceful degradation
```

**Score formula.** `score = (1 / max(50, latency_ms)) * success_rate`. The 50 ms
floor prevents a sub-millisecond outlier from dominating election; the rolling
`success_rate` over the last 5 probes is the stability signal. Both factors
matter — a very fast model that flakes 1-in-3 calls scores lower than a stable
model 2× slower.

**Provider-diverse BACKUP.** Mandatory, not advisory. A NIM-PRIMARY whose
underlying pool is throttled MUST fall to a non-NIM BACKUP, otherwise the second
call queues in the same throttle window. Iterate ranked candidates and return
the first whose `provider_of()` differs from PRIMARY's. If only one provider has
live candidates (regional outage), BACKUP gracefully degrades to the next-best
same-provider candidate.

**Cold-start fallback.** Before the first probe completes (process restart,
HF Space rebuild), `get_primary` / `get_backup` return `None`. `NimChainLLM.chat()`
catches that case and uses `chain[0]` as PRIMARY and `chain[1]` (preferring a
different provider) as BACKUP. The probe loop runs immediately on startup so
cold-start lasts at most a few seconds.

**Family exclusion.** Brain ↔ judge family diversity (Qwen brain ↔ Mistral judge)
is enforced by the caller via `exclude_families=[...]` on
`NimChainLLM.chat()`. The election then filters election-eligible candidates by
`_family_of()` before scoring. Families: `qwen`, `mistral`, `meta`, `openai`,
`deepseek`, `moonshot`, `minimax`, `nvidia`. A NIM-hosted GPT-OSS 120B and an
OpenRouter-hosted GPT-OSS 120B share the `openai` family and are NOT pickable
as brain ↔ judge pair.

## 5. Probe loop

`backend/llm_health.py::background_probe_loop` ticks every
`PROBE_INTERVAL_SEC = 300s` (KI-084 — was 60s; raised because the prior cadence
burned ~30-50K tokens/day on Groq's free-tier and self-tripped the 100K daily
TPD cap).

Each tick:
1. `probe_all()` — parallel `httpx.post` to every chain entry with the prompt
   `"Reply with exactly: ok"`, `max_tokens=1` (KI-084 — was 5; cuts probe-driven
   token spend ~50× since we never read the body content), `timeout=8s`.
2. Status flip rules: 200 + non-empty content ⇒ `healthy` (or `degraded` if
   latency > 5000ms); 3+ consecutive failures ⇒ `down`.
3. Append `(ok, latency_ms, ts, src='probe')` to `probe_history` (capped at 5).
4. `save()` atomic-write of `40-data/llm_health.json`.

Every `OPENROUTER_CREDITS_POLL_EVERY_N_TICKS = 2` ticks (i.e. 600s / 10min):
- `poll_openrouter_credits()` issues `GET https://openrouter.ai/api/v1/credits`,
  parses `{total_credits, total_usage}`, and stamps every `openrouter:`-prefixed
  candidate with `credits_unit="usd_balance"`, `credits_remaining = total_credits - total_usage`,
  `credits_low_water = $0.05`.

Initial OpenRouter credits poll fires immediately on startup so the elector has
a non-`None` USD balance before the first chat call.

## 6. Credit signal sources

| Provider | Signal source | Header / endpoint | Stored unit | Low-water | Producer |
|---|---|---|---|---|---|
| **Groq** | Response headers on every successful chat | `x-ratelimit-remaining-tokens-day` (preferred — daily TPD is what bit us in KI-084) + `x-ratelimit-reset-tokens-day` for reset deadline | `tokens_day` | `5000` tokens (≈ one ~2K-input / ~400-output fact-find round-trip with margin) | `groq_llm.py::chat` → `llm_health.update_credits_from_groq` |
| **OpenRouter** | Dedicated account endpoint | `GET /api/v1/credits` → `{total_credits, total_usage}` (account-level USD balance) + opportunistic `x-ratelimit-remaining` from response headers as between-poll fallback | `usd_balance` (authoritative) / `requests_min` (header fallback) | `$0.05` USD (5¢ safety margin — free-models charge $0 but the account-level signal still tells us if prepaid credits are gone) | `llm_health.poll_openrouter_credits` (10-min) + `update_credits_from_openrouter_headers` (per-call) |
| **NIM** | No clean header — local rate-meter | `_NIM_CALL_TIMES[model]` deque of monotonic timestamps over a 60s window | `requests_min` | `5.0` request slots; gate at `cap - headroom = 40 - 5 = 35` in-window calls | `nvidia_nim_llm.py::chat` → `llm_health.record_nim_call` |

For Groq specifically, daily TPD is the dominant signal — the minute-window
header is noisy and KI-084's 1h sin-bin already covers minute-window blips. We
deliberately ignore `x-ratelimit-remaining-tokens-min` to keep the elector
stable.

## 7. Per-phase httpx timeouts (KI-084)

`backend/providers/nvidia_nim_llm.py::NvidiaNimLLM.chat` uses an explicit
`httpx.Timeout` rather than the scalar `timeout=self.timeout`:

```python
client_timeout = httpx.Timeout(
    connect=2.0,    # TCP handshake must finish in 2s
    read=self.timeout,   # 12s for elected calls; 6s for legacy fast-brain calls
    write=2.0,      # request-body upload deadline
    pool=2.0,       # connection-pool checkout deadline
)
```

**Why each value.**

- `connect=2.0` — TCP handshake to `integrate.api.nvidia.com` is sub-100ms in
  steady state; anything past 2s means the ingress is down and we want the
  candidate demoted, not the chat call hanging.
- `read=self.timeout` (12.0 in the KI-080 elected path) — the wall-clock budget
  for the upstream to produce a complete response. Matches
  `NimChainLLM._ELECTED_CALL_TIMEOUT_S` so a stuck NIM pool can't burn the
  outer `wait_for` ceiling.
- `write=2.0` — our request bodies are <10 KB; 2s is generous.
- `pool=2.0` — if every NIM HTTP/2 connection is in use and we can't even check
  one out within 2s, fail fast so BACKUP gets called.

**Why per-phase, not scalar.** Pre-KI-084, `httpx` collapsed a scalar timeout to
a single read deadline. A stuck NIM pool could occupy the TCP connection past
the outer `asyncio.wait_for` cancellation — the BACKUP started but PRIMARY's
socket was still held, leaking a NIM concurrency slot. Explicit per-phase
deadlines guarantee the TCP connection itself releases independently.

## 8. Failure classification

`_classify_error(e)` in `nvidia_nim_llm.py` maps a raised exception to a stable
string consumed by `llm_health.report_failure`. The string drives the sin-bin
duration.

| Error class string | Source | Demote duration | Rationale |
|---|---|---|---|
| `Status429` | `HTTPStatusError.response.status_code == 429` | **3600s (1h)** — `DEGRADE_DURATION_LONG_S` | Free-tier daily quotas don't reset in 30s. KI-084. |
| `HTTPStatusError:503` / `:502` / `:500` | non-429 HTTP errors | 30s — `DEGRADED_WINDOW_SEC` | Upstream brownouts typically clear inside a minute. |
| `ReadTimeout` / `TimeoutException` | `httpx` per-phase or scalar timeout | 30s | TCP/upstream stall; next probe re-tests. |
| `ConnectError` / `ConnectTimeout` | DNS / TLS / TCP failures | 30s | Network blip; recover quickly. |
| Any other `Exception` class name | parse failures, unexpected payload shapes | 30s | Defensive same-window. |

The rate-limit detector
(`_is_rate_limit_error`) matches `"429"`, `"ratelimit"`, or `"rate_limit"` (case
insensitive). It deliberately does NOT match bare `"HTTPStatusError"` so a 503
falls to the short window, not the 1h window.

Side effect on every `report_failure`: a synthetic
`{"ok": False, "src": "chat"}` entry is appended to `probe_history` so the next
election's `success_rate` reflects the live failure before the next probe tick.
An async re-probe of the failed model is scheduled best-effort so a quota that
happened to reset early is picked up immediately.

## 9. Escalation path (KI-079)

`backend/fact_find_brain.py::drive_fact_find` wraps the FAST_BRAIN call in
`asyncio.wait_for(_TIMEOUT_S=25s)`. On `asyncio.TimeoutError`:

1. **Heavy-brain retry.** Log `KI-079: fast brain timeout …` and call
   `get_brain_llm()` (BRAIN_CHAIN) wrapped in
   `asyncio.wait_for(_TIMEOUT_S_ESCALATION=15s)`. The heavy chain uses a
   different election (Qwen 80B primary in steady state), different fallback
   ladder, and reaches OpenRouter + Groq earlier in the candidate pool —
   realistic escalation success cases land in 3-8s.
2. **Canonical fallback.** If heavy also times out, returns
   `_canonical_fallback(session, user_text, reason="timeout_after_escalation")`
   which:
   - Greedily applies `_normalize_for_slot` to every unfilled slot in priority
     order (age → dependents → income_band → existing_cover → primary_goal →
     location → parents_age → budget → name), with slot-specific trigger guards
     to prevent cross-contamination (KI-072 / KI-074).
   - Picks the next still-empty slot and returns the canonical question.
   - Fact-find never wedges. A fully-dead network still walks the user through
     fact-find via canonical questions.

Total worst-case wall-clock before canonical fallback: 25s (FAST) + 15s
(BRAIN escalation) = **40s**. The 25s FAST cap only fires when NIM is wedged AND
no cross-provider election candidate is electable, which is rare with KI-080 +
KI-085 in place.

## 10. Telemetry surface

`TurnResult.brain_used` is the single string downstream consumers (admin
analytics, eval harness) read to attribute outcomes. Emitted variants:

| brain_used | Meaning |
|---|---|
| `fact_find_brain::continue` | LLM brain succeeded, fact-find still in progress |
| `fact_find_brain::complete` | LLM brain succeeded, fact-find now complete |
| `fact_find_brain::fallback:timeout` | FAST_BRAIN_CHAIN exhausted, escalation flag NOT yet applied (pre-KI-079 leftover; should rarely appear) |
| `fact_find_brain::fallback:timeout_after_escalation` | FAST timed out AND heavy-brain (BRAIN_CHAIN) also timed out — canonical fallback fired |
| `fact_find_brain::fallback:llm_error_after_escalation` | FAST timed out, heavy-brain raised (non-timeout exception) — canonical fallback fired |
| `fact_find_brain::fallback:llm_error` | FAST raised a non-timeout exception (HTTP / parse / etc.) |
| `fact_find_brain::fallback:no_trailer` | Brain replied but the `<FF>{...}</FF>` block was missing or malformed |
| `fact_find_brain::fallback:empty_reply` | Brain replied with only a `<FF>` block — no prose |

In addition, every successful `NimChainLLM.chat` writes a JSONL record to
`40-data/llm_usage.jsonl` with `{role, chain_primary, served_model,
elected_primary, elected_backup, latency_ms, success, [fallback_phase]}` so the
admin tab can audit which candidate served each turn. The admin
`status_summary()` surface now also returns
`elections: {brain: {primary, backup}, fast_brain: {…}, judge: {…}}` and the
per-model `credits_remaining` / `credits_unit` / `credits_low_water` from
KI-085 so operators can see why a candidate is gated out.

## 11. Performance characteristics

**Per-turn LLM call count** (under normal conditions with at least one healthy
candidate per provider):

| Scenario | Calls | Wall-clock (steady state) |
|---|---|---|
| Happy path: elected PRIMARY succeeds | **1** | 2-6s (depends on which provider wins election; Groq LPU ~1s, NIM Qwen ~2-3s, NIM Nemotron ~1.6s) |
| PRIMARY fails real-time → BACKUP succeeds | **2** | 4-12s (PRIMARY's 12s read deadline + BACKUP latency) |
| Both fail → KI-079 escalation succeeds on heavy chain | **3** | 18-25s (FAST budget + 3-8s heavy escalation) |
| Total exhaustion → canonical fallback | (heavy chain attempted) | up to 40s + canonical reply |

**Probe-driven token spend** (KI-084 cadence). With 25 candidates × 1
token per probe × `300s` cadence = ~7K probe tokens/day on Groq's free-tier TPD
(well inside the 100K cap). Pre-KI-084 (60s cadence, 5 tokens per probe) was
~150K/day → tripped the cap on probe traffic alone.

**Expected p50 / p95 latency** in steady state with elected primaries (Groq
Llama-3.3-70B fast-brain / NIM Qwen 80B brain / Mistral Large 3 judge):

| Role | p50 | p95 |
|---|---|---|
| fast_brain (fact-find turn) | ~2.0s | ~6s (one BACKUP failover) |
| brain (synthesis / comparison) | ~3.5s | ~10s |
| judge (faithfulness Gate 4) | ~4.5s | ~12s |

## 12. Operational runbook

### Provider exhausts 0 credits (Groq daily TPD hits 100,000/100,000)

Sequence is fully automatic — no operator action required:

1. **Reactive** — next user chat turn that elects Groq raises `Status429` from
   the upstream. `_classify_error` returns `"Status429"`.
   `llm_health.report_failure` sets `degraded_until_monotonic = now + 3600s`
   and appends a failed `probe_history` entry.
2. **Election re-runs** on the next call. `is_electable(groq)` returns False
   (sin-bin). The elector picks the next-ranked non-Groq candidate (typically
   NIM Qwen 80B for fast-brain).
3. **User-visible behaviour.** First post-exhaustion turn pays ONE failover
   (PRIMARY=Groq raises 429 ⇒ BACKUP=NIM Qwen answers). Total wall-clock: 12s
   read-deadline + NIM Qwen latency ≈ 14-15s. User sees a real reply, not a
   canonical fallback. Every subsequent turn for the next hour is a single
   1-call NIM Qwen response (~2-3s).
4. **Proactive (KI-085) — what should have happened instead.** If
   `update_credits_from_groq` had received an `x-ratelimit-remaining-tokens-day`
   header showing < 5000 tokens on the previous successful call, election
   would have excluded Groq BEFORE the 429-producing call. The user would
   never have seen the 14-15s failover turn. KI-085 closes the one-turn
   reactive gap that KI-084 alone leaves.
5. **Recovery.** At `T + 1h` the sin-bin expires; `is_electable(groq)`
   becomes True again. If the daily quota actually reset by then, the next
   probe (or the next chat turn) re-stamps `credits_remaining` from headers
   and Groq re-enters election. If the daily quota did NOT reset, the next
   chat turn re-raises 429, sin-bin extends another hour.

### Live diagnosis worked example (2026-05-15)

Production hit `Status429` on Groq with the response headers showing
`x-ratelimit-remaining-tokens-day: 546` and `total used: 99,454 / 100,000`. Pre-
KI-085: every user turn for the next hour paid the 14-15s reactive failover
because the elector had no proactive signal. Post-KI-085: the *previous*
successful Groq call stamped `credits_remaining = 99454 - <last_call_tokens>`,
the next call saw `credits_remaining < 5000` and election excluded Groq before
the 429-producing call. Fast-brain primary flipped to NIM Qwen 80B; user saw
2-3s natural-LLM replies throughout the quota-exhausted window.

### When manual intervention IS needed

- **Full NIM regional outage** (all NIM-hosted candidates returning 5xx for
  >15 min). Probe loop marks every NIM candidate as `down` after 3 consecutive
  fails. Cross-provider candidates (OpenRouter, Groq) keep serving. No
  operator action; if both Groq and OpenRouter are also degraded, the canonical
  fallback path takes over and the user still gets a coherent (if scripted)
  reply. Page Rohit only if `_canonical_fallback` reason `:no_trailer` /
  `:empty_reply` rates spike — that means the brain *is* responding but
  malformed, which the chain logic can't auto-heal.
- **API key rotation.** `.env` (local) / HF Space environment secrets
  (production) hold `NVIDIA_NIM_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`.
  KI-081 pushed the Groq + OpenRouter keys to the HF Space environment so the
  chain works in production without code redeploys. Rotating any key requires:
  (a) update local `.env`, (b) update HF Space secrets via Settings → Repository
  secrets, (c) bounce the Space (`huggingface_hub.HfApi().restart_space()` or
  manual restart).
- **OpenRouter wallet refill.** `poll_openrouter_credits()` will pick up the
  new balance within 10 min. To accelerate, restart the Space.

## 13. Files touched (across KI-079 / KI-080 / KI-084 / KI-085)

- `backend/providers/nvidia_nim_llm.py` — `NimChainLLM.chat` election rewrite
  (KI-080), `_classify_error` for Status429 (KI-084), per-phase httpx timeouts
  (KI-084), `record_nim_call` hook on every NIM success (KI-085).
- `backend/llm_health.py` — KI-080 election + 60s probe (now 300s in KI-084),
  KI-084 1h rate-limit demote + `DEGRADE_DURATION_LONG_S` + probe cadence /
  max_tokens reduction, KI-085 `update_credits_from_groq` /
  `update_credits_from_openrouter_headers` / `poll_openrouter_credits` /
  `record_nim_call` + the `_has_credits` election predicate.
- `backend/providers/groq_llm.py` — `update_credits_from_groq` call after
  successful HTTP (KI-085).
- `backend/providers/openrouter_llm.py` — `update_credits_from_openrouter_headers`
  call after successful HTTP (KI-085).
- `backend/fact_find_brain.py` — `_TIMEOUT_S_ESCALATION = 15.0` + heavy-brain
  retry path on FAST timeout (KI-079).
