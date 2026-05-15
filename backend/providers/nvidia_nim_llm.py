"""NVIDIA NIM — primary brain + judge for the entire reasoning stack.

NIM exposes an OpenAI-compatible chat completions endpoint at
POST https://integrate.api.nvidia.com/v1/chat/completions.

Why NIM:
  - Frontier open-weights models hosted free (no card, no daily cap, 40 req/min)
  - Single provider replaces OpenRouter + DeepSeek-direct + Cerebras + Groq
  - Same Bearer-auth + OpenAI request shape — drop-in retry/backoff

Roles (tiered routing — pick brain by intent classification):
  - Heavy brain (comparison / recommendation / synthesis): deepseek-ai/deepseek-v4-pro
      DeepSeek's frontier MoE (1.6T total / 49B active, 1M context, MIT-
      licensed). Beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified and
      LiveCodeBench. Used when quality > latency.
  - Fast brain (voice turns / fact-find / simple QA): deepseek-ai/deepseek-v4-flash
      284B total / 13B active MoE, 1M context, MIT-licensed. ~27% FLOPs of
      V3.2 → significantly lower TTFT. Frontier-tier on HMMT 2026 + LiveCode-
      Bench. Used when voice latency dominates.
  - Judge (faithfulness Gate 4 + Hinglish drift LLM-judge): meta/llama-4-maverick-17b-128e-instruct
      Meta's MoE flagship (17B active / 400B total, 128 experts). Different
      company, different architecture, different training corpus from the
      brain — strongest cross-grading independence. The brain (DeepSeek) does
      not mark its own homework.

Sarvam stays for voice STT/TTS + Hindi/Hinglish/vernacular translation.
Everything else (brain, judge, eval grader) runs through this module.
"""

from __future__ import annotations

import asyncio
import json
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
_USAGE_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "40-data" / "llm_usage.jsonl"
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


# KI-088 (2026-05-15) — Global outbound NIM concurrency cap.
#
# Live probe at commit 6a47549 measured 20% brain-turn success despite
# KI-080 election + KI-085 credit gating + KI-087 NIM-first + KI-079
# escalation all live. The architecture is correct; the binding constraint
# is NIM's free-tier per-key concurrency (~3-5 slots).
#
# We have 5 sources of NIM traffic that all stack on the same key with no
# global throttle: probe loop (6-slot parallel burst every 300s), admin
# tab polling (every 30s), per-user chat turn (1-2 calls), concurrent
# users (N × 1-2), and the inner 4-attempt retry loop (holds a slot up
# to 15s on 429/5xx backoff).
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


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
# 2026-05-14 brain swap (D-022): NIM's DeepSeek-V4 + Meta Llama inference pools
# are repeatedly timing out (15-120s on chat completions, no response). Qwen
# pool is consistently fast (2s response, clean structured output). Mistral
# Large 3 (Reddit benchmark: 4.3s, works on free tier) is the cross-family
# judge replacing the timing-out Llama-4 Maverick.
#
# Heavy brain (complex queries — comparison, recommendation, synthesis):
# Qwen 3-Next 80B — 80B / 3B active MoE, frontier multilingual, very fast.
NIM_BRAIN_MODEL = "qwen/qwen3-next-80b-a3b-instruct"
# Fast brain (voice turns, fact-find, simple QA):
# Same model — Qwen 3-Next 80B is already fast (~2s response) and routes
# different intents to the same pool. Tiered routing kept for forward-compat.
NIM_FAST_BRAIN_MODEL = "qwen/qwen3-next-80b-a3b-instruct"
# Judge: Mistral Large 3 — 675B dense, MIT license. Different family from
# Qwen brain (Mistral vs Alibaba) so the judge sees the brain's output from
# a genuinely different decision surface ("the brain doesn't mark its own
# homework"). Reddit benchmark confirms ~4.3s response on NIM free tier.
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
            body["response_format"] = response_format

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
        # KI-088 (2026-05-15) — drop the inner 4-attempt exponential
        # backoff retry. The previous loop held a NIM concurrency slot
        # for up to ~15s on 429/5xx while sleeping between attempts,
        # which directly contributed to the queueing that the outer
        # semaphore is now sized to prevent.
        #
        # Retry was a pre-KI-080 vestige that predated the chain
        # election architecture. With KI-080 in place, NimChainLLM
        # already handles 429/5xx failover by:
        #   (1) catching the exception in _try(),
        #   (2) calling report_failure() so the elector demotes the
        #       slot-blocked candidate for ~30s, and
        #   (3) falling through to the elected backup (cross-provider
        #       by preference) within the same turn.
        # KI-079 then provides one more bite via BRAIN_CHAIN if both
        # elected models fail. Per-call retries inside this method
        # would only re-queue against the same slot-starved pool,
        # multiplying the queueing problem the semaphore fixes.
        #
        # The semaphore wraps ONLY the HTTP round-trip — not response
        # parsing or usage logging — so we cap concurrent network
        # traffic without serialising the rest of the pipeline.
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            async with _NIM_OUTBOUND_SEMAPHORE:
                resp = await client.post(url, headers=headers, json=body)
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
    # KI-155 (2026-05-15) — NIM-ONLY ENFORCEMENT. Cross-provider (Groq /
    # OpenRouter) fallbacks REMOVED. Groq's Llama-3.3-70B failed the `<FF>`
    # trailer contract during a fact-find probe and silently flipped the
    # entire pipeline to scripted prompts. Chain is now strictly NIM. Each
    # NIM candidate's `<FF>` adherence has been verified or is structurally
    # safer (different family / smaller routing surface). Pruned candidates
    # that have been "down" for 48+ consecutive probes (qwen3.5-122b,
    # gpt-oss-120b, deepseek-v4-pro) so the election pool only contains
    # demonstrably-healthy NIM models.
    # Primary: Qwen 3-Next 80B — 5/5 recent probes ok, clean JSON, multilingual
    "qwen/qwen3-next-80b-a3b-instruct",
    # 1st fallback: NVIDIA Nemotron-Super 49B — recent 3/3 probes ok, ~354ms
    # latency, different family (nvidia) from Qwen primary → preserves
    # cross-family-grading invariant if elevated to judge.
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    # 2nd fallback: Mistral Large 3 675B — recent 3/3 probes ok, different
    # family (mistral). Also the judge primary; only reached when both Qwen +
    # Nemotron are unavailable.
    "mistralai/mistral-large-3-675b-instruct-2512",
    # 3rd fallback: Meta Llama-4 Maverick 17B — 5/5 probes ok, different
    # family (meta), keeps the chain alive through a single-family outage.
    "meta/llama-4-maverick-17b-128e-instruct",
]

# Same chain for fast brain — Qwen 80B is already fast (~2s); no need for a
# separate "small + faster" model since the candidate chain already has
# Nemotron Nano further down. Last entry is Groq because its LPU inference
# is the lowest-TTFT free-tier option, so a fast-brain fall-through to it is
# still acceptable from a latency-budget standpoint.
FAST_BRAIN_CHAIN = [
    # KI-155 (2026-05-15) — NIM-ONLY ENFORCEMENT. Groq Llama-3.3-70B
    # REMOVED from candidate #2 (the KI-079 promotion) after it failed the
    # `<FF>` trailer contract in a live fact-find probe, silently
    # cascading the orchestrator to scripted prompts. Also dropped models
    # that have been "down" for 48+ consecutive probes (nemotron-3-nano-30b
    # = empty_content, qwen3.5-122b = timeout, gpt-oss-120b = empty_content,
    # deepseek-v4-flash = timeout) so the election pool only contains
    # demonstrably-healthy NIM models. With election (KI-080) picking the
    # actually-fastest healthy candidate per turn, chain order matters
    # only for cold-start; the elector handles steady-state.
    # Primary: Qwen 3-Next 80B — 5/5 recent probes ok, ~2s, multilingual,
    # verified `<FF>` adherence in production traffic.
    "qwen/qwen3-next-80b-a3b-instruct",
    # 1st fallback: NVIDIA Nemotron-Super 49B — 3/3 recent ok, ~354ms
    # (fastest healthy NIM model), different family for diversity.
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    # 2nd fallback: Mistral Large 3 675B — 3/3 recent ok, different family,
    # keeps fact-find alive through a Qwen+Nemotron simultaneous outage.
    "mistralai/mistral-large-3-675b-instruct-2512",
]

# Judge chain — non-Qwen (different family from brain primary so the judge
# never grades its own family's output).
# KI-155 (2026-05-15) — NIM-ONLY ENFORCEMENT. Groq + OpenRouter REMOVED.
# Dropped candidates that have been "down" for 48+ consecutive probes
# (gpt-oss-120b = empty_content, kimi-k2 = http_404, minimax-m2.5 = http_410)
# so the election pool only contains demonstrably-healthy NIM models.
JUDGE_CHAIN = [
    # Primary: Mistral Large 3 675B — 3/3 recent ok, different family
    # (mistral) from Qwen brain, preserves cross-family grading invariant.
    "mistralai/mistral-large-3-675b-instruct-2512",
    # 1st fallback: Meta Llama-4 Maverick 17B — 5/5 probes ok, different
    # family (meta), original judge primary pre-KI-080.
    "meta/llama-4-maverick-17b-128e-instruct",
    # 2nd fallback: NVIDIA Nemotron-Super 49B — 3/3 recent ok, different
    # family (nvidia/nemotron) from Qwen brain. Note: branded "llama" but
    # NVIDIA-finetuned, distinct decision surface.
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
]


def _classify_error(e: BaseException) -> str:
    """KI-084 — classify a chat exception into a stable string passed to
    llm_health.report_failure(). The string drives degradation duration:
    rate-limit failures (HTTP 429) get a 1h sin-bin; everything else 30s.

    We surface the HTTP status code explicitly because `type(e).__name__`
    is just `"HTTPStatusError"` for both 429 and 503 — losing the signal
    the elector needs to demote-long vs demote-short. When the exception
    carries `.response.status_code == 429` we tag it `"Status429"`; for
    other HTTP statuses we surface e.g. `"HTTPStatusError:503"`; for
    non-HTTP exceptions we keep the class name (`TimeoutException`,
    `ReadTimeout`, etc.) — matching the pre-KI-084 contract.
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
    """KI-080 sticky-primary router across multiple candidate models.

    Architectural shift (KI-080, 2026-05-15):
      PRE-KI-080: chat() iterated the full chain sequentially every turn.
        Under NIM per-key concurrency throttling, the first 5 NIM-hosted
        candidates queued together inside ONE turn and burned the 22s
        budget before any cross-provider fallback was ever reached. The
        10-turn live probe at commit 078ff45 showed 7/10 fact-find turns
        timing out at exactly 26.6s.

      POST-KI-080: a background probe loop ELECTS a primary + cross-
        provider backup per chain based on real probe latencies. chat()
        calls the elected primary ONCE per turn (1 LLM call). On failure,
        we demote the primary for ~30s and fall through to the elected
        backup (still 2 LLM calls max). The chain list is now the
        CANDIDATE POOL for election, not a per-call sequence.

    Worst case per turn: 2 LLM calls (was 5-6). Plus a final filter_chain
    refresh path is kept for the rare double-failure edge case so the
    pre-KI-080 graceful-degradation behaviour is preserved.

    `name` after a successful call reflects which model actually answered
    so downstream callers (orchestrator brain_used tag, eval logs) can
    audit which candidate produced the output. The KI-079 escalation in
    fact_find_brain still applies — if primary+backup both fail inside
    one turn, fact_find_brain gets one more bite via BRAIN_CHAIN.
    """
    def __init__(self, chain: list[str], api_key: Optional[str] = None,
                 timeout: float = 30.0, per_model_attempts: int = 1,
                 role: str = "unknown", total_budget_s: Optional[float] = None):
        if not chain:
            raise ValueError("chain must have at least one model")
        self.chain = chain
        self.api_key = api_key or getattr(settings, "NVIDIA_NIM_API_KEY", "")
        self.timeout = timeout
        # KI-021 (2026-05-14) — cumulative chain budget. If the chain has 8
        # fallbacks at 30s each, worst-case wall-clock is 4 min per turn — that
        # produced the p99 58s+ tail in the 100-persona audit. Default to a
        # cumulative ceiling of ~2.5× the per-link timeout so a healthy primary
        # always completes, but a cascading-failure chain bails fast.
        #
        # KI-080 — with election we only do 1-2 LLM calls per turn, so the
        # budget is rarely the binding constraint anymore. Kept for the
        # final filter_chain-refresh fallback path + cold-start edge cases.
        self.total_budget_s = total_budget_s if total_budget_s is not None else max(timeout * 2.5, 30.0)
        self.per_model_attempts = per_model_attempts
        self.role = role  # 'brain' | 'fast_brain' | 'judge' | 'unknown' — flows into usage log
        self._chain_name = role if role in ("brain", "fast_brain", "judge") else "unknown"
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

        KI-155 (2026-05-15) — NIM-ONLY ENFORCEMENT. Cross-provider
        (`openrouter:` / `groq:`) prefixes are explicitly rejected here even
        though the chains no longer contain them. This is a defense-in-depth
        short-circuit: if anyone (admin override, monkeypatch, future drift)
        injects a non-NIM candidate into a chain, the dispatcher raises
        rather than silently routing to a provider that has demonstrated
        contract drift (Groq Llama-3.3-70B / `<FF>` trailer failure).

        KI-085 — passes `chain_name=self._chain_name` so the credit
        trackers in the provider clients route their response-header
        signals to the right chain state.
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
        Families: 'qwen', 'mistral', 'meta', 'openai', 'deepseek', 'moonshot',
        'minimax', 'nvidia', 'unknown'.
        """
        m = model_id.lower()
        # Strip provider prefix first so 'groq:llama-3.3-70b' → 'meta' (it IS Meta Llama)
        if ":" in m:
            m = m.split(":", 1)[1]
        if "qwen" in m: return "qwen"
        if "mistral" in m: return "mistral"
        if "llama" in m or m.startswith("meta/"): return "meta"
        if "gpt-oss" in m or m.startswith("openai/"): return "openai"
        if "deepseek" in m: return "deepseek"
        if "kimi" in m or m.startswith("moonshot"): return "moonshot"
        if "minimax" in m: return "minimax"
        if "nemotron" in m or m.startswith("nvidia/"): return "nvidia"
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
        """KI-080 — sticky primary election. Call ONE elected candidate per
        turn, with at most ONE real-time fallback to the elected backup if
        primary fails. The chain list is the candidate POOL for election —
        NOT a per-call sequence.

        Cold-start path (no probe data yet): use self.chain[0] / [1].
        Brain/judge family-exclusion (e.g. brain doesn't grade own
        homework): apply exclusions to both elected primary + backup; if
        either is excluded, re-elect from the filtered pool by scanning
        the chain in order.
        Final fallback (both elected models fail): trigger a synchronous
        probe refresh + try whatever filter_chain now offers, walking the
        chain in order. This path is the pre-KI-080 graceful-degradation
        safety net for the double-failure edge case.
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
            # KI-085 (2026-05-15) — NIM has no clean rate-limit header, so
            # we maintain a local 60s rate-meter for every NIM-prefixed
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

        # --- Backup attempt (KI-080 single real-time fallback) ---------------
        if elected_backup and elected_backup != elected_primary:
            res = await _try(elected_backup)
            if res is not None:
                return res

        # --- Both elected failed — final safety net --------------------------
        # Trigger ONE synchronous probe refresh; whatever the refreshed
        # filter_chain offers, walk it in order and try anything we haven't
        # touched this turn. This is the pre-KI-080 graceful-degradation
        # behaviour preserved for the (rare) double-failure case.
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


# KI-025 (2026-05-14) — provider load-balancing.
# DEPRECATED 2026-05-15 by KI-080: primary election supersedes the 50/50
# rotation. The probe loop now picks the actually-faster candidate
# DYNAMICALLY (real probe latencies, not a coin flip), and the elected
# backup is chosen with explicit cross-provider preference — both signals
# the rotation was approximating heuristically. Kept around (not deleted)
# because the regression suite still pins its statistical behaviour. The
# get_*_llm factories no longer call it.
import random as _random


def _balanced_brain_chain(base: list[str], *, groq_first_probability: float = 0.5) -> list[str]:
    """KI-025 — provider load-balancing (DEPRECATED 2026-05-15 by KI-080).

    With `groq_first_probability` (default 50%), hoist the Groq Llama entry
    to the head of the chain so it serves as the primary instead of the
    NIM Qwen entry. The remaining candidates stay in their existing
    fallback order — Groq calls that fail (rare; LPU is very reliable)
    still get the full NIM fallback chain.

    SUPERSESSION NOTE (KI-080): the elector in `backend.llm_health` now
    picks the actually-faster candidate dynamically from background probe
    data, so this static-coin-flip rotation is no longer wired into the
    chat hot path. Kept exported because:
      (a) regression tests still pin its statistical behaviour, and
      (b) it remains a useful pure-function for ops / sim / overrides
          (e.g. wanting to force a non-elected order in a debug script).

    Uses per-call `random.random()` so concurrent async workers (the
    100-persona audit's 4 workers, the parallel 96-Q eval's 6 workers) each
    flip independently — no shared mutable cycle state, no GIL races, fair
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


def get_brain_llm() -> NimChainLLM:
    """Heavy brain — KI-080 sticky-primary election over BRAIN_CHAIN.

    Pre-KI-080: per-call _balanced_brain_chain rotation between NIM Qwen
    and Groq Llama (KI-025 50/50 heuristic) → 5-6 LLM calls per turn
    under degraded conditions.

    Post-KI-080: the background probe loop in backend.llm_health elects
    the actually-fastest candidate dynamically (probe-driven, not coin-
    flipped) and a cross-provider backup. chat() calls 1-2 candidates max
    per turn. The rotation is preserved as a deprecated pure-function in
    case overrides need it, but the factory no longer wires it in.

    KI-021 — per-link 12s (KI-080 ELECTED_CALL_TIMEOUT_S), total chain
    budget 35s (only binding for the final filter_chain-refresh safety net).
    """
    return NimChainLLM(chain=BRAIN_CHAIN, timeout=20.0,
                       role="brain", total_budget_s=35.0)


def get_fast_brain_llm() -> NimChainLLM:
    """Fast brain — KI-080 sticky-primary election over FAST_BRAIN_CHAIN.

    Pre-KI-080 (KI-025 + KI-078 + KI-079): per-call rotation between NIM
    Qwen and Groq Llama, 6s per-link timeout, 22s total chain budget,
    Groq promoted to chain position #2 so a single NIM degradation could
    fall through fast enough. Even so, the 10-turn live probe at commit
    078ff45 showed 7/10 fact-find turns hitting the wait_for cap at 26.6s
    because every chain-iteration burned the full budget exploring 5+
    queued NIM candidates.

    Post-KI-080: probe-driven election picks ONE primary per chain (the
    fastest-responding healthy candidate by rolling probe data) and ONE
    cross-provider backup. chat() invokes the primary ONCE with a 12s
    timeout, falls to the backup ONCE on failure, and only walks the
    filter_chain fallback as a final safety net. The KI-079 escalation in
    fact_find_brain still runs if primary+backup both fail, providing one
    more bite via BRAIN_CHAIN.

    KI-025 supersession: the 50/50 rotation is no longer wired in — see
    the docstring on `_balanced_brain_chain` for the reasoning.
    """
    return NimChainLLM(chain=FAST_BRAIN_CHAIN, timeout=6.0,
                       role="fast_brain", total_budget_s=22.0)


def get_judge_llm(language: str = "en") -> NimChainLLM:
    """The grader for faithfulness Gate 4 + Hinglish drift + eval harness.

    Returns a JUDGE_CHAIN — primary Mistral Large 3 (different family from
    Qwen brain so cross-grading independence is preserved), with non-Qwen
    fallbacks (GPT-OSS, Kimi, MiniMax, Llama-4 Maverick) when the primary
    pool is congested. `language` arg kept for call-site compatibility.
    """
    return NimChainLLM(chain=JUDGE_CHAIN, timeout=30.0, role="judge")
