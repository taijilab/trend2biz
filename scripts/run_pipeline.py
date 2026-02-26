#!/usr/bin/env python3
"""
Trend2Biz 端到端管道验证脚本

用法:
  python scripts/run_pipeline.py --offline   # 用 fixture HTML，无网络依赖
  python scripts/run_pipeline.py --live      # 真实请求 GitHub（需 GITHUB_TOKEN）

步骤:
  1. 解析 Trending HTML → 打印解析条目数
  2. 对第一个 repo 运行商业化推断 → 打印结果
  3. 对第一个 repo 运行评分 → 打印评分与 grade
  4. 验证所有字段完整性
  5. 打印 ✅ Pipeline OK 或 ❌ FAILED
"""
from __future__ import annotations

import argparse
import pathlib
import sys

# 确保 project root 在 path 里
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.services.biz import infer_biz_profile
from app.services.scoring import compute_score
from app.services.trending import fetch_trending_html, parse_trending_html

FIXTURE_HTML = pathlib.Path(__file__).parent.parent / "tests" / "fixtures" / "trending_daily_all.html"

SEPARATOR = "─" * 60


def step(n: int, title: str) -> None:
    print(f"\n{'━' * 60}")
    print(f"  步骤 {n}: {title}")
    print(f"{'━' * 60}")


def ok(msg: str) -> None:
    print(f"  ✅  {msg}")


def fail(msg: str) -> None:
    print(f"  ❌  {msg}")
    sys.exit(1)


def run_offline() -> None:
    print("\n[Offline 模式] 使用 fixture HTML，无网络请求")

    # Step 1: Parse HTML
    step(1, "解析 Trending HTML")
    if not FIXTURE_HTML.exists():
        fail(f"找不到 fixture 文件: {FIXTURE_HTML}")
    html = FIXTURE_HTML.read_text(encoding="utf-8")
    items = parse_trending_html(html)
    if not items:
        fail("解析结果为空，请检查 HTML fixture 或解析器")
    ok(f"解析到 {len(items)} 个 repo")
    for item in items:
        print(f"     #{item.rank:2d}  {item.repo_full_name:<40}  ⭐ {item.stars_delta_window or 'N/A'} today")

    # Step 2: Biz inference on first repo
    step(2, "商业化推断（第一个 repo）")
    first = items[0]
    biz = infer_biz_profile(first.repo_full_name, first.description, first.primary_language)
    ok(f"category         = {biz['category']}")
    ok(f"user_persona     = {biz['user_persona']}")
    ok(f"monetization     = {biz['monetization_candidates']}")
    ok(f"confidence       = {biz['confidence']}")

    required_biz_fields = ["category", "user_persona", "scenarios", "monetization_candidates", "confidence"]
    for field in required_biz_fields:
        if field not in biz:
            fail(f"biz 缺少字段: {field}")

    # Step 3: Scoring
    step(3, "YC 式评分")
    mock_metrics = {"stars": first.stars_total_hint or 5000, "commits_30d": 80, "contributors_90d": 8}
    score = compute_score(mock_metrics, biz)
    ok(f"total_score      = {score['total_score']}")
    ok(f"grade            = {score['grade']}")
    print(f"\n  分项得分:")
    print(f"    market       = {score['market_score']}")
    print(f"    traction     = {score['traction_score']}")
    print(f"    moat         = {score['moat_score']}")
    print(f"    team         = {score['team_score']}")
    print(f"    monetization = {score['monetization_score']}")
    print(f"    risk         = {score['risk_score']}")
    print(f"\n  亮点:")
    for h in score["highlights"]:
        print(f"    • {h}")
    print(f"\n  风险:")
    for r in score["risks"]:
        print(f"    • {r}")
    print(f"\n  追问清单:")
    for f_ in score["followups"]:
        print(f"    ? {f_}")

    # Step 4: Field completeness check
    step(4, "字段完整性检查")
    if not score["highlights"]:
        fail("highlights 为空（应为数据驱动）")
    if not score["risks"]:
        fail("risks 为空（应为数据驱动）")
    if not score["followups"]:
        fail("followups 为空")
    if score["grade"] not in ("S", "A", "B", "C"):
        fail(f"grade 非法值: {score['grade']}")
    ok("所有字段验证通过")

    print(f"\n{'═' * 60}")
    print("  ✅  Pipeline OK  [offline 模式]")
    print(f"{'═' * 60}\n")


def run_live() -> None:
    import os
    print("\n[Live 模式] 真实请求 GitHub Trending 页面")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("  ⚠️  未设置 GITHUB_TOKEN，API 限速为 60 req/h")

    step(1, "抓取 GitHub Trending (daily, all)")
    try:
        html = fetch_trending_html(since="daily", language="all")
    except Exception as e:
        fail(f"抓取失败: {e}")

    items = parse_trending_html(html)
    if not items:
        fail("解析结果为空，GitHub 页面结构可能已变更，请更新解析器")
    ok(f"解析到 {len(items)} 个 repo")
    for item in items[:5]:
        print(f"     #{item.rank:2d}  {item.repo_full_name:<45}  ⭐ {item.stars_delta_window or 'N/A'} today")
    if len(items) > 5:
        print(f"     ... 还有 {len(items) - 5} 条")

    step(2, "商业化推断（第一个 repo）")
    first = items[0]
    biz = infer_biz_profile(first.repo_full_name, first.description, first.primary_language)
    ok(f"category = {biz['category']},  confidence = {biz['confidence']}")

    step(3, "评分")
    mock_metrics = {"stars": first.stars_total_hint or 5000, "commits_30d": 80, "contributors_90d": 8}
    score = compute_score(mock_metrics, biz)
    ok(f"total_score = {score['total_score']},  grade = {score['grade']}")

    print(f"\n{'═' * 60}")
    print("  ✅  Pipeline OK  [live 模式]")
    print(f"{'═' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trend2Biz 端到端管道验证")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--offline", action="store_true", default=True, help="使用 fixture HTML（默认）")
    group.add_argument("--live", action="store_true", help="真实抓取 GitHub Trending")
    args = parser.parse_args()

    if args.live:
        run_live()
    else:
        run_offline()


if __name__ == "__main__":
    main()
