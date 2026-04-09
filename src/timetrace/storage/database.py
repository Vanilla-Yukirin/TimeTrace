"""SQLite database initialisation and low-level data access."""

from __future__ import annotations

import time
import uuid
from typing import Any

import aiosqlite
import structlog

from timetrace.storage.models import CaptureContext

logger = structlog.get_logger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS records (
    id          TEXT PRIMARY KEY,
    ts_start    INTEGER NOT NULL,
    ts_end      INTEGER,
    event_type  TEXT NOT NULL DEFAULT 'heartbeat',
    app_name    TEXT NOT NULL DEFAULT '',
    process_name TEXT NOT NULL DEFAULT '',
    window_title TEXT NOT NULL DEFAULT '',
    url         TEXT,
    capture_reason TEXT,
    status      TEXT NOT NULL DEFAULT 'captured',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_ts_start  ON records(ts_start);
CREATE INDEX IF NOT EXISTS idx_records_app_ts    ON records(app_name, ts_start);
CREATE INDEX IF NOT EXISTS idx_records_status    ON records(status)
    WHERE status LIKE 'pending_%';

CREATE TABLE IF NOT EXISTS screenshots (
    id          TEXT PRIMARY KEY,
    record_id   TEXT NOT NULL REFERENCES records(id),
    path        TEXT NOT NULL,
    thumb_path  TEXT,
    width       INTEGER,
    height      INTEGER,
    hash_sha256 TEXT,
    deleted_at  INTEGER,
    privacy_level TEXT NOT NULL DEFAULT 'normal',
    created_at  INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_screenshots_record  ON screenshots(record_id);

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


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id() -> str:
    return str(uuid.uuid4())


class Database:
    """Thin async wrapper around aiosqlite for TimeTrace data access."""

    def __init__(self, storage_cfg: Any) -> None:
        self._cfg = storage_cfg
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._cfg.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        logger.info("database.init", path=str(self._cfg.db_path))

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Database not initialised"
        return self._conn

    async def insert_record(self, ctx: CaptureContext, reason: str) -> str:
        record_id = _new_id()
        now = _now_ms()
        await self.conn.execute(
            """INSERT INTO records
               (id, ts_start, event_type, app_name, process_name,
                window_title, url, capture_reason, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record_id,
                now,
                "heartbeat",
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
        return record_id

    async def mark_pending(self, record_id: str) -> None:
        now = _now_ms()
        await self.conn.execute(
            """INSERT OR REPLACE INTO analysis_results
               (record_id, status, updated_at) VALUES (?, 'pending_vlm', ?)""",
            (record_id, now),
        )
        await self.conn.execute(
            "UPDATE records SET status='pending_vlm', updated_at=? WHERE id=?",
            (now, record_id),
        )
        await self.conn.commit()

    async def claim_next_task(self, kind: str) -> dict | None:
        now = _now_ms()
        async with self.conn.execute(
            """SELECT record_id FROM analysis_results
               WHERE status=? AND (next_retry_at IS NULL OR next_retry_at<=?)
               ORDER BY updated_at ASC LIMIT 1""",
            (kind, now),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None

        record_id = row["record_id"]
        await self.conn.execute(
            "UPDATE analysis_results SET status=?, locked_at=?, updated_at=? WHERE record_id=?",
            (f"processing_{kind}", now, now, record_id),
        )
        await self.conn.commit()
        return {"record_id": record_id}

    async def save_description(self, record_id: str, desc: str) -> None:
        now = _now_ms()
        await self.conn.execute(
            """UPDATE analysis_results
               SET vlm_desc=?, updated_at=? WHERE record_id=?""",
            (desc, now, record_id),
        )
        await self.conn.commit()

    async def transition(self, record_id: str, new_status: str) -> None:
        now = _now_ms()
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
        now = _now_ms()
        await self.conn.execute(
            """UPDATE analysis_results
               SET status='error_final', error_msg=?, updated_at=? WHERE record_id=?""",
            (error_msg, now, record_id),
        )
        await self.conn.commit()

    async def query_records(
        self,
        start_ms: int,
        end_ms: int,
        limit: int = 200,
        cursor: str | None = None,
    ) -> list[dict]:
        params: list[Any] = [start_ms, end_ms]
        extra = ""
        if cursor is not None:
            extra = "AND id > ?"
            params.append(cursor)
        params.append(limit)
        async with self.conn.execute(
            f"""SELECT * FROM records
                WHERE ts_start BETWEEN ? AND ? {extra}
                ORDER BY ts_start ASC LIMIT ?""",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
