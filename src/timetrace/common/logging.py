"""One JSON log contract for console, packaged client and server."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog

_PRIVATE_KEYS = {
    "authorization",
    "cookie",
    "password",
    "api_key",
    "auth_token",
    "token",
    "window_title",
    "title",
    "payload",
    "image",
    "image_bytes",
    "thumb_bytes",
}
_TOKEN = re.compile(r"(?:tt_live_|tt-init-|sk-)[A-Za-z0-9_-]+")
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"']+")
_URL = re.compile(r"https?://[^\s\"'<>]+")


def redact(value):
    """Keep traceback structure, while removing credentials and private input."""
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in _PRIVATE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _URL.sub(
            "[endpoint]", _BEARER.sub("Bearer [redacted]", _TOKEN.sub("[redacted]", value))
        )
    return value


def configure_logging(service: str, *, logs_dir: Path | None = None) -> Path | None:
    handlers: list[logging.Handler] = []
    path = None
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        path = logs_dir / f"{service}.log"
        handlers.append(
            RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        )
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))

    def context(_logger, _method, event):
        event["service"] = service
        return redact(event)

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        context,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    # HTTP library logs contain complete request URLs. The app emits route names.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.configure(
        processors=[*processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    return path
