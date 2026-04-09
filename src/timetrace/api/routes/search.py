"""Search endpoint (text similarity, Phase 1.5 placeholder)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["search"])


class SearchRequest(BaseModel):
    query_text: str
    start: int | None = None
    end: int | None = None
    top_k: int = 10


@router.post("/search")
async def search_activity(req: SearchRequest) -> dict:
    """Placeholder for text-similarity search (Phase 1.5+)."""
    return {"items": [], "query": req.query_text, "note": "search not yet implemented"}
