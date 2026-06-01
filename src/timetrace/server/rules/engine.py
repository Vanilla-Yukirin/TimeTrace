"""Rule/Feedback Engine – combines deterministic rules with the VLM's pick.

KNN voting was removed: with a small fixed taxonomy + a capable VLM, nearest-
neighbour voting over a sparse labelled set added noise, not signal. The
category decision is now rules-first, then the VLM's chosen category.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_WEIGHTS: dict[str, float] = {
    "user_edit": 5.0,
    "user_confirm": 3.0,
    "rule": 2.0,
    "vlm": 1.5,
}


@dataclass
class VlmPrediction:
    category: str
    confidence: float


@dataclass
class RuleSet:
    """Simple rule table mapping app/process/title patterns to categories."""

    rules: list[dict] = field(default_factory=list)

    def match(self, app: str, url: str | None, title: str) -> str | None:
        for rule in self.rules:
            if rule.get("app") and rule["app"].lower() in app.lower():
                return rule["category"]
            if url and rule.get("domain") and rule["domain"] in url:
                return rule["category"]
            if rule.get("title_kw") and rule["title_kw"].lower() in title.lower():
                return rule["category"]
        return None


def _add_vote(votes: dict[str, float], category: str, weight: float) -> None:
    votes[category] = votes.get(category, 0.0) + weight


def decide_category(
    app: str,
    url: str | None,
    title: str,
    vlm_pred: VlmPrediction | None,
    rules: RuleSet,
) -> tuple[str, float, dict]:
    """Return (final_category, confidence, decision_trace).

    A deterministic rule (app/domain/title match) outvotes the VLM; with no
    matching rule the VLM's chosen category wins. Empty inputs → uncategorized.
    """
    votes: dict[str, float] = {}

    rule_cat = rules.match(app, url, title)
    if rule_cat:
        _add_vote(votes, rule_cat, SOURCE_WEIGHTS["rule"])

    if vlm_pred:
        _add_vote(votes, vlm_pred.category, vlm_pred.confidence * SOURCE_WEIGHTS["vlm"])

    if not votes:
        return ("uncategorized", 0.0, {"signals": {}})

    sorted_cats = sorted(votes.items(), key=lambda x: x[1], reverse=True)
    top_cat, top_score = sorted_cats[0]
    second_score = sorted_cats[1][1] if len(sorted_cats) > 1 else 0.0
    confidence = top_score / (top_score + second_score) if (top_score + second_score) > 0 else 1.0

    trace = {
        "final_category": top_cat,
        "confidence": round(confidence, 4),
        "candidates": [{"cat": c, "score": round(s, 4)} for c, s in sorted_cats[:5]],
        "signals": {
            "rule": {"hit": rule_cat is not None, "cat": rule_cat},
            "vlm": {"cat": vlm_pred.category, "conf": vlm_pred.confidence} if vlm_pred else None,
        },
    }
    return (top_cat, confidence, trace)
