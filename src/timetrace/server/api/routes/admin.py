"""Admin routes — Bearer-token CRUD for the Web UI.

Cookie-only (``require_session``): managing API tokens is an interactive admin
operation, not something a bearer-holding script should do to itself. This is
the browser-facing twin of the ``timetrace-server tokens`` CLI, with one key
difference: these mutate the **running** ``ServerAuth`` in memory (+ persist),
so a freshly created token works immediately — no server restart.

Single-user note: there's no per-user scoping. Any logged-in session (= the
admin) can manage all tokens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from timetrace.server.api.deps import require_session_password_set

if TYPE_CHECKING:
    from timetrace.server.auth import ServerAuth
    from timetrace.server.users import CookiePrincipal

router = APIRouter(tags=["admin"], prefix="/admin")


class TokenSummary(BaseModel):
    """A token WITHOUT its value — safe to list. ``value`` is only ever
    returned once, at creation, by :class:`TokenCreated`."""

    label: str
    created_at: int | None


class TokenCreated(BaseModel):
    label: str
    value: str
    created_at: int | None


class CreateTokenRequest(BaseModel):
    # Labels double as the revoke handle and the .mcp.json server name hint,
    # so keep them simple. Bounded to avoid someone pasting a novel.
    label: str = Field(min_length=1, max_length=64)


class DeviceSummary(BaseModel):
    id: str
    token_label: str | None
    name: str
    description: str
    reported_name: str
    reported_description: str
    client_version: str
    capabilities: list[str]
    first_seen_at: int
    last_seen_at: int
    revoked_at: int | None


class UpdateDeviceRequest(BaseModel):
    name: str = Field(max_length=128)
    description: str = Field(default="", max_length=512)


def _auth(request: Request) -> ServerAuth:
    auth: ServerAuth | None = getattr(request.app.state, "auth", None)
    if auth is None:
        raise HTTPException(status_code=500, detail="bearer auth not configured")
    return auth


@router.get("/tokens", response_model=list[TokenSummary])
async def list_tokens(
    request: Request,
    _user: CookiePrincipal = Depends(require_session_password_set),
) -> list[TokenSummary]:
    auth = _auth(request)
    return [TokenSummary(label=t.label, created_at=t.created_at) for t in auth.tokens]


@router.post("/tokens", response_model=TokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    body: CreateTokenRequest,
    request: Request,
    _user: CookiePrincipal = Depends(require_session_password_set),
) -> TokenCreated:
    auth = _auth(request)
    try:
        entry = auth.add_token(body.label)
    except ValueError as e:
        # Duplicate label.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return TokenCreated(label=entry.label, value=entry.value, created_at=entry.created_at)


@router.delete("/tokens/{label}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    label: str,
    request: Request,
    _user: CookiePrincipal = Depends(require_session_password_set),
):
    auth = _auth(request)
    if not auth.revoke_token(label):
        raise HTTPException(status_code=404, detail=f"no token labelled {label!r}")
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/devices", response_model=list[DeviceSummary])
async def list_devices(
    request: Request,
    _user: CookiePrincipal = Depends(require_session_password_set),
) -> list[DeviceSummary]:
    return [DeviceSummary(**device) for device in await request.app.state.db.list_devices()]


@router.patch("/devices/{device_id}", response_model=DeviceSummary)
async def update_device(
    device_id: str,
    body: UpdateDeviceRequest,
    request: Request,
    _user: CookiePrincipal = Depends(require_session_password_set),
) -> DeviceSummary:
    db = request.app.state.db
    if not await db.update_device(device_id, name=body.name, description=body.description):
        raise HTTPException(status_code=404, detail=f"device not found: {device_id!r}")
    device = next(d for d in await db.list_devices() if d["id"] == device_id)
    return DeviceSummary(**device)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(
    device_id: str,
    request: Request,
    _user: CookiePrincipal = Depends(require_session_password_set),
):
    if not await request.app.state.db.set_device_revoked(device_id, revoked=True):
        raise HTTPException(status_code=404, detail=f"device not found: {device_id!r}")
    from fastapi import Response

    return Response(status_code=status.HTTP_204_NO_CONTENT)
