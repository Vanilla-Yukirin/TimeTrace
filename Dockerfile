# =============================================================================
# Dockerfile — TimeTrace server image (no capture, headless Linux deploy).
#
# Build:
#     docker build -t timetrace-server .
#
# Run (single command, ephemeral data):
#     docker run --rm -p 8765:8765 timetrace-server
#
# Run with persistent data + token + .env:
#     docker run -d --name timetrace-server \
#         -p 8765:8765 \
#         -v $HOME/TimeTraceData:/data \
#         -v $HOME/.config/timetrace-server:/tokens \
#         --env-file .env \
#         timetrace-server
#
# This image bundles ONLY the server side. The Windows-only capture deps
# (pywin32, mss, pynput, pystray) auto-skip via pyproject.toml platform
# markers, so the Linux build is clean. Capture lives on user desktops
# running `uv run timetrace-client`, talking HTTP to this container.
#
# Image philosophy:
#   - python:3.12-slim base (small but full apt for native wheels)
#   - uv for resolution + venv (matches dev workflow exactly)
#   - non-root runtime user (limits blast radius)
#   - data dirs mounted in, never baked in
# =============================================================================

FROM python:3.12-slim AS builder

# uv (the same tool dev uses) — install via the official standalone script,
# pinned to a known version to avoid surprise behaviour changes between builds.
COPY --from=ghcr.io/astral-sh/uv:0.5.13 /uv /usr/local/bin/uv

WORKDIR /app

# Copy lockfile + project metadata first so dep install layer caches across
# source changes. Only invalidates when pyproject/uv.lock change.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

# Install into a venv at /app/.venv. `--no-dev` skips pytest/ruff (we're not
# testing inside the image). The win32-marked deps in pyproject.toml are
# auto-skipped on Linux.
RUN uv sync --frozen --no-dev


# --------------------------------------------------------------------------- #
# Runtime stage — slimmer image, no build tooling                              #
# --------------------------------------------------------------------------- #
FROM python:3.12-slim AS runtime

# Defensive: a non-root user owns the data dirs the app writes into.
ARG TT_UID=10001
ARG TT_GID=10001
RUN groupadd --system --gid ${TT_GID} timetrace \
    && useradd --system --uid ${TT_UID} --gid timetrace --create-home timetrace

WORKDIR /app

# Copy the resolved venv + source from builder. This avoids re-resolving deps
# on every image build and keeps the runtime image free of compilers / git.
COPY --from=builder --chown=timetrace:timetrace /app /app

# Mount points: persistent data + the token file. Both default-empty inside
# the image; bind-mount real dirs at `docker run` time. tokens dir is its own
# mount so you can rotate / inspect tokens without touching data.
RUN mkdir -p /data /tokens \
    && chown -R timetrace:timetrace /data /tokens

ENV PATH="/app/.venv/bin:${PATH}" \
    TIMETRACE_DATA_DIR=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# `~/.config/timetrace-server/` lives at /tokens via this symlink so
# ServerAuth.load_or_generate() reads/writes the bind-mounted location.
RUN mkdir -p /home/timetrace/.config \
    && ln -sf /tokens /home/timetrace/.config/timetrace-server \
    && chown -h timetrace:timetrace /home/timetrace/.config/timetrace-server

USER timetrace

EXPOSE 8765

# Healthcheck mirrors deploy.sh's curl-based probe so docker stops/restarts
# the container if the API ever stops responding.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=3).status == 200 else 1)"

CMD ["timetrace-server"]
