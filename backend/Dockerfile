FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

# Phase 8 (ADR-010): the self-hosted audio backends add several hundred MB
# of runtimes for a feature that's off by default, so they're an opt-in
# build argument rather than part of every image. `docker compose build`
# is unchanged; `INSTALL_VOICE=true docker compose build` carries them.
ARG INSTALL_VOICE=false
RUN if [ "$INSTALL_VOICE" = "true" ]; then pip install --no-cache-dir -e ".[voice]"; else pip install --no-cache-dir -e .; fi

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
