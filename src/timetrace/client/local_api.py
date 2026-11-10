"""Loopback-only API and dashboard for the Windows capture client."""

from __future__ import annotations

import asyncio
import html
import secrets
import socket
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import httpx
import structlog
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel, ConfigDict

from timetrace import __version__
from timetrace.client.capture.settings import ScreenshotSettings, Theme
from timetrace.client.controller import (
    ClientController,
    ControllerMutationError,
)
from timetrace.client.core.endpoints import redact_endpoint_url
from timetrace.client.metrics import render_client_metrics
from timetrace.client.updates import check_for_update
from timetrace.client.windows_runtime import autostart_enabled, set_autostart

logger = structlog.get_logger(__name__)

_CONTROL_HOST = "127.0.0.1"
_DASHBOARD = Path(__file__).with_name("dashboard")


class EndpointPatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    enabled: bool


class ThemePatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    theme: Theme


def create_local_app(
    controller: ClientController,
    *,
    port: int,
    csrf_token: str | None = None,
) -> FastAPI:
    """Create a testable app with exact loopback Host/Origin boundaries."""
    csrf_token = csrf_token or secrets.token_urlsafe(32)
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if port == 80:
        # Browsers omit the default HTTP port from Host and Origin.
        allowed_hosts.update({"127.0.0.1", "localhost"})
    allowed_origins = {f"http://{host}" for host in allowed_hosts}
    app = FastAPI(
        title="TimeTrace Client Control",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.csrf_token = csrf_token

    @app.middleware("http")
    async def _security_boundary(request: Request, call_next):  # noqa: ANN001
        host = request.headers.get("host", "").lower()
        if host not in allowed_hosts:
            response: Response = PlainTextResponse("invalid host", status_code=421)
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin", "")
            supplied_csrf = request.headers.get("x-timetrace-csrf", "")
            session = request.cookies.get("timetrace_session", "")
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if origin not in allowed_origins:
                response = PlainTextResponse("invalid origin", status_code=403)
            elif not secrets.compare_digest(supplied_csrf, csrf_token):
                response = PlainTextResponse("invalid csrf token", status_code=403)
            elif not secrets.compare_digest(session, csrf_token):
                response = PlainTextResponse("invalid session", status_code=403)
            elif media_type != "application/json":
                response = PlainTextResponse("application/json required", status_code=415)
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-TimeTrace-Control"] = "client"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = (_DASHBOARD / "index.html").read_text(encoding="utf-8")
        response = HTMLResponse(page.replace("{{csrf}}", html.escape(csrf_token, quote=True)))
        response.set_cookie(
            "timetrace_session",
            csrf_token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    @app.get("/assets/app.js")
    async def script() -> Response:
        return Response(
            (_DASHBOARD / "app.js").read_text(encoding="utf-8"),
            media_type="text/javascript; charset=utf-8",
        )

    @app.get("/assets/style.css")
    async def style() -> Response:
        return Response(
            (_DASHBOARD / "style.css").read_text(encoding="utf-8"),
            media_type="text/css; charset=utf-8",
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict:
        return asdict(await controller.snapshot())

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            render_client_metrics(await controller.snapshot()),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    @app.get("/api/config")
    async def config() -> dict:
        cfg = controller.config
        try:
            autostart = await asyncio.to_thread(autostart_enabled)
        except OSError:
            autostart = None
        return {
            "version": __version__,
            "capture": cfg.capture.model_dump(),
            "theme": cfg.control.theme,
            "autostart": autostart,
            "images_enabled": cfg.privacy.store_images,
            "device": {
                "id": cfg.device.id,
                "name": cfg.device.name,
                "description": cfg.device.description,
            },
            "auth_token_present": bool(cfg.server.auth_token),
            "control": {"enabled": cfg.control.enabled, "port": cfg.control.port},
            "endpoints": [
                {
                    "index": index,
                    "name": ep.name,
                    "url": redact_endpoint_url(ep.url),
                    "enabled": ep.enabled,
                }
                for index, ep in enumerate(cfg.server.endpoints)
            ],
        }

    @app.get("/api/events")
    async def events(
        before: Annotated[int | None, Query(ge=1)] = None,
        after: Annotated[int | None, Query(ge=0)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> dict:
        if before is not None and after is not None:
            raise HTTPException(status_code=422, detail="请只提供一个分页游标")
        if controller.journal is None:
            return {"items": [], "more": False}
        return await controller.journal.page(before=before, after=after, limit=limit)

    @app.patch("/api/capture/settings")
    async def capture_settings(patch: ScreenshotSettings) -> dict:
        try:
            await controller.set_dashboard_settings(capture=patch)
        except ControllerMutationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return patch.model_dump()

    @app.patch("/api/settings/theme")
    async def theme(patch: ThemePatch) -> dict:
        try:
            await controller.set_dashboard_settings(theme=patch.theme)
        except ControllerMutationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"theme": patch.theme}

    @app.patch("/api/settings/autostart")
    async def autostart(patch: EndpointPatch) -> dict:
        try:
            value = await asyncio.to_thread(set_autostart, patch.enabled)
        except Exception as exc:
            controller.record_error("autostart", exc)
            raise HTTPException(status_code=409, detail="无法修改 Windows 自启动") from exc
        return {"enabled": value}

    @app.post("/api/update/check")
    async def update(_payload: Annotated[dict, Body()]) -> dict:
        try:
            return await check_for_update()
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="暂时无法查询 GitHub Release") from exc

    @app.post("/api/capture/pause")
    async def pause(request: Request, _payload: Annotated[dict, Body()]) -> dict:
        return asdict(await controller.set_paused(True))

    @app.post("/api/capture/resume")
    async def resume(request: Request, _payload: Annotated[dict, Body()]) -> dict:
        return asdict(await controller.set_paused(False))

    @app.patch("/api/endpoints/{index}")
    async def endpoint(index: int, patch: EndpointPatch, request: Request) -> dict:
        try:
            snapshot = await controller.set_endpoint_enabled(index, patch.enabled)
        except ControllerMutationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return asdict(snapshot)

    @app.post("/api/shutdown", status_code=202)
    async def shutdown(request: Request, _payload: Annotated[dict, Body()]) -> dict[str, bool]:
        return {"accepted": controller.request_shutdown()}

    return app


async def serve_local_control(
    controller: ClientController,
    stop_event: asyncio.Event,
    *,
    port: int,
    show_dashboard: bool = False,
) -> None:
    """Serve on IPv4 loopback; a bind conflict degrades only this feature."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        # POSIX restart must be able to bind while the previous connection is
        # in TIME_WAIT. SO_REUSEPORT remains off: only one listener owns it.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((_CONTROL_HOST, port))
        sock.listen(128)
        sock.setblocking(False)
    except OSError as exc:
        sock.close()
        controller.record_error("local_ui_bind", exc)
        logger.warning("client.local_ui_bind_failed", host=_CONTROL_HOST, port=port, exc_info=True)
        return

    actual_port = int(sock.getsockname()[1])
    url = f"http://{_CONTROL_HOST}:{actual_port}"
    controller.set_control_url(url)
    app = create_local_app(controller, port=actual_port)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=_CONTROL_HOST,
            port=actual_port,
            access_log=False,
            log_level="warning",
            # PyInstaller's windowed bootloader intentionally sets stderr to
            # None. Uvicorn's default formatter calls stderr.isatty() while
            # constructing Config, which would crash the whole TaskGroup.
            # The client already owns rotating-file logging, so do not let
            # Uvicorn replace it.
            log_config=None,
        )
    )
    server.install_signal_handlers = lambda: None
    serve_task = asyncio.create_task(server.serve(sockets=[sock]), name="local_api_server")
    stop_task = asyncio.create_task(stop_event.wait(), name="local_api_stop")
    logger.info("client.local_ui_started", url=url)
    try:
        if show_dashboard:
            await controller.open_control_panel()
        done, _ = await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            server.should_exit = True
        elif serve_task in done:
            await serve_task
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        controller.record_error("local_ui", exc)
        logger.warning("client.local_ui_failed", exc_info=True)
    finally:
        server.should_exit = True
        if not stop_task.done():
            stop_task.cancel()
        if not serve_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(serve_task), timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                serve_task.cancel()
        await asyncio.gather(stop_task, serve_task, return_exceptions=True)
        controller.set_control_url(None)
        sock.close()
        logger.info("client.local_ui_stopped")
