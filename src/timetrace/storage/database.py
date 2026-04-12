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
        await self._seed_categories()
        logger.info("database.init", path=str(self._cfg.db_path))

    async def _seed_categories(self) -> None:
        """Insert built-in categories if they don't exist yet."""
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

    # ------------------------------------------------------------------ #
    # Records                                                              #
    # ------------------------------------------------------------------ #

    async def insert_record(
        self,
        ctx: CaptureContext,
        reason: str,
        event_type: str = "heartbeat",
    ) -> str:
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
        return record_id

    async def close_record(self, record_id: str) -> None:
        """Set ts_end on a record (e.g. when window switches away)."""
        now = _now_ms()
        await self.conn.execute(
            "UPDATE records SET ts_end=?, updated_at=? WHERE id=?",
            (now, now, record_id),
        )
        await self.conn.commit()

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

    async def query_records(
        self,
        start_ms: int,
        end_ms: int,
        limit: int = 200,
        cursor: str | None = None,
        app_name: str | None = None,
        apps: list[str] | None = None,
        keyword: str | None = None,
    ) -> list[dict]:
        conditions = ["ts_start BETWEEN ? AND ?"]
        params: list[Any] = [start_ms, end_ms]

        if cursor is not None:
            conditions.append("ts_start > (SELECT ts_start FROM records WHERE id=?)")
            params.append(cursor)

        if app_name:
            conditions.append("app_name = ?")
            params.append(app_name)

        if apps:
            placeholders = ",".join("?" * len(apps))
            conditions.append(f"app_name IN ({placeholders})")
            params.extend(apps)

        if keyword:
            conditions.append("window_title LIKE ?")
            params.append(f"%{keyword}%")

        where = " AND ".join(conditions)
        params.append(limit)

        async with self.conn.execute(
            f"""SELECT r.*, a.vlm_desc, a.category_final, a.confidence
                FROM records r
                LEFT JOIN analysis_results a ON a.record_id = r.id
                WHERE {where}
                ORDER BY r.ts_start ASC LIMIT ?""",
            params,
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
    ) -> str:
        screenshot_id = _new_id()
        now = _now_ms()
        await self.conn.execute(
            """INSERT INTO screenshots
               (id, record_id, path, thumb_path, width, height,
                hash_sha256, privacy_level, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                screenshot_id,
                record_id,
                path,
                thumb_path,
                width,
                height,
                hash_sha256,
                privacy_level,
                now,
            ),
        )
        await self.conn.commit()
        return screenshot_id

    async def get_screenshots_for_record(self, record_id: str) -> list[dict]:
        async with self.conn.execute(
            "SELECT * FROM screenshots WHERE record_id=? AND deleted_at IS NULL",
            (record_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Analysis worker                                                      #
    # ------------------------------------------------------------------ #

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
        async with self.conn.execute(query) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Settings                                                             #
    # ------------------------------------------------------------------ #

    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self.conn.execute("SELECT value_json FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value_json"] if row else default

    async def set_setting(self, key: str, value_json: str) -> None:
        now = _now_ms()
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
        """
        now = _now_ms()
        cutoff = now - timeout_ms
        async with self.conn.execute(
            """SELECT record_id, status FROM analysis_results
               WHERE status LIKE 'processing_%' AND locked_at <= ?""",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()

        count = 0
        for row in rows:
            pending_status = row["status"].replace("processing_", "pending_", 1)
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

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
