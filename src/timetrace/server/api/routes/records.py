"""Records query endpoint."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from timetrace.server.db.sqlite import UNKNOWN_DEVICE, normalize_device_filter

router = APIRouter(tags=["records"])

_THUMBS_PREFIX = ("thumbs/", "thumbs\\")


def _strip_prefix(path: str) -> str:
    for prefix in _THUMBS_PREFIX:
        if path.startswith(prefix):
            return path[len(prefix) :].replace("\\", "/")
    return path.replace("\\", "/")


def _strip_thumbs_prefix(row: dict) -> dict:
    """Normalize thumb_path fields to be relative to thumbs_dir (not data_dir).

    DB stores paths like "thumbs/2026/04/15/uuid.jpg" (relative to data_dir).
    The /thumbs static route serves from thumbs_dir, so the URL must be
    "/thumbs/2026/04/15/uuid.jpg" — i.e. strip the leading "thumbs/" segment.
    Applies to both the record-level thumb_path and each screenshot's thumb_path.
    """
    row = dict(row)
    if row.get("thumb_path"):
        row["thumb_path"] = _strip_prefix(row["thumb_path"])
    if row.get("screenshots"):
        row["screenshots"] = [
            {**s, "thumb_path": _strip_prefix(s["thumb_path"])} if s.get("thumb_path") else s
            for s in row["screenshots"]
        ]
    return row


def _parse_csv(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [tok.strip() for tok in raw.split(",") if tok.strip()]
    return items or None


@router.get("/records")
async def list_records(
    request: Request,
    start: int = 0,
    end: int = 9_999_999_999_999,
    limit: int = 200,
    cursor: str | None = None,
    app: str | None = None,
    apps: str | None = None,
    categories: str | None = None,
    q: str | None = None,
    device_id: str | None = None,
) -> dict:
    """Return records within a time range (epoch ms).

    - **start** / **end**: epoch milliseconds (inclusive)
    - **limit**: max items returned (≤ 500)
    - **cursor**: last record id for keyset pagination
    - **app**: exact app_name (single); kept for backwards compatibility
    - **apps**: comma-separated app_name list (multi-select)
    - **categories**: comma-separated category_final list
    - **q**: keyword over window_title OR vlm_desc (LIKE %q%)
    """
    db = request.app.state.db
    device_id = device_filter(device_id)
    limit = min(limit, 500)
    rows = await db.query_records(
        start,
        end,
        limit=limit,
        cursor=cursor,
        app_name=app,
        apps=_parse_csv(apps),
        categories=_parse_csv(categories),
        keyword=q,
        device_id=device_id,
    )
    next_cursor = rows[-1]["id"] if len(rows) == limit else None
    return {"items": [_strip_thumbs_prefix(r) for r in rows], "next_cursor": next_cursor}


def device_filter(device_id: str | None) -> str | None:
    """Route-level wrapper: invalid device scope → 422."""
    try:
        return normalize_device_filter(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/devices")
async def list_devices(request: Request) -> dict:
    """Devices for read-side scoping (selector, badges, lanes).

    Business-route view of the device registry: display ``label`` plus the
    owner-written name/description, never token bindings. ``unknown`` is the
    bucket for records captured before device identity existed.
    """
    db = request.app.state.db
    labels = await db.device_labels()
    items = [
        {
            "id": d["id"],
            "label": labels.get(d["id"], d["id"][:8]),
            "name": d["name"],
            "description": d["description"],
            "reported_name": d["reported_name"],
            "last_seen_at": d["last_seen_at"],
            "revoked_at": d["revoked_at"],
        }
        for d in await db.list_devices()
    ]
    return {
        "items": items,
        "unknown": {
            "id": UNKNOWN_DEVICE,
            "label": "未知设备",
            "record_count": await db.count_unknown_device_records(),
        },
    }


@router.get("/apps")
async def list_apps(request: Request) -> dict:
    """Return distinct app names with record counts (most used first)."""
    db = request.app.state.db
    return {"items": await db.list_apps()}


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
