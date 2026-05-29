"""Retrieval fusion — combine multiple ranked result lists into one.

Used by the search surfaces (REST /v1/search, MCP search_activity) to fuse
the keyword channel (FTS5 BM25 / LIKE) with the vector channel (cosine over
text embeddings). Kept transport-agnostic and dependency-free so it's trivial
to unit test and reuse.
"""

from __future__ import annotations

from collections.abc import Sequence

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
