# Production Dockerfile for Strava Zones Django Backend
FROM python:3.12-slim

SHELL ["/bin/bash", "-c"]

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
WORKDIR /app
ENV PYTHONPATH=/app

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin -c "Docker image user" appuser

# Install essential system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /bin/uv

COPY pyproject.toml constraints.txt ./

# Install production Python dependencies
RUN uv pip install --system . -c constraints.txt \
    && rm -rf /root/.cache/uv

COPY . .

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "strava_zones_backend.wsgi:application"]
