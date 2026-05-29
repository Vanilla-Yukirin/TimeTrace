"""Shared FastAPI dependencies for the auth layer.

Two principal types, one union, two FastAPI deps:

- ``require_session`` — cookie-only. Use on routes that act on the *user*
  (change password, manage sessions, future "device list"). A bearer token
  shouldn't be able to change the human's password.
- ``require_principal`` — cookie OR bearer. Use on routes that produce or
  consume *data* (records, search, feedback, thumbs, mcp). Browser users
  reach them via cookie; MCP clients / scripts / capture clients reach them
  via Bearer token. Returns one of two dataclasses so routes can audit-
  distinguish which channel served the request:

    - :class:`~timetrace.server.users.CookiePrincipal`  (human at browser)
    - :class:`~timetrace.server.auth.BearerPrincipal`   (machine via API key)

Ingest stays on its own dedicated bearer dep (``make_bearer_dependency``) —
it's a write surface that should *never* be invoked from a human session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from timetrace.server.auth import BearerPrincipal
from timetrace.server.users import CookiePrincipal

if TYPE_CHECKING:
    from timetrace.server.auth import ServerAuth
    from timetrace.server.users import UserStore


Principal = CookiePrincipal | BearerPrincipal


def _extract_bearer(authorization: str | None) -> str | None:
    """Return the token value if Authorization is a well-formed Bearer header."""
    if not authorization:
        return None
    if not authorization.startswith("Bearer "):
        return None
    return authorization.removeprefix("Bearer ").strip() or None


async def _resolve_cookie(
    request: Request, session_id: str
) -> CookiePrincipal | None:
    """Look up a cookie session id → CookiePrincipal or None."""
    users: UserStore | None = getattr(request.app.state, "users", None)
    if users is None:
        return None
    return await users.resolve_session(session_id)


def _resolve_bearer(request: Request, token: str) -> BearerPrincipal | None:
    """Look up a bearer token → BearerPrincipal or None."""
    auth: ServerAuth | None = getattr(request.app.state, "auth", None)
    if auth is None:
        return None
    label = auth.find_label(token)
    if label is None:
        return None
    return BearerPrincipal(token_label=label)


# Detail string returned (403) when a session whose user still has the forced
# password-change flag tries to reach a gated route. Surfaced so the frontend /
# a script gets a clear reason instead of a generic 401.
_MUST_CHANGE_DETAIL = "password change required before accessing this resource"


async def require_session(
    request: Request,
    tt_session: str | None = Cookie(default=None),
) -> CookiePrincipal:
    """Cookie-only — 401 if missing or invalid. Allows a must-change session.

    Used by the auth-self routes /v1/auth/me, /logout, /change-password — these
    MUST stay reachable while ``password_must_change`` is set so the user can
    actually change their password. Routes that should be BLOCKED until the
    password is changed use :func:`require_session_password_set` instead.
    """
    if not tt_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
        )
    user = await _resolve_cookie(request, tt_session)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session expired or invalid",
        )
    return user


async def require_session_password_set(
    user: CookiePrincipal = Depends(require_session),
) -> CookiePrincipal:
    """Cookie session whose user has ALREADY changed the default password.

    Used by /v1/admin/* and /docs — admin operations a still-default account
    must not perform. A must-change session gets 403 (not 401: it IS
    authenticated, it's just gated until the password is changed).
    """
    if user.must_change_password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_MUST_CHANGE_DETAIL)
    return user


async def require_principal(
    request: Request,
    tt_session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> Principal:
    """Cookie OR Bearer — 401 if neither produces a valid principal.

    Order:
      1. Cookie session (cheaper for the common browser path).
      2. Bearer token (machine path).

    SECURITY: a cookie session whose user still has ``password_must_change``
    set is REJECTED with 403 here — otherwise the forced first-login password
    change would be a frontend-only redirect that any non-browser client
    (curl / script) skips, letting default admin/admin credentials read all
    data and mint bearer tokens before the password is ever changed. Bearer
    principals are machine tokens with no must-change concept and pass through.
    """
    if tt_session:
        user = await _resolve_cookie(request, tt_session)
        if user is not None:
            if user.must_change_password:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=_MUST_CHANGE_DETAIL
                )
            return user

    token = _extract_bearer(authorization)
    if token is not None:
        machine = _resolve_bearer(request, token)
        if machine is not None:
            return machine

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
