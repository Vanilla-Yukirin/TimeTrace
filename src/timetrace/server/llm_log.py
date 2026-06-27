"""Unified LLM-request ledger.

Every LLM call in the server (worker VLM describe / narrative / report /
ask_agent) goes through :func:`timed_chat_completion`, which times the call,
pulls the REAL token usage off the response (not an estimate), and emits one
:class:`LLMRequestLog` row to a sink.

The sink is a module-level default set once at startup (:func:`set_default_sink`)
so call sites that hold no DB reference (``VLMClient``, ``OpenAINarrativeLLM``)
can still log without threading a ``db`` through their constructors. Tests leave
the default unset → logging is a no-op. A failing sink never breaks the actual
LLM call (errors are swallowed + logged).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LLMRequestLog:
    """One LLM call's ledger row (timing + real usage + content sizes)."""

    caller: str
    model: str | None
    ts_start: int
    ts_end: int
    duration_ms: int
    status: str  # 'ok' | 'error'
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    prompt_chars: int | None = None
    completion_chars: int | None = None
    error: str | None = None


RequestLogSink = Callable[[LLMRequestLog], Awaitable[None]]

_default_sink: RequestLogSink | None = None


def set_default_sink(sink: RequestLogSink | None) -> None:
    """Install the process-wide sink (bootstrap calls this once with a DB writer)."""
    global _default_sink
    _default_sink = sink


def _extract_usage(resp: Any) -> dict:
    """Pull token counts off ``response.usage`` (best-effort across providers)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    details = getattr(u, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", None) if details is not None else None
    return {
        "prompt_tokens": getattr(u, "prompt_tokens", None),
        "completion_tokens": getattr(u, "completion_tokens", None),
        "reasoning_tokens": reasoning,
        "total_tokens": getattr(u, "total_tokens", None),
    }


def _completion_chars(resp: Any) -> int | None:
    """Output content size = content + reasoning_content (LM Studio splits them)."""
    try:
        msg = resp.choices[0].message
    except (IndexError, AttributeError):
        return None
    content = getattr(msg, "content", None) or ""
    reasoning = getattr(msg, "reasoning_content", None) or ""
    return len(content) + len(reasoning)


async def _emit(sink: RequestLogSink | None, log: LLMRequestLog) -> None:
    target = sink if sink is not None else _default_sink
    if target is None:
        return
    try:
        await target(log)
    except Exception:  # noqa: BLE001 — logging must never break the real call
        logger.warning("llm_log.sink_failed", caller=log.caller, exc_info=True)


async def timed_chat_completion(
    client: Any,
    *,
    caller: str,
    model: str,
    sink: RequestLogSink | None = None,
    prompt_chars: int | None = None,
    **create_kwargs: Any,
) -> Any:
    """Run ``client.chat.completions.create`` and log one ledger row.

    Re-raises on error after logging the failed attempt, so callers keep their
    existing error handling. ``create_kwargs`` pass through verbatim (messages,
    response_format, timeout, extra_body, max_tokens, temperature, ...). ``sink``
    overrides the module default (mainly for tests); omit it in production.
    """
    t0 = time.time()
    try:
        resp = await client.chat.completions.create(model=model, **create_kwargs)
    except Exception as exc:
        t1 = time.time()
        await _emit(
            sink,
            LLMRequestLog(
                caller=caller,
                model=model,
                ts_start=int(t0 * 1000),
                ts_end=int(t1 * 1000),
                duration_ms=int((t1 - t0) * 1000),
                status="error",
                prompt_chars=prompt_chars,
                error=str(exc)[:500],
            ),
        )
        raise
    t1 = time.time()
    await _emit(
        sink,
        LLMRequestLog(
            caller=caller,
            model=model,
            ts_start=int(t0 * 1000),
            ts_end=int(t1 * 1000),
            duration_ms=int((t1 - t0) * 1000),
            status="ok",
            prompt_chars=prompt_chars,
            completion_chars=_completion_chars(resp),
            **_extract_usage(resp),
        ),
    )
    return resp
