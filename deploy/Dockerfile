# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM node:22-bookworm-slim AS frontend-builder

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefix frontend
COPY frontend ./frontend
RUN npm run build --prefix frontend

FROM python:3.12-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.12-slim-bookworm AS runtime

ARG VCS_REF=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="TimeTrace Server" \
      org.opencontainers.image.description="TimeTrace API, MCP, database worker, and memory pipeline" \
      org.opencontainers.image.source="https://github.com/Vanilla-Yukirin/TimeTrace" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV HOME=/home/timetrace \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd --gid 1000 timetrace \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/timetrace timetrace

WORKDIR /app
COPY --from=builder --chown=timetrace:timetrace /app/.venv /app/.venv
COPY --from=frontend-builder --chown=timetrace:timetrace \
    /src/frontend-dist /opt/timetrace/release/frontend-dist
COPY --chown=timetrace:timetrace \
    skills/timetrace/SKILL.md \
    /opt/timetrace/skills/timetrace/SKILL.md
COPY --chown=timetrace:timetrace \
    deploy/docker-compose.yml \
    deploy/deploy-container.sh \
    timetrace-update.sh \
    /opt/timetrace/release/

USER 1000:1000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).read()"]

CMD ["timetrace-server"]
