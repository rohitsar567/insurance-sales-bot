"""Voyage AI — embeddings (voyage-3).

Endpoint: POST https://api.voyageai.com/v1/embeddings
Auth: Bearer token.
Request: { "input": [...], "model": "voyage-3", "input_type": "document"|"query" }
Response: { "data": [{"embedding": [...]}], "usage": {...} }

Free-tier rate limits:
  - 3 RPM for voyage-3 (without payment method on file)
  - ~120K tokens per minute
So we cap batch_size to ~32 chunks (~20K tokens) and sleep ~21s between batches,
with exponential backoff retry on 429.
"""

from __future__ import annotations

import asyncio
import random
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
        # free-tier safe defaults
        batch_size: int = 32,
        inter_batch_sleep_s: float = 21.0,
        max_retries: int = 6,
    ):
        self.api_key = api_key or settings.VOYAGE_API_KEY
        self.model = model
        self.timeout = timeout
        self.batch_size = batch_size
        self.inter_batch_sleep_s = inter_batch_sleep_s
        self.max_retries = max_retries
        if not self.api_key:
            raise RuntimeError("VOYAGE_API_KEY not set in .env")

    async def _embed_batch(
        self,
        client: httpx.AsyncClient,
        batch: list[str],
        input_type: str,
    ) -> list[list[float]]:
        url = f"{VOYAGE_BASE}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {"input": batch, "model": self.model, "input_type": input_type}

        for attempt in range(self.max_retries):
            try:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code == 429:
                    # Exponential backoff with jitter; honor Retry-After if present
                    ra = resp.headers.get("Retry-After")
                    wait = float(ra) if ra and ra.isdigit() else min(60.0, (2 ** attempt) * 8) + random.uniform(0, 4)
                    print(f"    voyage 429 attempt={attempt+1} sleeping {wait:.1f}s", flush=True)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                payload = resp.json()
                return [item["embedding"] for item in payload["data"]]
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (500, 502, 503, 504):
                    wait = (2 ** attempt) * 4 + random.uniform(0, 2)
                    print(f"    voyage {e.response.status_code} attempt={attempt+1} sleeping {wait:.1f}s", flush=True)
                    await asyncio.sleep(wait)
                    continue
                raise
            except (httpx.ReadTimeout, httpx.ConnectError) as e:
                wait = (2 ** attempt) * 4 + random.uniform(0, 2)
                print(f"    voyage {type(e).__name__} attempt={attempt+1} sleeping {wait:.1f}s", flush=True)
                await asyncio.sleep(wait)
                continue
        raise RuntimeError(f"voyage embed: exhausted {self.max_retries} retries on batch of {len(batch)}")

    async def embed(
        self,
        texts: list[str],
        input_type: Literal["document", "query"] = "document",
    ) -> list[list[float]]:
        if not texts:
            return []

        # Skip throttling for tiny queries (input_type=query, single text)
        throttle = input_type == "document" and len(texts) > 4

        all_vectors: list[list[float]] = []
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start : start + self.batch_size]
                vectors = await self._embed_batch(client, batch, input_type)
                all_vectors.extend(vectors)
                if throttle and start + self.batch_size < len(texts):
                    await asyncio.sleep(self.inter_batch_sleep_s)
        return all_vectors
