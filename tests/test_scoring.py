"""
评分模型单元测试：
- 公式正确性
- 边界值
- grade 阈值
- highlights/risks 数据驱动
"""
from __future__ import annotations

import pytest

from app.services.scoring import compute_score, _grade


# ---------------------------------------------------------------------------
# grade 阈值
# ---------------------------------------------------------------------------

def test_grade_S():
    assert _grade(8.5) == "S"
    assert _grade(9.0) == "S"
    assert _grade(10.0) == "S"


def test_grade_A():
    assert _grade(7.0) == "A"
    assert _grade(8.49) == "A"


def test_grade_B():
    assert _grade(5.5) == "B"
    assert _grade(6.99) == "B"


def test_grade_C():
    assert _grade(0.0) == "C"
    assert _grade(5.49) == "C"


# ---------------------------------------------------------------------------
# compute_score 公式
# ---------------------------------------------------------------------------

def test_normal_case_returns_all_fields():
    metrics = {"stars": 10000, "commits_30d": 100, "contributors_90d": 10}
    biz = {"category": "agent", "monetization_candidates": ["SaaS", "Open-core"]}
    result = compute_score(metrics, biz)

    required_keys = [
        "market_score", "traction_score", "moat_score", "team_score",
        "monetization_score", "risk_score", "total_score", "grade",
        "highlights", "risks", "followups", "explanations",
    ]
    for key in required_keys:
        assert key in result, f"缺少字段: {key}"


def test_zero_metrics_gives_low_grade():
    metrics = {"stars": 0, "commits_30d": 0, "contributors_90d": 0}
    biz = None
    result = compute_score(metrics, biz)
    assert result["grade"] != "S", "零 metrics 不应得 S"
    assert result["total_score"] < 8.5


def test_high_stars_increases_traction():
    low = compute_score({"stars": 0, "commits_30d": 0, "contributors_90d": 0}, None)
    high = compute_score({"stars": 50000, "commits_30d": 0, "contributors_90d": 0}, None)
    assert high["traction_score"] > low["traction_score"]


def test_agent_category_increases_market():
    no_biz = compute_score({"stars": 1000, "commits_30d": 10, "contributors_90d": 3}, None)
    agent_biz = compute_score(
        {"stars": 1000, "commits_30d": 10, "contributors_90d": 3},
        {"category": "agent", "monetization_candidates": ["SaaS"]},
    )
    assert agent_biz["market_score"] > no_biz["market_score"]


def test_scores_capped_at_10():
    metrics = {"stars": 9999999, "commits_30d": 9999, "contributors_90d": 999}
    biz = {"category": "agent", "monetization_candidates": ["SaaS"]}
    result = compute_score(metrics, biz)
    for key in ["market_score", "traction_score", "moat_score", "team_score"]:
        assert result[key] <= 10.0, f"{key} 超过上限 10"


def test_contributors_affects_risk():
    few = compute_score({"stars": 5000, "commits_30d": 50, "contributors_90d": 1}, None)
    many = compute_score({"stars": 5000, "commits_30d": 50, "contributors_90d": 20}, None)
    assert many["risk_score"] > few["risk_score"]


# ---------------------------------------------------------------------------
# highlights/risks 数据驱动
# ---------------------------------------------------------------------------

def test_highlights_not_empty():
    metrics = {"stars": 5000, "commits_30d": 50, "contributors_90d": 5}
    biz = {"category": "observability", "monetization_candidates": ["SaaS"]}
    result = compute_score(metrics, biz)
    assert len(result["highlights"]) >= 1, "highlights 不应为空"


def test_risks_not_empty():
    metrics = {"stars": 100, "commits_30d": 5, "contributors_90d": 1}
    biz = None
    result = compute_score(metrics, biz)
    assert len(result["risks"]) >= 1, "risks 不应为空"


def test_high_traction_triggers_hot_highlight():
    # stars=80000 → traction > 7.0 → 应触发"热度高"相关 highlight
    metrics = {"stars": 80000, "commits_30d": 200, "contributors_90d": 30}
    biz = {"category": "agent", "monetization_candidates": ["SaaS"]}
    result = compute_score(metrics, biz)
    combined = " ".join(result["highlights"])
    assert "热度" in combined or "社区" in combined or "贡献者" in combined


def test_low_contributors_triggers_bus_factor_risk():
    metrics = {"stars": 5000, "commits_30d": 50, "contributors_90d": 2}
    biz = {"category": "developer-tools", "monetization_candidates": ["SaaS"]}
    result = compute_score(metrics, biz)
    combined = " ".join(result["risks"])
    assert "bus factor" in combined or "贡献者" in combined


def test_followups_not_empty():
    metrics = {"stars": 5000, "commits_30d": 50, "contributors_90d": 5}
    biz = {"category": "agent", "monetization_candidates": ["SaaS"]}
    result = compute_score(metrics, biz)
    assert len(result["followups"]) >= 1


def test_agent_category_followup_content():
    metrics = {"stars": 15000, "commits_30d": 100, "contributors_90d": 10}
    biz = {"category": "agent", "monetization_candidates": ["SaaS"]}
    result = compute_score(metrics, biz)
    combined = " ".join(result["followups"])
    # agent 方向应有 Agent 相关追问
    assert "Agent" in combined or "私有化" in combined or "幻觉" in combined


def test_explanations_contains_signals():
    metrics = {"stars": 12345, "commits_30d": 78, "contributors_90d": 6}
    biz = {"category": "security", "monetization_candidates": ["Open-core"]}
    result = compute_score(metrics, biz)
    signals = result["explanations"]["signals"]
    assert signals["stars"] == 12345
    assert signals["commits_30d"] == 78
    assert signals["contributors_90d"] == 6
    assert signals["category"] == "security"
