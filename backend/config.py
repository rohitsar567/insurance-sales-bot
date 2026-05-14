"""Centralized settings loaded from .env via pydantic-settings.

All API keys + tunables live here. Never read os.environ directly elsewhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    # Provider keys
    SARVAM_API_KEY: str = os.environ.get("SARVAM_API_KEY", "")
    VOYAGE_API_KEY: str = os.environ.get("VOYAGE_API_KEY", "")
    # NVIDIA NIM — single provider hosting the entire reasoning stack:
    #   Brain  = meta/llama-3.3-70b-instruct
    #   Judge  = meta/llama-4-maverick-17b-128e-instruct (different arch from brain)
    # Free tier: 40 req/min, no daily cap, no card. Replaces OpenRouter +
    # direct DeepSeek + Cerebras + Groq (four legacy providers retired
    # 2026-05-14 in favor of single-provider consolidation — see D-019).
    NVIDIA_NIM_API_KEY: str = os.environ.get("NVIDIA_NIM_API_KEY", "")

    # CROSS-PROVIDER FALLBACKS — last-resort entries appended to BRAIN_CHAIN +
    # FAST_BRAIN_CHAIN + JUDGE_CHAIN so the brain + judge survive a full NIM
    # outage (regional ingress brownout, full-pool 5xx, etc.). NIM remains
    # the PRIMARY provider — these only get hit after every NIM candidate in
    # the chain has failed. Both keys are optional: if unset the fallback is
    # simply skipped by NimChainLLM and the chain continues.
    #   OPENROUTER_API_KEY — https://openrouter.ai/keys  (free-tier OSS models)
    #   GROQ_API_KEY       — https://console.groq.com/keys (LPU inference, lowest TTFT)
    OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")

    # Sarvam endpoints (voice STT/TTS + Indic translation only — not brain anymore)
    SARVAM_BASE_URL: str = "https://api.sarvam.ai"
    SARVAM_STT_PATH: str = "/speech-to-text"
    SARVAM_TTS_PATH: str = "/text-to-speech"
    SARVAM_CHAT_PATH: str = "/v1/chat/completions"

    # Sarvam model identifiers
    SARVAM_STT_MODEL: str = "saarika:v2.5"
    SARVAM_TTS_MODEL: str = "bulbul:v2"
    SARVAM_TTS_SPEAKER: str = "anushka"  # natural female advisor voice
    SARVAM_LLM_MODEL: str = "sarvam-m"  # used by translator.py for Indic translation

    # Voyage (legacy — embeddings now via local BGE; kept for back-compat with extracted/ artifacts)
    VOYAGE_MODEL: str = "voyage-3"

    # NVIDIA NIM (single source of truth for brain + judge — tiered routing)
    # 2026-05-14 brain swap (D-022): DeepSeek-V4 + Meta Llama NIM pools time out
    # repeatedly. Qwen 3-Next 80B + Mistral Large 3 are the working production
    # models on NIM free tier as of 2026-05-14.
    NVIDIA_NIM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_NIM_BRAIN_MODEL: str = "qwen/qwen3-next-80b-a3b-instruct"
    NVIDIA_NIM_FAST_BRAIN_MODEL: str = "qwen/qwen3-next-80b-a3b-instruct"
    NVIDIA_NIM_JUDGE_MODEL: str = "mistralai/mistral-large-3-675b-instruct-2512"

    # Storage paths
    CORPUS_DIR: Path = ROOT / "rag" / "corpus"
    EXTRACTED_DIR: Path = ROOT / "rag" / "extracted"
    VECTORS_DIR: Path = ROOT / "rag" / "vectors"
    STRUCTURED_DB: Path = ROOT / "rag" / "policies.duckdb"

    # Tunables (overrideable via env vars so the hyperparameter sweep can iterate)
    CHUNK_TOKENS: int = int(os.environ.get("CHUNK_TOKENS", "800"))
    CHUNK_OVERLAP_TOKENS: int = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "120"))
    RAG_TOP_K: int = int(os.environ.get("RAG_TOP_K", "5"))

    @classmethod
    def validate(cls) -> list[str]:
        """Return list of missing required keys. Empty list = healthy."""
        missing = []
        for k in ("SARVAM_API_KEY", "NVIDIA_NIM_API_KEY"):
            if not getattr(cls, k):
                missing.append(k)
        return missing


settings = Settings()
