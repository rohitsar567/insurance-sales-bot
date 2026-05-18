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
    # NVIDIA NIM — single provider hosting the reasoning stack (brain +
    # judge; concrete model IDs are set on NVIDIA_NIM_*_MODEL below).
    # Free tier: 40 req/min, no daily cap, no card.
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

    # Sarvam endpoints (voice STT/TTS + Indic translation only)
    SARVAM_BASE_URL: str = "https://api.sarvam.ai"
    SARVAM_STT_PATH: str = "/speech-to-text"
    SARVAM_TTS_PATH: str = "/text-to-speech"
    SARVAM_CHAT_PATH: str = "/v1/chat/completions"

    # Sarvam model identifiers
    SARVAM_STT_MODEL: str = "saarika:v2.5"
    SARVAM_TTS_MODEL: str = "bulbul:v2"
    SARVAM_TTS_SPEAKER: str = "anushka"  # natural female advisor voice
    SARVAM_LLM_MODEL: str = "sarvam-m"  # Sarvam model for Indic translation

    # Voyage — embeddings run on local BGE; this is kept for back-compat
    # with existing extracted/ artifacts.
    VOYAGE_MODEL: str = "voyage-3"

    # NVIDIA NIM (single source of truth for brain + judge — tiered
    # routing). Qwen 3-Next 80B + Mistral Large 3 are the production
    # models on NIM free tier.
    NVIDIA_NIM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    NVIDIA_NIM_BRAIN_MODEL: str = "qwen/qwen3-next-80b-a3b-instruct"
    NVIDIA_NIM_FAST_BRAIN_MODEL: str = "qwen/qwen3-next-80b-a3b-instruct"
    NVIDIA_NIM_JUDGE_MODEL: str = "mistralai/mistral-large-3-675b-instruct-2512"

    # Storage paths
    CORPUS_DIR: Path = ROOT / "rag" / "corpus"
    EXTRACTED_DIR: Path = ROOT / "rag" / "extracted"
    VECTORS_DIR: Path = ROOT / "rag" / "vectors"
    STRUCTURED_DB: Path = ROOT / "rag" / "policies.duckdb"
    # Single source of truth for the curated-facts directory. Resolves to
    # <repo_root>/40-data; the directory name is intentionally kept
    # (parallel to 70-docs/80-audit).
    DATA_DIR: Path = ROOT / "40-data"

    # Tunables (overrideable via env vars so the hyperparameter sweep can iterate)
    CHUNK_TOKENS: int = int(os.environ.get("CHUNK_TOKENS", "800"))
    CHUNK_OVERLAP_TOKENS: int = int(os.environ.get("CHUNK_OVERLAP_TOKENS", "120"))
    RAG_TOP_K: int = int(os.environ.get("RAG_TOP_K", "5"))

    # Quarantine TTL — user-uploaded PDFs live in the SEPARATE
    # `user_uploads_quarantine` Chroma collection. They are NOT durable
    # corpus; a session's upload is auto-purged after this many seconds of
    # no further uploads from that session, so the quarantine index can't
    # grow unbounded and stale private docs don't linger. Default 24h.
    # The periodic purge task sweeps every QUARANTINE_PURGE_INTERVAL_SEC.
    QUARANTINE_TTL_SECONDS: int = int(
        os.environ.get("QUARANTINE_TTL_SECONDS", str(24 * 3600))
    )
    QUARANTINE_PURGE_INTERVAL_SEC: int = int(
        os.environ.get("QUARANTINE_PURGE_INTERVAL_SEC", str(30 * 60))
    )

    @classmethod
    def validate(cls) -> list[str]:
        """Return list of missing required keys. Empty list = healthy."""
        missing = []
        for k in ("SARVAM_API_KEY", "NVIDIA_NIM_API_KEY"):
            if not getattr(cls, k):
                missing.append(k)
        return missing


settings = Settings()
