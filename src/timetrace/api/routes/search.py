"""Multi-modal search endpoint: keyword + reference image(s).

Pipeline (see `infra/overview/roadmap.md` for context):

    images[0]            ─► pHash          ─► PHashIndex.search ─► visual_hits
    images[all] ─► VLM   ─► description(s) ─► BM25-like text     ─► semantic_hits
    q (+ no semantic)    ─► LIKE fallback                        ─► text_hits
                                                    │
                                                    ▼
                                             RRF (k=60) fuse
                                                    │
                                                    ▼
                          JOIN screenshots/records/analysis_results
                                                    │
                                                    ▼
                          post-filter (apps / categories)
                                                    │
                                                    ▼
                                           top-N items

`_bm25_search` is currently a LIKE fallback over `vlm_desc`; once FTS5 tables
are built it is the only function that needs to flip to a `MATCH` query.
"""

from __future__ import annotations

import io
from typing import Any

import structlog
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from PIL import Image

from timetrace.phash_index.hash import compute_phash
from timetrace.vlm.client import VLMError, format_description

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["search"])

_RRF_K = 60
_MAX_IMAGES = 5
_MAX_FUSED = 1000  # Hard cap on fused candidates handed to enrichment (bounds IN (?, ?, …))
_THUMBS_PREFIX = ("thumbs/", "thumbs\\")


def _escape_like(s: str) -> str:
    """Escape SQL LIKE metacharacters so literal % and _ don't act as wildcards."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --------------------------------------------------------------------------- #
# BM25 fallback (LIKE-based scoring; FTS5 MATCH is the planned upgrade)        #
# --------------------------------------------------------------------------- #


async def _bm25_search(
    db: Any,
    query_text: str,
    ts_range: tuple[int, int] | None,
    limit: int,
) -> list[tuple[str, float]]:
    """BM25-ranked screenshots by VLM description.

    Current implementation: LIKE fallback over vlm_desc (vlm_desc is NULL
    everywhere today, so this returns []). Will become an FTS5 MATCH query once
    FTS5 tables are built.

    Returns list of (screenshot_id, score) ordered by relevance desc.
    """
    tokens = [t for t in query_text.split() if len(t) >= 2]
    if not tokens:
        return []

    conditions = ["s.deleted_at IS NULL", "a.vlm_desc IS NOT NULL"]
    params: list[Any] = []
    if ts_range is not None:
        conditions.append("r.ts_start BETWEEN ? AND ?")
        params.extend(ts_range)

    # Escape LIKE metachars so literal % / _ in user tokens don't over-match.
    like_clauses = " + ".join(
        ["(CASE WHEN a.vlm_desc LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END)"] * len(tokens)
    )
    for tok in tokens:
        params.append(f"%{_escape_like(tok)}%")
    params.append(limit)

    sql = f"""
        SELECT * FROM (
            SELECT s.id AS sid, r.ts_start AS ts,
                   ({like_clauses}) AS score
            FROM screenshots s
            JOIN records r ON r.id = s.record_id
            JOIN analysis_results a ON a.record_id = s.record_id
            WHERE {" AND ".join(conditions)}
        )
        WHERE score > 0
        ORDER BY score DESC, ts DESC
        LIMIT ?
    """
    async with db.lock:
        async with db.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
    return [(row["sid"], float(row["score"])) for row in rows]


async def _like_fallback(
    db: Any,
    q: str,
    ts_range: tuple[int, int] | None,
    limit: int,
) -> list[tuple[str, float]]:
    """Keyword LIKE over window_title + vlm_desc, returning screenshot ids."""
    conditions = [
        "s.deleted_at IS NULL",
        "(r.window_title LIKE ? ESCAPE '\\' OR a.vlm_desc LIKE ? ESCAPE '\\')",
    ]
    like = f"%{_escape_like(q)}%"
    params: list[Any] = [like, like]
    if ts_range is not None:
        conditions.append("r.ts_start BETWEEN ? AND ?")
        params.extend(ts_range)
    params.append(limit)

    sql = f"""
        SELECT s.id AS sid, r.ts_start AS ts
        FROM screenshots s
        JOIN records r ON r.id = s.record_id
        LEFT JOIN analysis_results a ON a.record_id = s.record_id
        WHERE {" AND ".join(conditions)}
        ORDER BY r.ts_start DESC
        LIMIT ?
    """
    async with db.lock:
        async with db.conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
    # Rank by ts_start desc → score = 1/(1+position)
    return [(row["sid"], 1.0 / (1 + i)) for i, row in enumerate(rows)]


# --------------------------------------------------------------------------- #
# RRF fusion                                                                    #
# --------------------------------------------------------------------------- #


def _rrf_merge(channels: list[list[tuple[str, float]]], k: int = _RRF_K) -> list[dict]:
    """Fuse multiple ranked channels via Reciprocal Rank Fusion.

    Each `channel` is a list of `(screenshot_id, score)` already ordered best-first.
    Returns `[{screenshot_id, rrf_score, ranks: {channel_idx: rank}}, ...]` sorted
    by RRF score desc. Screenshots absent from a channel contribute 0 from it.
    """
    fused: dict[str, dict[str, Any]] = {}
    for idx, channel in enumerate(channels):
        for rank, (sid, _score) in enumerate(channel, start=1):
            entry = fused.setdefault(sid, {"screenshot_id": sid, "rrf_score": 0.0, "ranks": {}})
            entry["rrf_score"] += 1.0 / (k + rank)
            entry["ranks"][idx] = rank
    return sorted(fused.values(), key=lambda e: e["rrf_score"], reverse=True)


# --------------------------------------------------------------------------- #
# Enrichment + post-filter                                                      #
# --------------------------------------------------------------------------- #


def _strip_thumb(path: str | None) -> str | None:
    if not path:
        return path
    for prefix in _THUMBS_PREFIX:
        if path.startswith(prefix):
            return path[len(prefix) :].replace("\\", "/")
    return path.replace("\\", "/")


def _build_reasons(
    ranks: dict[int, int],
    visual_distances: dict[str, int],
    sid: str,
) -> list[str]:
    reasons = []
    if 0 in ranks and sid in visual_distances:
        reasons.append(f"pHash 距离 {visual_distances[sid]}")
    if 1 in ranks:
        reasons.append(f"语义 rank {ranks[1]}")
    if 2 in ranks:
        reasons.append(f"关键词 rank {ranks[2]}")
    return reasons


async def _enrich_and_filter(
    db: Any,
    fused: list[dict],
    visual_distances: dict[str, int],
    apps: list[str] | None,
    categories: list[str] | None,
    limit: int,
) -> list[dict]:
    if not fused:
        return []
    sids = [e["screenshot_id"] for e in fused]
    rows = await db.get_screenshots_with_records(sids)
    meta_by_sid = {row["screenshot_id"]: row for row in rows}

    apps_set = set(apps) if apps else None
    cats_set = set(categories) if categories else None

    items: list[dict] = []
    for entry in fused:
        sid = entry["screenshot_id"]
        meta = meta_by_sid.get(sid)
        if meta is None:
            continue
        if apps_set and meta["app_name"] not in apps_set:
            continue
        if cats_set and meta.get("category_final") not in cats_set:
            continue

        items.append(
            {
                "screenshot_id": sid,
                "record_id": meta["record_id"],
                "ts_start": meta["ts_start"],
                "ts_end": meta.get("ts_end"),
                "app_name": meta["app_name"],
                "window_title": meta["window_title"],
                "url": meta.get("url"),
                "thumb_path": _strip_thumb(meta.get("thumb_path")),
                "vlm_desc": meta.get("vlm_desc"),
                "category_final": meta.get("category_final"),
                "match": {
                    "visual_distance": visual_distances.get(sid),
                    "semantic_rank": entry["ranks"].get(1),
                    "text_rank": entry["ranks"].get(2),
                    "rrf_score": entry["rrf_score"],
                    "reasons": _build_reasons(entry["ranks"], visual_distances, sid),
                },
            }
        )
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------- #
# Endpoint                                                                      #
# --------------------------------------------------------------------------- #


def _parse_csv(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [tok.strip() for tok in raw.split(",") if tok.strip()]
    return items or None


@router.post("/search/by-image")
async def search_by_image(
    request: Request,
    images: list[UploadFile] = File(...),
    visual: bool = Form(True),
    semantic: bool = Form(True),
    radius: int = Form(10),
    q: str | None = Form(None),
    start: int | None = Form(None),
    end: int | None = Form(None),
    apps: str | None = Form(None),
    categories: str | None = Form(None),
    limit: int = Form(50),
) -> dict:
    """Multi-modal search combining pHash visual similarity + VLM semantic similarity."""
    db = request.app.state.db
    phash_index = request.app.state.phash_index

    if not images:
        raise HTTPException(400, "at least one reference image is required")
    if len(images) > _MAX_IMAGES:
        raise HTTPException(400, f"too many reference images; max {_MAX_IMAGES}")
    if not visual and not semantic and not q:
        raise HTTPException(400, "specify at least one of visual, semantic, or q")

    pil_images: list[Image.Image] = []
    for uf in images:
        raw = await uf.read()
        try:
            img = Image.open(io.BytesIO(raw))
            img.load()
        except Exception as exc:
            raise HTTPException(400, f"cannot decode image {uf.filename!r}: {exc}") from exc
        pil_images.append(img)

    # Half-open time windows are honoured — only both-None means "all time".
    if start is not None or end is not None:
        ts_range = (start if start is not None else 0, end if end is not None else 2**62)
    else:
        ts_range = None
    limit = max(1, min(limit, 200))
    apps_list = _parse_csv(apps)
    cats_list = _parse_csv(categories)

    # ---------- Visual channel (pHash on first image only) ----------
    visual_hits: list[tuple[str, float]] = []
    visual_distances: dict[str, int] = {}
    visual_status = "disabled"
    if visual:
        if phash_index is None or len(phash_index) == 0:
            visual_status = "unavailable"
        else:
            ph = compute_phash(pil_images[0])
            raw_hits = phash_index.search(ph, radius=radius, ts_range=ts_range, k=limit * 4)
            # Already sorted by distance asc
            visual_hits = [(sid, float(-dist)) for dist, sid in raw_hits]
            visual_distances = {sid: dist for dist, sid in raw_hits}
            visual_status = "ok"

    # ---------- Semantic channel (VLM describe all images → BM25) ----------
    semantic_hits: list[tuple[str, float]] = []
    semantic_status = "disabled"
    vlm_client = getattr(request.app.state, "vlm_client", None)
    if semantic:
        descriptions: list[str] = []
        if vlm_client is not None and pil_images:
            for uf, img in zip(images, pil_images, strict=False):
                try:
                    payload = await vlm_client.describe(img, window_title=uf.filename)
                except VLMError as exc:
                    logger.info("search.vlm_describe_failed", error=str(exc))
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning("search.vlm_describe_unexpected", error=str(exc))
                    continue
                descriptions.append(format_description(payload))
        if not descriptions:
            semantic_status = "unavailable"
        else:
            query_text = " ".join(descriptions)
            if q:
                query_text = f"{q} {query_text}"
            semantic_hits = await _bm25_search(db, query_text, ts_range, limit * 4)
            semantic_status = "ok"

    # ---------- Keyword-only channel (only when semantic off but q given) ----------
    text_hits: list[tuple[str, float]] = []
    if q and not semantic:
        text_hits = await _like_fallback(db, q, ts_range, limit * 4)

    # ---------- Fuse ----------
    fused = _rrf_merge([visual_hits, semantic_hits, text_hits])
    # Bound the enrichment IN-clause regardless of channel widths.
    fused = fused[:_MAX_FUSED]
    items = await _enrich_and_filter(db, fused, visual_distances, apps_list, cats_list, limit)

    logger.info(
        "search.by_image",
        images=len(pil_images),
        visual=visual_status,
        semantic=semantic_status,
        visual_hits=len(visual_hits),
        semantic_hits=len(semantic_hits),
        text_hits=len(text_hits),
        returned=len(items),
    )

    return {
        "items": items,
        "total": len(items),
        "visual_channel": visual_status,
        "semantic_channel": semantic_status,
    }
