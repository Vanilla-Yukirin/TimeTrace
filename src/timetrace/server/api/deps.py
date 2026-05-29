"""Shared FastAPI dependencies for the auth layer.

Phase 1 ships ``require_session`` (cookie-only) — used by /v1/auth/me,
/logout, /change-password, /admin/* and the protected /docs entry.

Phase 4 will extend this with ``require_session_or_bearer`` for the browser/AI
business routes (records / search / feedback). They go in the same module so
``app.py`` only imports from one place.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Cookie, HTTPException, Request, status

if TYPE_CHECKING:
    from timetrace.server.users import SessionUser, UserStore


async def require_session(
    request: Request,
    tt_session: str | None = Cookie(default=None),
) -> SessionUser:
    """Resolve a cookie session id → :class:`SessionUser`, or 401.

    Cookie name is hardcoded to ``tt_session`` matching ``AuthConfig.cookie_name``.
    If you ever rename the cookie, update both in lockstep.
    """
    if not tt_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    users: UserStore | None = getattr(request.app.state, "users", None)
    if users is None:
        # Misconfigured server (e.g. test forgot to wire UserStore). Surface
        # as 500 — this is not a client problem.
        raise HTTPException(status_code=500, detail="auth not configured")
    user = await users.resolve_session(tt_session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired or invalid",
        )
    return user
