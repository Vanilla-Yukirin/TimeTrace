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

On server first-start :meth:`ServerAuth.load_or_generate` creates the file
with a freshly-minted token and returns ``was_generated=True`` so the caller
can log it for the user to copy into ``timetrace-client init``.

The token-file I/O is intentionally consolidated here. Admin tooling
(``timetrace-server tokens add/revoke``) reaches into the same on-disk
schema, so :meth:`ServerAuth.read_tokens` / :meth:`ServerAuth.write_tokens`
are public classmethods rather than ad-hoc helpers in two modules — see
the 2026-05-16 schema-drift review note.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from fastapi import Header, HTTPException, Request, status

# Public — admin tooling reaches in for the same file location. Keeping
# these public-by-name (no underscore) is intentional; if you want to
# change where tokens live, change it once here.
DEFAULT_TOKEN_DIR = Path.home() / ".config" / "timetrace-server"
TOKEN_FILE_NAME = "tokens.json"
TOKEN_PREFIX = "tt_live_"


def _harden_token_file_perms(path: Path) -> None:
    """chmod 600 on POSIX so other Linux users can't read the token file.

    No-op on Windows (NTFS default user-home ACLs are already restrictive
    enough; chmod via Python wouldn't map to the actual permission model).
    Best-effort: a chmod failure on POSIX is logged via the OSError-swallow
    rather than crashing first-start.
    """
    if sys.platform == "win32":
        return
    try:
        path.chmod(0o600)
    except OSError:
        # Filesystem may not support chmod (e.g. mounted FAT); not fatal.
        pass


@dataclass
class TokenEntry:
    value: str
    label: str = "default"
    created_at: int | None = None


@dataclass(frozen=True)
class BearerPrincipal:
    """The subject of an authenticated Bearer-token request.

    Twin of ``users.CookiePrincipal``. ``deps.require_principal`` returns one or
    the other so routes can audit-distinguish "human at browser" from
    "machine using API key" without re-parsing headers.

    Carries the token's ``label`` (not its value!) so logs / audit trails can
    say which integration made a given call ("claude-code", "capture-yuki-win")
    without ever printing the secret.
    """

    token_label: str
    token_fingerprint: str


class ServerAuth:
    """In-memory token validator with disk-backed persistence."""

    def __init__(self, tokens: list[TokenEntry], token_dir: Path | None = None) -> None:
        self._tokens = list(tokens)
        self._token_set = {t.value for t in self._tokens}
        # Remembered so the Web UI's add/revoke can persist to the SAME file
        # the instance was loaded from. None → DEFAULT_TOKEN_DIR (matches the
        # CLI). The CLI path doesn't construct via load_or_generate, so it
        # keeps using the classmethods; the running server uses these instance
        # methods for hot mutation.
        self._token_dir = token_dir

    # ------------------------------------------------------------------ #
    # Token-file I/O (the single owner of the on-disk schema)            #
    # ------------------------------------------------------------------ #

    @staticmethod
    def token_path(token_dir: Path | None = None) -> Path:
        """Return the resolved tokens.json path under ``token_dir`` (or default)."""
        return (token_dir or DEFAULT_TOKEN_DIR) / TOKEN_FILE_NAME

    @classmethod
    def read_tokens(cls, token_dir: Path | None = None) -> list[TokenEntry]:
        """Read all token entries from disk; empty list if the file does not exist."""
        path = cls.token_path(token_dir)
        if not path.exists():
            return []
        data = json.loads(path.read_text("utf-8"))
        return [TokenEntry(**e) for e in data.get("tokens", [])]

    @classmethod
    def write_tokens(
        cls,
        tokens: list[TokenEntry],
        token_dir: Path | None = None,
    ) -> Path:
        """Persist ``tokens`` to disk and apply POSIX permission hardening.

        Creates ``token_dir`` if missing. Returns the resolved path written.
        Centralising this means any future schema change (extra fields, a
        version marker, etc.) lives in one place.
        """
        path = cls.token_path(token_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"tokens": [asdict(t) for t in tokens]}, indent=2)
        path.write_text(payload, encoding="utf-8")
        _harden_token_file_perms(path)
        return path

    # ------------------------------------------------------------------ #
    # First-start convenience                                              #
    # ------------------------------------------------------------------ #

    @classmethod
    def load_or_generate(
        cls, token_dir: Path | None = None
    ) -> tuple[ServerAuth, bool, TokenEntry | None]:
        """Read tokens from disk, or mint one if the file does not exist.

        Returns ``(auth, was_generated, generated_entry)``. When
        ``was_generated`` is True the caller should log ``generated_entry.value``
        so the user can copy it into the client init flow.
        """
        # Materialise the dir even when the file already exists, so a
        # subsequent admin `tokens add` doesn't trip on a missing parent.
        (token_dir or DEFAULT_TOKEN_DIR).mkdir(parents=True, exist_ok=True)
        existing = cls.read_tokens(token_dir)
        if existing:
            return cls(existing, token_dir=token_dir), False, None

        new_token = TokenEntry(
            value=cls.mint_token_value(),
            label="default",
            created_at=int(time.time() * 1000),
        )
        cls.write_tokens([new_token], token_dir)
        return cls([new_token], token_dir=token_dir), True, new_token

    @staticmethod
    def mint_token_value() -> str:
        """Generate a fresh ``tt_live_<32urlbytes>`` token value."""
        return TOKEN_PREFIX + secrets.token_urlsafe(32)

    # ------------------------------------------------------------------ #
    # Runtime validator surface                                            #
    # ------------------------------------------------------------------ #

    def is_valid(self, token: str) -> bool:
        return token in self._token_set

    def find_label(self, token: str) -> str | None:
        """Return the label of a matching token, or ``None`` if unknown.

        Used by ``require_principal`` to mint a :class:`BearerPrincipal` with
        the label set — so subsequent audit logs can name the caller
        ("which integration is hitting /v1/records?") without ever logging
        the token value.
        """
        for t in self._tokens:
            if t.value == token:
                return t.label
        return None

    def principal_for(self, token: str) -> BearerPrincipal | None:
        """Resolve a token without exposing or persisting the secret itself."""
        label = self.find_label(token)
        if label is None:
            return None
        return BearerPrincipal(
            token_label=label,
            token_fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )

    @property
    def tokens(self) -> list[TokenEntry]:
        return list(self._tokens)

    # ------------------------------------------------------------------ #
    # Hot mutation (Web UI admin tokens CRUD) — updates in-memory set AND  #
    # persists to disk so the change takes effect WITHOUT a server restart #
    # (unlike the CLI in admin_cmd.py, which writes-then-restart).         #
    # ------------------------------------------------------------------ #

    def add_token(self, label: str) -> TokenEntry:
        """Mint + register + persist a new token. Returns the entry (with value).

        Raises ``ValueError`` if the label already exists — labels are the
        handle the Web UI revokes by, so they must be unique.
        """
        if any(t.label == label for t in self._tokens):
            raise ValueError(f"a token labelled {label!r} already exists")
        entry = TokenEntry(
            value=self.mint_token_value(),
            label=label,
            created_at=int(time.time() * 1000),
        )
        self._tokens.append(entry)
        self._token_set.add(entry.value)
        self.write_tokens(self._tokens, self._token_dir)
        return entry

    def revoke_token(self, label: str) -> bool:
        """Remove the token with ``label`` from memory + disk.

        Returns True if a token was removed, False if no such label.
        """
        target = next((t for t in self._tokens if t.label == label), None)
        if target is None:
            return False
        self._tokens = [t for t in self._tokens if t is not target]
        self._token_set.discard(target.value)
        self.write_tokens(self._tokens, self._token_dir)
        return True


def make_bearer_dependency(auth: ServerAuth):
    """Build a FastAPI dependency that 401s requests without a valid bearer."""

    async def require_bearer(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> BearerPrincipal:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or malformed Authorization header",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization.removeprefix("Bearer ").strip()
        principal = auth.principal_for(token)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Router-level dependencies discard return values. Put the verified
        # principal on request.state so ingest can bind a device to the token
        # fingerprint without parsing the Authorization header a second time.
        request.state.bearer_principal = principal
        return principal

    return require_bearer
