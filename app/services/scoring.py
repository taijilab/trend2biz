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


# Per-category follow-up question sets (2 base + 1 grade-dependent appended)
_FOLLOWUP_BY_CATEGORY: dict[str, list[str]] = {
    "agent": [
        "Agent 可靠性与幻觉率在生产环境的测试情况？",
        "是否支持企业级私有化部署？",
    ],
    "security": [
        "是否通过行业安全认证（SOC2/ISO27001）？",
        "漏洞响应 SLA 承诺如何？",
    ],
    "data-platform": [
        "数据规模 benchmark（TB 级处理能力）？",
        "是否支持 GDPR 合规与数据主权要求？",
    ],
    "observability": [
        "与现有 Prometheus/Grafana 生态的集成路径？",
        "企业版 SLA 保障方案？",
    ],
    "fintech": [
        "监管合规路径（金融牌照 / 沙盒测试）？",
        "核心交易延迟 benchmark？",
    ],
    "biotech": [
        "临床试验或监管路径（FDA/CE）？",
        "与主流基因组测序平台的兼容性？",
    ],
    "robotics-iot": [
        "实时控制延迟指标（ms 级）？",
        "与 ROS 生态的集成深度？",
    ],
    "edu-tech": [
        "学习效果可量化指标（完课率 / 测评提升）？",
        "与 LMS 标准（SCORM/xAPI）的兼容性？",
    ],
    "media-tech": [
        "媒体处理吞吐量 benchmark？",
        "CDN / 存储成本模型？",
    ],
    "low-code": [
        "非技术用户上手时间（Time to First Value）？",
        "自定义逻辑扩展的边界在哪里？",
    ],
    "enterprise-saas": [
        "核心客户付费转化漏斗数据？",
        "与 Salesforce/SAP 生态的集成路径？",
    ],
    "infra": [
        "与主流云平台（AWS/GCP/Azure）的适配深度？",
        "高可用与灾难恢复方案？",
    ],
    "devops": [
        "与现有 CI/CD 工具链的集成复杂度？",
        "大规模（1000+ 服务）部署案例？",
    ],
    "devtools": [
        "开发者日活跃使用率与留存数据？",
        "与主流 IDE 的集成深度？",
    ],
    "developer-tools": [
        "核心用户付费场景是什么？",
        "与主流开发工具链的集成深度？",
    ],
}

# Fallback market score by category (for biz profiles without market_base in explanations)
_MARKET_BASE_BY_CATEGORY: dict[str, float] = {
    "agent": 6.8,
    "security": 6.5,
    "fintech": 6.5,
    "biotech": 6.5,
    "robotics-iot": 6.3,
    "data-platform": 6.2,
    "observability": 6.0,
    "low-code": 6.0,
    "media-tech": 5.8,
    "enterprise-saas": 5.8,
    "infra": 5.8,
    "edu-tech": 5.8,
    "devops": 5.5,
    "devtools": 5.2,
    "developer-tools": 5.0,
}


def _build_highlights(market: float, traction: float, moat: float, monetization: float, biz: Optional[dict]) -> list[str]:
    highlights = []
    if traction >= 7.0:
        highlights.append("近期热度高，社区关注度强劲")
    if moat >= 6.5:
        highlights.append("贡献者社区活跃，生态护城河较强")
    if market >= 6.5 and biz and biz.get("category"):
        highlights.append(f"赛道市场空间大（{biz['category']} 方向高增长）")
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
    category = (biz.get("category") if biz else None) or "developer-tools"
    base = _FOLLOWUP_BY_CATEGORY.get(category, _FOLLOWUP_BY_CATEGORY["developer-tools"])
    followups = list(base[:2])
    if grade in ("S", "A"):
        followups.append("是否有知名企业客户背书或标杆案例？")
    else:
        followups.append("增长放缓原因：竞品挤压 or 产品成熟度不足？")
    return followups[:3]


def compute_score(metrics: dict, biz: Optional[dict]) -> dict:
    stars = metrics.get("stars") or 0
    commits_30d = metrics.get("commits_30d") or 0
    contributors_90d = metrics.get("contributors_90d") or 0

    # Resolve market base: prefer biz.explanations.market_base, fall back to category map
    market_base = 5.2
    if biz:
        expl = biz.get("explanations") or {}
        if "market_base" in expl:
            market_base = float(expl["market_base"])
        elif biz.get("category"):
            market_base = _MARKET_BASE_BY_CATEGORY.get(biz["category"], 5.2)

    market = min(10.0, market_base)
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
            "signals_text": {
                "market": f"商业赛道：{biz.get('category') if biz else 'N/A'}，市场评分 {round(market, 1)}",
                "traction": f"{stars:,} Stars，近 30 天 {commits_30d} 次提交，牵引力评分 {round(traction, 1)}",
                "moat": f"近 90 天 {contributors_90d} 位活跃贡献者，护城河评分 {round(moat, 1)}",
                "team": (
                    f"近 90 天 {contributors_90d} 位活跃贡献者，多团队规模，单点风险低" if contributors_90d >= 30 else
                    f"近 90 天 {contributors_90d} 位活跃贡献者，中等规模，有外部参与" if contributors_90d >= 10 else
                    f"近 90 天 {contributors_90d} 位活跃贡献者，小团队，主要由核心作者驱动" if contributors_90d >= 3 else
                    f"近 90 天 {contributors_90d} 位活跃贡献者，高度依赖核心作者，单点风险高"
                ),
                "monetization": f"变现路径{'已识别' if biz and biz.get('monetization_candidates') else '待挖掘'}，评分 {monetization}",
                "risk": f"贡献者{'充足（≥5 人）' if contributors_90d >= 5 else '不足（<5 人）'}，风险评分 {risk}",
            },
        },
    }
