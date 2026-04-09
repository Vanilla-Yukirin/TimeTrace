"""Records query endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["records"])


@router.get("/records")
async def list_records(
    request: Request,
    start: int = 0,
    end: int = 9_999_999_999_999,
    limit: int = 200,
    cursor: str | None = None,
) -> dict:
    """Return records within a time range (epoch ms)."""
    db = request.app.state.db
    rows = await db.query_records(start, end, limit=limit, cursor=cursor)
    next_cursor = rows[-1]["id"] if rows else None
    return {"items": rows, "next_cursor": next_cursor}
