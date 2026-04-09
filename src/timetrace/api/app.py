"""FastAPI application factory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

from timetrace.api.routes import feedback, records, search

if TYPE_CHECKING:
    from timetrace.storage.database import Database


def create_app(db: Database) -> FastAPI:
    app = FastAPI(
        title="TimeTrace Local API",
        version="0.1.0",
        description="Local-first desktop activity memory layer – Local API",
    )

    app.state.db = db

    app.include_router(records.router, prefix="/v1")
    app.include_router(search.router, prefix="/v1")
    app.include_router(feedback.router, prefix="/v1")

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app
