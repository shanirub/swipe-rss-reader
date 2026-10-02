# syntax=docker/dockerfile:1
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /usr/local/bin/uv

# Cron runner for containers (logs job output to stdout, no root needed).
ADD --checksum=sha256:a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1 --chmod=755 \
    https://github.com/aptible/supercronic/releases/download/v0.2.49/supercronic-linux-amd64 \
    /usr/local/bin/supercronic

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

# Dependencies first (pinned by uv.lock) so code changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini crontab ./
RUN uv sync --frozen --no-dev --no-editable

ENV PATH=/app/.venv/bin:$PATH
RUN useradd --system --uid 10001 app && mkdir /data && chown app:app /data
USER app
