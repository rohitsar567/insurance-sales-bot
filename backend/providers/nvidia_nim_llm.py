"""NVIDIA NIM — single-brain reasoning stack.

NIM exposes an OpenAI-compatible chat completions endpoint at
POST https://integrate.api.nvidia.com/v1/chat/completions.

Why NIM:
  - Frontier open-weights models hosted free (no card, no daily cap, 40 req/min)
  - Single provider for the reasoning stack
  - Same Bearer-auth + OpenAI request shape — drop-in retry/backoff

Role:
  - Brain (every reasoning turn): one elected candidate from BRAIN_CHAIN.
      The chain is ordered Qwen 3-Next → Mistral Large 3 → Llama-4 Maverick
      → Nemotron-Super (last resort). Election picks the actually
      healthy/fastest candidate per turn from background probe data.

BRAIN_CHAIN is the only chain: voice fact-find reuses it with its own
budget, and faithfulness is enforced by in-process checks. Sarvam stays
for voice STT/TTS + Hindi/Hinglish/vernacular translation.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult
from backend.providers.openrouter_llm import OpenRouterLLM
from backend.providers.groq_llm import GroqLLM


# Usage log — append-only JSONL with cheap 1 MB rotation. Consumed by
# GET /api/admin/usage for the admin control panel. Path is two parents up
# from this file (backend/providers/nvidia_nim_llm.py → repo root / data).
_USAGE_LOG_PATH = settings.DATA_DIR / "llm_usage.jsonl"
_USAGE_LOG_MAX_BYTES = 1_000_000  # 1 MB cap — rotate to .bak when exceeded
_usage_lock = asyncio.Lock()


def _now_iso_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _append_usage(record: dict) -> None:
    """Append one JSONL record to 40-data/llm_usage.jsonl with 1 MB rotation.

    Best-effort: never raises. Usage logging must NEVER break a chat call.
    Rotation: if file is >1 MB, rename to ``llm_usage.jsonl.bak`` (overwriting
    any existing ``.bak``) and start fresh.
    """
    try:
        async with _usage_lock:
            _USAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                if _USAGE_LOG_PATH.exists() and _USAGE_LOG_PATH.stat().st_size > _USAGE_LOG_MAX_BYTES:
                    bak = _USAGE_LOG_PATH.with_suffix(_USAGE_LOG_PATH.suffix + ".bak")
                    _USAGE_LOG_PATH.replace(bak)
            except Exception:
                pass  # rotation failure is non-fatal
            line = json.dumps(record, ensure_ascii=False) + "\n"
            with _USAGE_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # Logging is best-effort — silently swallow.
        pass


# Global outbound NIM concurrency cap.
#
# The binding constraint is NIM's free-tier per-key concurrency (~3-5
# slots). Multiple sources of NIM traffic stack on the same key with no
# global throttle: the probe loop, admin tab polling, per-user chat turns
# (1-2 calls), concurrent users (N × 1-2), and retry backoff.
#
# 6+ in-flight → queue → 15-25s response times → bot's 12s outer cap
# fires → user sees fallback.
#
# This module-level semaphore is shared across ALL NvidiaNimLLM instances
# in the process. It wraps ONLY the actual httpx.post — not election,
# response parsing, or usage logging — so it serialises the NIM round-
# trip without serialising the whole reasoning pipeline. With cap=2,
# our own in-flight count never exceeds NIM's idle concurrency budget,
# so probes + admin + users + retries cannot starve each other.
_NIM_OUTBOUND_SEMAPHORE = asyncio.Semaphore(2)


# Module-level httpx.AsyncClient singleton.
#
# A single shared client (rather than a fresh pool per chat() call) avoids
# churning TCP connections / dropping TLS sessions / leaking pool slots
# under concurrency. Explicit Limits:
#   - max_connections=10        caps total outbound (NIM + probes share)
#   - max_keepalive_connections=5  keeps a warm pool for the hot path
# The client is constructed lazily (first .chat() call) so cold imports
# don't pay TLS/handshake cost on processes that never call NIM (e.g.
# CLI tools, eval harness).
_NIM_HTTPX_CLIENT_LOCK = threading.Lock()
_NIM_HTTPX_CLIENT: Optional[httpx.AsyncClient] = None


def _get_shared_nim_client() -> httpx.AsyncClient:
    """Return the module-level shared httpx.AsyncClient, constructing it
    lazily on first call. The client is process-wide; do NOT close it from
    individual chat() calls — the OS reclaims sockets at process exit.

    Per-request timeout is applied at .post() call time, not at construction,
    so the same shared pool serves chat() (long timeouts) and probes
    (short timeouts) without contention.
    """
    global _NIM_HTTPX_CLIENT
    if _NIM_HTTPX_CLIENT is not None:
        return _NIM_HTTPX_CLIENT
    with _NIM_HTTPX_CLIENT_LOCK:
        if _NIM_HTTPX_CLIENT is None:
            _NIM_HTTPX_CLIENT = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
    return _NIM_HTTPX_CLIENT


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Heavy brain (complex queries — comparison, recommendation, synthesis):
# Qwen 3-Next 80B — 80B / 3B active MoE, frontier multilingual, very fast.
NIM_BRAIN_MODEL = "qwen/qwen3-next-80b-a3b-instruct"
# Fast brain (voice turns, fact-find, simple QA):
# Same model — Qwen 3-Next 80B is already fast (~2s response) and routes
# different intents to the same pool. Tiered routing kept for forward-compat.
NIM_FAST_BRAIN_MODEL = "qwen/qwen3-next-80b-a3b-instruct"
# Judge: Mistral Large 3 — 675B dense, MIT license. Different family from
# the Qwen brain (Mistral vs Alibaba) so the judge sees the brain's output
# from a genuinely different decision surface ("the brain doesn't mark its
# own homework").
NIM_JUDGE_MODEL = "mistralai/mistral-large-3-675b-instruct-2512"


class NvidiaNimLLM(LLMProvider):
    name = "nim"

    def __init__(
        self,
        model: str = NIM_BRAIN_MODEL,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key or getattr(settings, "NVIDIA_NIM_API_KEY", "")
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError(
                "NVIDIA_NIM_API_KEY not set. Get a key at https://build.nvidia.com "
                "and add NVIDIA_NIM_API_KEY=nvapi-... to .env"
            )
        self.name = f"nim::{model.split('/')[-1]}"

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        url = f"{NVIDIA_NIM_BASE_URL}/chat/completions"
        body: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            # NIM forwards OpenAI-shape response_format to the upstream
            # model. For models that support `nvext.guided_json`
            # (NIM's stricter constrained-decoding surface), surface that
            # too when the caller passes a JSON schema (response_format
            # with `json_schema`). The plain `{"type": "json_object"}` flag
            # works on most NIM models as-is; we just keep it forwarded.
            body["response_format"] = response_format
            if response_format.get("type") == "json_schema":
                schema = response_format.get("json_schema", {}).get("schema")
                if schema:
                    body.setdefault("nvext", {})["guided_json"] = schema

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # KI-084 — per-phase httpx timeouts. Previously `timeout=self.timeout`
        # collapsed to a single read deadline; httpx applied it to the *whole*
        # connection lifecycle, so a stuck NIM pool could occupy the
        # connection past the outer wait_for cancellation (the BACKUP elected
        # model starts but the PRIMARY socket is still held → NIM concurrency
        # slot leaks). Explicit connect/read/write/pool deadlines guarantee
        # the TCP connection itself times out independently and the slot is
        # freed even if the upstream is mid-response.
        client_timeout = httpx.Timeout(
            connect=2.0,
            read=self.timeout,
            write=2.0,
            pool=2.0,
        )
        # No inner retry loop. 429/5xx failover is handled by NimChainLLM:
        #   (1) the exception is caught in _try(),
        #   (2) report_failure() demotes the slot-blocked candidate for
        #       ~30s, and
        #   (3) the elected backup (cross-provider by preference) is tried
        #       within the same turn.
        # If both elected models fail, BRAIN_CHAIN provides one more bite.
        # Per-call retries inside this method would only re-queue against
        # the same slot-starved pool, multiplying the queueing the
        # semaphore prevents.
        #
        # The semaphore wraps ONLY the HTTP round-trip — not response
        # parsing or usage logging — so we cap concurrent network
        # traffic without serialising the rest of the pipeline.
        # Use the module-level shared httpx.AsyncClient (singleton with a
        # bounded connection pool). Per-request timeout is applied at
        # .post() call time so the shared pool serves both long-timeout
        # chat() and short-timeout probe() calls without contention.
        client = _get_shared_nim_client()
        async with _NIM_OUTBOUND_SEMAPHORE:
            resp = await client.post(
                url, headers=headers, json=body, timeout=client_timeout
            )
        resp.raise_for_status()
        payload = resp.json()

        choice = payload["choices"][0]
        msg = choice.get("message", {}) or {}
        # NIM reasoning models (Nemotron Super etc.) emit output in reasoning_content
        # instead of content. Llama-3.3-70B + Llama-4 Maverick both return normal
        # content, but guard against the variant in case we ever swap in a
        # reasoning model.
        text = msg.get("content") or msg.get("reasoning_content") or ""
        usage = payload.get("usage", {})
        return LLMResult(
            text=text,
            model=payload.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw=payload,
        )


# =============================================================================
# Multi-model fallback chains — OpenRouter-style switchability inside NIM
# =============================================================================
#
# NIM hosts ~110 models across different inference pools. Individual pools
# go up/down with NIM's load (we've seen DeepSeek-V4 + Meta Llama pools time
# out for hours while Qwen + Mistral pools stay sub-2s). Hardcoding ONE
# brain model is brittle.
#
# Solution (D-022): each LLM role has a CHAIN of candidate models in
# preference order. On timeout / 5xx / parse-fail, the chain falls through
# to the next candidate. Chains were curated to keep brain-vs-judge family
# diversity (Qwen brain → Mistral judge → OpenAI judge → etc.) so the
# cross-family-grading invariant survives any failover.

BRAIN_CHAIN = [
    # NIM-only: the chain contains only NIM-hosted candidates whose `<FF>`
    # trailer adherence is verified or structurally safer (different
    # family / smaller routing surface). Nemotron is LAST RESORT — it is
    # the weakest practical NIM model and only serves when every
    # higher-quality model is simultaneously down.
    # Primary: Qwen 3-Next 80B — clean JSON, multilingual.
    "qwen/qwen3-next-80b-a3b-instruct",
    # 1st fallback: Mistral Large 3 675B — different family (mistral).
    # Strongest fallback.
    "mistralai/mistral-large-3-675b-instruct-2512",
    # 2nd fallback: Meta Llama-4 Maverick 17B — different family (meta),
    # keeps the chain alive through a Qwen+Mistral outage.
    "meta/llama-4-maverick-17b-128e-instruct",
    # 3rd / LAST RESORT: NVIDIA Nemotron-Super 49B — only reached when
    # Qwen + Mistral + Maverick are ALL down simultaneously. Kept in the
    # pool for fail-loud > down.
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
]

# BRAIN_CHAIN is the only chain — every reasoning role resolves to it.
# Voice fact-find reuses get_brain_llm() with a tighter outer wait_for
# cap; faithfulness is enforced by in-process checks.


def _classify_error(e: BaseException) -> str:
    """Classify a chat exception into a stable string passed to
    llm_health.report_failure(). The string drives degradation duration:
    rate-limit failures (HTTP 429) get a 1h sin-bin; everything else 30s.

    We surface the HTTP status code explicitly because `type(e).__name__`
    is just `"HTTPStatusError"` for both 429 and 503 — losing the signal
    the elector needs to demote-long vs demote-short. When the exception
    carries `.response.status_code == 429` we tag it `"Status429"`; for
    other HTTP statuses we surface e.g. `"HTTPStatusError:503"`; for
    non-HTTP exceptions we keep the class name (`TimeoutException`,
    `ReadTimeout`, etc.).
    """
    cls = type(e).__name__
    try:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None) if resp is not None else None
        if status == 429:
            return "Status429"
        if status is not None:
            return f"{cls}:{status}"
    except Exception:
        pass
    return cls


class NimChainLLM(LLMProvider):
    """Sticky-primary router across multiple candidate models.

    A background probe loop ELECTS a primary + backup per chain based on
    real probe latencies. chat() calls the elected primary ONCE per turn
    (1 LLM call). On failure, it demotes the primary for ~30s and falls
    through to the elected backup (2 LLM calls max). The chain list is
    the CANDIDATE POOL for election, not a per-call sequence.

    Worst case per turn: 2 LLM calls. A final filter_chain refresh path
    handles the rare double-failure edge case so the call still
    degrades gracefully.

    `name` after a successful call reflects which model actually answered
    so downstream callers (brain_used tag, eval logs) can audit which
    candidate produced the output. If primary+backup both fail inside one
    turn, fact_find_brain gets one more bite via BRAIN_CHAIN.
    """
    def __init__(self, chain: list[str], api_key: Optional[str] = None,
                 timeout: float = 30.0, per_model_attempts: int = 1,
                 role: str = "unknown", total_budget_s: Optional[float] = None):
        if not chain:
            raise ValueError("chain must have at least one model")
        self.chain = chain
        self.api_key = api_key or getattr(settings, "NVIDIA_NIM_API_KEY", "")
        self.timeout = timeout
        # Cumulative chain budget. Without a ceiling, a chain of N
        # fallbacks at 30s each could run for minutes per turn. Default
        # to ~2.5× the per-link timeout so a healthy primary always
        # completes but a cascading-failure chain bails fast. With
        # election doing only 1-2 LLM calls per turn the budget is
        # rarely binding; it still guards the final filter_chain-refresh
        # fallback path + cold-start edge cases.
        self.total_budget_s = total_budget_s if total_budget_s is not None else max(timeout * 2.5, 30.0)
        self.per_model_attempts = per_model_attempts
        self.role = role  # 'brain' | 'unknown' — flows into usage log
        self._chain_name = role if role == "brain" else "unknown"
        self.model = chain[0]
        self.name = f"nim-chain::{self._short_id(chain[0])}"

    @staticmethod
    def _short_id(model_id: str) -> str:
        """Build the audit-log suffix used in `self.name`.

        Preserves the provider prefix (`openrouter:` / `groq:`) so logs make
        it obvious which provider answered after a cross-provider fall-
        through. Examples:
          - 'qwen/qwen3-next-80b-a3b-instruct'      -> 'qwen3-next-80b-a3b-instruct'
          - 'openrouter:openai/gpt-oss-120b'        -> 'openrouter:gpt-oss-120b'
          - 'groq:llama-3.3-70b-versatile'          -> 'groq:llama-3.3-70b-versatile'
        """
        if ":" in model_id:
            prefix, rest = model_id.split(":", 1)
            return f"{prefix}:{rest.split('/')[-1]}"
        return model_id.split("/")[-1]

    def _get_worker_for(self, model_id: str, timeout: float) -> LLMProvider:
        """Dispatch a chain entry to the NIM provider client.

        NIM-only: cross-provider (`openrouter:` / `groq:`) prefixes are
        explicitly rejected here. This is a defense-in-depth short-circuit
        — if anyone (admin override, monkeypatch, future drift) injects a
        non-NIM candidate into a chain, the dispatcher raises rather than
        routing to a provider outside the validated pool.

        Passes `chain_name=self._chain_name` so the credit trackers in the
        provider clients route their response-header signals to the right
        chain state.
        """
        if model_id.startswith(("openrouter:", "groq:", "or:")):
            raise RuntimeError(
                f"NimChainLLM ({self._chain_name}): non-NIM candidate "
                f"'{model_id}' rejected. Chains are NIM-only as of KI-155."
            )
        return NvidiaNimLLM(model=model_id, api_key=self.api_key, timeout=timeout)

    @staticmethod
    def _family_of(model_id: str) -> str:
        """Coarse family bucket for cross-grading-independence checks.

        Two models in the SAME family must never be paired as brain ↔ judge
        because they share weights / training corpus / decision surface, so
        the judge would effectively grade its own siblings' output.
        Families: 'qwen', 'mistral', 'meta', 'openai', 'openai_oss',
        'deepseek', 'moonshot', 'minimax', 'nvidia', 'nemotron', 'llama3',
        'google', 'unknown'.

        EVERY model in BRAIN_CHAIN must map to a known family. The
        `assert_family_coverage()` helper walks the chain on demand (from
        tests / admin / probe loop) and raises if any candidate falls
        through to "unknown".
        """
        m = (model_id or "").lower()
        # Recognise Google's Gemini family BEFORE prefix-stripping so ids
        # like "google/gemini-2.5-flash" and bare "gemini-..." both land
        # in the "google" bucket, so the judge-vs-brain cross-family
        # invariant can exclude a Gemini brain from a Gemini judge.
        if "gemini" in m or m.startswith("google/"):
            return "google"
        # Strip provider prefix first so 'groq:llama-3.3-70b' → 'meta' (it IS Meta Llama)
        if ":" in m:
            m = m.split(":", 1)[1]
        # A4 — nemotron is its own decision surface (NVIDIA fine-tuned on
        # top of llama but with materially different post-training); keep
        # it distinct from generic "nvidia/" so cross-family checks don't
        # treat a Llama-4 vs Nemotron pairing as same-family.
        if "nemotron" in m: return "nemotron"
        if "qwen" in m: return "qwen"
        if "mistral" in m: return "mistral"
        # A4 — llama version-aware buckets. llama-3.x is materially distinct
        # from llama-4 (different architecture + post-training). The wider
        # "meta" bucket stays as a fallback for unknown-version llama ids.
        if "llama-3" in m or "llama3" in m: return "llama3"
        if "llama" in m or m.startswith("meta/"): return "meta"
        # A4 — gpt-oss (OpenAI's open-weights line) is distinct from
        # closed-source GPT models. Keep them on separate buckets so a
        # GPT-OSS judge over a GPT-OSS brain isn't accidentally allowed.
        if "gpt-oss" in m: return "openai_oss"
        if m.startswith("openai/") or "gpt-4" in m or "gpt-5" in m: return "openai"
        if "deepseek" in m: return "deepseek"
        if "kimi" in m or m.startswith("moonshot"): return "moonshot"
        if "minimax" in m: return "minimax"
        if m.startswith("nvidia/"): return "nvidia"
        return "unknown"

    # KI-080 — per-call timeout used in the sticky-primary path. Each elected
    # candidate gets at most this long; with at most 2 calls per turn this
    # cleanly fits inside any caller's outer wait_for cap (25s for fact-find,
    # higher for brain/judge).
    _ELECTED_CALL_TIMEOUT_S = 12.0

    async def _call_one(
        self,
        model: str,
        *,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        response_format: Optional[dict],
        timeout: float,
    ) -> LLMResult:
        """Single-model HTTP call. Extracted from the old chain-iteration
        loop (KI-080) so chat() can call a SPECIFIC candidate ONCE without
        the iterate-the-chain envelope.

        Raises whatever the underlying provider raises (TimeoutException /
        HTTPStatusError / network error). Caller is responsible for failure
        handling (report_failure + fall through to backup).
        """
        worker = self._get_worker_for(model, timeout)
        return await worker.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
        exclude_models: Optional[list[str]] = None,
        exclude_families: Optional[list[str]] = None,
    ) -> LLMResult:
        """Sticky primary election. Call ONE elected candidate per turn,
        with at most ONE real-time fallback to the elected backup if
        primary fails. The chain list is the candidate POOL for election —
        NOT a per-call sequence.

        Cold-start path (no probe data yet): use self.chain[0] / [1].
        Brain/judge family-exclusion (e.g. brain doesn't grade own
        homework): apply exclusions to both elected primary + backup; if
        either is excluded, re-elect from the filtered pool by scanning
        the chain in order.
        Final fallback (both elected models fail): trigger a synchronous
        probe refresh + try whatever filter_chain now offers, walking the
        chain in order. This path is the graceful-degradation safety net
        for the double-failure edge case.
        """
        from backend import llm_health

        # Apply caller's exclusion list FIRST (brain doesn't grade own homework).
        excl_m = set(exclude_models or [])
        excl_f = set(exclude_families or [])
        def _allowed(m: Optional[str]) -> bool:
            if not m:
                return False
            if m in excl_m:
                return False
            if self._family_of(m) in excl_f:
                return False
            return True

        # Filter the chain by exclusions (kept for the final-fallback path
        # + cold-start primary/backup election).
        allowed_chain = [m for m in self.chain if _allowed(m)]
        if not allowed_chain:
            # Every candidate excluded — relax family constraint, keep exact
            # model constraint. Better to use a same-family model than to
            # fail the request entirely.
            allowed_chain = [m for m in self.chain if m not in excl_m]
        if not allowed_chain:
            raise RuntimeError("NimChainLLM: every chain candidate is excluded.")

        chain_primary = self.chain[0] if self.chain else None
        call_t0 = time.time()

        # --- Election (KI-080) ----------------------------------------------
        elected_primary: Optional[str] = None
        elected_backup: Optional[str] = None
        try:
            primary_candidate = llm_health.get_primary(self._chain_name)
            backup_candidate = llm_health.get_backup(self._chain_name)
        except Exception:
            primary_candidate, backup_candidate = None, None

        if primary_candidate and _allowed(primary_candidate):
            elected_primary = primary_candidate
        else:
            # Cold-start / excluded election → first allowed chain entry.
            elected_primary = allowed_chain[0]

        if backup_candidate and _allowed(backup_candidate) and backup_candidate != elected_primary:
            elected_backup = backup_candidate
        else:
            # Cold-start / excluded election → first allowed chain entry
            # that isn't the elected primary. Prefer a different provider.
            for m in allowed_chain:
                if m != elected_primary and llm_health.provider_of(m) != llm_health.provider_of(elected_primary):
                    elected_backup = m
                    break
            if elected_backup is None:
                for m in allowed_chain:
                    if m != elected_primary:
                        elected_backup = m
                        break

        tried: list[str] = []
        last_err: Optional[Exception] = None

        async def _try(model: str) -> Optional[LLMResult]:
            """Attempt one elected candidate. On success: stamp self.model
            + self.name, report_success, log usage, return result. On
            failure: report_failure, mutate last_err, return None.

            CancelledError / KeyboardInterrupt / SystemExit are re-raised
            (KI-078) so an outer wait_for cancellation bubbles up
            instead of getting swallowed by the fallback path.
            """
            nonlocal last_err
            tried.append(model)
            attempt_t0 = time.time()
            try:
                result = await self._call_one(
                    model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    timeout=self._ELECTED_CALL_TIMEOUT_S,
                )
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                # KI-078 — propagate cancellation immediately.
                raise
            except Exception as e:
                last_err = e
                try:
                    llm_health.report_failure(
                        self._chain_name, model, _classify_error(e)
                    )
                except Exception:
                    pass
                return None

            # Success — stamp + log.
            latency_ms = int((time.time() - attempt_t0) * 1000)
            try:
                llm_health.report_success(self._chain_name, model, latency_ms)
            except Exception:
                pass
            # NIM has no clean rate-limit header, so we maintain a local
            # 60s rate-meter for every NIM-prefixed
            # candidate (i.e. anything without an openrouter:/groq: prefix).
            # Groq + OpenRouter stamp credits from response headers inside
            # their own .chat() methods.
            if not model.startswith(("openrouter:", "groq:")):
                try:
                    llm_health.record_nim_call(self._chain_name, model)
                except Exception:
                    pass
            self.model = model
            self.name = f"nim-chain::{self._short_id(model)}"
            total_ms = int((time.time() - call_t0) * 1000)
            await _append_usage({
                "ts": _now_iso_z(),
                "role": self.role,
                "chain_primary": chain_primary,
                "served_model": model,
                "latency_ms": total_ms,
                "success": True,
                "elected_primary": elected_primary,
                "elected_backup": elected_backup,
            })
            return result

        # --- Primary attempt -------------------------------------------------
        res = await _try(elected_primary)
        if res is not None:
            return res

        # --- Backup attempt (single real-time fallback) ----------------------
        if elected_backup and elected_backup != elected_primary:
            res = await _try(elected_backup)
            if res is not None:
                return res

        # --- Both elected failed — final safety net --------------------------
        # Trigger ONE synchronous probe refresh; whatever the refreshed
        # filter_chain offers, walk it in order and try anything not yet
        # touched this turn. Graceful-degradation path for the (rare)
        # double-failure case.
        try:
            # KI-099 — bound the probe-refresh cost. On a hot user-facing turn, a
            # 60-80s probe walk through ~10 candidates is unacceptable. If we've
            # already spent half the call's total_budget_s before reaching here,
            # skip the probe refresh and raise immediately — the next turn will
            # benefit from the existing probe cache or the background probe loop
            # (300s cadence) will refresh it.
            elapsed = time.time() - call_t0
            if elapsed > self.total_budget_s * 0.4:
                raise RuntimeError(
                    f"NimChainLLM budget exhausted before probe-refresh ({elapsed:.1f}s > {self.total_budget_s * 0.4:.1f}s); "
                    f"skipping probe_all + raising"
                )
            await llm_health.probe_all()
            refreshed = llm_health.filter_chain(allowed_chain)
            for model in refreshed:
                if model in tried:
                    continue
                elapsed = time.time() - call_t0
                if elapsed >= self.total_budget_s:
                    break
                attempt_t0 = time.time()
                try:
                    per_link_timeout = min(
                        self._ELECTED_CALL_TIMEOUT_S,
                        max(2.0, self.total_budget_s - elapsed),
                    )
                    result = await self._call_one(
                        model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        response_format=response_format,
                        timeout=per_link_timeout,
                    )
                except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    last_err = e
                    try:
                        llm_health.report_failure(
                            self._chain_name, model, _classify_error(e)
                        )
                    except Exception:
                        pass
                    continue

                try:
                    llm_health.report_success(
                        self._chain_name, model, int((time.time() - attempt_t0) * 1000)
                    )
                except Exception:
                    pass
                # KI-085 — bump NIM rate-meter on post-reprobe successes too.
                if not model.startswith(("openrouter:", "groq:")):
                    try:
                        llm_health.record_nim_call(self._chain_name, model)
                    except Exception:
                        pass
                self.model = model
                self.name = f"nim-chain::{self._short_id(model)}"
                total_ms = int((time.time() - call_t0) * 1000)
                await _append_usage({
                    "ts": _now_iso_z(),
                    "role": self.role,
                    "chain_primary": chain_primary,
                    "served_model": model,
                    "latency_ms": total_ms,
                    "success": True,
                    "elected_primary": elected_primary,
                    "elected_backup": elected_backup,
                    "fallback_phase": "post_reprobe",
                })
                return result
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            pass  # probe refresh failure must never block the outer raise

        # Total exhaustion — log + raise. fact_find_brain.drive_fact_find
        # (KI-079) catches the resulting RuntimeError + escalates one more
        # time to BRAIN_CHAIN before falling to the canonical reply.
        latency_ms = int((time.time() - call_t0) * 1000)
        await _append_usage({
            "ts": _now_iso_z(),
            "role": self.role,
            "chain_primary": chain_primary,
            "served_model": None,
            "latency_ms": latency_ms,
            "success": False,
            "elected_primary": elected_primary,
            "elected_backup": elected_backup,
            "tried": tried,
        })
        raise RuntimeError(
            f"NimChainLLM ({self._chain_name}) elected primary={elected_primary} "
            f"and backup={elected_backup} both failed; tried={tried}. "
            f"Last error: {type(last_err).__name__ if last_err else 'None'}: "
            f"{str(last_err)[:120] if last_err else ''}"
        ) from last_err


# Provider load-balancing pure-function. Not wired into the chat hot path
# (the probe-driven elector in backend.llm_health selects candidates);
# retained because the routing regression suite pins its statistical
# behaviour and it is a useful pure-function for ops / sim / overrides.
import random as _random


def _balanced_brain_chain(base: list[str], *, groq_first_probability: float = 0.5) -> list[str]:
    """Provider load-balancing pure-function.

    With `groq_first_probability` (default 50%), hoist the Groq Llama entry
    to the head of the chain so it serves as the primary instead of the
    NIM Qwen entry. The remaining candidates stay in their existing
    fallback order — Groq calls that fail (rare; LPU is very reliable)
    still get the full NIM fallback chain.

    Not wired into the chat hot path (the elector in `backend.llm_health`
    picks candidates dynamically from background probe data). Retained
    because the routing regression suite pins its statistical behaviour
    and it is a useful pure-function for ops / sim / overrides (e.g.
    forcing a non-elected order in a debug script).

    Uses per-call `random.random()` so concurrent async workers each flip
    independently — no shared mutable cycle state, no GIL races, fair
    distribution in aggregate. `groq_first_probability` is overrideable
    primarily for testing."""
    if _random.random() >= groq_first_probability:
        return list(base)  # standard NIM-primary order
    groq_idx = next((i for i, m in enumerate(base) if m.startswith("groq:")), None)
    if groq_idx is None:
        return list(base)
    rotated = list(base)
    groq_model = rotated.pop(groq_idx)
    return [groq_model, *rotated]


def assert_family_coverage() -> dict[str, str]:
    """Verify every model in BRAIN_CHAIN maps to a known family (i.e. NOT
    'unknown'). Returns a {model: family} dict on success; raises
    RuntimeError listing any 'unknown'-mapped models on failure.

    Also verifies nemotron is the TAIL (last entry) of BRAIN_CHAIN so the
    last-resort ordering invariant survives any chain edit.

    Walks BRAIN_CHAIN (the only chain). Call from admin diagnostics or
    tests; not invoked on import to keep cold-start cheap. Safe to run
    from a probe loop tick.
    """
    coverage: dict[str, str] = {}
    unknown: list[str] = []
    for m in BRAIN_CHAIN:
        fam = NimChainLLM._family_of(m)
        coverage[m] = fam
        if fam == "unknown":
            unknown.append(m)
    if unknown:
        raise RuntimeError(
            f"_family_of returned 'unknown' for: {unknown}. "
            "Extend NimChainLLM._family_of so cross-family checks "
            "have a stable bucket for every candidate."
        )
    # KI-175 tail-position invariant — nemotron MUST be the last entry of
    # BRAIN_CHAIN (it's the last-resort fallback).
    nemo_idx = next(
        (i for i, m in enumerate(BRAIN_CHAIN) if "nemotron" in m.lower()), None
    )
    if nemo_idx is not None and nemo_idx != len(BRAIN_CHAIN) - 1:
        raise RuntimeError(
            f"KI-175 violation: nemotron must be the LAST entry of BRAIN_CHAIN "
            f"(found at index {nemo_idx}, chain length {len(BRAIN_CHAIN)})."
        )
    return coverage


def get_brain_llm() -> NimChainLLM:
    """Heavy brain — sticky-primary election over BRAIN_CHAIN.

    The background probe loop in backend.llm_health elects the
    actually-fastest candidate dynamically (probe-driven) plus a backup.
    chat() calls 1-2 candidates max per turn. (The _balanced_brain_chain
    rotation pure-function is available for overrides but not wired in
    here.)

    Per-link 20s timeout, total chain budget 35s (only binding for the
    final filter_chain-refresh safety net).
    """
    return NimChainLLM(chain=BRAIN_CHAIN, timeout=20.0,
                       role="brain", total_budget_s=35.0)


# get_brain_llm() is the brain factory; BRAIN_CHAIN is the only chain.
