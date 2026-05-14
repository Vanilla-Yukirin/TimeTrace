"""Tests for the Rule/Feedback Engine."""

from timetrace.server.rules.engine import KnnNeighbor, RuleSet, VlmPrediction, decide_category


def _rules() -> RuleSet:
    return RuleSet(
        rules=[
            {"app": "code", "category": "development"},
            {"domain": "github.com", "category": "development"},
            {"title_kw": "slack", "category": "communication"},
        ]
    )


def test_rule_match_by_app():
    cat, conf, trace = decide_category(
        app="VSCode",
        url=None,
        title="main.py",
        vlm_pred=None,
        knn_neighbors=[],
        rules=_rules(),
    )
    assert cat == "development"
    assert conf == 1.0


def test_rule_match_by_domain():
    cat, _, _ = decide_category(
        app="Chrome",
        url="https://github.com/user/repo",
        title="Pull Request",
        vlm_pred=None,
        knn_neighbors=[],
        rules=_rules(),
    )
    assert cat == "development"


def test_vlm_pred_used_when_no_rule():
    cat, conf, _ = decide_category(
        app="unknown",
        url=None,
        title="something",
        vlm_pred=VlmPrediction(category="learning", confidence=0.9),
        knn_neighbors=[],
        rules=RuleSet(),
    )
    assert cat == "learning"
    assert conf == 1.0


def test_knn_neighbors_contribute():
    neighbors = [
        KnnNeighbor(category="gaming", distance=0.1, source="knn"),
        KnnNeighbor(category="gaming", distance=0.2, source="knn"),
        KnnNeighbor(category="work", distance=0.5, source="knn"),
    ]
    cat, _, _ = decide_category(
        app="unknown",
        url=None,
        title="something",
        vlm_pred=None,
        knn_neighbors=neighbors,
        rules=RuleSet(),
    )
    assert cat == "gaming"


def test_no_signals_returns_uncategorized():
    cat, conf, _ = decide_category(
        app="",
        url=None,
        title="",
        vlm_pred=None,
        knn_neighbors=[],
        rules=RuleSet(),
    )
    assert cat == "uncategorized"
    assert conf == 0.0
