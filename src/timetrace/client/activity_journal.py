"""Bounded, local client activity feed with cursor pagination."""

import asyncio
import sqlite3
import time
from collections import deque
from contextlib import closing
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class ActivityJournal:
    def __init__(self, path: Path):
        self.path = path
        self._pending = deque(maxlen=1024)

    def emit(self, kind: str, detail: str = ""):
        self._pending.append((time.time(), kind, detail[:160]))

    def _write(self, batch):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=1)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                "kind TEXT NOT NULL, detail TEXT NOT NULL)"
            )
            db.executemany("INSERT INTO events(ts,kind,detail) VALUES (?,?,?)", batch)
            db.execute("DELETE FROM events WHERE id <= (SELECT max(id)-5000 FROM events)")

    async def _flush(self):
        if not self._pending:
            return
        batch = list(self._pending)
        self._pending.clear()
        try:
            await asyncio.to_thread(self._write, batch)
        except (OSError, sqlite3.Error):
            self._pending.extendleft(reversed(batch))
            logger.warning("client.activity_journal_write_failed", exc_info=True)

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            await self._flush()
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.6)
            except TimeoutError:
                pass
        await self._flush()

    async def page(self, *, before: int | None, after: int | None, limit: int):
        return await asyncio.to_thread(self._page, before, after, limit)

    def _page(self, before, after, limit):
        if not self.path.is_file():
            return {"items": [], "more": False}
        clause, args = "", ()
        if before is not None:
            clause, args = "WHERE id < ?", (before,)
        if after is not None:
            clause, args = "WHERE id > ?", (after,)
        order = "ASC" if after is not None else "DESC"
        try:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    f"SELECT id, ts, kind, detail FROM events {clause} ORDER BY id {order} LIMIT ?",
                    (*args, limit + 1),
                ).fetchall()
        except sqlite3.Error:
            return {"items": [], "more": False}
        return {"items": [dict(row) for row in rows[:limit]], "more": len(rows) > limit}
