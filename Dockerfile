FROM python:3.12-slim

# Install uv from official Astral image for 10x faster builds and minimal image layer overhead
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

COPY requirements.txt pyproject.toml ./
RUN uv pip install --no-cache -r requirements.txt && \
    useradd --system --create-home appuser && mkdir -p /data && chown appuser:appuser /data

COPY app ./app
COPY scripts ./scripts
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "16", "--no-access-log"]
