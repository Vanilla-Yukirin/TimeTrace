"""Authenticated full-resolution screenshot server.

Companion to ``thumbs.py``. The timeline + detail views use the small JPEG
thumbnails (``/thumbs/...``); the lightbox ("点击放大") wants the pixel-exact
PNG so a zoomed-in screenshot stays sharp. Those originals live under
``data_dir/screenshots/...`` and the DB stores their path WITH the
``screenshots/`` prefix (relative to data_dir), so this route serves straight
from ``data_dir`` root rather than a sub-dir.

Same auth + path-traversal model as thumbs: ``require_principal`` (cookie OR
bearer) and a ``relative_to(base)`` containment check. Distinct route prefix
(``/blob``) so it can't be reached via the thumbs handler and vice versa.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from timetrace.server.api.deps import require_principal

if TYPE_CHECKING:
    from timetrace.server.api.deps import Principal


router = APIRouter(tags=["blob"])


@router.get("/blob/{path:path}", include_in_schema=False)
async def get_blob(
    path: str,
    request: Request,
    _principal: Principal = Depends(require_principal),
) -> FileResponse:
    """Serve a single full-resolution file from ``app.state.data_dir``.

    ``path`` is the DB-stored relative path (e.g.
    ``screenshots/2026/06/01/...png``). Returns 404 on missing file, traversal
    attempt, or non-file target — identical posture to the thumbs route.
    """
    data_dir_str: str = getattr(request.app.state, "data_dir", "") or ""
    if not data_dir_str:
        raise HTTPException(status_code=404, detail="storage not configured")

    base = Path(data_dir_str).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e

    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(
        target,
        headers={"Cache-Control": "private, max-age=86400"},
    )
