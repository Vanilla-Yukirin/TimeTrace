"""Browser-side auth routes: login / logout / me / change-password.

These four routes form the cookie-session entry: ``POST /v1/auth/login`` mints
a session id and drops it into an HttpOnly cookie; subsequent requests carry
the cookie automatically. ``GET /v1/auth/me`` is the gate the frontend's
``RequireAuth`` polls on mount to decide between Timeline page, login redirect,
or forced-change-password redirect.

Bearer-token CRUD lives in ``/v1/admin/tokens`` (Phase 6 work, not here).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from timetrace.server.api.deps import require_session
from timetrace.server.users import (
    InvalidPasswordError,
    LoginLockedOutError,
)

if TYPE_CHECKING:
    from timetrace.common.config import AuthConfig
    from timetrace.server.users import CookiePrincipal, UserStore

router = APIRouter(tags=["auth"], prefix="/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    username: str
    must_change_password: bool


class MeResponse(BaseModel):
    username: str
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


def _client_ip(request: Request) -> str:
    """Best-effort source IP for rate-limit keying.

    Trusts ``X-Forwarded-For`` if set (nginx sets it for public requests).
    For loopback requests there's no XFF; falls back to peer address.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # XFF is "client, proxy1, proxy2" — left-most is the original client.
        return xff.split(",", 1)[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _set_session_cookie(
    response: Response, session_id: str, *, cfg: AuthConfig
) -> None:
    """Drop the session cookie with the configured attributes."""
    response.set_cookie(
        key=cfg.cookie_name,
        value=session_id,
        max_age=cfg.session_ttl_s,
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, *, cfg: AuthConfig) -> None:
    response.delete_cookie(
        key=cfg.cookie_name,
        path="/",
        httponly=True,
        secure=cfg.cookie_secure,
        samesite="lax",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest, request: Request, response: Response
) -> LoginResponse:
    users: UserStore = request.app.state.users
    cfg: AuthConfig = request.app.state.auth_cfg
    try:
        session = await users.authenticate(
            body.username,
            body.password,
            ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except LoginLockedOutError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed login attempts",
            headers={"Retry-After": str(e.retry_after_s)},
        ) from e
    except PermissionError as e:
        # Same response shape for "user not found" and "wrong password" — don't
        # leak which one to a probing attacker.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid username or password",
        ) from e

    _set_session_cookie(response, session.session_id, cfg=cfg)
    return LoginResponse(
        username=session.username,
        must_change_password=session.must_change_password,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    user: CookiePrincipal = Depends(require_session),
) -> Response:
    users: UserStore = request.app.state.users
    cfg: AuthConfig = request.app.state.auth_cfg
    await users.revoke_session(user.session_id)
    _clear_session_cookie(response, cfg=cfg)
    # 204 No Content
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeResponse)
async def me(user: CookiePrincipal = Depends(require_session)) -> MeResponse:
    return MeResponse(
        username=user.username,
        must_change_password=user.must_change_password,
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    user: CookiePrincipal = Depends(require_session),
) -> Response:
    users: UserStore = request.app.state.users
    try:
        await users.change_password(
            user.username,
            body.old_password,
            body.new_password,
            current_session_id=user.session_id,
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="old password is incorrect",
        ) from e
    except InvalidPasswordError as e:
        raise HTTPException(
            status_code=422,
            detail=str(e),
        ) from e
    except ValueError as e:
        # "new password must differ from old"
        raise HTTPException(
            status_code=422,
            detail=str(e),
        ) from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)
