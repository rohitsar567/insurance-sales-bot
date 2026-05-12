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

# System deps for pdfplumber + torch CPU + sentence-transformers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpoppler-cpp-dev \
    pkg-config \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download the embedding model so the first request is fast (no cold load)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

# Copy the backend source + RAG modules + data
COPY backend ./backend
COPY rag ./rag
COPY eval ./eval
COPY docs ./docs

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
