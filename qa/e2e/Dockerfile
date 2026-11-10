ARG SERVER_IMAGE=timetrace-e2e-server:local
FROM ${SERVER_IMAGE}
USER root
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv
COPY pyproject.toml uv.lock /tmp/qa-deps/
RUN cd /tmp/qa-deps \
    && uv export --locked --only-group dev --no-hashes --format requirements-txt -o /tmp/qa-requirements.txt \
    && uv pip install --python /app/.venv/bin/python -r /tmp/qa-requirements.txt
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
RUN python -m playwright install --with-deps chromium \
    && mkdir -p /artifacts && chown 1000:1000 /artifacts
COPY --chown=1000:1000 qa/e2e /qa
USER 1000:1000
