FROM python:3.12-slim

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends cron && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Create logs directory
RUN mkdir -p /app/logs

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy the rest of the application
COPY . .

# Install cron jobs for log cleanup
RUN crontab /app/scripts/log-cleanup.cron

# Expose the API port
EXPOSE 8000

# Run cron daemon and the application
CMD ["sh", "-c", "cron && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"]
