# Backend FastAPI Application Docker Image
#
# This Dockerfile builds the Python backend application.
#
# What it does:
# 1. Uses Python 3.12 slim as base (small footprint)
# 2. Installs uv (fast Python package manager)
# 3. Installs Python dependencies from pyproject.toml
# 4. Copies the application code
# 5. Exposes port 8000 and runs uvicorn server
#
# Note: This is an alternative to the main Dockerfile at apps/backend/Dockerfile
# The main one includes cron for log cleanup.

FROM python:3.12-slim

# Install uv package manager from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files and install packages
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy the entire application
COPY . .

# Expose the uvicorn port
EXPOSE 8000

# Run the FastAPI app with uvicorn
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
