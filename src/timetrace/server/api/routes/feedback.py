"""Feedback endpoint – user confirms or corrects a classification."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    record_id: str
    action: str  # "confirm" | "edit"
    category: str | None = None
    tags: list[str] | None = None
    user_note: str | None = None


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request) -> dict:
    """Persist user feedback for a record classification."""
    if req.action not in ("confirm", "edit"):
        raise HTTPException(status_code=422, detail="action must be 'confirm' or 'edit'")

    db = request.app.state.db

    # Snapshot the prior classification for the before/after audit pair.
    category_before = await db.get_category_final(req.record_id)

    feedback_id = await db.insert_feedback(
        record_id=req.record_id,
        action=req.action,
        category_before=category_before,
        category_after=req.category,
        tags_before=None,
        tags_after=json.dumps(req.tags) if req.tags else None,
        user_note=req.user_note,
    )

    return {"status": "accepted", "record_id": req.record_id, "feedback_id": feedback_id}


@router.get("/categories")
async def list_categories(request: Request) -> dict:
    """Return all visible activity categories."""
    db = request.app.state.db
    cats = await db.get_categories(include_hidden=False)
    return {"categories": cats}
