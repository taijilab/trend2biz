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
    bus_factor = metrics.get("bus_factor_top1_share") or 0.0
    license_spdx = (metrics.get("license_spdx") or "").upper()

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

    # team — penalize high bus_factor (single-maintainer concentration)
    team = min(10.0, 4.0 + contributors_90d / 30)
    if bus_factor > 0.7:
        team = max(1.0, team - 1.0)
    elif bus_factor > 0.5:
        team = max(1.0, team - 0.5)

    # monetization — 4-tier based on candidate type (substring match for zh/en mixed strings)
    _commercial_kws = ("saas", "cloud", "enterprise", "api", "企业版", "付费", "订阅", "商业")
    if biz and biz.get("monetization_candidates"):
        mc = biz["monetization_candidates"]
        if any(kw in m.lower() for m in mc for kw in _commercial_kws):
            monetization = 7.5  # clear commercial path
        else:
            monetization = 6.5  # candidates exist but non-mainstream
    else:
        monetization = 5.0  # no monetization signal
    # bonus if ARR data exists
    if biz:
        _biz_expl = biz.get("explanations") or {}
        if isinstance(_biz_expl.get("revenue_info"), dict) and _biz_expl["revenue_info"].get("arr"):
            monetization = min(10.0, monetization + 1.5)

    # risk — base on contributors, then adjust for license and bus_factor
    risk = 7.0 if contributors_90d >= 5 else 5.5
    if "AGPL" in license_spdx or "GPL" in license_spdx:
        risk -= 0.5   # copyleft reduces commercial risk score
    if "BUSL" in license_spdx or "SSPL" in license_spdx:
        risk -= 1.0   # source-available / commercial restriction
    if "MIT" in license_spdx or "APACHE" in license_spdx:
        risk += 0.3   # permissive license bonus
    if bus_factor > 0.8:
        risk -= 1.0   # extreme single-maintainer risk
    elif bus_factor > 0.5:
        risk -= 0.5   # moderate single-maintainer risk
    risk = max(1.0, min(10.0, risk))

    # hype score — based on star growth acceleration
    hype_score: Optional[float] = None
    star_history = metrics.get("star_history") or []
    if len(star_history) >= 3:
        # star_history: list of (date_str, stars_int) sorted ascending
        try:
            recent_growth = (star_history[-1][1] - star_history[-2][1]) / max(star_history[-2][1], 1)
            prev_growth   = (star_history[-2][1] - star_history[-3][1]) / max(star_history[-3][1], 1)
            acceleration  = recent_growth - prev_growth
            hype_score = min(10.0, max(1.0, 5.0 + acceleration * 20 + stars / 20000))
        except Exception:
            pass
    if hype_score is None:
        # fallback: pure absolute star volume
        hype_score = min(10.0, max(1.0, 4.5 + stars / 20000))

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
            "hype_score": round(hype_score, 2),
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
                "monetization": f"变现路径{'已识别' if biz and biz.get('monetization_candidates') else '待挖掘'}，评分 {round(monetization, 1)}",
                "risk": f"贡献者{'充足（≥5 人）' if contributors_90d >= 5 else '不足（<5 人）'}，License:{license_spdx or '未知'}，风险评分 {round(risk, 1)}",
                "hype": f"Hype 评分 {round(hype_score, 1)}（{'基于增长加速度' if len(star_history) >= 3 else '基于绝对星数'}）",
            },
        },
    }


# ---------------------------------------------------------------------------
# V5 Decision Engine
# ---------------------------------------------------------------------------

def _grade_v5(total: float) -> str:
    if total >= 9.0:
        return "S"
    if total >= 7.5:
        return "A"
    if total >= 6.0:
        return "B"
    if total >= 4.0:
        return "C"
    return "D"


# Structural score baselines by category (tech moat / platform potential heuristic)
_STRUCTURAL_BY_CATEGORY: dict[str, float] = {
    "agent":           7.5,
    "security":        7.0,
    "fintech":         6.8,
    "data-platform":   7.0,
    "infra":           7.2,
    "devops":          6.5,
    "devtools":        6.0,
    "developer-tools": 5.8,
    "observability":   6.5,
    "low-code":        6.0,
    "enterprise-saas": 6.2,
    "edu-tech":        5.5,
    "media-tech":      5.5,
    "biotech":         7.0,
    "robotics-iot":    7.0,
}


def _maintainer_risk_matrix(
    bus_factor: float,
    contributors_90d: int,
    license_spdx: str,
    owner_type: Optional[str],
    category: Optional[str],
) -> dict:
    """Compute 6-dimension Maintainer Risk Matrix.

    Each dimension returns a dict with 'level' ('high'|'medium'|'low') and 'note'.
    """
    # 1. Concentration risk (top-1 contributor share)
    if bus_factor > 0.8:
        concentration = {"level": "high", "note": f"顶级贡献者占 {bus_factor:.0%}，极高单点风险"}
    elif bus_factor > 0.5:
        concentration = {"level": "medium", "note": f"顶级贡献者占 {bus_factor:.0%}，中等集中度"}
    else:
        concentration = {"level": "low", "note": f"贡献者分布相对分散（top-1 占 {bus_factor:.0%}）"}

    # 2. Founder dependency (solo user owner + few contributors)
    if owner_type == "User" and contributors_90d < 5:
        founder_dep = {"level": "high", "note": "个人仓库 + 活跃贡献者 <5，创始人依赖度极高"}
    elif owner_type == "User":
        founder_dep = {"level": "medium", "note": "个人仓库，但有一定外部贡献者参与"}
    else:
        founder_dep = {"level": "low", "note": "组织仓库，创始人个人依赖度相对较低"}

    # 3. API/Upstream dependency (category heuristic)
    _infra_categories = {"infra", "devops", "observability", "data-platform"}
    if category in _infra_categories:
        api_upstream = {"level": "medium", "note": f"{category} 项目通常有较强的上游云厂商 API 依赖"}
    elif category == "agent":
        api_upstream = {"level": "high", "note": "Agent 项目高度依赖底层 LLM API，上游变动风险高"}
    else:
        api_upstream = {"level": "low", "note": "上游 API 依赖风险较低"}

    # 4. Company concentration (org with few contributors)
    if owner_type == "Organization" and contributors_90d < 10:
        company_conc = {"level": "high", "note": "组织仓库但活跃贡献者 <10，公司内部项目风险"}
    elif owner_type == "Organization" and contributors_90d < 30:
        company_conc = {"level": "medium", "note": "组织仓库，贡献者规模中等"}
    else:
        company_conc = {"level": "low", "note": "贡献者社区规模充足，公司集中度风险低"}

    # 5. License risk
    _lic = license_spdx.upper()
    if "BUSL" in _lic or "SSPL" in _lic or "COMMONS" in _lic:
        license_risk = {"level": "high", "note": f"License {license_spdx} 含商业限制条款"}
    elif "AGPL" in _lic:
        license_risk = {"level": "medium", "note": "AGPL 强传染性，企业商用需谨慎评估"}
    elif "GPL" in _lic and "LGPL" not in _lic:
        license_risk = {"level": "medium", "note": "GPL 传染性，混合使用需隔离"}
    elif license_spdx in ("", "NOASSERTION", "OTHER"):
        license_risk = {"level": "medium", "note": "License 未明确，商用合规需独立核查"}
    else:
        license_risk = {"level": "low", "note": f"License {license_spdx or '宽松'} 商用友好"}

    # 6. Community depth
    if contributors_90d >= 30:
        community_depth = {"level": "low", "note": f"近 90 天 {contributors_90d} 位活跃贡献者，社区深度充足"}
    elif contributors_90d >= 10:
        community_depth = {"level": "medium", "note": f"近 90 天 {contributors_90d} 位活跃贡献者，社区初具规模"}
    else:
        community_depth = {"level": "high", "note": f"近 90 天仅 {contributors_90d} 位活跃贡献者，社区深度不足"}

    return {
        "concentration": concentration,
        "founder_dependency": founder_dep,
        "api_upstream": api_upstream,
        "company_concentration": company_conc,
        "license_risk": license_risk,
        "community_depth": community_depth,
    }


def _investment_stage(stars: int, contributors_90d: int, commits_30d: int, biz: Optional[dict]) -> str:
    """Classify project into investment stage."""
    has_arr = False
    has_commercial = False
    if biz:
        expl = biz.get("explanations") or {}
        if isinstance(expl.get("revenue_info"), dict) and expl["revenue_info"].get("arr"):
            has_arr = True
        mc = biz.get("monetization_candidates") or []
        _kws = ("saas", "cloud", "enterprise", "api", "企业版", "付费", "订阅", "商业")
        if any(kw in m.lower() for m in mc for kw in _kws):
            has_commercial = True

    if has_arr and stars > 5000:
        return "Scaling"
    if has_commercial:
        return "Commercializing"
    if stars >= 2000 or (stars >= 500 and commits_30d > 50):
        return "PMF Signal"
    if stars >= 100 and contributors_90d >= 2:
        return "Early OSS"
    return "Idea"


def _investment_window(stage: str) -> str:
    return {
        "Idea":           "观察",
        "Early OSS":      "Pre-seed",
        "PMF Signal":     "Seed",
        "Commercializing": "Series A",
        "Scaling":        "战略投资",
    }.get(stage, "观察")


def compute_score_v5(metrics: dict, biz: Optional[dict]) -> dict:
    """V5 Decision Engine: 4-layer scoring H×0.30 + C×0.30 + S×0.30 + M×0.10."""
    stars = metrics.get("stars") or 0
    commits_30d = metrics.get("commits_30d") or 0
    contributors_90d = metrics.get("contributors_90d") or 0
    open_issues = metrics.get("open_issues") or 0
    prs_30d = metrics.get("prs_30d") or 0
    issues_30d = metrics.get("issues_30d") or 0
    bus_factor = metrics.get("bus_factor_top1_share") or 0.0
    license_spdx = (metrics.get("license_spdx") or "")
    owner_type = metrics.get("owner_type")
    star_history = metrics.get("star_history") or []

    category = None
    biz_expl: dict = {}
    if biz:
        category = biz.get("category")
        biz_expl = biz.get("explanations") or {}

    # ── Health Score (30%) ───────────────────────────────────────────────────
    # Sub-dim 1: issue response rate (lower open_issues/issues_30d ratio = better)
    if issues_30d > 0:
        backlog_ratio = open_issues / (issues_30d * 3)  # 3-month expected backlog
        issue_response = min(10.0, max(1.0, 10.0 - backlog_ratio * 3))
    else:
        issue_response = 5.0  # no data — neutral

    # Sub-dim 2: PR merge activity
    if prs_30d > 0:
        pr_merge = min(10.0, max(1.0, 4.0 + prs_30d / 5))
    else:
        pr_merge = max(1.0, min(7.0, 3.0 + commits_30d / 50))  # fallback to commits

    # Sub-dim 3: commit velocity
    commit_velocity = min(10.0, max(1.0, 4.0 + commits_30d / 60))

    # Sub-dim 4: bus factor (inverted — high concentration → low health)
    bus_factor_inv = min(10.0, max(1.0, 10.0 - bus_factor * 5))

    health = (issue_response * 0.25 + pr_merge * 0.25 + commit_velocity * 0.25 + bus_factor_inv * 0.25)

    # ── Commercial Score (30%) ───────────────────────────────────────────────
    _commercial_kws = ("saas", "cloud", "enterprise", "api", "企业版", "付费", "订阅", "商业")
    if biz and biz.get("monetization_candidates"):
        mc = biz["monetization_candidates"]
        if any(kw in m.lower() for m in mc for kw in _commercial_kws):
            commercial = 7.5
        else:
            commercial = 6.5
    else:
        commercial = 5.0
    if isinstance(biz_expl.get("revenue_info"), dict) and biz_expl["revenue_info"].get("arr"):
        commercial = min(10.0, commercial + 1.5)

    # ── Structural Score (30%) ───────────────────────────────────────────────
    v5_struct = biz_expl.get("v5_structural") or {}
    if v5_struct and isinstance(v5_struct.get("score"), (int, float)):
        structural = float(v5_struct["score"])
    else:
        structural = _STRUCTURAL_BY_CATEGORY.get(category or "", 5.5)

    # ── Momentum Score (10%) ─────────────────────────────────────────────────
    if len(star_history) >= 3:
        try:
            recent_growth = (star_history[-1][1] - star_history[-2][1]) / max(star_history[-2][1], 1)
            prev_growth = (star_history[-2][1] - star_history[-3][1]) / max(star_history[-3][1], 1)
            acceleration = recent_growth - prev_growth
            momentum = min(10.0, max(1.0, 5.0 + acceleration * 20 + stars / 20000))
        except Exception:
            momentum = min(10.0, max(1.0, 4.5 + stars / 20000))
    else:
        momentum = min(10.0, max(1.0, 4.5 + stars / 20000))

    total = health * 0.30 + commercial * 0.30 + structural * 0.30 + momentum * 0.10
    grade = _grade_v5(total)

    # ── Maintainer Risk Matrix ────────────────────────────────────────────────
    maintainer_risk = _maintainer_risk_matrix(
        bus_factor, contributors_90d, license_spdx, owner_type, category
    )

    # ── Investment Stage / Window ─────────────────────────────────────────────
    stage = _investment_stage(stars, contributors_90d, commits_30d, biz)
    investment_window = _investment_window(stage)

    return {
        "market_score": round(commercial, 2),    # repurposed: commercial → market_score column
        "traction_score": round(health, 2),       # repurposed: health → traction_score column
        "moat_score": round(structural, 2),       # repurposed: structural → moat_score column
        "team_score": round(momentum, 2),
        "monetization_score": round(commercial, 2),
        "risk_score": round(health, 2),
        "total_score": round(total, 2),
        "grade": grade,
        "model_name": "v5-decision-engine",
        "highlights": _build_highlights(commercial, health, structural, commercial, biz),
        "risks": _build_risks(health, structural, contributors_90d, stars, biz),
        "followups": _build_followups(biz, grade),
        "explanations": {
            "model": "v5-decision-engine",
            "health_score": round(health, 2),
            "commercial_score": round(commercial, 2),
            "structural_score": round(structural, 2),
            "momentum_score": round(momentum, 2),
            "hype_score": round(momentum, 2),
            "stage": stage,
            "investment_window": investment_window,
            "maintainer_risk": maintainer_risk,
            "v5_structural": v5_struct or None,
            "weights": {
                "health": 0.30,
                "commercial": 0.30,
                "structural": 0.30,
                "momentum": 0.10,
            },
            "signals": {
                "stars": stars,
                "commits_30d": commits_30d,
                "contributors_90d": contributors_90d,
                "prs_30d": prs_30d,
                "issues_30d": issues_30d,
                "category": category,
            },
            "signals_text": {
                "health": f"Health {round(health, 1)}/10 — issue_resp:{round(issue_response,1)} pr_merge:{round(pr_merge,1)} velocity:{round(commit_velocity,1)} bus_factor_inv:{round(bus_factor_inv,1)}",
                "commercial": f"Commercial {round(commercial, 1)}/10 — {'明确商业路径' if commercial >= 7.5 else '候选路径' if commercial >= 6.5 else '待挖掘'}",
                "structural": f"Structural {round(structural, 1)}/10 — {'LLM 评估' if v5_struct else '赛道基线估算'}（{category or '未知赛道'}）",
                "momentum": f"Momentum {round(momentum, 1)}/10 — {stars:,} Stars，{'加速增长' if len(star_history) >= 3 else '绝对量估算'}",
            },
        },
    }
