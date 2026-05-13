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
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
    # Cerebras — primary LLM judge / fallback brain when set. Same Llama-3.3-70B
    # model as Groq but ~30 req/sec free-tier vs Groq's 30 req/min. Drops in
    # via the get_judge_llm() chain in backend/providers/cerebras_llm.py.
    CEREBRAS_API_KEY: str = os.environ.get("CEREBRAS_API_KEY", "")

    # Sarvam endpoints
    SARVAM_BASE_URL: str = "https://api.sarvam.ai"
    SARVAM_STT_PATH: str = "/speech-to-text"
    SARVAM_TTS_PATH: str = "/text-to-speech"
    SARVAM_CHAT_PATH: str = "/v1/chat/completions"

    # Sarvam model identifiers
    SARVAM_STT_MODEL: str = "saarika:v2.5"
    SARVAM_TTS_MODEL: str = "bulbul:v2"
    SARVAM_TTS_SPEAKER: str = "anushka"  # natural female advisor voice; configurable
    SARVAM_LLM_MODEL: str = "sarvam-m"

    # Voyage
    VOYAGE_MODEL: str = "voyage-3"

    # Groq (grader + fallback brain)
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_GRADER_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_BRAIN_MODEL: str = "llama-3.3-70b-versatile"

    # OpenRouter (alt fallback brain)
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_BRAIN_MODEL: str = "deepseek/deepseek-chat-v3-0324"

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
        for k in ("SARVAM_API_KEY", "VOYAGE_API_KEY", "GROQ_API_KEY"):
            if not getattr(cls, k):
                missing.append(k)
        return missing


settings = Settings()
