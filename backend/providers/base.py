"""Common types + abstract base classes for provider clients.

The interfaces are deliberately narrow:
- A speech-to-text provider takes audio bytes and returns text.
- A text-to-speech provider takes text and returns audio bytes.
- An LLM provider takes a list of messages and returns a text completion.
- An embeddings provider takes a list of strings and returns vectors.

Concrete implementations live in sibling modules (sarvam_*, groq_*, voyage_*,
openrouter_*). The orchestrator imports these by interface, never by concrete
class — so swapping providers is a config change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---------- shared message types ----------

@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class STTResult:
    text: str
    language_code: Optional[str] = None
    confidence: Optional[float] = None  # 0-1 if provider returns it
    raw: dict = field(default_factory=dict)


@dataclass
class LLMResult:
    text: str
    model: str
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    raw: dict = field(default_factory=dict)


# ---------- abstract interfaces ----------

class STTProvider(ABC):
    name: str

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language_code: Optional[str] = None,
    ) -> STTResult: ...


class TTSProvider(ABC):
    name: str

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        language_code: str = "en-IN",
        speaker: Optional[str] = None,
    ) -> bytes:
        """Return raw audio bytes (WAV by default)."""
        ...


class LLMProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        response_format: Optional[dict] = None,
    ) -> LLMResult: ...


class EmbeddingsProvider(ABC):
    name: str
    model: str
    dimension: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
