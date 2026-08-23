# ruff: noqa: E501
"""Loopback-only control API and small browser UI for ``timetrace-client``."""

from __future__ import annotations

import asyncio
import secrets
import socket
from dataclasses import asdict
from typing import Annotated

import structlog
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict

from timetrace.client.controller import (
    ClientController,
    ControllerMutationError,
)
from timetrace.client.core.endpoints import redact_endpoint_url

logger = structlog.get_logger(__name__)

_CONTROL_HOST = "127.0.0.1"


class EndpointPatch(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    enabled: bool


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
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        response = HTMLResponse(_render_html(csrf_token))
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
        return Response(_APP_JS, media_type="text/javascript; charset=utf-8")

    @app.get("/assets/style.css")
    async def style() -> Response:
        return Response(_STYLE_CSS, media_type="text/css; charset=utf-8")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status")
    async def status() -> dict:
        return asdict(await controller.snapshot())

    @app.get("/api/config")
    async def config() -> dict:
        cfg = controller.config
        return {
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
) -> None:
    """Serve on IPv4 loopback; a bind conflict degrades only this feature."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
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
        )
    )
    server.install_signal_handlers = lambda: None
    serve_task = asyncio.create_task(server.serve(sockets=[sock]), name="local_api_server")
    stop_task = asyncio.create_task(stop_event.wait(), name="local_api_stop")
    logger.info("client.local_ui_started", url=url)
    try:
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


def _render_html(csrf_token: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="timetrace-csrf" content="{csrf_token}">
<title>TimeTrace 客户端</title><link rel="stylesheet" href="/assets/style.css"></head>
<body><main><header><div><p class="eyebrow">TIMETRACE CLIENT</p><h1>本地控制面板</h1></div>
<span id="live" class="badge">连接中</span></header>
<section class="hero"><div><span class="label">采集状态</span><strong id="capture">—</strong></div>
<button id="toggle">—</button></section>
<section class="grid"><article><span class="label">活动连接</span><strong id="active">—</strong></article>
<article><span class="label">待上传</span><strong id="pending">—</strong><small id="backlog"></small></article>
<article><span class="label">最近采集</span><strong id="last-capture">—</strong></article>
<article><span class="label">最近上传</span><strong id="last-upload">—</strong></article></section>
<section><h2>连接路径</h2><div id="endpoints" class="endpoints"></div></section>
<section id="error-box" class="error hidden"><span class="label">最近错误</span><strong id="error"></strong></section>
<footer>只监听 127.0.0.1 · 不展示截图或鉴权密钥 <button id="shutdown" class="link">退出客户端</button></footer>
</main><script src="/assets/app.js" defer></script></body></html>"""


_APP_JS = r"""
const csrf = document.querySelector('meta[name="timetrace-csrf"]').content;
const $ = (id) => document.getElementById(id);
const when = (value) => value ? new Date(value * 1000).toLocaleString() : '尚无';
const bytes = (value) => value < 1024 ? `${value} B` : value < 1048576 ? `${(value/1024).toFixed(1)} KB` : `${(value/1048576).toFixed(1)} MB`;
async function mutate(url, method='POST', body={}) {
  const response = await fetch(url, {method, credentials:'same-origin', headers:{'Content-Type':'application/json','X-TimeTrace-CSRF':csrf}, body:JSON.stringify(body)});
  if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
  return response.json();
}
async function refresh() {
  try {
    const s = await (await fetch('/api/status', {cache:'no-store'})).json();
    $('live').textContent = '本机运行中'; $('live').className = 'badge ok';
    $('capture').textContent = s.paused ? `已暂停${s.pause_persisted?'':'（未保存）'}` : '正在采集';
    $('toggle').textContent = s.paused ? '继续采集' : '暂停采集'; $('toggle').dataset.paused = s.paused;
    $('active').textContent = s.active_endpoint || '暂无可用连接';
    $('pending').textContent = `${s.outbox_pending} 项`;
    $('backlog').textContent = `${bytes(s.outbox_bytes)} · 最早 ${when(s.outbox_oldest_at)}`;
    $('last-capture').textContent = when(s.last_capture_at); $('last-upload').textContent = when(s.last_upload_at);
    $('error-box').classList.toggle('hidden', !s.last_error); $('error').textContent = s.last_error ? `${s.last_error} · ${when(s.last_error_at)}` : '';
    $('endpoints').replaceChildren(...s.endpoints.map(ep => {
      const row=document.createElement('div'); row.className='endpoint';
      const state=ep.active?'● 活动':ep.healthy===true?'○ 正常':ep.healthy===false?'× 不可用':'? 未探测';
      const info=document.createElement('div'); const name=document.createElement('strong'); name.textContent=ep.name;
      const meta=document.createElement('small'); meta.textContent=`${state} · ${ep.url}`; info.append(name,meta);
      const button=document.createElement('button'); button.textContent=ep.enabled?'停用':'启用'; button.className='secondary';
      button.onclick=async()=>{button.disabled=true;try{await mutate(`/api/endpoints/${ep.index}`,'PATCH',{enabled:!ep.enabled});await refresh();}catch(e){alert(e.message)}finally{button.disabled=false}};
      row.append(info,button); return row;
    }));
  } catch (_) { $('live').textContent='连接中断'; $('live').className='badge bad'; }
}
$('toggle').onclick=async()=>{const paused=$('toggle').dataset.paused==='true';const result=await mutate(paused?'/api/capture/resume':'/api/capture/pause');if(result.warning)alert(result.warning);await refresh();};
$('shutdown').onclick=async()=>{if(confirm('退出 TimeTrace 客户端？')) await mutate('/api/shutdown');};
refresh(); setInterval(refresh, 2000);
"""


_STYLE_CSS = r"""
:root{color-scheme:dark;font-family:Inter,"Segoe UI",sans-serif;background:#0a0d12;color:#eef3f8}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#182538 0,transparent 40%),#0a0d12}main{max-width:900px;margin:auto;padding:48px 24px}header,.hero,.endpoint,footer{display:flex;align-items:center;justify-content:space-between;gap:16px}h1{margin:4px 0 28px;font-size:32px}h2{font-size:16px;margin-top:28px}.eyebrow,.label,small{color:#8d9aab;font-size:12px}.eyebrow{letter-spacing:.18em}.badge{padding:7px 11px;border:1px solid #394554;border-radius:99px}.ok{color:#8ce8bb;border-color:#277957}.bad{color:#ff9b9b;border-color:#823939}.hero,article,.endpoint,.error{background:#111720;border:1px solid #26303c;border-radius:14px;padding:20px}.hero strong,article strong{display:block;font-size:22px;margin-top:6px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin:12px 0}.endpoints{display:grid;gap:8px}.endpoint small{display:block;margin-top:5px;word-break:break-all}button{border:0;border-radius:9px;padding:10px 15px;background:#6da8ff;color:#07101d;font-weight:700;cursor:pointer}.secondary{background:#253140;color:#dce8f6}.error{margin-top:16px;border-color:#6b3434}.error strong{display:block;margin-top:6px;color:#ffb0b0}.hidden{display:none}footer{margin-top:28px;color:#778393;font-size:12px}.link{background:none;color:#9fb9dc;padding:4px}@media(max-width:600px){main{padding:28px 16px}.grid{grid-template-columns:1fr}header{align-items:flex-start}.hero{align-items:flex-end}}
"""
