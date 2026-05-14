# Multi-stage build:
#   Stage 1 — build the Next.js frontend to a static export
#   Stage 2 — Python runtime serving FastAPI + the built frontend on the same port

# ----------------------------------------------------------------------------
# Stage 1 — Node builder
# ----------------------------------------------------------------------------
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend

# Install deps first for layer caching
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

# Copy the rest of the frontend and build
COPY frontend/ ./
# In production, the frontend calls the same origin (no separate backend URL).
ENV NEXT_PUBLIC_BACKEND_URL=""
# Static-export the app — produces ./out
RUN npm run build

# ----------------------------------------------------------------------------
# Stage 2 — Python runtime (FastAPI + corpus + Chroma + DuckDB + frontend)
# ----------------------------------------------------------------------------
FROM python:3.11-slim
WORKDIR /app

# System deps:
#   pdfplumber + torch CPU + sentence-transformers → build-essential, libpoppler
#   pydub (webm→wav transcode for Sarvam STT) → ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpoppler-cpp-dev \
    pkg-config \
    poppler-utils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so the first request is fast (no cold load)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Copy the backend source + RAG modules (rag/ holds .py only at this stage)
COPY backend ./backend
COPY rag ./rag
COPY eval ./eval
COPY docs ./docs
# Curated structured data the backend reads at request time:
#   - 40-data/reviews/<slug>.json    → /api/insurers/{slug}/reviews
#   - 40-data/policy_facts/*.json    → marketplace + scorecard fact cards
#   - 40-data/premiums/*.json        → premium calculator illustrative baseline
# Total ~2.3 MB — small enough to bake into the Space image.
COPY data ./data

# Pull the large data (corpus PDFs + pre-built Chroma vectors + extracted JSONs)
# from the companion HF dataset rather than baking it into the Space repo.
# Why: the free-tier Space repo has a 1 GB cap; rag/corpus + rag/vectors is
# ~310 MB and would have made the Space repo unviable on top of the regular
# code. HF datasets get 50 GB free quota — the right place for this data.
# Public dataset, no token needed at build time. See D-019.
RUN python -c "\
from huggingface_hub import snapshot_download; \
snapshot_download(\
    repo_id='rohitsar567/insurance-bot-data', \
    repo_type='dataset', \
    local_dir='/app/rag', \
    allow_patterns=['rag/corpus/**','rag/vectors/**','rag/extracted/**'], \
) " && \
    # The dataset preserves the rag/ prefix in path_in_repo, so the snapshot
    # writes to /app/rag/rag/corpus/... — flatten one level so existing
    # backend imports (rag/corpus/, rag/vectors/) keep working unchanged.
    if [ -d /app/rag/rag ]; then \
        cp -r /app/rag/rag/* /app/rag/ && rm -rf /app/rag/rag; \
    fi && \
    echo "Dataset pull complete:" && \
    du -sh /app/rag/corpus /app/rag/vectors /app/rag/extracted 2>&1 | sed 's/^/  /'

# Copy the built frontend from stage 1
COPY --from=frontend-builder /app/frontend/out ./frontend/out

# HF Spaces sends traffic to $PORT (default 7860). uvicorn will bind to it.
ENV PORT=7860
EXPOSE 7860

# Copy entrypoint and make it executable
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# Use a non-root user (HF Spaces recommends this for Docker spaces)
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user

# Start: entrypoint validates Chroma + (re-)ingests if needed, then runs uvicorn
CMD ["sh", "/app/entrypoint.sh"]
