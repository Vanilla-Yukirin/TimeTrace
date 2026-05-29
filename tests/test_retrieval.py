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
    """Weighting the keyword channel heavier should pull its #1 above a
    vector-only #1."""
    fts = ["k"]            # keyword channel top
    vec = ["v", "k"]       # vector channel top is v, k is 2nd
    # Equal weights: v=1/60, k=1/60+1/61 → k wins anyway. Use a case where
    # weighting flips the result: give vector more items so v's lead is real.
    fts = ["k", "a", "b", "c"]
    vec = ["v", "a", "b", "c"]
    unweighted = reciprocal_rank_fusion([fts, vec])
    # k and v both rank #1 in their channel → tie at 1/60; broken by id asc → k
    assert unweighted[0][0] == "k"
    # Weight vector channel 2x → v's 1/60*2 beats k's 1/60*1
    weighted = reciprocal_rank_fusion([fts, vec], weights=[1.0, 2.0])
    assert weighted[0][0] == "v"


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
