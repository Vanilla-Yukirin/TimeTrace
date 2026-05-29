"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from timetrace.server.api.deps import require_session
from timetrace.server.api.mcp_auth import BearerOnlyMiddleware
from timetrace.server.api.routes import auth as auth_routes
from timetrace.server.api.routes import feedback, ingest, records, search, thumbs
from timetrace.server.auth import make_bearer_dependency
from timetrace.server.mcp_layer.server import build_mcp_server

_logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from timetrace.common.config import AuthConfig, StorageConfig, VLMConfig
    from timetrace.server.auth import ServerAuth
    from timetrace.server.db import Database
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

    # Phase 1 (login system): cookie-session routes. Phase 4 will gate the
    # business routes below with require_principal; today records/search/
    # feedback remain unauthenticated and the public proxy stays blocked via
    # frpc until Phase 7.
    if users is not None and auth_cfg is not None:
        app.include_router(auth_routes.router, prefix="/v1")
    app.include_router(records.router, prefix="/v1")
    app.include_router(search.router, prefix="/v1")
    app.include_router(feedback.router, prefix="/v1")
    # Phase 2 (login system): /thumbs is the first business route to actually
    # gate. It's behind cookie-or-bearer so the browser and MCP / capture
    # clients can both render images.
    if storage_cfg is not None:
        app.include_router(thumbs.router)
    if auth is not None:
        bearer = make_bearer_dependency(auth)
        app.include_router(ingest.router, prefix="/v1", dependencies=[Depends(bearer)])
    else:
        app.include_router(ingest.router, prefix="/v1")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    # /docs + /openapi.json behind cookie session — don't expose the API map
    # to public scanners. Only wired when ``users`` is configured (test/legacy
    # code paths that pass users=None keep the routes off entirely).
    if users is not None:

        @app.get("/openapi.json", include_in_schema=False)
        async def protected_openapi(_=Depends(require_session)) -> JSONResponse:
            return JSONResponse(
                get_openapi(
                    title=app.title,
                    version=app.version,
                    description=app.description,
                    routes=app.routes,
                )
            )

        @app.get("/docs", include_in_schema=False)
        async def protected_docs(_=Depends(require_session)):
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

    return app
