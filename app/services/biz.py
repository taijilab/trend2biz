from __future__ import annotations

from typing import Optional


def infer_biz_profile(repo_name: str, description: Optional[str], language: Optional[str]) -> dict:
    text = f"{repo_name} {description or ''}".lower()

    category = "developer-tools"
    scenarios = ["研发效率"]
    persona = "Dev"
    monetization = ["Open-core", "SaaS"]
    buyer = "研发负责人"

    if any(k in text for k in ["agent", "rag", "llm", "ai"]):
        category = "agent"
        scenarios = ["智能自动化", "内容生产"]
        persona = "Data"
        buyer = "数据负责人"
    elif any(k in text for k in ["observability", "trace", "monitor"]):
        category = "observability"
        scenarios = ["可观测性"]
        persona = "Infra"
        buyer = "平台负责人"
    elif any(k in text for k in ["security", "vuln", "auth", "siem"]):
        category = "security"
        scenarios = ["安全防护", "风险检测"]
        persona = "Sec"
        buyer = "安全负责人"

    delivery_forms = ["library" if (language and language.lower() in {"python", "rust", "go", "typescript"}) else "server"]

    return {
        "category": category,
        "user_persona": persona,
        "scenarios": scenarios,
        "value_props": ["效率", "降本"],
        "delivery_forms": delivery_forms,
        "monetization_candidates": monetization,
        "buyer": buyer,
        "sales_motion": "PLG",
        "confidence": 0.65,
        "explanations": {"method": "rule-v1", "signals": [repo_name, description or ""]},
    }
