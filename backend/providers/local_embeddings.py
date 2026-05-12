"""Local embeddings via sentence-transformers.

Used as the v1 production embedder because Voyage's free-tier rate limit
(3 RPM) makes ingestion of 75+ PDFs impractical in our time budget.

Default model: BAAI/bge-small-en-v1.5 — 384-dim, ~110MB, top-of-class on
MTEB English benchmark for its size. Faster than 1024-dim cloud models on
CPU and trivially small on GPU.

The interface matches EmbeddingsProvider exactly, so the orchestrator and
RAG pipeline don't change — just the import in ingest/retrieve.
"""

from __future__ import annotations

from typing import Literal, Optional

from backend.providers.base import EmbeddingsProvider


class LocalEmbeddings(EmbeddingsProvider):
    name = "local-bge"

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        device: Optional[str] = None,
    ):
        # Lazy import so this module loads fast even if model isn't downloaded
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device=device)
        self.dimension = self.model.get_sentence_embedding_dimension()

    async def embed(
        self,
        texts: list[str],
        input_type: Literal["document", "query"] = "document",
    ) -> list[list[float]]:
        if not texts:
            return []
        # BGE recommends a small query-side instruction; not strictly required
        if input_type == "query":
            texts = [f"Represent this sentence for searching relevant passages: {t}" for t in texts]
        # Encode synchronously on CPU/GPU; small batches don't need true async
        vectors = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return vectors.tolist()
