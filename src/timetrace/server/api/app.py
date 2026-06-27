"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import structlog
from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from timetrace.server.api.deps import (
    require_principal,
    require_session_password_set,
)
from timetrace.server.api.mcp_auth import BearerOnlyMiddleware
from timetrace.server.api.routes import admin as admin_routes
from timetrace.server.api.routes import agent as agent_routes
from timetrace.server.api.routes import (
    audit,
    blob,
    feedback,
    ingest,
    llm_requests,
    records,
    reports,
    search,
    settings,
    skill,
    summaries,
    thumbs,
)
from timetrace.server.api.routes import auth as auth_routes
from timetrace.server.auth import make_bearer_dependency
from timetrace.server.mcp_layer.server import build_mcp_server

_logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from timetrace.common.config import AuthConfig, StorageConfig, VLMConfig
    from timetrace.server.auth import ServerAuth
    from timetrace.server.db import Database
    from timetrace.server.embedding.client import EmbeddingClient
    from timetrace.server.phash_index.index import PHashIndex
    from timetrace.server.storage.blob import BlobStorage
    from timetrace.server.users import UserStore
    from timetrace.server.vlm.client import VLMClient


def create_app(
    db: Database,
    storage_cfg: StorageConfig | None = None,
    phash_index: PHashIndex | None = None,
    vlm_client: VLMClient | None = None,
    blob_storage: BlobStorage | None = None,
    auth: ServerAuth | None = None,
    vlm_cfg: VLMConfig | None = None,
    users: UserStore | None = None,
    auth_cfg: AuthConfig | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> FastAPI:
    # Build MCP first so we can wire its session manager into FastAPI lifespan.
    # FastMCP's streamable_http_app() needs the session manager's anyio task
    # group running for the whole app lifetime; mounting alone isn't enough.
    mcp_server = build_mcp_server(db, vlm_cfg)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        _logger.info("api.lifespan.enter", mcp_session_manager_running=True)
        async with mcp_server.session_manager.run():
            yield
        _logger.info("api.lifespan.exit")

    # Disable default /docs and /openapi.json — we re-expose them below behind
    # ``Depends(require_session)`` so public deploys don't leak the API map.
    # Local dev users can still see them after logging in.
    app = FastAPI(
        title="TimeTrace Local API",
        version="0.1.0",
        description="Local-first desktop activity memory layer – Local API",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.state.db = db
    app.state.phash_index = phash_index
    app.state.vlm_client = vlm_client
    app.state.blob_storage = blob_storage
    app.state.auth = auth
    app.state.users = users
    app.state.auth_cfg = auth_cfg
    app.state.vlm_cfg = vlm_cfg
    app.state.embedding_client = embedding_client
    if storage_cfg is not None:
        app.state.data_dir = str(storage_cfg.data_dir)
        # Materialise the dir up front (first-run has no screenshots yet);
        # then expose its Path on app.state for the /thumbs route to read.
        storage_cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
        app.state.thumbs_dir = storage_cfg.thumbs_dir
    else:
        app.state.data_dir = ""
        app.state.thumbs_dir = None

    # api_host / api_port default values; overridden by main.py if needed
    app.state.api_host = "127.0.0.1"
    app.state.api_port = 8765

    # Phase 1 (login system): cookie-session routes.
    if users is not None and auth_cfg is not None:
        app.include_router(auth_routes.router, prefix="/v1")
    # Phase 6 (login system): admin tokens CRUD. Cookie-only (require_session
    # baked into the router). Needs both a UserStore (for the cookie gate) and
    # a ServerAuth (the thing it mutates).
    if users is not None and auth is not None:
        app.include_router(admin_routes.router, prefix="/v1")

    # Phase 4 (login system): business routes (records / search / feedback)
    # gated with require_principal (cookie OR bearer). Skipped when ``users``
    # is None — legacy test fixtures that pass ``create_app(db)`` with no
    # auth wiring still want the unauthenticated behavior, matching the
    # ``auth=None → ingest open`` pattern below.
    business_deps = [Depends(require_principal)] if users is not None else []
    app.include_router(records.router, prefix="/v1", dependencies=business_deps)
    app.include_router(audit.router, prefix="/v1", dependencies=business_deps)
    app.include_router(llm_requests.router, prefix="/v1", dependencies=business_deps)
    app.include_router(summaries.router, prefix="/v1", dependencies=business_deps)
    app.include_router(search.router, prefix="/v1", dependencies=business_deps)
    app.include_router(feedback.router, prefix="/v1", dependencies=business_deps)
    app.include_router(agent_routes.router, prefix="/v1", dependencies=business_deps)
    app.include_router(reports.router, prefix="/v1", dependencies=business_deps)
    app.include_router(settings.router, prefix="/v1", dependencies=business_deps)

    # Phase 2 (login system): /thumbs is its own route module because the
    # FileResponse path-traversal logic doesn't belong on records/etc. — but
    # the auth model is the same (cookie OR bearer). /blob serves the
    # full-resolution screenshot originals (lightbox zoom) from data_dir with
    # the same auth + traversal posture.
    if storage_cfg is not None:
        app.include_router(thumbs.router)
        app.include_router(blob.router)
    if auth is not None:
        bearer = make_bearer_dependency(auth)
        app.include_router(ingest.router, prefix="/v1", dependencies=[Depends(bearer)])
    else:
        app.include_router(ingest.router, prefix="/v1")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    # Claude Code skill download — open (it's docs; the MCP it describes is still
    # bearer-gated). A local agent fetches /skill and installs the returned
    # markdown so it learns to drive TimeTrace over MCP.
    app.include_router(skill.router)

    # /docs + /openapi.json behind cookie session — don't expose the API map
    # to public scanners. Only wired when ``users`` is configured (test/legacy
    # code paths that pass users=None keep the routes off entirely).
    if users is not None:

        @app.get("/openapi.json", include_in_schema=False)
        async def protected_openapi(_=Depends(require_session_password_set)) -> JSONResponse:
            return JSONResponse(
                get_openapi(
                    title=app.title,
                    version=app.version,
                    description=app.description,
                    routes=app.routes,
                )
            )

        @app.get("/docs", include_in_schema=False)
        async def protected_docs(_=Depends(require_session_password_set)):
            return get_swagger_ui_html(
                openapi_url="/openapi.json",
                title=app.title + " – Swagger UI",
            )

    # MCP server: exposes activity context as tools to external AI agents
    # (Claude Code / Desktop). Mounted at /mcp, streamable-HTTP transport.
    # Phase 3 (login system): when a ServerAuth is configured, wrap the
    # sub-app in BearerOnlyMiddleware so the mount only honors valid bearer
    # tokens — cookies / sessions don't apply here (MCP clients aren't
    # browsers). The wrap MUST happen before ``mount`` because starlette
    # freezes the middleware stack of a mounted app.
    mcp_app = mcp_server.streamable_http_app()
    if auth is not None:
        mcp_app = BearerOnlyMiddleware(mcp_app, auth)
    app.mount("/mcp", mcp_app)

    # CSRF defense-in-depth (only in secure/public mode): SameSite=Lax already
    # blocks the classic cross-SITE POST, but a same-SITE sibling subdomain
    # (e.g. evil.yukirin.me) is treated as same-site and would still send the
    # cookie on a cross-subdomain forged POST. Reject any cookie-authenticated
    # mutating request whose Origin host doesn't match the served Host.
    # Skipped entirely in dev (cookie_secure=False) because the Vite proxy
    # rewrites Host while the browser Origin stays :5173, which would false-403.
    # Bearer requests (no cookie) and login (no cookie yet) are unaffected.
    if auth_cfg is not None and auth_cfg.cookie_secure:
        _cookie_name = auth_cfg.cookie_name
        _mutating = {"POST", "PUT", "PATCH", "DELETE"}

        @app.middleware("http")
        async def csrf_origin_guard(request, call_next):
            if request.method in _mutating and request.cookies.get(_cookie_name):
                origin = request.headers.get("origin")
                if origin:
                    origin_host = urlparse(origin).netloc
                    host = request.headers.get("host", "")
                    if origin_host and origin_host != host:
                        return JSONResponse(
                            {"detail": "cross-origin request refused"},
                            status_code=403,
                        )
            return await call_next(request)

    return app
