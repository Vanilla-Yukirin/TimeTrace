"""Unit tests for reciprocal_rank_fusion."""

import pytest

from timetrace.server.retrieval import reciprocal_rank_fusion


def test_rrf_single_list_preserves_order():
    fused = reciprocal_rank_fusion([["a", "b", "c"]])
    assert [doc for doc, _ in fused] == ["a", "b", "c"]


def test_rrf_consensus_top_wins():
    """An id ranked highly in BOTH channels beats one ranked #1 in only one."""
    fts = ["x", "a", "b"]   # x is #1 here
    vec = ["a", "x", "c"]   # a is #1 here, x is #2
    fused = reciprocal_rank_fusion([fts, vec])
    # a: 1/61 + 1/60 ; x: 1/60 + 1/61 — identical sums → tie, broken by id asc
    # so "a" comes before "x". Both beat single-list-only b/c.
    ids = [doc for doc, _ in fused]
    assert set(ids[:2]) == {"a", "x"}
    assert ids[2] in ("b", "c")


def test_rrf_id_only_in_one_list_still_appears():
    fused = reciprocal_rank_fusion([["a"], ["b"]])
    ids = {doc for doc, _ in fused}
    assert ids == {"a", "b"}


def test_rrf_empty_lists():
    assert reciprocal_rank_fusion([[], []]) == []
    assert reciprocal_rank_fusion([]) == []


def test_rrf_weights_bias_channel():
    """Weighting one channel heavier flips which channel's #1 wins the tie.

    Each channel has a single distinct top item. Unweighted both score 1/60 →
    tie broken by id-asc ('k' < 'v') → 'k' first. Weighting the vector
    channel 2x makes v=2/60 beat k=1/60 → 'v' first.
    """
    fts = ["k"]
    vec = ["v"]
    unweighted = reciprocal_rank_fusion([fts, vec])
    assert unweighted[0][0] == "k"  # tie at 1/60, id-asc → k

    weighted = reciprocal_rank_fusion([fts, vec], weights=[1.0, 2.0])
    assert weighted[0][0] == "v"  # v: 2/60 > k: 1/60


def test_rrf_weights_length_mismatch_raises():
    with pytest.raises(ValueError, match="weights length"):
        reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])


def test_rrf_deterministic_tie_break():
    """Same inputs → same output order every call (id-asc tie-break)."""
    lists = [["a", "b"], ["b", "a"]]  # perfectly symmetric → tie on both
    r1 = reciprocal_rank_fusion(lists)
    r2 = reciprocal_rank_fusion(lists)
    assert r1 == r2
    assert [d for d, _ in r1] == ["a", "b"]  # id-asc
