"""Login-system user store: password hashing, session minting, rate limiting.

Two-channel auth in TimeTrace:

- **Cookie session** — browser users, password-authenticated. Lives here.
- **Bearer token** — machine-to-machine (capture client, MCP). Lives in
  ``server/auth.py::ServerAuth`` and ``~/.config/timetrace-server/tokens.json``.

``UserStore`` is the cookie-side equivalent of ``ServerAuth``. It owns:

- bcrypt hashing / verification (we use ``bcrypt`` directly, not passlib —
  one dep, no premature abstraction)
- session id minting (``secrets.token_urlsafe``)
- in-memory per-IP login rate-limit (5 failures / 15 min → 30 min lockout)
- password complexity validation (8+ chars, contains letter + digit)

All persistent state goes through the ``Database`` CRUD methods
(``insert_user`` / ``get_session`` / ``delete_sessions_except`` / ...).
Restarts wipe the in-memory rate-limit counters — acceptable for a single-user
system; an attacker waiting through restarts gets nothing useful.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import bcrypt
import structlog

if TYPE_CHECKING:
    from timetrace.common.config import AuthConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CookiePrincipal:
    """The subject of an authenticated cookie request.

    Twin of ``auth.BearerPrincipal``. ``deps.require_principal`` returns one or
    the other so routes can audit-distinguish "human at browser" from "machine
    using API key" without re-parsing headers.
    """

    username: str
    session_id: str
    must_change_password: bool


class InvalidPasswordError(ValueError):
    """Raised when a new password doesn't meet complexity requirements.

    Wraps the human-readable reason; routes map this to HTTP 422.
    """


class LoginLockedOutError(RuntimeError):
    """Raised when a login attempt is rate-limited; carries seconds-remaining."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__(f"too many failures, retry after {retry_after_s}s")
        self.retry_after_s = retry_after_s


def _now_s() -> float:
    return time.time()


def _now_ms() -> int:
    return int(_now_s() * 1000)


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password with bcrypt (cost 12)."""
    # cost=12 ≈ 250ms on a Cortex-A78 / 80ms on a desktop x86 — slow enough to
    # frustrate offline cracking, fast enough that login isn't user-visible.
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plaintext.encode("utf-8"), salt).decode("ascii")


def verify_password(plaintext: str, hashed: str) -> bool:
    """Constant-time bcrypt verification. Wrong / malformed hash → False."""
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        # Malformed hash (e.g. corrupted DB row). Treat as auth failure rather
        # than crashing the request — the route will return 401.
        logger.warning("password.verify_malformed_hash")
        return False


# bcrypt (the C lib, 4.x/5.x) refuses passwords longer than 72 BYTES — 5.x
# raises ValueError outright. Cap explicitly in validation so an over-long
# password fails as a clean InvalidPasswordError ("too long") instead of
# bubbling bcrypt's raw internal message out of a generic except ValueError.
# Note: bytes, not chars — ~25 emoji already exceed 72 bytes.
_BCRYPT_MAX_BYTES = 72


def validate_new_password(plaintext: str) -> None:
    """Enforce password complexity. Raise :class:`InvalidPasswordError` on rejection.

    Rules (single-user, public-internet but rate-limited):
      - 8+ characters, at most 72 bytes (bcrypt's hard limit)
      - contains at least one letter AND one digit

    Special characters are not required — the rate-limit + bcrypt + 30-day
    cookie life is the actual defense. Forcing ``!@#`` produces sticky-note
    passwords without buying meaningful entropy.
    """
    if len(plaintext) < 8:
        raise InvalidPasswordError("password must be at least 8 characters")
    if len(plaintext.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise InvalidPasswordError(
            f"password must be at most {_BCRYPT_MAX_BYTES} bytes "
            "(note: non-ASCII characters count as multiple bytes)"
        )
    if not any(c.isalpha() for c in plaintext):
        raise InvalidPasswordError("password must contain at least one letter")
    if not any(c.isdigit() for c in plaintext):
        raise InvalidPasswordError("password must contain at least one digit")


# Fixed bcrypt hash of a random throwaway string, computed once at import. Used
# to spend the SAME ~bcrypt cost on the unknown-username path as on the
# wrong-password path, so login latency doesn't leak whether a username exists
# (timing oracle). The plaintext is random and never matches any real password.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class _LoginRateLimiter:
    """In-memory per-IP failure counter with lockout windows.

    Stored as ``{ip: [timestamps...]}`` of recent failures (s). Each ``check``
    call drops failures older than ``window_s``; if length ≥ threshold the IP
    is locked out until the oldest failure + ``lockout_s``.

    For a single-user system per-IP is fine; if you ever multi-tenant this,
    swap the key to ``(ip, username)`` to stop one tenant from locking
    another out via shared NAT.
    """

    def __init__(self, *, window_s: int, threshold: int, lockout_s: int) -> None:
        self._window_s = window_s
        self._threshold = threshold
        self._lockout_s = lockout_s
        self._failures: dict[str, list[float]] = {}

    def check(self, ip: str) -> None:
        """Raise :class:`LoginLockedOutError` if ``ip`` is currently locked out."""
        now = _now_s()
        fails = [t for t in self._failures.get(ip, []) if now - t < self._window_s]
        self._failures[ip] = fails
        if len(fails) >= self._threshold:
            # Lockout window starts at the oldest in-window failure. Once the
            # oldest ages out of the window, len(fails) drops below threshold
            # and the next attempt is allowed again.
            retry_after = int(fails[0] + self._lockout_s - now)
            if retry_after > 0:
                raise LoginLockedOutError(retry_after)

    def record_failure(self, ip: str) -> None:
        self._failures.setdefault(ip, []).append(_now_s())

    def record_success(self, ip: str) -> None:
        self._failures.pop(ip, None)


class UserStore:
    """Façade for cookie-side auth: bcrypt + sessions + rate limit on one knob."""

    def __init__(self, db: Database, cfg: AuthConfig) -> None:
        self._db = db
        self._cfg = cfg
        self._rate = _LoginRateLimiter(
            window_s=cfg.login_rate_window_s,
            threshold=cfg.login_rate_threshold,
            lockout_s=cfg.login_lockout_s,
        )

    # -------------------------------------------------------------- #
    # First-start seeding                                              #
    # -------------------------------------------------------------- #

    async def ensure_admin_seeded(self) -> bool:
        """Insert ``cfg.admin_username`` / hash(initial) on empty tables.

        Idempotent: returns ``True`` if a row was inserted, ``False`` if any
        user already exists (single-user system — never overwrites).
        Caller should log the returned bool so the first-start banner can
        remind the user the initial password is the default.
        """
        if await self._db.count_users() > 0:
            return False
        await self._db.insert_user(
            self._cfg.admin_username,
            hash_password(self._cfg.admin_initial_password),
            must_change=True,
        )
        # Log the seeded password ONLY when it's a randomly-minted one the
        # operator otherwise has no way to learn (public deploy, no env). Never
        # log the literal "admin" default (pointless) and never log an
        # operator-supplied password (they already know it). journal/syslog
        # persists, so we gate the one case where logging is both necessary and
        # acceptable.
        if self._cfg.admin_password_is_default:
            logger.warning(
                "auth.admin_seeded",
                username=self._cfg.admin_username,
                password="admin (default — change on first login)",
            )
        else:
            logger.warning(
                "auth.admin_seeded",
                username=self._cfg.admin_username,
                initial_password=self._cfg.admin_initial_password,
                note="randomly minted — copy now, change on first login",
            )
        return True

    # -------------------------------------------------------------- #
    # Login / session                                                 #
    # -------------------------------------------------------------- #

    async def authenticate(
        self,
        username: str,
        password: str,
        *,
        ip: str,
        user_agent: str | None,
    ) -> CookiePrincipal:
        """Verify creds + mint a session id. Raises on failure.

        Failure cases:
        - :class:`LoginLockedOutError` — rate-limited
        - :class:`PermissionError` — wrong username or password (caller maps to 401)
        """
        self._rate.check(ip)
        user = await self._db.get_user(username)
        # Always run a bcrypt verification — against the real hash if the user
        # exists, else against a fixed dummy hash — so the unknown-username path
        # costs the same ~bcrypt time as the wrong-password path. Without this,
        # `user is None` short-circuits before bcrypt and a fast 401 leaks
        # "username doesn't exist" (timing oracle), defeating the deliberately
        # identical "invalid username or password" message.
        password_ok = verify_password(
            password, user["password_hash"] if user is not None else _DUMMY_HASH
        )
        if user is None or not password_ok:
            self._rate.record_failure(ip)
            raise PermissionError("invalid credentials")
        self._rate.record_success(ip)

        session_id = self._mint_session_id()
        expires_at = _now_ms() + self._cfg.session_ttl_s * 1000
        await self._db.insert_session(
            session_id,
            username,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
        # Cap concurrent sessions: evict the oldest beyond the limit so a 30-day
        # session set can't grow unbounded and a stale captured session ages out.
        await self._db.prune_sessions_over_cap(username, self._cfg.max_sessions_per_user)
        return CookiePrincipal(
            username=username,
            session_id=session_id,
            must_change_password=bool(user["password_must_change"]),
        )

    async def resolve_session(self, session_id: str) -> CookiePrincipal | None:
        """Map a cookie id back to a session subject; ``None`` if invalid/expired.

        Touches ``last_seen_at`` as a side effect (cheap; audit/telemetry only,
        not a security gate).
        """
        row = await self._db.get_session(session_id)
        if row is None:
            return None
        if row["expires_at"] < _now_ms():
            # Lazy purge; the reclaim loop also sweeps in bulk.
            await self._db.delete_session(session_id)
            return None
        user = await self._db.get_user(row["username"])
        if user is None:
            # Orphaned session (user row vanished). Drop the session for hygiene.
            await self._db.delete_session(session_id)
            return None
        await self._db.touch_session(session_id)
        return CookiePrincipal(
            username=row["username"],
            session_id=session_id,
            must_change_password=bool(user["password_must_change"]),
        )

    async def revoke_session(self, session_id: str) -> None:
        """Delete this session row; logout / session-expired hook."""
        await self._db.delete_session(session_id)

    # -------------------------------------------------------------- #
    # Password change                                                  #
    # -------------------------------------------------------------- #

    async def change_password(
        self,
        username: str,
        old_password: str,
        new_password: str,
        *,
        current_session_id: str,
    ) -> None:
        """Verify old, validate new, persist hash, revoke all other sessions.

        Raises:
          PermissionError — old password wrong (route maps to 401)
          InvalidPasswordError — new password fails complexity (route maps to 422)
          ValueError — new == old (route maps to 422)
        """
        user = await self._db.get_user(username)
        if user is None or not verify_password(old_password, user["password_hash"]):
            raise PermissionError("invalid old password")
        if old_password == new_password:
            raise ValueError("new password must differ from old password")
        validate_new_password(new_password)

        await self._db.update_user_password(
            username,
            hash_password(new_password),
            must_change=False,
        )
        # Kick every other device — initial admin/admin may have been observed
        # by a passer-by; changing password should boot any session that wasn't
        # the one performing the change.
        purged = await self._db.delete_sessions_except(username, current_session_id)
        logger.info(
            "auth.password_changed",
            username=username,
            other_sessions_revoked=purged,
        )

    # -------------------------------------------------------------- #
    # Helpers                                                          #
    # -------------------------------------------------------------- #

    @staticmethod
    def _mint_session_id() -> str:
        """32-byte URL-safe random; ~43 ascii chars, ~256 bits entropy."""
        return secrets.token_urlsafe(32)
