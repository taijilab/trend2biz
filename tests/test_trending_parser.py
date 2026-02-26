"""
Trending HTML 解析器回归测试（来自 spec 的 8 条断言）
所有测试基于 fixture HTML，不依赖网络。
"""
from __future__ import annotations

import re

import pytest

from app.services.trending import TrendingItemParsed, parse_trending_html


@pytest.fixture(scope="module")
def parsed_items(trending_fixture_html) -> list[TrendingItemParsed]:
    return parse_trending_html(trending_fixture_html)


# 断言 1: 能解析到内容
def test_items_count(parsed_items):
    assert len(parsed_items) >= 1, "至少应解析到 1 个 repo"


# 断言 2: rank 从 1 开始连续
def test_rank_sequential(parsed_items):
    ranks = [item.rank for item in parsed_items]
    assert ranks[0] == 1, "第一条 rank 应为 1"
    for i, rank in enumerate(ranks):
        assert rank == i + 1, f"rank 应连续，位置 {i} 期望 {i+1}，实际 {rank}"


# 断言 3: repo_full_name 满足 owner/name 格式
def test_repo_full_name_format(parsed_items):
    pattern = re.compile(r"^[\w.\-]+/[\w.\-]+$")
    for item in parsed_items:
        assert pattern.match(item.repo_full_name), (
            f"repo_full_name '{item.repo_full_name}' 不满足 owner/name 格式"
        )


# 断言 4: repo_url 以 https://github.com/ 开头
def test_repo_url_prefix(parsed_items):
    for item in parsed_items:
        assert item.repo_url.startswith("https://github.com/"), (
            f"repo_url '{item.repo_url}' 应以 https://github.com/ 开头"
        )


# 断言 5: stars_delta_window 为 int 或 None，不是字符串
def test_stars_delta_type(parsed_items):
    for item in parsed_items:
        assert item.stars_delta_window is None or isinstance(item.stars_delta_window, int), (
            f"stars_delta_window 应为 int 或 None，实际为 {type(item.stars_delta_window)}"
        )


# 断言 6: description 字段存在（可为 None 但不应缺失）
def test_description_field_exists(parsed_items):
    for item in parsed_items:
        assert hasattr(item, "description"), "item 应包含 description 字段"


# 断言 7: primary_language 字段存在（可为 None 但不应缺失）
def test_primary_language_field_exists(parsed_items):
    for item in parsed_items:
        assert hasattr(item, "primary_language"), "item 应包含 primary_language 字段"


# 断言 8: fixture 数据语义一致性检查（固定输入 → 固定输出）
def test_fixture_semantic_consistency(parsed_items):
    """验证 fixture HTML 的解析结果符合预期（黄金样本测试）。"""
    # fixture 中有 4 个 repo
    assert len(parsed_items) == 4

    # 第一个 repo
    first = parsed_items[0]
    assert first.repo_full_name == "openai/gpt-researcher"
    assert first.repo_url == "https://github.com/openai/gpt-researcher"
    assert first.primary_language == "Python"
    assert isinstance(first.stars_delta_window, int)
    assert first.stars_delta_window > 0

    # 第二个 repo：无 stars delta
    second = parsed_items[1]
    assert second.repo_full_name == "rustlang/rustlings"
    assert second.primary_language == "Rust"
    assert second.stars_delta_window is None

    # 第三个 repo：无 language，有 stars delta
    third = parsed_items[2]
    assert third.repo_full_name == "awesome-llm/awesome-llm-apps"
    assert third.primary_language is None
    assert isinstance(third.stars_delta_window, int)

    # 第四个 repo：无 description
    fourth = parsed_items[3]
    assert fourth.repo_full_name == "vercel/next.js"
    assert fourth.primary_language == "TypeScript"
    assert fourth.description is None


# 额外：stars_total_hint 和 forks_total_hint 类型检查
def test_stars_and_forks_hint_types(parsed_items):
    for item in parsed_items:
        if item.stars_total_hint is not None:
            assert isinstance(item.stars_total_hint, int)
        if item.forks_total_hint is not None:
            assert isinstance(item.forks_total_hint, int)
