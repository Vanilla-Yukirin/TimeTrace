"""MCP tool definitions – exposes TimeTrace context to external AI agents.

Tools (minimum set for Phase 1.5):
  - list_categories
  - get_activity
  - search_activity
  - get_category_stats
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from timetrace.storage.database import Database


async def list_categories(db: Database) -> dict:
    """Return all known activity categories."""
    async with db.conn.execute("SELECT id, name, parent_id FROM categories") as cur:
        rows = await cur.fetchall()
    return {"categories": [dict(r) for r in rows]}


async def get_activity(
    db: Database,
    start: int,
    end: int,
    max_items: int = 100,
    summary: bool = False,
) -> dict:
    """Return structured activity context for a time window.

    Images are never included; only metadata and VLM descriptions are returned.
    """
    rows = await db.query_records(start, end, limit=max_items)
    items = [
        {
            "id": r["id"],
            "ts_start": r["ts_start"],
            "app_name": r["app_name"],
            "window_title": r["window_title"],
            "status": r["status"],
        }
        for r in rows
    ]
    return {"items": items, "start": start, "end": end}


async def get_category_stats(
    db: Database,
    start: int,
    end: int,
    categories: list[str] | None = None,
) -> dict:
    """Return time-spent aggregates per category (Phase 1.5+, stub)."""
    return {"stats": [], "note": "category stats not yet implemented"}


async def search_activity(
    db: Database,
    query_text: str,
    start: int | None = None,
    end: int | None = None,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,
) -> dict:
    """Text-similarity search over activity records (Phase 1.5+ stub)."""
    return {"items": [], "query": query_text, "note": "vector search not yet implemented"}
