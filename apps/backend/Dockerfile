# ──────────────────────────────────────────────────────────────────────
# Stage 1 — Build dependencies with uv
# ──────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv (fast Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy only dependency files first → maximizes Docker layer caching
COPY pyproject.toml uv.lock ./

# Create venv and install production deps (no dev tools in final image)
RUN uv sync --frozen --no-dev


# ──────────────────────────────────────────────────────────────────────
# Stage 2 — Runtime (slim, production-ready)
# ──────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install only cron (for log cleanup), nothing else
RUN apt-get update \
    && apt-get install -y --no-install-recommends cron \
    && rm -rf /var/lib/apt/lists/*

# Copy uv binary (needed for uv run to activate venv)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Create logs directory
RUN mkdir -p /app/logs

# Copy the entire venv from builder (all Python deps, no compilation toolchain)
COPY --from=builder /app/.venv /app/.venv

# Copy pyproject.toml so uv knows how to resolve the venv
COPY pyproject.toml ./

# Copy only the application source
COPY app/ ./app/
COPY scripts/log-cleanup.cron ./scripts/log-cleanup.cron

# Install the cron job for log rotation
RUN crontab ./scripts/log-cleanup.cron

# Expose the API port
EXPOSE 8000

# Start cron daemon in background, then run the FastAPI server
CMD ["sh", "-c", "cron && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
