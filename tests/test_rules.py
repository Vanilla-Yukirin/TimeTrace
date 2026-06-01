"""Tests for the Rule/Feedback Engine (rules + VLM; KNN removed)."""

from timetrace.server.rules.engine import RuleSet, VlmPrediction, decide_category


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
        rules=_rules(),
    )
    assert cat == "development"


def test_vlm_pred_used_when_no_rule():
    cat, conf, _ = decide_category(
        app="unknown",
        url=None,
        title="something",
        vlm_pred=VlmPrediction(category="learning", confidence=0.9),
        rules=RuleSet(),
    )
    assert cat == "learning"
    assert conf == 1.0


def test_rule_outvotes_vlm():
    # A deterministic rule beats the VLM's pick (rule weight > vlm weight).
    cat, _, _ = decide_category(
        app="VSCode",
        url=None,
        title="main.py",
        vlm_pred=VlmPrediction(category="entertainment", confidence=1.0),
        rules=_rules(),
    )
    assert cat == "development"


def test_no_signals_returns_uncategorized():
    cat, conf, _ = decide_category(
        app="",
        url=None,
        title="",
        vlm_pred=None,
        rules=RuleSet(),
    )
    assert cat == "uncategorized"
    assert conf == 0.0
