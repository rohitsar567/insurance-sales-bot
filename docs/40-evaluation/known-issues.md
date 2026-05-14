# Known Issues + Quality Sprint Log

Living document. Every defect we find — whether via code review, eval audit,
or production observation — lands here with severity, root cause, and the
plan to fix it. Closed issues stay in the log with a `**FIXED in <sha>**`
annotation so reviewers can audit the project's quality trajectory.

## Severity scale

- **P0 / Critical** — User-visible incorrect behavior, BFSI compliance risk,
  or data loss. Block any release.
- **P1 / High** — Silent degradation; user gets a worse experience but it
  doesn't visibly break. Ship a fix in the next sprint.
- **P2 / Medium** — Edge case; cosmetic; non-critical path. Backlog.
- **P3 / Low** — Code smell or minor inefficiency.

---

## Open issues

### KI-001 — Gate 4 (LLM judge) fails OPEN on judge error

**Severity:** P0
**Source:** `backend/faithfulness.py:253-255`
**Discovered:** Code-review sweep 2026-05-14

When the judge LLM call fails (network, rate limit, JSON parse error, NIM
408/503), Gate 4 currently returns `supported=True, ["judge_error_failopen"]`.
The reply ships through without grading.

In BFSI this is the *unsafe* default — an unsupported claim that should
have been blocked by Gate 4 leaks through to the user because the judge
hiccupped. The audit log preserves `judge_error_failopen` but the user
never sees the gate failed.

**Fix plan:** Add `FAITHFULNESS_FAIL_CLOSED` env var (default `true` in
production, `false` in dev / smoke tests). When fail-closed, return
`supported=False, ["judge_unavailable_failclosed"]` so the orchestrator's
cross-check retry path or final refusal fires instead.

---

### KI-002 — Session-state disk flush silently swallows errors

**Severity:** P1
**Source:** `backend/session_state.py:67-68`
**Discovered:** Code-review sweep 2026-05-14

`SessionState._flush()` writes the profile JSON to disk via a tmp+replace
pattern. On disk-full, EACCES, or JSON encode error, the bare `except
Exception: pass` drops the failure with zero observability. The user's
profile is silently lost on the next Space restart. They redo fact-find.

**Fix plan:** Add `logging.warning("session flush failed for %s: %s",
session_id, e)` — keep the no-crash behaviour but surface failure rate to
the HF Space logs so we can detect when it starts happening.

---

### KI-003 — Session-state disk load silently returns None on schema drift

**Severity:** P1
**Source:** `backend/session_state.py:114-115`
**Discovered:** Code-review sweep 2026-05-14

When the on-disk session JSON has a schema mismatch (Profile dataclass
field renamed, type changed), `_load_from_disk` catches the exception and
returns `None`. The user gets a fresh blank profile and has to redo
fact-find. No log, no metric.

**Fix plan:** Log the exception with `session_id` so we know schema drift
is happening. Also: tighten the existing valid-field filter (line 105-106)
to additionally type-check values so a stringified int doesn't pass through.

---

### KI-004 — Indic translator failure → original Indic text flows into English brain silently

**Severity:** P1
**Source:** `backend/orchestrator.py:148-149`
**Discovered:** Code-review sweep 2026-05-14

When Sarvam-M translator fails on an Indic query, the orchestrator falls
through with the original Indic text, sending it to the English-trained
DeepSeek/NIM brain. The brain handles it imperfectly. The user gets a
degraded reply with no indication that the translator failed.

**Fix plan:** Log the failure (`logging.warning("Indic translator failed
for session %s, lang=%s: %s", session_id, language, e)`). Optionally
return a soft refusal in Indic ("Sorry, I'm having trouble with the
translation right now — could you ask in English?") instead of silently
mis-routing.

---

### KI-005 — Profile-RAG chunk upsert failure silently swallowed

**Severity:** P1
**Source:** `backend/orchestrator.py:285-287`
**Discovered:** Code-review sweep 2026-05-14

After the conversational profile-update extractor lands a new field, the
orchestrator re-upserts the profile chunk into Chroma so retrieval reflects
the latest state. If that Chroma write fails (lock, disk, schema), the
exception is swallowed. Subsequent turns retrieve the *stale* profile.
The user thinks the bot incorporates their new fact ("I just got
diabetes"); it actually doesn't.

**Fix plan:** Log the failure + record in `TurnResult.profile_updates`
that the upsert hit a problem so the frontend can show a small warning
or retry.

---

### KI-006 — Conversational profile extraction failure silently swallowed

**Severity:** P2
**Source:** `backend/orchestrator.py:288-289`
**Discovered:** Code-review sweep 2026-05-14

If `extract_profile_updates()` itself raises (rare; NIM unavailable), the
mid-chat profile-update feature is silently disabled for that turn. User
won't know why their "I just turned 40" didn't take.

**Fix plan:** Log + add to `TurnResult.profile_updates_meta` so the
frontend could surface "we missed an update — try mentioning it again".

---

### KI-007 — Indic cascade total failure → English reply with zero log

**Severity:** P2
**Source:** `backend/orchestrator.py:425-426`
**Discovered:** Code-review sweep 2026-05-14

When all three Indic drift gates fail (or `translate_to_indic` itself
raises), we fall back to English. The user asked in Hinglish but gets
English. No log of which gate failed.

**Fix plan:** Add structured logging of which gate caused the fall-back
(`anchor` / `llmjudge` / `cosine`) so we can tune thresholds against real
production drift data.

---

### KI-008 — TTS preprocess can swallow blocking content

**Severity:** P3
**Source:** `backend/main.py:258-272`
**Discovered:** Code-review sweep 2026-05-14

`tts_preprocess()` is called inside a `try: … except Exception as e: log
+ return text only` block. If the preprocessor strips the acronym expansion
incorrectly, the TTS voice would butcher PED / SI / IRDAI etc. No fall-back
to a hard-coded acronym dict — we just log + skip.

**Fix plan:** Add a regression test for `tts_preprocess()` covering the 20
most common BFSI acronyms.

---

### KI-009 — Live-mode VAD: no calibration on entry

**Severity:** P2
**Source:** `frontend/src/lib/useLiveConversation.ts` — `rmsThreshold: 28`
**Discovered:** Code-review sweep 2026-05-14

The RMS threshold is a constant. Quiet speakers, far-mic users, and noisy
backgrounds all hit one fixed bar. Some users won't trigger VAD; others
will trigger it on background noise.

**Fix plan:** Calibrate the threshold by sampling 1 second of ambient
audio on Live-mode entry. Set threshold to `mean(ambient) + 2 * sigma`.

---

### KI-010 — Audit runner: output unbuffered required `PYTHONUNBUFFERED=1`

**Severity:** P3
**Source:** `tools/audit/run_audit.py`
**Discovered:** Self-test 2026-05-14

Initial run had zero progress prints in the captured log because Python's
default stdout buffering held lines until process exit. Fixed in the same
session: all `print()` calls now use `flush=True` and we add per-5-turn
progress prints. Document for future tooling.

**Status:** FIXED in commit during audit framework rollout.

---

## Closed issues (this session)

- **Issue 1: Full-duplex voice barge-in** — shipped in `d31e132`.
- **Issue 2 + 4: Garbage profile recording + sidebar sync** — shipped in `9a1b321`.
- **Issue 3: Sarvam STT 400 (webm→wav)** — shipped in `a777198`.
- **Bug A: Cold-start "Load failed"** — shipped in `f81328f`.
- **Bug B: Citation chip insurer prefix** — shipped in `f81328f`.
- **Bug C: "Try again" intent handling** — shipped in `f81328f`.

---

## Quality-sprint cadence

Every batch of fixes ships as one commit referencing the KI numbers it
closes. The audit run (`audit_results/<run_id>/report.md`) is the
empirical signal for whether a fix is actually working in production.

The standing ratio target: **for every 1 user-facing bug a reviewer
catches, we should close 5 internal issues from this log before the next
review.**

### KI-011 — Fact-find re-ask infinite loop under load — **FIXED in `171f2a4`**

**Severity:** P0
**Source:** `backend/orchestrator.py` fact-find branch + `backend/fact_find_normalizer.py` LLM-only path
**Discovered:** First persona of the 100-persona audit (P002, verbose style)

When NIM rate-limited the Llama-3.3-70B normalizer under audit
concurrency, the LLM call raised, the orchestrator marked the answer
ambiguous, kept `awaiting_question_id` set, and re-asked the same
question on the next turn. User moved on with answers to OTHER
questions. Normalizer rejected them. Bot re-asked again. Infinite loop.

**Fix:** Keyword fast-path in `fact_find_normalizer.py` (hand-curated
substring matchers for 9 metros, 15 tier1 cities, dependents
combinations, income/budget bands, primary goals, common health
conditions) bypasses the LLM for ~80% of answers. Re-ask cap in
`orchestrator.py` gives up after 2 failed normalizations on the same
question and marks it asked. Production audit on persona P002
post-fix: 30/30 turns completed in 85s with 0 refusals (vs. infinite
loop before).

### KI-012 — Bot stuck in fact_find_complete readback loop — **FIXED in next commit**

**Severity:** P0
**Source:** `backend/orchestrator.py` fact-find-complete branch
**Discovered:** Reviewing audit transcript of P002 post-KI-011-fix

After fact-find completes, the orchestrator only calls
`session.set_awaiting(None)` — it does NOT flip
`session.free_form_session = True`. On every subsequent turn, the
classifier still routes through the fact-find branch (because
`free_form_session` is false), `next_question()` returns None (all
fields captured), and the code path emits the readback summary AGAIN
instead of going to retrieval + brain.

**Effect:** P002 used 19 of 30 turns repeating the same readback
"Got it — here's what I've understood: …" instead of answering the
user's real policy questions. Every persona that completes fact-find
hits this.

**Fix:** When fact-find completes, set
`session.free_form_session = True` and flush to disk. Subsequent turns
skip the fact-find branch entirely and go through retrieval + brain
as intended.
