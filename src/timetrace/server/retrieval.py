"""Retrieval fusion — combine multiple ranked result lists into one.

Used by every search surface — REST ``/v1/search/text``, the agent and MCP
``search_activity`` — to fuse the keyword channel (FTS5 BM25 / LIKE) with the
vector channel (cosine over text embeddings), plus a narrative channel over the
summary pyramid. :func:`reciprocal_rank_fusion` stays pure; the ``retrieve_*``
functions own the I/O so the surfaces cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from timetrace.server.embedding.client import EmbeddingError

if TYPE_CHECKING:
    from timetrace.server.db import Database
    from timetrace.server.embedding.client import EmbeddingClient

logger = structlog.get_logger(__name__)

# Standard RRF constant. Dampens the contribution of very-high ranks so the
# fusion isn't dominated by whichever channel happens to rank something #1.
# 60 is the value from the original Cormack et al. RRF paper and what most
# systems use.
_RRF_K = 60


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = _RRF_K,
    weights: Sequence[float] | None = None,
) -> list[tuple[str, float]]:
    """Fuse several ranked id-lists into one via Reciprocal Rank Fusion.

    Each input list is ordered best-first. An id's fused score is the sum over
    all lists of ``weight / (k + rank)`` where ``rank`` is its 0-based position
    in that list (so rank 0 → 1/(k+0), the largest term). Ids absent from a
    list contribute nothing for that list.

    Args:
        ranked_lists: one ordered id-list per channel (e.g. [fts_ids, vec_ids]).
        k: RRF damping constant (default 60).
        weights: optional per-channel multipliers (same length as
            ``ranked_lists``). Defaults to all-1.0. Use to bias e.g. keyword
            over vector when exact matches matter more.

    Returns:
        ``[(id, fused_score), ...]`` sorted by score desc. Stable for ties via
        first-seen order isn't guaranteed by sort alone, so we tie-break on the
        id string to keep output deterministic across runs.
    """
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError(
            f"weights length {len(weights)} != ranked_lists length {len(ranked_lists)}"
        )

    scores: dict[str, float] = {}
    for lst, weight in zip(ranked_lists, weights, strict=True):
        for rank, doc_id in enumerate(lst):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)

    # Sort by score desc, tie-break on id asc for determinism.
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# --------------------------------------------------------------------------- #
# Unified retrieval — the one search path REST, MCP and the agent share        #
# --------------------------------------------------------------------------- #


@dataclass
class RecordRetrieval:
    """Fused record ranking plus per-channel diagnostics."""

    ranked: list[tuple[str, float]] = field(default_factory=list)
    keyword_ranks: dict[str, int] = field(default_factory=dict)
    semantic_ranks: dict[str, int] = field(default_factory=dict)
    keyword_channel: str = "ok"
    semantic_channel: str = "unavailable"


async def retrieve_records(
    db: Database,
    embedder: EmbeddingClient | None,
    query: str,
    start_ms: int,
    end_ms: int,
    *,
    limit: int = 50,
    device_id: str | None = None,
    apps: list[str] | None = None,
    categories: list[str] | None = None,
) -> RecordRetrieval:
    """Rank records for ``query``: keyword (FTS5 BM25 / LIKE) + semantic
    (text-embedding cosine), fused with RRF.

    Scope filters (time, device, apps, categories) are applied inside each
    channel's candidate query, never after fusion. Without an embedder, or if
    embedding fails, it degrades to keyword-only and says so in
    ``semantic_channel``.
    """
    out = RecordRetrieval()
    fetch = max(limit * 4, 20)  # over-fetch each channel before fusion
    kw_rows = await db.query_records(
        start_ms,
        end_ms,
        limit=fetch,
        keyword=query,
        apps=apps,
        categories=categories,
        device_id=device_id,
        order="desc",
    )
    keyword_ids = [r["id"] for r in kw_rows]

    semantic_ids: list[str] = []
    if embedder is not None:
        try:
            qvec = await embedder.embed(query)
            hits = await db.vector_search(
                qvec,
                fetch,
                start_ms=start_ms,
                end_ms=end_ms,
                device_id=device_id,
                apps=apps,
                categories=categories,
            )
            semantic_ids = [rid for rid, _ in hits]
            out.semantic_channel = "ok"
        except EmbeddingError as exc:
            logger.info("retrieve.embed_failed", error=str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.warning("retrieve.embed_unexpected", error=str(exc))

    out.keyword_ranks = {rid: i + 1 for i, rid in enumerate(keyword_ids)}
    out.semantic_ranks = {rid: i + 1 for i, rid in enumerate(semantic_ids)}
    out.ranked = reciprocal_rank_fusion([keyword_ids, semantic_ids])[: max(limit, 1)]
    return out


async def retrieve_summaries(
    db: Database,
    query: str,
    start_ms: int,
    end_ms: int,
    *,
    grains: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Narrated pyramid windows whose narrative matches ``query`` (FTS/LIKE)."""
    rows = await db.search_summaries_text(query, start_ms, end_ms, grains=grains, limit=limit)
    return [r for r in rows if (r.get("description") or "").strip()]


async def fetch_records_by_ids(db: Database, ids: list[str]) -> dict[str, dict]:
    """Batch-fetch record rows (vlm_desc / category / first thumb) keyed by id.

    One query, IN-bounded by the caller's fused-candidate cap. Column shape
    mirrors what /v1/records returns so the frontend renders text-search hits
    with the same card component.
    """
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    sql = f"""
        SELECT r.id AS record_id, r.ts_start, r.ts_end, r.app_name,
               r.window_title, r.url, r.device_id, a.vlm_desc, a.category_final,
               s.thumb_path
        FROM records r
        LEFT JOIN analysis_results a ON a.record_id = r.id
        LEFT JOIN (
            SELECT record_id, MIN(thumb_path) AS thumb_path
            FROM screenshots WHERE deleted_at IS NULL GROUP BY record_id
        ) s ON s.record_id = r.id
        WHERE r.id IN ({placeholders})
    """
    async with db.lock:
        async with db.conn.execute(sql, ids) as cur:
            rows = await cur.fetchall()
    return {row["record_id"]: dict(row) for row in rows}
