"""Bearer-only ASGI middleware for the mounted ``/mcp`` sub-app.

Why this isn't a FastAPI ``Depends``:
  ``app.mount("/mcp", mcp_server.streamable_http_app())`` hands the sub-tree
  to an independent ASGI app (FastMCP's starlette instance). FastAPI's
  ``dependencies=[...]`` chain only wraps **routes registered on the parent
  app**, not requests dispatched into a mount. So we have to gate at the
  ASGI layer.

Why this isn't ``BaseHTTPMiddleware``:
  ``BaseHTTPMiddleware`` materialises the response body to bridge starlette's
  request/response API, which **breaks SSE / chunked streaming**. MCP's
  ``streamable_http_app`` is built around that pattern (the name says so).
  A pure ASGI middleware that just inspects ``scope`` and decides
  401-or-passthrough leaves the inner streaming behavior untouched.

Why bearer-only and not cookie-or-bearer:
  /mcp is machine-to-machine by design. The clients are Claude Code / Desktop
  / other AI agents reading the ``.mcp.json`` headers. There's no browser-cookie
  flow here. A token that admin minted via the Web UI is the only path in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from timetrace.server.auth import ServerAuth


_REJECT_BODY = b'{"detail":"missing or invalid bearer token"}'


class BearerOnlyMiddleware:
    """Pure ASGI middleware: pass-through for valid bearer, 401 otherwise.

    Must be applied **before** the sub-app is mounted into a parent
    starlette/FastAPI app — once mounted, starlette freezes the middleware
    stack of the sub-app and ``add_middleware`` becomes a no-op.

    Lifespan / WebSocket scopes are passed through untouched (the inner app
    needs them for ``session_manager.run()``). Only ``http`` scopes are gated.
    """

    def __init__(self, app, auth: ServerAuth) -> None:
        self.app = app
        self._auth = auth

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            # lifespan / websocket / anything else — let it through. The
            # session manager's anyio task group depends on lifespan reaching
            # the inner app intact.
            await self.app(scope, receive, send)
            return

        token = _extract_bearer_from_scope(scope)
        if token is None or not self._auth.is_valid(token):
            await _send_401(send)
            return

        await self.app(scope, receive, send)


def _extract_bearer_from_scope(scope) -> str | None:
    """Pull ``Authorization: Bearer <token>`` out of an ASGI scope's headers."""
    # scope["headers"] is a list of (bytes_name, bytes_value) tuples,
    # lowercase names per ASGI spec.
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            decoded = value.decode("latin-1")
            if not decoded.startswith("Bearer "):
                return None
            return decoded.removeprefix("Bearer ").strip() or None
    return None


async def _send_401(send) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
                (b"content-length", str(len(_REJECT_BODY)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": _REJECT_BODY})
