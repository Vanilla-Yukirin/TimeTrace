"""Analysis Worker — N concurrent VLM consumers with retry + circuit breaker."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from PIL import Image

from timetrace.vlm.client import VLMClient, VLMError, format_description
from timetrace.vlm.health import VLMHealthGate

if TYPE_CHECKING:
    from timetrace.config import StorageConfig, WorkerConfig
    from timetrace.storage.database import Database

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_S = 1.0
_DISABLED_IDLE_INTERVAL_S = 30.0


class AnalysisWorker:
    """State-machine driver for `analysis_results`.

    With VLM configured: launches `vlm_concurrency` consumer coroutines that
    each loop on `acquire() → claim → describe → save → ack`. Failures bump
    retry_count + next_retry_at and bounce the row back to pending. Cross-worker
    consecutive failures trip the `VLMHealthGate` into SLEEPING; recovery is
    gated on heartbeat probes.

    Without VLM (no API key): idles silently (no consumption) so `pending_vlm`
    rows stay queued for whenever VLM is configured later.
    """

    def __init__(
        self,
        db: Database,
        vlm: VLMClient | None = None,
        gate: VLMHealthGate | None = None,
        cfg: WorkerConfig | None = None,
        storage_cfg: StorageConfig | None = None,
    ) -> None:
        self._db = db
        self._vlm = vlm
        self._gate = gate
        from timetrace.config import WorkerConfig as _WorkerConfig  # noqa: PLC0415

        self._cfg = cfg or _WorkerConfig()
        self._storage_cfg = storage_cfg

    async def run(self) -> None:
        if self._vlm is None or self._gate is None:
            logger.info("analysis_worker.disabled", reason="no_vlm_client")
            while True:
                await asyncio.sleep(_DISABLED_IDLE_INTERVAL_S)

        n = max(1, self._cfg.vlm_concurrency)
        logger.info("analysis_worker.started", concurrency=n)
        try:
            await asyncio.gather(*(self._consume_loop(i) for i in range(n)))
        except asyncio.CancelledError:
            logger.info("analysis_worker.cancelled")
            raise

    async def _consume_loop(self, worker_id: int) -> None:
        while True:
            try:
                if not await self._gate.acquire():
                    await asyncio.sleep(self._gate.probe_interval_s / 2)
                    continue
                task = await self._db.claim_next_task(kind="pending_vlm")
                if task is None:
                    await asyncio.sleep(_POLL_INTERVAL_S)
                    continue
                await self._handle_one(task, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("worker.loop_error", worker_id=worker_id)
                await asyncio.sleep(_POLL_INTERVAL_S)

    async def _handle_one(self, task: dict, worker_id: int) -> None:
        record_id = task["record_id"]
        retry_count = task.get("retry_count") or 0

        meta = await self._db.get_record_meta(record_id)
        if meta is None:
            await self._db.mark_error_final(record_id, "record vanished before VLM call")
            return

        screenshot_rel = meta["screenshot_path"]
        if not screenshot_rel:
            # No screenshot to describe — short-circuit without bothering the model.
            await self._db.transition(record_id, "vlm_done")
            logger.info("worker.vlm_skipped_no_image", record_id=record_id)
            return

        if self._storage_cfg is None:
            await self._fail(record_id, retry_count, "storage_cfg missing")
            return

        abs_path = Path(self._storage_cfg.data_dir) / screenshot_rel
        try:
            image = await asyncio.to_thread(_load_image, abs_path)
        except Exception as exc:  # noqa: BLE001
            await self._fail(record_id, retry_count, f"image load failed: {exc}")
            return

        try:
            payload = await self._vlm.describe(image, window_title=meta["window_title"])
        except VLMError as exc:
            await self._gate.report_failure()
            await self._fail(record_id, retry_count, str(exc))
            return

        await self._gate.report_success()
        text = format_description(payload)
        await self._db.save_description(record_id, text)
        await self._db.transition(record_id, "vlm_done")
        logger.info(
            "worker.vlm_done",
            worker_id=worker_id,
            record_id=record_id,
            chars=len(text),
        )

    async def _fail(self, record_id: str, retry_count: int, error_msg: str) -> None:
        new_retry = retry_count + 1
        if new_retry > self._cfg.max_retries:
            await self._db.mark_error_final(record_id, f"max_retries_exceeded: {error_msg}")
            logger.warning(
                "worker.error_final",
                record_id=record_id,
                retries=new_retry,
                error=error_msg,
            )
            return
        backoff_s = min(
            self._cfg.backoff_base_s * (2 ** (new_retry - 1)),
            self._cfg.backoff_max_s,
        )
        next_retry_at = int(time.time() * 1000 + backoff_s * 1000)
        await self._db.mark_error_retryable(record_id, error_msg, new_retry, next_retry_at)
        logger.info(
            "worker.error_retryable",
            record_id=record_id,
            retry=new_retry,
            backoff_s=backoff_s,
            error=error_msg,
        )


def _load_image(path: Path) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img
