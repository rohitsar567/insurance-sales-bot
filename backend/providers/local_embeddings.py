"""Local embeddings via sentence-transformers.

Used as the v1 production embedder because Voyage's free-tier rate limit
(3 RPM) makes ingestion of 75+ PDFs impractical in our time budget.

Default model: BAAI/bge-small-en-v1.5 — 384-dim, ~110MB, top-of-class on
MTEB English benchmark for its size. Faster than 1024-dim cloud models on
CPU and trivially small on GPU.

The interface matches EmbeddingsProvider exactly, so the RAG pipeline
doesn't change — only the import in ingest/retrieve.
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
        import os
        from sentence_transformers import SentenceTransformer

        # Device autodetect: MPS on Apple Silicon when available (2-3x faster
        # than CPU on long chunks), CUDA if present, else CPU. Honor explicit
        # override via constructor arg OR EMBED_DEVICE env var so HF Space
        # (no MPS) and local Mac (with MPS) pick the right path.
        if device is None:
            device = os.environ.get("EMBED_DEVICE", "").strip() or None
        if device is None:
            try:
                import torch
                if torch.backends.mps.is_available():
                    device = "mps"
                elif torch.cuda.is_available():
                    device = "cuda"
                else:
                    device = "cpu"
            except Exception:
                device = "cpu"

        self.model_name = model_name
        self.device = device
        self.model = SentenceTransformer(model_name, device=device)
        self.dimension = self.model.get_sentence_embedding_dimension()
        # Warm-up call on MPS — first kernel JIT compile is ~3-5s; doing it
        # in __init__ rather than first encode() makes the first user request
        # fast. CPU/CUDA skip this (their first call has no JIT penalty).
        if device == "mps":
            try:
                self.model.encode(["warmup"] * 2, batch_size=2, show_progress_bar=False)
            except Exception:
                pass

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
        # Batch size scales by device: MPS / CUDA throughput benefits from
        # bigger batches; CPU prefers smaller to avoid memory pressure on M1.
        # MPS/CUDA use batch 128 to minimise GPU kernel launches during
        # bulk re-ingest (800-token chunks at 128 ≈ 100 MB per batch, well
        # within Mac M-series unified memory). CPU stays at 32 to keep peak
        # RSS bounded on machines without a GPU.
        batch = 128 if self.device in ("mps", "cuda") else 32
        vectors = self.model.encode(
            texts,
            batch_size=batch,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return vectors.tolist()
