"""Unified LLM-request ledger feed (powers the /llm-log panel).

Newest-first, keyset-paginated by ``ts_start``. Read-only; gated by
``require_principal`` (mounted in the business_deps block of ``create_app``).
Rows come straight from ``llm_requests`` — the timing + real token usage is
captured at the call site (see ``server/llm_log.py``), nothing derived here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["llm-log"])


@router.get("/llm-requests")
async def list_llm_requests(
    request: Request,
    caller: str | None = None,
    status: str | None = None,
    before_ts: int | None = None,
    limit: int = 100,
) -> dict:
    """Newest-first page of LLM calls.

    - **caller**: filter to one source (``worker_vlm`` / ``narrate`` / ``ask_agent``)
    - **status**: ``ok`` / ``error``
    - **before_ts**: keyset cursor — pass the last row's ``ts_start`` to page back
    - **limit**: page size (≤ 500)
    """
    db = request.app.state.db
    limit = max(1, min(limit, 500))
    rows = await db.query_llm_requests(
        caller=caller, status=status, before_ts=before_ts, limit=limit
    )
    last = rows[-1] if len(rows) == limit else None
    next_cursor = last["ts_start"] if last is not None else None
    return {"items": rows, "next_cursor": next_cursor}


@router.get("/llm-requests/stats")
async def llm_request_stats(request: Request, since_ts: int | None = None) -> dict:
    """Aggregate counts/tokens/avg-latency for the ledger header card."""
    db = request.app.state.db
    return await db.llm_request_stats(since_ts=since_ts)
