# ─────────────────────────────────────────────────────────────────────────────
# AI Opportunity Discovery System — Production Dockerfile
# Multi-stage build for minimal final image
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Frontend Builder ──────────────────────────────────────────────
# frontend/dist is gitignored, so it is NOT present in a fresh clone/checkout.
# The API serves the SPA from that path, so it MUST be built inside the image —
# otherwise the container starts successfully but every page returns 404.
FROM node:22-slim AS frontend

WORKDIR /build

# Copy manifests first so `npm ci` is cached until dependencies actually change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ── Stage 2: Python Builder ─────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install system dependencies for building
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Stage 3: Runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . .

# Overlay the frontend bundle built in Stage 1. Done after `COPY . .` so a
# stray local dist/ can never shadow the freshly built one.
COPY --from=frontend /build/dist /app/frontend/dist

# Create data and logs directories
RUN mkdir -p data logs

# Default environment file
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "from database.database import Database; db=Database(); db.connect(); db.close()" || exit 1

# Run the scheduler by default
CMD ["python", "main.py"]