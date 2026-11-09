"""Authenticated operational metrics; labels never contain activity or identity."""

from __future__ import annotations

import asyncio
import re
import time
import uuid

import structlog
from fastapi import Depends
from fastapi.responses import JSONResponse, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from timetrace.server.api.deps import require_principal

logger = structlog.get_logger(__name__)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ServerMetrics:
    def __init__(self):
        self.registry = CollectorRegistry()
        self.llm_calls = Counter(
            "timetrace_llm_requests_total",
            "Instrumented model requests",
            ["caller", "status"],
            registry=self.registry,
        )
        self.llm_duration = Histogram(
            "timetrace_llm_request_duration_seconds",
            "Instrumented model request duration",
            ["caller"],
            buckets=(1, 5, 10, 30, 60, 120, 300),
            registry=self.registry,
        )
        self.requests = Counter(
            "timetrace_http_requests_total",
            "Completed HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "timetrace_http_request_duration_seconds",
            "HTTP duration",
            ["method", "route"],
            registry=self.registry,
        )
        self.analysis = Gauge(
            "timetrace_analysis_records",
            "Analysis records by state",
            ["state"],
            registry=self.registry,
        )
        self.queue_age = Gauge(
            "timetrace_analysis_oldest_pending_seconds",
            "Oldest queued record",
            registry=self.registry,
        )
        self.summaries = Gauge(
            "timetrace_summary_windows",
            "Summary windows by scope/grain/state",
            ["scope", "grain", "state"],
            registry=self.registry,
        )
        self.last_summary = Gauge(
            "timetrace_summary_latest_completed_timestamp_seconds",
            "End of latest completed summary window",
            ["scope", "grain"],
            registry=self.registry,
        )
        self.tick_success = Gauge(
            "timetrace_background_last_success_timestamp_seconds",
            "Last successful background sweep",
            ["task"],
            registry=self.registry,
        )
        self.tick_errors = Counter(
            "timetrace_background_errors_total",
            "Failed background sweeps",
            ["task"],
            registry=self.registry,
        )
        self.tasks = Gauge(
            "timetrace_background_task_running",
            "Required task is running",
            ["task"],
            registry=self.registry,
        )
        self._lock = asyncio.Lock()
        self._refreshed = float("-inf")

    async def refresh(self, db):
        async with self._lock:
            if time.monotonic() - self._refreshed < 5:
                return
            # A bounded snapshot of aggregates, never per-record labels or bodies.
            async with db.lock:
                async with db.conn.execute(
                    "SELECT status,COUNT(*) FROM analysis_results GROUP BY status"
                ) as cur:
                    analysis = await cur.fetchall()
                async with db.conn.execute(
                    "SELECT MIN(queued_at) FROM analysis_results "
                    "WHERE status IN ('pending_vlm','error_retry')"
                ) as cur:
                    oldest = (await cur.fetchone())[0]
                summaries = []
                for scope, table in [("global", "summaries"), ("device", "device_summaries")]:
                    async with db.conn.execute(
                        f"SELECT grain,status,COUNT(*),MAX(window_end) FROM {table} "
                        "GROUP BY grain,status"
                    ) as cur:
                        summaries.extend((scope, *row) for row in await cur.fetchall())
            self.analysis.clear()
            for state, count in analysis:
                self.analysis.labels(state).set(count)
            self.queue_age.set(max(0, time.time() - oldest / 1000) if oldest else 0)
            self.summaries.clear()
            self.last_summary.clear()
            for scope, grain, state, count, latest in summaries:
                self.summaries.labels(scope, grain, state).set(count)
                if state == "narrated":
                    self.last_summary.labels(scope, grain).set(latest / 1000)
            self._refreshed = time.monotonic()


class RequestTelemetry:
    """Pure ASGI middleware also measures MCP/streaming requests."""

    def __init__(self, app, metrics):
        self.app, self.metrics = app, metrics

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        supplied = (
            dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("ascii", "ignore")
        )
        request_id = supplied if _REQUEST_ID.fullmatch(supplied) else uuid.uuid4().hex
        context = structlog.contextvars.bind_contextvars(request_id=request_id)
        status, started = 500, time.monotonic()

        async def measured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode()),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        except Exception:
            logger.exception("http.request_failed")
            raise
        finally:
            route = getattr(scope.get("route"), "path", None)
            route = route or ("/mcp" if scope["path"].startswith("/mcp") else "unmatched")
            method = scope.get("method", "OTHER")
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                method = "OTHER"
            elapsed = time.monotonic() - started
            self.metrics.requests.labels(method, route, str(status)).inc()
            self.metrics.latency.labels(method, route).observe(elapsed)
            logger.info(
                "http.request_completed",
                method=method,
                route=route,
                status=status,
                duration_ms=round(elapsed * 1000, 2),
            )
            structlog.contextvars.reset_contextvars(**context)


def install_observability(app, db):
    metrics = ServerMetrics()
    app.state.metrics = metrics
    app.state.background_tasks = {}
    app.add_middleware(RequestTelemetry, metrics=metrics)

    @app.get("/readyz", include_in_schema=False)
    async def ready():
        try:
            async with asyncio.timeout(2):
                async with db.lock:
                    async with db.conn.execute("SELECT 1") as cur:
                        await cur.fetchone()
            running = all(not task.done() for task in app.state.background_tasks.values())
            if not running:
                return JSONResponse({"status": "not_ready"}, status_code=503)
        except Exception:
            logger.exception("server.readiness_failed")
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    @app.get("/metrics", include_in_schema=False, dependencies=[Depends(require_principal)])
    async def scrape():
        await metrics.refresh(db)
        for name, task in app.state.background_tasks.items():
            metrics.tasks.labels(name).set(not task.done())
        return Response(
            generate_latest(metrics.registry), headers={"Content-Type": CONTENT_TYPE_LATEST}
        )
