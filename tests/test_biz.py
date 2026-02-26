"""
商业化推断（biz inference）单元测试
"""
from __future__ import annotations

import pytest

from app.services.biz import infer_biz_profile


REQUIRED_FIELDS = [
    "category", "user_persona", "scenarios", "value_props",
    "delivery_forms", "monetization_candidates", "buyer",
    "sales_motion", "confidence", "explanations",
]


def test_all_required_fields_present():
    result = infer_biz_profile("myrepo", "A simple tool", "Python")
    for field in REQUIRED_FIELDS:
        assert field in result, f"缺少字段: {field}"


def test_agent_keyword_in_name():
    result = infer_biz_profile("llm-agent-framework", "Run AI agents", "Python")
    assert result["category"] == "agent"


def test_agent_keyword_in_description():
    result = infer_biz_profile("myrepo", "Build and deploy RAG pipelines for LLM", "Go")
    assert result["category"] == "agent"


def test_observability_keyword():
    result = infer_biz_profile("otel-collector", "Distributed tracing and monitoring", "Go")
    assert result["category"] == "observability"


def test_security_keyword():
    result = infer_biz_profile("auth-service", "SIEM and vulnerability scanning", "Rust")
    assert result["category"] == "security"


def test_default_category_developer_tools():
    result = infer_biz_profile("my-cli-tool", "A handy command line utility", "Go")
    assert result["category"] == "developer-tools"


def test_confidence_is_float_between_0_and_1():
    result = infer_biz_profile("myrepo", "description", "Python")
    assert isinstance(result["confidence"], float)
    assert 0.0 <= result["confidence"] <= 1.0


def test_monetization_candidates_is_list():
    result = infer_biz_profile("myrepo", "description", "Python")
    assert isinstance(result["monetization_candidates"], list)
    assert len(result["monetization_candidates"]) >= 1


def test_scenarios_is_list():
    result = infer_biz_profile("myrepo", "description", "Python")
    assert isinstance(result["scenarios"], list)


def test_none_description_handled():
    result = infer_biz_profile("myrepo", None, None)
    assert result["category"] in {"agent", "observability", "security", "developer-tools"}


def test_explanations_contains_method():
    result = infer_biz_profile("myrepo", "description", "Python")
    assert "method" in result["explanations"]
