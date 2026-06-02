"""User settings endpoints (per-app classification overrides + knowledge).

Backs the Settings KV editor. GET returns the stored overrides dict; PUT
validates + persists it. Mutating writes go through ``save_overrides`` so an
invalid category / oversized map is rejected with 400 rather than poisoning
``category_final`` downstream.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from timetrace.server.settings.overrides import load_overrides, save_overrides

router = APIRouter(tags=["settings"])


@router.get("/settings/app-overrides")
async def get_app_overrides(request: Request) -> dict:
    db = request.app.state.db
    return await load_overrides(db)


@router.put("/settings/app-overrides")
async def put_app_overrides(payload: dict, request: Request) -> dict:
    db = request.app.state.db
    try:
        return await save_overrides(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
