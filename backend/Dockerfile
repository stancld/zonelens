FROM debian:bullseye-slim

SHELL ["/bin/bash", "-c"]

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install essential system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy uv from the official docker image
COPY --from=ghcr.io/astral-sh/uv:0.7.11 /uv /bin/uv

# Python installation
ENV PYTHON_VERSION=3.13
ENV PYTHON_DIR=/opt/python
# Set Python path to include the app directory
ENV PYTHONPATH=/app
# Use the virtual environment automatically
ENV PATH="$PYTHON_DIR/bin:$PATH"

COPY tools/install_python.sh tools/
RUN ./tools/install_python.sh

# Install production Python dependencies
COPY pyproject.toml .
COPY constraints.txt .

RUN uv pip install . -c constraints.txt \
    && rm -rf /root/.cache/uv

COPY . .

EXPOSE 8000

CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:8000", "strava_zones_backend.wsgi:application"]
