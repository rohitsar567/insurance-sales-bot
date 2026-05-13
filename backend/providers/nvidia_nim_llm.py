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
from typing import Optional

import httpx

from backend.config import settings
from backend.providers.base import ChatMessage, LLMProvider, LLMResult


NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
# Heavy brain (complex queries — comparison, recommendation, synthesis):
# DeepSeek-V4-Pro — 1.6T total / 49B active MoE, 1M context, MIT license.
# Beats Opus-4.6 + GPT-5.4 on SimpleQA-Verified (57.9% vs 46.2% / 45.3%) and on
# LiveCodeBench / Codeforces. Used for queries where quality > latency.
NIM_BRAIN_MODEL = "deepseek-ai/deepseek-v4-pro"
# Fast brain (voice turns, fact-find, simple QA):
# DeepSeek-V4-Flash — 284B total / 13B active MoE, 1M context, MIT license.
# 27% of V3.2 single-token FLOPs, 10% of KV cache → significantly lower TTFT
# than V4-Pro. Frontier-tier on HMMT 2026 (94.8%) and LiveCodeBench (91.6%).
# Used for queries where voice latency dominates.
NIM_FAST_BRAIN_MODEL = "deepseek-ai/deepseek-v4-flash"
# Judge: Meta Llama-4 Maverick — 400B total / 17B active MoE, 128 experts.
# Different company, different architecture family from DeepSeek, different
# training corpus — strongest possible cross-grading independence between
# brain and judge ("the brain doesn't mark its own homework").
NIM_JUDGE_MODEL = "meta/llama-4-maverick-17b-128e-instruct"


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


def get_brain_llm() -> NvidiaNimLLM:
    """Heavy brain — DeepSeek-V4-Pro on NIM. Use for complex queries
    (comparison, recommendation, synthesis) where quality > latency."""
    return NvidiaNimLLM(model=NIM_BRAIN_MODEL)


def get_fast_brain_llm() -> NvidiaNimLLM:
    """Fast brain — DeepSeek-V4-Flash on NIM. Use for voice turns and
    fact-find where TTFT latency dominates UX."""
    return NvidiaNimLLM(model=NIM_FAST_BRAIN_MODEL)


def get_judge_llm(language: str = "en") -> NvidiaNimLLM:
    """The grader for faithfulness Gate 4 + Hinglish drift + eval harness.

    Always returns Llama-4 Maverick regardless of language — Meta's MoE
    flagship gives strong multilingual grading, and is a different *family*
    from the DeepSeek brain (different company, different architecture,
    different training corpus). The brain does not mark its own homework.

    `language` arg kept for call-site compatibility with the legacy chain.
    """
    return NvidiaNimLLM(model=NIM_JUDGE_MODEL)
