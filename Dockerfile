# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.8.22 AS uv_bin

FROM python:3.12-slim

COPY --from=uv_bin /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install third-party dependencies first for better layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/data

EXPOSE 8765

CMD ["python", "-m", "bms_assistant"]
