"""Seed only the disposable Compose home, then exec the production entry point."""

import asyncio
import os
from pathlib import Path

from timetrace.common.config import StorageConfig
from timetrace.server.auth import ServerAuth, TokenEntry
from timetrace.server.db import Database
from timetrace.server.db.sqlite import UNKNOWN_DEVICE

HISTORY_START_MS = 978307200000  # outside the automatic rollup lookback
HISTORY_END_MS = HISTORY_START_MS + 3_600_000


async def seed_historical_summary():
    db = Database(StorageConfig())
    await db.init()
    try:
        for grain, end in (("5min", HISTORY_START_MS + 300_000), ("1h", HISTORY_END_MS)):
            sid = await db.upsert_device_summary(
                device_scope=UNKNOWN_DEVICE,
                grain=grain,
                scope_key="e2e-historical-window",
                window_start=HISTORY_START_MS,
                window_end=end,
                day_local="2001-01-01",
                metrics_json='{"record_count": 1}',
                record_count=1,
            )
            await db.save_device_summary_narrative(
                sid, description="E2E historical fixture", evaluation="", body_json="{}"
            )
    finally:
        await db.close()


if __name__ == "__main__":
    assert Path.home() == Path("/home/timetrace")
    if not ServerAuth.read_tokens():
        ServerAuth.write_tokens([TokenEntry(value="e2e-only-machine-token", label="e2e")])
    asyncio.run(seed_historical_summary())
    os.execvp("timetrace-server", ["timetrace-server"])
