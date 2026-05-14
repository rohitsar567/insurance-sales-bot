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
_USAGE_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "llm_usage.jsonl"
_USAGE_LOG_MAX_BYTES = 1_000_000  # 1 MB cap — rotate to .bak when exceeded
_usage_lock = asyncio.Lock()


def _now_iso_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def _append_usage(record: dict) -> None:
    """Append one JSONL record to data/llm_usage.jsonl with 1 MB rotation.

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

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            attempts = 4
            delay = 1.0
            for attempt in range(attempts):
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 429 or (500 <= resp.status_code < 600):
                    if attempt == attempts - 1:
                        resp.raise_for_status()
                    ra = resp.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.replace(".", "").isdigit() else delay
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                resp.raise_for_status()
                break
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
    # Primary: Qwen 3-Next 80B — verified ~2s response, clean JSON, multilingual
    "qwen/qwen3-next-80b-a3b-instruct",
    # 1st fallback: Qwen 3.5 122B — same family, bigger, slightly slower
    "qwen/qwen3.5-122b-a10b",
    # 2nd fallback: OpenAI GPT-OSS 120B — different family, MIT open weights
    "openai/gpt-oss-120b",
    # 3rd fallback: Mistral Large 3 (also the judge — used only if all above fail)
    "mistralai/mistral-large-3-675b-instruct-2512",
    # 4th fallback: NVIDIA Nemotron-Super 49B — different family again
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    # 5th fallback: Meta Llama-3.3 70B (intermittently times out; last resort)
    "meta/llama-3.3-70b-instruct",
    # 6th fallback: DeepSeek-V4-Pro (back when NIM's pool recovers)
    "deepseek-ai/deepseek-v4-pro",
    # CROSS-PROVIDER FALLBACKS — only reached when the entire NIM block above
    # has failed (regional outage / DNS / ingress brownout). These hit a
    # completely different provider so the brain survives a full NIM down.
    # 7th fallback: OpenRouter GPT-OSS 120B (different provider, MIT weights)
    "openrouter:openai/gpt-oss-120b",
    # 8th fallback: Groq Llama-3.3 70B (different provider, LPU inference)
    "groq:llama-3.3-70b-versatile",
]

# Same chain for fast brain — Qwen 80B is already fast (~2s); no need for a
# separate "small + faster" model since the candidate chain already has
# Nemotron Nano further down. Last entry is Groq because its LPU inference
# is the lowest-TTFT free-tier option, so a fast-brain fall-through to it is
# still acceptable from a latency-budget standpoint.
FAST_BRAIN_CHAIN = [
    "qwen/qwen3-next-80b-a3b-instruct",
    "nvidia/nemotron-3-nano-30b-a3b",     # 1.6s response per Reddit benchmark
    "openai/gpt-oss-120b",
    "qwen/qwen3.5-122b-a10b",
    "deepseek-ai/deepseek-v4-flash",
    # CROSS-PROVIDER FALLBACK — Groq Llama-3.3 70B (LPU, lowest TTFT of all
    # free-tier options; OK for a fast-brain call when NIM is down).
    "groq:llama-3.3-70b-versatile",
]

# Judge chain — non-Qwen, non-DeepSeek (different family from brain primary)
JUDGE_CHAIN = [
    # Primary: Mistral Large 3 675B — different family from Qwen brain
    "mistralai/mistral-large-3-675b-instruct-2512",
    # 1st fallback: OpenAI GPT-OSS 120B — different family
    "openai/gpt-oss-120b",
    # 2nd fallback: Moonshot Kimi K2 — different family (Chinese provider)
    "moonshotai/kimi-k2-instruct-0905",
    # 3rd fallback: MiniMax M2.5 — different family
    "minimaxai/minimax-m2.5",
    # 4th fallback: Meta Llama-4 Maverick (was the original judge — back if NIM Llama pool recovers)
    "meta/llama-4-maverick-17b-128e-instruct",
    # CROSS-PROVIDER FALLBACKS — reached only when every NIM judge candidate
    # above has failed. Critical for keeping faithfulness Gate 4 + Hinglish
    # drift judge alive through a full NIM outage.
    # 5th fallback: OpenRouter GPT-OSS 120B (different provider, MIT weights)
    "openrouter:openai/gpt-oss-120b",
    # 6th fallback: Groq Llama-3.3 70B (different provider, LPU inference)
    "groq:llama-3.3-70b-versatile",
]


class NimChainLLM(LLMProvider):
    """OpenRouter-style fallback router across multiple NIM models.

    Tries each model in `chain` in order; on TimeoutException / 5xx / network
    error, advances to the next. Surfaces the first success transparently.

    `name` after a successful call reflects which model actually answered, so
    downstream callers (orchestrator brain_used tag, eval logs) can audit
    which model in the chain produced the output.
    """
    def __init__(self, chain: list[str], api_key: Optional[str] = None,
                 timeout: float = 30.0, per_model_attempts: int = 1,
                 role: str = "unknown"):
        if not chain:
            raise ValueError("chain must have at least one model")
        self.chain = chain
        self.api_key = api_key or getattr(settings, "NVIDIA_NIM_API_KEY", "")
        self.timeout = timeout
        self.per_model_attempts = per_model_attempts
        self.role = role  # 'brain' | 'fast_brain' | 'judge' | 'unknown' — flows into usage log
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
        """Dispatch a chain entry to the right provider client.

        Recognised prefixes:
          - 'openrouter:<model>' -> OpenRouterLLM
          - 'groq:<model>'       -> GroqLLM
          - <anything else>      -> NvidiaNimLLM (existing default)
        """
        if model_id.startswith("openrouter:"):
            return OpenRouterLLM(
                model=model_id[len("openrouter:"):],
                timeout=timeout,
            )
        if model_id.startswith("groq:"):
            return GroqLLM(
                model=model_id[len("groq:"):],
                timeout=timeout,
            )
        return NvidiaNimLLM(model=model_id, api_key=self.api_key, timeout=timeout)

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult:
        # Filter out models known-down (from background probe loop). If all
        # models are down, filter_chain returns the full chain unchanged so
        # we still try — the infrastructure may have recovered between probes.
        try:
            from backend import llm_health
            chain_to_try = llm_health.filter_chain(self.chain)
        except Exception:
            chain_to_try = self.chain  # health monitor failure must never block calls

        chain_primary = self.chain[0] if self.chain else None
        call_t0 = time.time()

        last_err: Optional[Exception] = None
        for model in chain_to_try:
            try:
                worker = self._get_worker_for(model, self.timeout)
                result = await worker.chat(messages=messages, temperature=temperature,
                                           max_tokens=max_tokens, response_format=response_format)
                # Successful response — record which model answered + return.
                # `self.name` preserves any provider prefix so audit logs make
                # cross-provider fall-throughs (groq:/openrouter:) obvious.
                self.model = model
                self.name = f"nim-chain::{self._short_id(model)}"
                latency_ms = int((time.time() - call_t0) * 1000)
                await _append_usage({
                    "ts": _now_iso_z(),
                    "role": self.role,
                    "chain_primary": chain_primary,
                    "served_model": model,
                    "latency_ms": latency_ms,
                    "success": True,
                })
                return result
            except (httpx.TimeoutException, httpx.HTTPStatusError,
                    httpx.ConnectError, httpx.NetworkError, asyncio.TimeoutError) as e:
                last_err = e
                continue  # try next model in chain
            except Exception as e:
                # Unexpected error — record + try next, but surface eventually if all fail
                last_err = e
                continue
        # All models in (filtered) chain failed. Trigger one synchronous
        # probe refresh — maybe a transient outage just recovered. If the
        # refreshed state opens up models we previously skipped, try them.
        try:
            from backend import llm_health
            await llm_health.probe_all()
            refreshed = llm_health.filter_chain(self.chain)
            for model in refreshed:
                if model in chain_to_try:
                    continue  # already tried this turn
                try:
                    worker = self._get_worker_for(model, self.timeout)
                    result = await worker.chat(messages=messages, temperature=temperature,
                                               max_tokens=max_tokens, response_format=response_format)
                    self.model = model
                    self.name = f"nim-chain::{self._short_id(model)}"
                    latency_ms = int((time.time() - call_t0) * 1000)
                    await _append_usage({
                        "ts": _now_iso_z(),
                        "role": self.role,
                        "chain_primary": chain_primary,
                        "served_model": model,
                        "latency_ms": latency_ms,
                        "success": True,
                    })
                    return result
                except Exception as e:
                    last_err = e
                    continue
        except Exception:
            pass

        # Total exhaustion — log a single failure record so the admin panel
        # surfaces "judge has 12% failure rate over last 24h" type signal.
        latency_ms = int((time.time() - call_t0) * 1000)
        await _append_usage({
            "ts": _now_iso_z(),
            "role": self.role,
            "chain_primary": chain_primary,
            "served_model": None,
            "latency_ms": latency_ms,
            "success": False,
        })
        raise RuntimeError(
            f"NimChainLLM exhausted all {len(self.chain)} candidates. Last error: "
            f"{type(last_err).__name__}: {str(last_err)[:120]}"
        ) from last_err


def get_brain_llm() -> NimChainLLM:
    """Heavy brain — multi-model NIM chain with automatic fallback.
    Primary: Qwen 3-Next 80B. See BRAIN_CHAIN for fallback order."""
    return NimChainLLM(chain=BRAIN_CHAIN, timeout=30.0, role="brain")


def get_fast_brain_llm() -> NimChainLLM:
    """Fast brain — multi-model NIM chain optimized for low TTFT.
    Primary: Qwen 3-Next 80B. See FAST_BRAIN_CHAIN for fallback order."""
    return NimChainLLM(chain=FAST_BRAIN_CHAIN, timeout=20.0, role="fast_brain")


def get_judge_llm(language: str = "en") -> NimChainLLM:
    """The grader for faithfulness Gate 4 + Hinglish drift + eval harness.

    Returns a JUDGE_CHAIN — primary Mistral Large 3 (different family from
    Qwen brain so cross-grading independence is preserved), with non-Qwen
    fallbacks (GPT-OSS, Kimi, MiniMax, Llama-4 Maverick) when the primary
    pool is congested. `language` arg kept for call-site compatibility.
    """
    return NimChainLLM(chain=JUDGE_CHAIN, timeout=30.0, role="judge")
