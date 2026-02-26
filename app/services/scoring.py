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


def _build_highlights(market: float, traction: float, moat: float, monetization: float, biz: Optional[dict]) -> list[str]:
    highlights = []
    if traction >= 7.0:
        highlights.append("近期热度高，社区关注度强劲")
    if moat >= 6.5:
        highlights.append(f"贡献者社区活跃（contributors_90d 指标显著）")
    if market >= 6.0 and biz and biz.get("category") in {"agent", "security"}:
        highlights.append(f"赛道市场空间大（{biz.get('category')} 方向高增长）")
    if monetization >= 7.0 and biz and biz.get("monetization_candidates"):
        candidates = "、".join(biz["monetization_candidates"][:2])
        highlights.append(f"商业化路径清晰（{candidates}）")
    if not highlights:
        highlights.append("已进入 GitHub Trending 榜单，具备基础曝光度")
    return highlights[:3]


def _build_risks(traction: float, moat: float, contributors_90d: int, stars: int, biz: Optional[dict]) -> list[str]:
    risks = []
    if contributors_90d < 5:
        risks.append("活跃贡献者偏少，存在 bus factor 风险")
    if stars < 500:
        risks.append("Star 数较低，社区认可度待验证")
    if traction < 5.5:
        risks.append("近期增长动能不足，需关注热度持续性")
    if not biz or not biz.get("monetization_candidates"):
        risks.append("商业化路径尚不明确")
    if not risks:
        risks.append("团队规模与收入数据无法从 GitHub 获取，需补充验证")
    return risks[:3]


def _build_followups(biz: Optional[dict], grade: str) -> list[str]:
    category = biz.get("category") if biz else None
    followups = []

    if category == "agent":
        followups += ["Agent 可靠性与幻觉率如何？", "是否支持企业级私有化部署？"]
    elif category == "observability":
        followups += ["与现有 Prometheus/Grafana 生态的集成路径？", "企业版 SLA 保障方案？"]
    elif category == "security":
        followups += ["是否通过行业安全认证（SOC2/ISO27001）？", "漏洞响应 SLA 承诺？"]
    elif category == "data":
        followups += ["数据规模 benchmark（TB 级）？", "是否支持 GDPR 合规？"]
    else:
        followups += ["核心用户付费场景是什么？", "与主流开发工具链的集成深度？"]

    if grade in ("S", "A"):
        followups.append("是否有知名企业客户背书或标杆案例？")
    else:
        followups.append("增长放缓原因：竞品挤压 or 产品成熟度不足？")

    return followups[:3]


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
    grade = _grade(total)

    return {
        "market_score": round(market, 2),
        "traction_score": round(traction, 2),
        "moat_score": round(moat, 2),
        "team_score": round(team, 2),
        "monetization_score": round(monetization, 2),
        "risk_score": round(risk, 2),
        "total_score": round(total, 2),
        "grade": grade,
        "highlights": _build_highlights(market, traction, moat, monetization, biz),
        "risks": _build_risks(traction, moat, contributors_90d, stars, biz),
        "followups": _build_followups(biz, grade),
        "explanations": {
            "weights": {
                "market": 0.25,
                "traction": 0.25,
                "moat": 0.15,
                "team": 0.10,
                "monetization": 0.20,
                "risk": 0.05,
            },
            "signals": {
                "stars": stars,
                "commits_30d": commits_30d,
                "contributors_90d": contributors_90d,
                "category": biz.get("category") if biz else None,
            },
        },
    }
