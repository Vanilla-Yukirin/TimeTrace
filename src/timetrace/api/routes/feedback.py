"""Feedback endpoint – user confirms or corrects a classification."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(tags=["feedback"])


class FeedbackRequest(BaseModel):
    record_id: str
    action: str  # "confirm" | "edit"
    category: str | None = None
    tags: list[str] | None = None


@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, request: Request) -> dict:
    """Accept user feedback for a record classification."""
    # Phase 2: persist to feedback table and update KNN prototype store.
    return {"status": "accepted", "record_id": req.record_id}
