# Atlas — Production Dockerfile
# Multi-stage build: builder (installs deps) + runtime (lean final image)

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first (Docker layer cache).
COPY pyproject.toml ./
COPY README.md ./

# Install all dependencies into a virtual environment.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip && \
    pip install -e ".[dev]" --no-cache-dir

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

# Runtime system dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtual environment from the builder stage.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create a non-root user for the application.
RUN groupadd --gid 1001 atlas && \
    useradd --uid 1001 --gid atlas --shell /bin/bash --create-home atlas

# Copy application code.
COPY --chown=atlas:atlas app/ ./app/
COPY --chown=atlas:atlas alembic.ini ./

# Create upload directory.
RUN mkdir -p /app/data/uploads && chown -R atlas:atlas /app/data

USER atlas

# Health check against the /ready endpoint.
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/health/ready || exit 1

# Default: run the FastAPI app with Uvicorn.
# Override the command in docker-compose.yml for the worker service.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
