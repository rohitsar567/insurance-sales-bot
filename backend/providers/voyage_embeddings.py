"""Voyage AI — embeddings (voyage-3).

Endpoint: POST https://api.voyageai.com/v1/embeddings
Auth: Bearer token.
Request: { "input": [...], "model": "voyage-3", "input_type": "document"|"query" }
Response: { "data": [{"embedding": [...]}], "usage": {...} }
"""

from __future__ import annotations

from typing import Literal, Optional

import httpx

from backend.config import settings
from backend.providers.base import EmbeddingsProvider


VOYAGE_BASE = "https://api.voyageai.com/v1"


class VoyageEmbeddings(EmbeddingsProvider):
    name = "voyage"
    dimension = 1024  # voyage-3 dimension

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = settings.VOYAGE_MODEL,
        timeout: float = 60.0,
    ):
        self.api_key = api_key or settings.VOYAGE_API_KEY
        self.model = model
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("VOYAGE_API_KEY not set in .env")

    async def embed(
        self,
        texts: list[str],
        input_type: Literal["document", "query"] = "document",
    ) -> list[list[float]]:
        if not texts:
            return []

        url = f"{VOYAGE_BASE}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
        }

        # Voyage caps a single batch at 128 inputs; chunk if needed
        all_vectors: list[list[float]] = []
        BATCH = 128
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(texts), BATCH):
                batch = texts[start : start + BATCH]
                body["input"] = batch
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                payload = resp.json()
                vectors = [item["embedding"] for item in payload["data"]]
                all_vectors.extend(vectors)

        return all_vectors
