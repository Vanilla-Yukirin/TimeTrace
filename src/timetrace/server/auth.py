"""Bearer-token auth for the server's ingest endpoints.

Tokens are persisted in `~/.config/timetrace-server/tokens.json` (JSON over
TOML — chosen over the kickoff's `tokens.toml` because stdlib `json` writes
in addition to reading; tomllib is read-only and pulling in tomli-w for one
file isn't worth it). The file shape is forward-compatible with adding more
metadata fields per token:

    {
      "tokens": [
        {"value": "tt_live_...", "label": "default", "created_at": 1747300000000}
      ]
    }

On server first-start `ServerAuth.load_or_generate(...)` creates the file
with a freshly-minted token and returns `was_generated=True` so the caller
can log it for the user to copy into `timetrace-client init`.

Auth is applied at the FastAPI router level via `make_bearer_dependency`;
unauthenticated routes (healthz, /thumbs/* static, frontend-facing read
endpoints today) skip the dependency. Future P3b adds the same dependency
to the close endpoint and any other write paths.
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import Header, HTTPException, status

_DEFAULT_TOKEN_DIR = Path.home() / ".config" / "timetrace-server"
_TOKEN_FILE_NAME = "tokens.json"
_TOKEN_PREFIX = "tt_live_"


@dataclass
class TokenEntry:
    value: str
    label: str = "default"
    created_at: int | None = None


class ServerAuth:
    """In-memory token validator with disk-backed persistence."""

    def __init__(self, tokens: list[TokenEntry]) -> None:
        self._tokens = list(tokens)
        self._token_set = {t.value for t in self._tokens}

    @classmethod
    def load_or_generate(
        cls, token_dir: Path | None = None
    ) -> tuple[ServerAuth, bool, TokenEntry | None]:
        """Read tokens from disk, or mint one if the file does not exist.

        Returns ``(auth, was_generated, generated_entry)``. When
        ``was_generated`` is True the caller should log ``generated_entry.value``
        so the user can copy it into the client init flow.
        """
        token_dir = token_dir or _DEFAULT_TOKEN_DIR
        token_dir.mkdir(parents=True, exist_ok=True)
        token_file = token_dir / _TOKEN_FILE_NAME

        if token_file.exists():
            data = json.loads(token_file.read_text("utf-8"))
            tokens = [TokenEntry(**e) for e in data.get("tokens", [])]
            return cls(tokens), False, None

        new_token = TokenEntry(
            value=cls._generate_token(),
            label="default",
            created_at=int(time.time() * 1000),
        )
        data = {"tokens": [asdict(new_token)]}
        token_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return cls([new_token]), True, new_token

    @staticmethod
    def _generate_token() -> str:
        return _TOKEN_PREFIX + secrets.token_urlsafe(32)

    def is_valid(self, token: str) -> bool:
        return token in self._token_set

    @property
    def tokens(self) -> list[TokenEntry]:
        return list(self._tokens)


def make_bearer_dependency(auth: ServerAuth):
    """Build a FastAPI dependency that 401s requests without a valid bearer."""

    async def require_bearer(authorization: str | None = Header(default=None)) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or malformed Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.removeprefix("Bearer ").strip()
        if not auth.is_valid(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_bearer
