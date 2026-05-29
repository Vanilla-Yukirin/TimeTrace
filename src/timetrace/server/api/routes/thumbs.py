"""Authenticated thumbnail server.

Replaces the original ``app.mount("/thumbs", StaticFiles(...))`` because
``StaticFiles`` is an independent ASGI app and bypasses FastAPI's
``Depends`` chain — we couldn't gate it. This module re-implements the
small slice of StaticFiles behavior we actually need (GET a single file,
with the right Content-Type and a private Cache-Control), wraps it in
``require_principal`` for the cookie-or-bearer guard, and pins the path
inside ``thumbs_dir`` to defend against ``../`` traversal.

Performance note: every thumb request now goes through Python + a session
or token lookup. The session lookup is one indexed SQLite SELECT (sub-ms);
the bearer lookup is in-memory O(N) where N is the number of tokens, which
realistically is 1-5. A timeline page with ~30 thumbnails costs <30ms of
total auth overhead — comfortable for the use case. If it ever becomes
a hotspot we can move to nginx ``auth_request`` against ``/v1/auth/me``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from timetrace.server.api.deps import require_principal

if TYPE_CHECKING:
    from timetrace.server.api.deps import Principal


router = APIRouter(tags=["thumbs"])


@router.get("/thumbs/{path:path}", include_in_schema=False)
async def get_thumb(
    path: str,
    request: Request,
    _principal: Principal = Depends(require_principal),
) -> FileResponse:
    """Serve a single thumbnail file from ``app.state.thumbs_dir``.

    Returns 404 on:
      - missing file
      - path traversal attempt (``..`` escaping the dir)
      - any non-file target (symlink to dir, etc.)

    ``Cache-Control: private`` keeps a downstream CDN from caching what is
    user-private content even if the operator someday flips a CDN in front.
    """
    thumbs_dir: Path | None = getattr(request.app.state, "thumbs_dir", None)
    if thumbs_dir is None:
        # No storage configured (e.g. minimal test harness). Fail visibly.
        raise HTTPException(status_code=404, detail="thumbs not configured")

    # Path traversal defense: resolve both sides and verify containment.
    # ``Path.resolve(strict=False)`` collapses ``..`` without requiring the
    # file to exist (we still validate is_file() below).
    base = thumbs_dir.resolve()
    target = (thumbs_dir / path).resolve()
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
