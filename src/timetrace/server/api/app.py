"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from timetrace.server.api.routes import feedback, ingest, records, search

if TYPE_CHECKING:
    from timetrace.common.config import StorageConfig
    from timetrace.server.db import Database
    from timetrace.server.phash_index.index import PHashIndex
    from timetrace.server.storage.blob import BlobStorage
    from timetrace.server.vlm.client import VLMClient


def create_app(
    db: Database,
    storage_cfg: StorageConfig | None = None,
    phash_index: PHashIndex | None = None,
    vlm_client: VLMClient | None = None,
    blob_storage: BlobStorage | None = None,
) -> FastAPI:
    app = FastAPI(
        title="TimeTrace Local API",
        version="0.1.0",
        description="Local-first desktop activity memory layer – Local API",
    )

    app.state.db = db
    app.state.phash_index = phash_index
    app.state.vlm_client = vlm_client
    app.state.blob_storage = blob_storage
    if storage_cfg is not None:
        app.state.data_dir = str(storage_cfg.data_dir)
        # Ensure thumbs dir exists before mounting (first-run has no screenshots yet)
        storage_cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
        app.mount(
            "/thumbs",
            StaticFiles(directory=storage_cfg.thumbs_dir),
            name="thumbs",
        )
    else:
        app.state.data_dir = ""

    # api_host / api_port default values; overridden by main.py if needed
    app.state.api_host = "127.0.0.1"
    app.state.api_port = 8765

    app.include_router(records.router, prefix="/v1")
    app.include_router(search.router, prefix="/v1")
    app.include_router(feedback.router, prefix="/v1")
    app.include_router(ingest.router, prefix="/v1")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app
