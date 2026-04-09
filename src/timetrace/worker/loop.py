"""Analysis Worker – polls pending tasks and runs VLM/embedding pipeline."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from timetrace.storage.database import Database

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_S = 0.2


class AnalysisWorker:
    """Asynchronous worker that drives the per-record analysis state machine.

    State machine:
        captured → pending_vlm → vlm_done
        vlm_done → pending_embed → embed_done
        embed_done → pending_classify → done
        (any stage) → error_retryable | error_final
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def run(self) -> None:
        logger.info("analysis_worker.started")
        while True:
            processed = await self._process_next()
            if not processed:
                await asyncio.sleep(_POLL_INTERVAL_S)

    async def _process_next(self) -> bool:
        task = await self._db.claim_next_task(kind="pending_vlm")
        if task is None:
            return False

        try:
            desc = await self._describe(task)
            await self._db.save_description(task["record_id"], desc)
            await self._db.transition(task["record_id"], "vlm_done")
            logger.info("worker.vlm_done", record_id=task["record_id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("worker.error", record_id=task["record_id"], error=str(exc))
            await self._db.mark_error_final(task["record_id"], str(exc))

        return True

    async def _describe(self, task: dict) -> str:
        """Stub VLM description – replace with real provider call in Phase 1.5."""
        return f"[pending description for record {task['record_id']}]"
