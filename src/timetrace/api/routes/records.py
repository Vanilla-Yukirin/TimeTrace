"""Records query endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["records"])

_THUMBS_PREFIX = ("thumbs/", "thumbs\\")


def _strip_thumbs_prefix(row: dict) -> dict:
    """Normalize thumb_path to be relative to thumbs_dir (not data_dir).

    DB stores paths like "thumbs/2026/04/15/uuid.jpg" (relative to data_dir).
    The /thumbs static route serves from thumbs_dir, so the URL must be
    "/thumbs/2026/04/15/uuid.jpg" — i.e. strip the leading "thumbs/" segment.
    """
    tp = row.get("thumb_path")
    if tp:
        for prefix in _THUMBS_PREFIX:
            if tp.startswith(prefix):
                row = {**row, "thumb_path": tp[len(prefix) :].replace("\\", "/")}
                break
    return row


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
    return {"items": [_strip_thumbs_prefix(r) for r in rows], "next_cursor": next_cursor}


@router.get("/records/{record_id}")
async def get_record(record_id: str, request: Request) -> dict:
    """Return a single record with full detail (screenshots list included)."""
    db = request.app.state.db
    record = await db.get_record_by_id(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return _strip_thumbs_prefix(record)


@router.get("/runtime-info")
async def runtime_info(request: Request) -> dict:
    """Return read-only runtime information for the Settings page."""
    from timetrace import __version__  # noqa: PLC0415

    return {
        "version": __version__,
        "data_dir": str(request.app.state.data_dir),
        "api_host": request.app.state.api_host,
        "api_port": request.app.state.api_port,
    }
