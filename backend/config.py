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
    # Heavy brain (quality > latency): DeepSeek-V4-Pro (1.6T/49B MoE)
    # Fast brain (latency > quality): DeepSeek-V4-Flash (284B/13B MoE)
    # Judge: Meta Llama-4 Maverick (400B/17B MoE) — different family = cross-grading independence
    NVIDIA_NIM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_NIM_BRAIN_MODEL: str = "deepseek-ai/deepseek-v4-pro"
    NVIDIA_NIM_FAST_BRAIN_MODEL: str = "deepseek-ai/deepseek-v4-flash"
    NVIDIA_NIM_JUDGE_MODEL: str = "meta/llama-4-maverick-17b-128e-instruct"

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
