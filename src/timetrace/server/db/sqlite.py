"""SQLite database initialisation and low-level data access.

aiosqlite uses a single shared connection for all coroutines. Concurrent access
from capture + multiple worker consumers + the API would interleave cursors
and `commit()` calls on that one connection, producing
``OperationalError: cannot commit transaction - SQL statements in progress``.

To prevent this we serialize every public method behind ``self._lock``. Reads
become sequential too, but at TimeTrace's volume (a few writes/sec, infrequent
queries) the throughput cost is negligible compared to the alternative of
multi-connection plumbing.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import aiosqlite
import structlog

from timetrace.common.models import CaptureContext

logger = structlog.get_logger(__name__)

_MAX_ORPHAN_BRIDGE_MS = 5 * 60 * 1000

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS records (
    id               TEXT PRIMARY KEY,
    client_record_id TEXT,
    ts_start         INTEGER NOT NULL,
    ts_end           INTEGER,
    event_type       TEXT NOT NULL DEFAULT 'heartbeat',
    app_name         TEXT NOT NULL DEFAULT '',
    process_name     TEXT NOT NULL DEFAULT '',
    window_title     TEXT NOT NULL DEFAULT '',
    url              TEXT,
    capture_reason   TEXT,
    status           TEXT NOT NULL DEFAULT 'captured',
    created_at       INTEGER NOT NULL,
    updated_at       INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_ts_start  ON records(ts_start);
CREATE INDEX IF NOT EXISTS idx_records_app_ts    ON records(app_name, ts_start);
CREATE INDEX IF NOT EXISTS idx_records_status    ON records(status)
    WHERE status LIKE 'pending_%';
-- Partial unique index: enforces idempotency when client_record_id is supplied
-- but lets pre-P3a rows (NULL) coexist without conflicting with each other.
CREATE UNIQUE INDEX IF NOT EXISTS idx_records_client_record_id
    ON records(client_record_id) WHERE client_record_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS screenshots (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id),
    path        TEXT NOT NULL,
    thumb_path  TEXT,
    width       INTEGER,
    height      INTEGER,
    hash_sha256 TEXT,
    phash       BLOB,
    deleted_at  INTEGER,
    privacy_level TEXT NOT NULL DEFAULT 'normal',
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_screenshots_record  ON screenshots(record_id);
-- Partial UNIQUE: at most one row per (record_id, hash_sha256). Lets the ingest
-- route absorb outbox at-least-once replays without inserting a duplicate
-- screenshot row + double-feeding the pHash index. Pre-P3a rows with NULL
-- hash_sha256 are excluded so the migration is non-breaking.
CREATE UNIQUE INDEX IF NOT EXISTS idx_screenshots_record_hash
    ON screenshots(record_id, hash_sha256) WHERE hash_sha256 IS NOT NULL;

CREATE TABLE IF NOT EXISTS analysis_results (
    record_id           TEXT PRIMARY KEY REFERENCES records(id),
    vlm_desc            TEXT,
    vlm_model           TEXT,
    vlm_latency_ms      INTEGER,
    category_suggested  TEXT,
    category_final      TEXT,
    confidence          REAL,
    decision_trace      TEXT,
    error_code          TEXT,
    error_msg           TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    next_retry_at       INTEGER,
    status              TEXT NOT NULL DEFAULT 'pending_vlm',
    locked_at           INTEGER,
    updated_at          INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_analysis_status ON analysis_results(status);

CREATE TABLE IF NOT EXISTS categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    parent_id   TEXT REFERENCES categories(id),
    description TEXT,
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    is_hidden   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tags (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS record_tags (
    record_id   TEXT NOT NULL REFERENCES records(id),
    tag_id      TEXT NOT NULL REFERENCES tags(id),
    source      TEXT,
    confidence  REAL,
    PRIMARY KEY (record_id, tag_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id              TEXT PRIMARY KEY,
    record_id       TEXT NOT NULL REFERENCES records(id),
    action          TEXT NOT NULL,
    category_before TEXT,
    category_after  TEXT,
    tags_before     TEXT,
    tags_after      TEXT,
    user_note       TEXT,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value_json  TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);
"""

_BUILTIN_CATEGORIES = [
    ("work/coding", "工作/编程", None),
    ("work/meeting", "工作/会议", None),
    ("work/writing", "工作/写作", None),
    ("work/other", "工作/其他", None),
    ("study/reading", "学习/阅读", None),
    ("study/video", "学习/视频", None),
    ("study/other", "学习/其他", None),
    ("entertainment/video", "娱乐/视频", None),
    ("entertainment/game", "娱乐/游戏", None),
    ("entertainment/other", "娱乐/其他", None),
    ("social/chat", "社交/聊天", None),
    ("social/other", "社交/其他", None),
    ("system/idle", "系统/空闲", None),
    ("system/other", "系统/其他", None),
    ("uncategorized", "未分类", None),
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


def _processing_to_pending(status: str) -> str:
    """Map an in-flight worker status back to its pending queue.

    Accepts both the current ``processing_<base>`` form and the legacy
    ``processing_pending_<base>`` form so historic rows from earlier builds
    don't get permanently orphaned.
    """
    if status.startswith("processing_pending_"):
        return "pending_" + status[len("processing_pending_") :]
    if status.startswith("processing_"):
        return "pending_" + status[len("processing_") :]
    return status


class SqliteDatabase:
    """SQLite-backed implementation of the Database surface.

    Today this is the only Database implementation; PostgresDatabase lands at P5.
    Until then `timetrace.server.db.Database` is exported as an alias for this
    class so type annotations and construction can continue to share a name.
    """

    def __init__(self, storage_cfg: Any) -> None:
        self._cfg = storage_cfg
        self._conn: aiosqlite.Connection | None = None
        # Serializes access to the shared aiosqlite connection (single-thread
        # SQLite worker) so concurrent cursors + commits don't trample each other.
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        async with self._lock:
            self._cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._cfg.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(_SCHEMA)
            await self._conn.commit()
            await self._migrate()
            healed = await self._close_open_records_before(_now_ms(), tail_ts=None)
            if healed:
                logger.info("database.heal_open_records_on_start", count=healed)
            capped = await self._cap_implausible_record_durations(_now_ms())
            if capped:
                logger.info("database.cap_implausible_record_durations", count=capped)
            # _seed_categories' commit also flushes the heal UPDATE above.
            await self._seed_categories()
            logger.info("database.init", path=str(self._cfg.db_path))

    async def _migrate(self) -> None:
        """Idempotent schema migrations for databases created by older versions.

        Caller must hold ``self._lock``.
        """
        async with self._conn.execute("PRAGMA table_info(screenshots)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        if "phash" not in cols:
            await self._conn.execute("ALTER TABLE screenshots ADD COLUMN phash BLOB")
            await self._conn.commit()
            logger.info("database.migrate", added_column="screenshots.phash")

        async with self._conn.execute("PRAGMA table_info(records)") as cur:
            record_cols = {row["name"] for row in await cur.fetchall()}
        if "client_record_id" not in record_cols:
            await self._conn.execute("ALTER TABLE records ADD COLUMN client_record_id TEXT")
            await self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_client_record_id "
                "ON records(client_record_id) WHERE client_record_id IS NOT NULL"
            )
            await self._conn.commit()
            logger.info("database.migrate", added_column="records.client_record_id")

        # Backfill the (record_id, hash_sha256) UNIQUE for DBs created before
        # the dedup work in P3a-cleanup. Idempotent; the index uses IF NOT EXISTS.
        await self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_screenshots_record_hash "
            "ON screenshots(record_id, hash_sha256) WHERE hash_sha256 IS NOT NULL"
        )
        await self._conn.commit()

    async def _seed_categories(self) -> None:
        """Insert built-in categories if they don't exist yet.

        Caller must hold ``self._lock``.
        """
        for cat_id, name, parent_id in _BUILTIN_CATEGORIES:
            await self._conn.execute(
                """INSERT OR IGNORE INTO categories (id, name, parent_id, is_builtin)
                   VALUES (?, ?, ?, 1)""",
                (cat_id, name, parent_id),
            )
        await self._conn.commit()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not initialised"
        return self._conn

    @property
    def lock(self) -> asyncio.Lock:
        """Public connection lock for callers that bypass Database methods.

        External code that touches ``db.conn`` directly (route helpers, the
        pHash loader, tests that prep state) must wrap their access in
        ``async with db.lock:`` so it cannot interleave with concurrent
        commits issued from other coroutines.
        """
        return self._lock

    # ------------------------------------------------------------------ #
    # Records                                                              #
    # ------------------------------------------------------------------ #

    async def insert_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
        *,
        client_record_id: str | None = None,
    ) -> str:
        record_id = _new_id()
        now = _now_ms()
        async with self._lock:
            healed = await self._close_open_records_before(now, tail_ts=now)
            if healed:
                logger.info("database.heal_open_records_on_insert", count=healed)
                # Commit the heal independently of the INSERT below so that a
                # client_record_id UNIQUE violation doesn't roll back the heal
                # work and leave a long-open transaction holding it pending.
                await self.conn.commit()
            try:
                await self.conn.execute(
                    """INSERT INTO records
                       (id, client_record_id, ts_start, event_type, app_name,
                        process_name, window_title, url, capture_reason,
                        status, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record_id,
                        client_record_id,
                        now,
                        event_type,
                        ctx.app_name,
                        ctx.process_name,
                        ctx.window_title,
                        ctx.url,
                        reason,
                        "captured",
                        now,
                        now,
                    ),
                )
                await self.conn.commit()
            except aiosqlite.IntegrityError:
                # Drop the failed INSERT's implicit transaction so the next
                # caller starts clean. (Without this the lock acquired by the
                # next method would re-enter an already-open transaction.)
                await self.conn.rollback()
                raise
        return record_id

    async def find_record_by_client_id(self, client_record_id: str) -> dict | None:
        """Return the record carrying the given client-generated id, if any."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT * FROM records WHERE client_record_id = ?",
                (client_record_id,),
            ) as cur:
                row = await cur.fetchone()
        return dict(row) if row else None

    async def ingest_or_get_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str,
        client_record_id: str,
    ) -> tuple[str, bool]:
        """Insert a record with the given client_record_id, or return the
        existing one if a row already carries this id.

        Returns ``(record_id, was_new)``. Uses the ``client_record_id``
        UNIQUE INDEX as the atomic check — a parallel ingest losing the race
        catches the IntegrityError and re-reads the winner.
        """
        try:
            rid = await self.insert_record(
                ctx,
                reason=reason,
                event_type=event_type,
                client_record_id=client_record_id,
            )
            return rid, True
        except aiosqlite.IntegrityError:
            existing = await self.find_record_by_client_id(client_record_id)
            if existing is None:
                # Should be impossible: UNIQUE violation but row missing on re-read.
                # Re-raise so the caller doesn't silently corrupt state.
                raise
            return existing["id"], False

    async def close_record(self, record_id: str, ts_end: int | None = None) -> bool:
        """Set ts_end on a record (e.g. when window switches away).

        When `ts_end` is None the server clock is used; HttpBackend passes the
        client's clock so the time the user actually stopped using the window
        is honoured rather than the time the server happened to receive the
        close call.

        Returns True iff a row was updated. Routes that need to surface a 404
        for an unknown id check this — silently returning False used to let
        a stale HttpBackend close calls go nowhere with no signal.
        """
        now = _now_ms()
        ts_end_value = ts_end if ts_end is not None else now
        async with self._lock:
            cur = await self.conn.execute(
                "UPDATE records SET ts_end=?, updated_at=? WHERE id=?",
                (ts_end_value, now, record_id),
            )
            rowcount = cur.rowcount
            await cur.close()
            await self.conn.commit()
        return rowcount > 0

    async def _close_open_records_before(
        self,
        ts_cutoff: int,
        *,
        tail_ts: int | None,
    ) -> int:
        """Close any orphaned `records.ts_end IS NULL` rows older than `ts_cutoff`.

        For each open row, ts_end is set to the next plausible boundary:
          1. MIN(next.ts_start), if it is within `_MAX_ORPHAN_BRIDGE_MS`.
          2. Otherwise the `tail_ts` fallback, if it is within that same
             threshold.
          3. Otherwise the row's own ts_start, giving zero-duration. Old
             crash-orphans then render as points rather than day-spanning bars.

        Deliberately does NOT use ``records.updated_at`` as a fallback: the
        worker bumps it on transition / save_description, so an old orphan
        whose VLM completes long after the original capture would get an
        incorrectly-late ts_end — exactly the cross-day-bar bug we're fixing.

        Caller must hold ``self._lock`` AND is responsible for committing.
        Returns the number of rows updated.
        """
        now = _now_ms()
        if tail_ts is None:
            sql = """UPDATE records
                     SET ts_end = COALESCE(
                             (SELECT MIN(n.ts_start) FROM records n
                              WHERE n.ts_start > records.ts_start
                                AND n.ts_start - records.ts_start <= ?),
                             records.ts_start
                         ),
                         updated_at = ?
                     WHERE ts_end IS NULL AND ts_start < ?"""
            params: tuple = (_MAX_ORPHAN_BRIDGE_MS, now, ts_cutoff)
        else:
            sql = """UPDATE records
                     SET ts_end = COALESCE(
                             (SELECT MIN(n.ts_start) FROM records n
                              WHERE n.ts_start > records.ts_start
                                AND n.ts_start - records.ts_start <= ?),
                             CASE
                                 WHEN ? - records.ts_start <= ? THEN ?
                                 ELSE records.ts_start
                             END
                         ),
                         updated_at = ?
                     WHERE ts_end IS NULL AND ts_start < ?"""
            params = (
                _MAX_ORPHAN_BRIDGE_MS,
                tail_ts,
                _MAX_ORPHAN_BRIDGE_MS,
                tail_ts,
                now,
                ts_cutoff,
            )

        cur = await self.conn.execute(sql, params)
        rowcount = cur.rowcount
        await cur.close()
        return rowcount

    async def _cap_implausible_record_durations(self, now: int) -> int:
        """Collapse impossible record spans left by older orphan-heal logic.

        Capture normally emits at least one heartbeat every 30 seconds while
        active, and idle transitions close the current record after 180 seconds.
        A single record spanning more than `_MAX_ORPHAN_BRIDGE_MS` is therefore
        a stale boundary artifact, not a trustworthy activity duration.

        Caller must hold ``self._lock`` AND is responsible for committing.
        Returns the number of rows updated.
        """
        cur = await self.conn.execute(
            """UPDATE records
               SET ts_end = ts_start,
                   updated_at = ?
               WHERE ts_end IS NOT NULL
                 AND ts_end - ts_start > ?""",
            (now, _MAX_ORPHAN_BRIDGE_MS),
        )
        rowcount = cur.rowcount
        await cur.close()
        return rowcount

    async def mark_pending(self, record_id: str) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT OR IGNORE INTO analysis_results
                   (record_id, status, updated_at) VALUES (?, 'pending_vlm', ?)""",
                (record_id, now),
            )
            await self.conn.execute(
                "UPDATE records SET status='pending_vlm', updated_at=?"
                " WHERE id=? AND status='captured'",
                (now, record_id),
            )
            await self.conn.commit()

    async def query_records(
        self,
        start_ms: int,
        end_ms: int,
        limit: int = 200,
        cursor: str | None = None,
        app_name: str | None = None,
        apps: list[str] | None = None,
        categories: list[str] | None = None,
        keyword: str | None = None,
    ) -> list[dict]:
        conditions = ["r.ts_start BETWEEN ? AND ?"]
        params: list[Any] = [start_ms, end_ms]

        if cursor is not None:
            conditions.append("r.ts_start > (SELECT ts_start FROM records WHERE id=?)")
            params.append(cursor)

        if app_name:
            conditions.append("r.app_name = ?")
            params.append(app_name)

        if apps:
            placeholders = ",".join("?" * len(apps))
            conditions.append(f"r.app_name IN ({placeholders})")
            params.extend(apps)

        if categories:
            placeholders = ",".join("?" * len(categories))
            conditions.append(f"a.category_final IN ({placeholders})")
            params.extend(categories)

        if keyword:
            # Match across window title and VLM description (vlm_desc is NULL until Phase 1.5).
            # Escape LIKE metachars so literal %/_ in user input don't over-match.
            conditions.append(
                "(r.window_title LIKE ? ESCAPE '\\' OR a.vlm_desc LIKE ? ESCAPE '\\')"
            )
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            params.append(like)
            params.append(like)

        where = " AND ".join(conditions)
        params.append(limit)

        async with self._lock:
            async with self.conn.execute(
                f"""SELECT r.*, a.vlm_desc, a.category_final, a.confidence,
                           s.thumb_path,
                           (SELECT COUNT(*) FROM screenshots
                            WHERE record_id = r.id AND deleted_at IS NULL
                           ) AS screenshot_count
                    FROM records r
                    LEFT JOIN analysis_results a ON a.record_id = r.id
                    LEFT JOIN (
                        SELECT record_id, MIN(thumb_path) AS thumb_path
                        FROM screenshots
                        WHERE deleted_at IS NULL
                        GROUP BY record_id
                    ) s ON s.record_id = r.id
                    WHERE {where}
                    ORDER BY r.ts_start ASC LIMIT ?""",
                params,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def list_apps(self) -> list[dict]:
        """Return distinct app names with record counts, most-used first."""
        async with self._lock:
            async with self.conn.execute(
                """SELECT app_name AS name, COUNT(*) AS count
                   FROM records
                   WHERE app_name != ''
                   GROUP BY app_name
                   ORDER BY count DESC, name ASC"""
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_screenshots_with_records(self, screenshot_ids: list[str]) -> list[dict]:
        """Fetch rich metadata for the given screenshot ids (used by image search)."""
        if not screenshot_ids:
            return []
        placeholders = ",".join("?" * len(screenshot_ids))
        async with self._lock:
            async with self.conn.execute(
                f"""SELECT s.id AS screenshot_id, s.record_id, s.thumb_path,
                           r.ts_start, r.ts_end, r.app_name, r.window_title, r.url,
                           a.vlm_desc, a.category_final, a.confidence
                    FROM screenshots s
                    JOIN records r ON r.id = s.record_id
                    LEFT JOIN analysis_results a ON a.record_id = s.record_id
                    WHERE s.id IN ({placeholders}) AND s.deleted_at IS NULL""",
                screenshot_ids,
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Screenshots                                                          #
    # ------------------------------------------------------------------ #

    async def insert_screenshot(
        self,
        record_id: str,
        path: str,
        thumb_path: str | None,
        width: int,
        height: int,
        hash_sha256: str,
        privacy_level: str = "normal",
        phash: bytes | None = None,
    ) -> tuple[str, bool]:
        """Insert a screenshot row; returns ``(screenshot_id, was_new)``.

        Idempotent on ``(record_id, hash_sha256)``: if the same screenshot was
        already inserted for this record (outbox at-least-once replay) we
        return the existing id with ``was_new=False`` so the caller can skip
        side-effects like phash_index.insert.
        """
        screenshot_id = _new_id()
        now = _now_ms()
        async with self._lock:
            try:
                await self.conn.execute(
                    """INSERT INTO screenshots
                       (id, record_id, path, thumb_path, width, height,
                        hash_sha256, phash, privacy_level, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        screenshot_id,
                        record_id,
                        path,
                        thumb_path,
                        width,
                        height,
                        hash_sha256,
                        phash,
                        privacy_level,
                        now,
                    ),
                )
                await self.conn.commit()
                return screenshot_id, True
            except aiosqlite.IntegrityError:
                # Duplicate (record_id, hash_sha256) — read back the id we already have.
                await self.conn.rollback()
                async with self.conn.execute(
                    "SELECT id FROM screenshots WHERE record_id=? AND hash_sha256=?",
                    (record_id, hash_sha256),
                ) as cur:
                    row = await cur.fetchone()
                if row is None:
                    # Should be impossible: UNIQUE violation but row missing on re-read.
                    raise
                return row["id"], False

    async def get_record_ts_start(self, record_id: str) -> int | None:
        """Return `records.ts_start` for the given record, or None if missing."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT ts_start FROM records WHERE id=?", (record_id,)
            ) as cur:
                row = await cur.fetchone()
        return row["ts_start"] if row else None

    async def get_screenshots_for_record(self, record_id: str) -> list[dict]:
        async with self._lock:
            return await self._get_screenshots_for_record_unlocked(record_id)

    async def _get_screenshots_for_record_unlocked(self, record_id: str) -> list[dict]:
        # Explicit column list — `phash` BLOB is intentionally excluded so the
        # row dict stays JSON-serializable when surfaced via the API.
        async with self.conn.execute(
            """SELECT id, record_id, path, thumb_path, width, height,
                      hash_sha256, deleted_at, privacy_level, created_at
               FROM screenshots WHERE record_id=? AND deleted_at IS NULL""",
            (record_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Analysis worker                                                      #
    # ------------------------------------------------------------------ #

    async def claim_next_task(self, kind: str) -> dict | None:
        """Atomically claim the next task in `kind` state and flip it to processing.

        ``kind`` is the queue name (e.g. ``pending_vlm``); the in-flight status
        is ``processing_<base>`` where ``<base>`` strips the ``pending_`` prefix.
        That way ``reclaim_stale_tasks`` can map ``processing_vlm`` back to
        ``pending_vlm`` cleanly with a simple prefix swap.

        Single UPDATE...WHERE...IN(SELECT)...RETURNING avoids the race that the
        old SELECT-then-UPDATE form had under multiple concurrent workers.
        """
        now = _now_ms()
        base = kind.removeprefix("pending_")
        processing = f"processing_{base}"
        async with self._lock:
            async with self.conn.execute(
                """UPDATE analysis_results
                   SET status = ?, locked_at = ?, updated_at = ?
                   WHERE record_id = (
                       SELECT record_id FROM analysis_results
                       WHERE status = ? AND (next_retry_at IS NULL OR next_retry_at <= ?)
                       ORDER BY updated_at ASC
                       LIMIT 1
                   ) AND status = ?
                   RETURNING record_id, retry_count""",
                (processing, now, now, kind, now, kind),
            ) as cur:
                row = await cur.fetchone()
            await self.conn.commit()
        if row is None:
            return None
        return {"record_id": row["record_id"], "retry_count": row["retry_count"]}

    async def get_record_meta(self, record_id: str) -> dict | None:
        """Return record metadata + first non-deleted screenshot path for VLM.

        Returns:
            {"record_id", "window_title", "screenshot_path"} or None if missing.
        """
        async with self._lock:
            async with self.conn.execute(
                """SELECT r.window_title AS window_title,
                          (SELECT path FROM screenshots
                           WHERE record_id = r.id AND deleted_at IS NULL
                           ORDER BY created_at ASC LIMIT 1) AS screenshot_path
                   FROM records r
                   WHERE r.id = ?""",
                (record_id,),
            ) as cur:
                row = await cur.fetchone()
        if row is None:
            return None
        return {
            "record_id": record_id,
            "window_title": row["window_title"],
            "screenshot_path": row["screenshot_path"],
        }

    async def mark_error_retryable(
        self,
        record_id: str,
        error_msg: str,
        retry_count: int,
        next_retry_at: int,
    ) -> None:
        """Bounce a task back to pending with backoff so it's re-claimed later."""
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """UPDATE analysis_results
                   SET status='pending_vlm',
                       error_msg=?,
                       retry_count=?,
                       next_retry_at=?,
                       locked_at=NULL,
                       updated_at=?
                   WHERE record_id=?""",
                (error_msg, retry_count, next_retry_at, now, record_id),
            )
            await self.conn.execute(
                "UPDATE records SET status='pending_vlm', updated_at=? WHERE id=?",
                (now, record_id),
            )
            await self.conn.commit()

    async def save_description(self, record_id: str, desc: str) -> None:
        now = _now_ms()
        async with self._lock:
            async with self.conn.execute(
                "UPDATE analysis_results SET vlm_desc=?, updated_at=? WHERE record_id=?",
                (desc, now, record_id),
            ) as cur:
                rows_updated = cur.rowcount
            await self.conn.commit()
        if rows_updated == 0:
            logger.warning("database.save_description.not_found", record_id=record_id)

    async def transition(self, record_id: str, new_status: str) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                "UPDATE analysis_results SET status=?, updated_at=? WHERE record_id=?",
                (new_status, now, record_id),
            )
            await self.conn.execute(
                "UPDATE records SET status=?, updated_at=? WHERE id=?",
                (new_status, now, record_id),
            )
            await self.conn.commit()

    async def mark_error_final(self, record_id: str, error_msg: str) -> None:
        """Park a task in `error_final` and mirror the status onto the record.

        Mirroring keeps `records.status` (driving the timeline / API view) in
        sync with `analysis_results.status` (driving the worker state machine);
        otherwise a row that exhausts retries can show `pending_vlm` or
        `processing_vlm` in the UI forever.
        """
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """UPDATE analysis_results
                   SET status='error_final', error_msg=?, locked_at=NULL, updated_at=?
                   WHERE record_id=?""",
                (error_msg, now, record_id),
            )
            await self.conn.execute(
                "UPDATE records SET status='error_final', updated_at=? WHERE id=?",
                (now, record_id),
            )
            await self.conn.commit()

    # ------------------------------------------------------------------ #
    # Feedback                                                             #
    # ------------------------------------------------------------------ #

    async def insert_feedback(
        self,
        record_id: str,
        action: str,
        category_before: str | None,
        category_after: str | None,
        tags_before: str | None = None,
        tags_after: str | None = None,
        user_note: str | None = None,
    ) -> str:
        feedback_id = _new_id()
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT INTO feedback
                   (id, record_id, action, category_before, category_after,
                    tags_before, tags_after, user_note, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    feedback_id,
                    record_id,
                    action,
                    category_before,
                    category_after,
                    tags_before,
                    tags_after,
                    user_note,
                    now,
                ),
            )
            # Update category_final in analysis_results when user edits
            if action == "edit" and category_after:
                await self.conn.execute(
                    """UPDATE analysis_results SET category_final=?, updated_at=?
                       WHERE record_id=?""",
                    (category_after, now, record_id),
                )
            await self.conn.commit()
        return feedback_id

    # ------------------------------------------------------------------ #
    # Categories                                                           #
    # ------------------------------------------------------------------ #

    async def get_categories(self, include_hidden: bool = False) -> list[dict]:
        query = "SELECT * FROM categories"
        if not include_hidden:
            query += " WHERE is_hidden=0"
        query += " ORDER BY name"
        async with self._lock:
            async with self.conn.execute(query) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Settings                                                             #
    # ------------------------------------------------------------------ #

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self._lock:
            async with self.conn.execute(
                "SELECT value_json FROM settings WHERE key=?", (key,)
            ) as cur:
                row = await cur.fetchone()
        return row["value_json"] if row else default

    async def set_setting(self, key: str, value_json: str) -> None:
        now = _now_ms()
        async with self._lock:
            await self.conn.execute(
                """INSERT OR REPLACE INTO settings (key, value_json, updated_at)
                   VALUES (?, ?, ?)""",
                (key, value_json, now),
            )
            await self.conn.commit()

    # ------------------------------------------------------------------ #
    # Housekeeping                                                         #
    # ------------------------------------------------------------------ #

    async def reclaim_stale_tasks(self, timeout_ms: int = 300_000) -> int:
        """Reset tasks stuck in processing_* back to pending_*.

        Called periodically to recover from worker crashes.
        Returns the number of tasks reclaimed.

        Handles both the current ``processing_<base>`` shape and the legacy
        ``processing_pending_<base>`` shape that older builds wrote — both map
        back to ``pending_<base>`` so an orphaned row is never lost.
        """
        now = _now_ms()
        cutoff = now - timeout_ms
        async with self._lock:
            async with self.conn.execute(
                """SELECT record_id, status FROM analysis_results
                   WHERE status LIKE 'processing_%' AND locked_at <= ?""",
                (cutoff,),
            ) as cur:
                rows = await cur.fetchall()

            count = 0
            for row in rows:
                pending_status = _processing_to_pending(row["status"])
                await self.conn.execute(
                    "UPDATE analysis_results"
                    " SET status=?, locked_at=NULL, updated_at=? WHERE record_id=?",
                    (pending_status, now, row["record_id"]),
                )
                count += 1

            if count:
                await self.conn.commit()
                logger.info("database.reclaim_stale_tasks", count=count)
        return count

    async def get_category_final(self, record_id: str) -> str | None:
        """Return analysis_results.category_final for a record, or None."""
        async with self._lock:
            async with self.conn.execute(
                "SELECT category_final FROM analysis_results WHERE record_id=?",
                (record_id,),
            ) as cur:
                row = await cur.fetchone()
        return row["category_final"] if row else None

    async def get_record_by_id(self, record_id: str) -> dict | None:
        """Return a single record with analysis results and screenshots list."""
        async with self._lock:
            async with self.conn.execute(
                """SELECT r.*, a.vlm_desc, a.category_final, a.confidence,
                          a.status AS analysis_status
                   FROM records r
                   LEFT JOIN analysis_results a ON a.record_id = r.id
                   WHERE r.id = ?""",
                (record_id,),
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["screenshots"] = await self._get_screenshots_for_record_unlocked(record_id)
        return result

    async def close(self) -> None:
        async with self._lock:
            if self._conn:
                await self._conn.close()
                self._conn = None
