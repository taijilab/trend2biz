from __future__ import annotations

from typing import Optional


def _grade(total: float) -> str:
    if total >= 8.5:
        return "S"
    if total >= 7.0:
        return "A"
    if total >= 5.5:
        return "B"
    return "C"


def compute_score(metrics: dict, biz: Optional[dict]) -> dict:
    stars = metrics.get("stars") or 0
    commits_30d = metrics.get("commits_30d") or 0
    contributors_90d = metrics.get("contributors_90d") or 0

    market = min(10.0, 5.0 + (1.0 if biz and biz.get("category") in {"agent", "security"} else 0.2))
    traction = min(10.0, 4.0 + stars / 10000 + commits_30d / 100)
    moat = min(10.0, 4.5 + contributors_90d / 20)
    team = min(10.0, 4.0 + contributors_90d / 30)
    monetization = 7.5 if biz and biz.get("monetization_candidates") else 5.0
    risk = 7.0 if contributors_90d >= 5 else 5.5

    total = (
        market * 0.25
        + traction * 0.25
        + moat * 0.15
        + team * 0.10
        + monetization * 0.20
        + risk * 0.05
    )

    return {
        "market_score": round(market, 2),
        "traction_score": round(traction, 2),
        "moat_score": round(moat, 2),
        "team_score": round(team, 2),
        "monetization_score": round(monetization, 2),
        "risk_score": round(risk, 2),
        "total_score": round(total, 2),
        "grade": _grade(total),
        "highlights": ["趋势热度高", "具备商业化路径", "社区活跃度良好"],
        "risks": ["团队信息不完整", "收入数据缺失"],
        "followups": ["确认企业付费场景", "验证留存与部署门槛"],
        "explanations": {
            "weights": {
                "market": 0.25,
                "traction": 0.25,
                "moat": 0.15,
                "team": 0.10,
                "monetization": 0.20,
                "risk": 0.05,
            }
        },
    }
