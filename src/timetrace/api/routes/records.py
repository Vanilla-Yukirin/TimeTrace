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
    app: str | None = None,
    q: str | None = None,
) -> dict:
    """Return records within a time range (epoch ms).

    - **start** / **end**: epoch milliseconds (inclusive)
    - **limit**: max items returned (≤ 500)
    - **cursor**: last record id for keyset pagination
    - **app**: filter by exact app_name
    - **q**: filter by window_title keyword (LIKE %q%)
    """
    db = request.app.state.db
    limit = min(limit, 500)
    rows = await db.query_records(
        start,
        end,
        limit=limit,
        cursor=cursor,
        app_name=app,
        keyword=q,
    )
    next_cursor = rows[-1]["id"] if len(rows) == limit else None
    return {"items": rows, "next_cursor": next_cursor}
