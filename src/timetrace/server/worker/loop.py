"""Analysis Worker — N concurrent VLM consumers with retry + circuit breaker."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from PIL import Image

from timetrace.server.embedding.client import EmbeddingClient, EmbeddingError
from timetrace.server.rules.engine import VlmPrediction, decide_category
from timetrace.server.settings.overrides import build_ruleset, find_note, load_overrides
from timetrace.server.vlm.client import VLMClient, VLMError, format_description
from timetrace.server.vlm.health import VLMHealthGate

if TYPE_CHECKING:
    from timetrace.common.config import StorageConfig, WorkerConfig
    from timetrace.server.db import Database

logger = structlog.get_logger(__name__)

_POLL_INTERVAL_S = 1.0
_DISABLED_IDLE_INTERVAL_S = 30.0
_BACKFILL_BATCH = 32
_BACKFILL_MAX_CONSECUTIVE_FAILS = 5  # run of failures w/o success → endpoint down, abort


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
        embedding: EmbeddingClient | None = None,
    ) -> None:
        self._db = db
        self._vlm = vlm
        self._gate = gate
        from timetrace.common.config import WorkerConfig as _WorkerConfig  # noqa: PLC0415

        self._cfg = cfg or _WorkerConfig()
        self._storage_cfg = storage_cfg
        # Optional — when None, _embed_and_save short-circuits silently. Worker
        # NEVER blocks vlm_done on embedding success; embedding is best-effort,
        # fillable by a separate backfill sweep (Phase 2).
        self._embedding = embedding

    async def run(self) -> None:
        coros: list = []
        # Embedding backfill is independent of VLM — runs whenever an embedding
        # client is configured, even if VLM is off. One-shot: drains the
        # backlog of vlm_done-but-not-embedded rows then the coro returns;
        # gather keeps awaiting the (infinite) consume / idle coros.
        if self._embedding is not None:
            coros.append(self._backfill_embeddings())
            logger.info("analysis_worker.backfill_scheduled")

        if self._vlm is None or self._gate is None:
            logger.info("analysis_worker.vlm_disabled", reason="no_vlm_client")
            coros.append(self._idle_forever())
        else:
            n = max(1, self._cfg.vlm_concurrency)
            logger.info("analysis_worker.started", concurrency=n)
            coros.extend(self._consume_loop(i) for i in range(n))

        try:
            await asyncio.gather(*coros)
        except asyncio.CancelledError:
            logger.info("analysis_worker.cancelled")
            raise

    async def _idle_forever(self) -> None:
        while True:
            await asyncio.sleep(_DISABLED_IDLE_INTERVAL_S)

    async def _backfill_embeddings(self) -> None:
        """One-shot sweep: embed every vlm_done row that has a description but
        no text_embedding yet (rows created before the embedding stage landed,
        or rows whose live embedding call failed).

        Best-effort, with two distinct failure modes:

        - **Per-row failure** (empty text / dim mismatch / a single oversized
          desc): that row's id goes into ``skipped`` so the next ``fetch``
          excludes it, then we continue. Without this a poison row at the
          front of the queue would abort the sweep on every restart and
          starve every healthy row behind it.
        - **Endpoint down**: surfaces as a run of consecutive failures. After
          ``_BACKFILL_MAX_CONSECUTIVE_FAILS`` in a row with no success between,
          we assume the endpoint is unreachable and stop; the next worker
          restart retries from where it left off (skipped set resets, so
          previously-skipped poison rows get one more chance each restart —
          cheap, bounded).
        """
        if self._embedding is None:
            return
        total = 0
        skipped: set[str] = set()
        consecutive_fails = 0
        while True:
            try:
                batch = await self._db.fetch_rows_needing_text_embedding(
                    limit=_BACKFILL_BATCH, exclude_ids=skipped
                )
            except Exception:  # noqa: BLE001
                logger.exception("worker.backfill_fetch_failed")
                return
            if not batch:
                break
            for row in batch:
                try:
                    vec = await self._embedding.embed(row["vlm_desc"])
                except Exception as exc:  # noqa: BLE001  (EmbeddingError + anything unexpected)
                    skipped.add(row["record_id"])
                    consecutive_fails += 1
                    logger.warning(
                        "worker.backfill_embed_failed",
                        record_id=row["record_id"],
                        error=str(exc),
                        consecutive_fails=consecutive_fails,
                        embedded_so_far=total,
                    )
                    if consecutive_fails >= _BACKFILL_MAX_CONSECUTIVE_FAILS:
                        logger.warning(
                            "worker.backfill_aborted_endpoint_down",
                            embedded=total,
                            skipped=len(skipped),
                        )
                        return
                    continue
                consecutive_fails = 0
                await self._db.save_text_embedding(row["record_id"], vec, self._embedding.model)
                total += 1
            logger.info("worker.backfill_progress", embedded=total, skipped=len(skipped))
        if total or skipped:
            logger.info("worker.backfill_complete", embedded=total, skipped=len(skipped))

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

        # User per-app overrides (settings KV, empty by default). Loaded fresh
        # per task so edits take effect without a restart — one indexed PK
        # lookup, negligible next to the VLM call.
        overrides = await load_overrides(self._db)
        app_name = meta.get("app_name") or ""

        vlm_t0 = time.monotonic()
        try:
            payload = await self._vlm.describe(
                image,
                window_title=meta["window_title"],
                app_note=find_note(overrides, app_name),
            )
        except VLMError as exc:
            await self._gate.report_failure()
            await self._fail(record_id, retry_count, str(exc))
            return
        # monotonic clock for the duration (immune to wall-clock adjustments);
        # the queued_at/done_at *timestamps* stay on _now_ms() wall time.
        vlm_latency_ms = int((time.monotonic() - vlm_t0) * 1000)

        text = format_description(payload)
        if not text:
            # A syntactically valid JSON object with every descriptive field
            # blank is not a successful analysis. Keep it retryable instead of
            # persisting a misleading vlm_done row. This guard also protects
            # alternate/test VLM implementations that bypass VLMClient's
            # response validator.
            await self._gate.report_failure()
            await self._fail(record_id, retry_count, "VLM returned no descriptive text")
            return
        await self._gate.report_success()
        await self._db.save_description(
            record_id,
            text,
            vlm_model=getattr(self._vlm, "model", None),
            vlm_latency_ms=vlm_latency_ms,
        )

        # Classification: a user rule (if any) deterministically outvotes the
        # VLM's pick (rule weight 2.0 > vlm 1.5); with no matching rule the VLM's
        # chosen category wins. Rules come from the per-app overrides setting.
        # Persist the VLM's raw pick (category_suggested) + the vote breakdown
        # (decision_trace) so the audit feed can show "VLM said X → rule → Y"
        # and the pyramid rollup can reuse them.
        suggested = payload.get("category") or "uncategorized"
        final_cat, confidence, trace = decide_category(
            app=app_name,
            url=meta.get("url"),
            title=meta.get("window_title") or "",
            vlm_pred=VlmPrediction(category=suggested, confidence=1.0),
            rules=build_ruleset(overrides),
        )
        await self._db.set_category_final(
            record_id,
            final_cat,
            category_suggested=suggested,
            confidence=confidence,
            decision_trace=json.dumps(trace, ensure_ascii=False),
        )

        await self._db.transition(record_id, "vlm_done")
        logger.info(
            "worker.vlm_done",
            worker_id=worker_id,
            record_id=record_id,
            chars=len(text),
            category=final_cat,
        )
        # Best-effort embedding stage. Failure → log + skip; vlm_done remains
        # the durable state. Phase 2 backfill sweeps any rows that landed
        # here with NULL embedding (worker down / endpoint down / etc).
        await self._embed_and_save(record_id, text, worker_id)

    async def _embed_and_save(self, record_id: str, text: str, worker_id: int) -> None:
        if self._embedding is None or not text:
            return
        try:
            vec_bytes = await self._embedding.embed(text)
        except EmbeddingError as exc:
            logger.warning(
                "worker.embedding_failed",
                worker_id=worker_id,
                record_id=record_id,
                error=str(exc),
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception(
                "worker.embedding_unexpected",
                worker_id=worker_id,
                record_id=record_id,
            )
            return
        await self._db.save_text_embedding(record_id, vec_bytes, self._embedding.model)
        logger.info(
            "worker.embedding_done",
            worker_id=worker_id,
            record_id=record_id,
            bytes=len(vec_bytes),
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
