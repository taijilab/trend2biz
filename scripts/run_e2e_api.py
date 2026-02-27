#!/usr/bin/env python3
"""
Trend2Biz 一键端到端 API 脚本
使用 GitHub REST API 采集真实指标

用法:
  python scripts/run_e2e_api.py                     # 交互式：先看榜单，再选分析
  python scripts/run_e2e_api.py --top 3             # 直接分析前 3 名，不询问
  python scripts/run_e2e_api.py --lang python --since weekly

两阶段流程:
  阶段一  快速列出今日 Trending 完整榜单（秒出）
  阶段二  选择哪些项目拉 GitHub 指标 + 商业评分
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import httpx

BASE = "http://127.0.0.1:8765/api/v1"
HEALTH = "http://127.0.0.1:8765"
SERVER_PORT = 8765
POLL_INTERVAL = 1.5
POLL_TIMEOUT = 120

SEP = "━" * 62
LANG_ICON = {
    "Python": "🐍", "TypeScript": "🟦", "JavaScript": "🟨",
    "Go": "🐹", "Rust": "🦀", "Java": "☕", "C++": "⚙️",
    "C": "⚙️", "Ruby": "💎", "Swift": "🍎", "Kotlin": "🟪",
}


def step(n: int, title: str) -> None:
    print(f"\n{SEP}\n  步骤 {n}: {title}\n{SEP}")


def ok(msg: str) -> None:
    print(f"  ✅  {msg}")


def warn(msg: str) -> None:
    print(f"  ⚠️   {msg}")


def fail(msg: str, proc=None) -> None:
    print(f"\n  ❌  {msg}\n")
    if proc:
        proc.terminate()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_server(env: dict) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--port", str(SERVER_PORT), "--log-level", "warning"]
    return subprocess.Popen(
        cmd, cwd=str(ROOT), env={**os.environ, **env},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def wait_for_server(proc: subprocess.Popen, timeout: int = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            fail(f"Server exited early:\n{out}")
        try:
            if httpx.get(f"{HEALTH}/ping", timeout=2).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.5)
    fail("Server did not start within timeout", proc)


# ---------------------------------------------------------------------------
# DB migration (idempotent)
# ---------------------------------------------------------------------------

def ensure_db_schema() -> None:
    import sqlite3
    db_path = ROOT / "trend2biz.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(jobs)")
    cols = {r[1] for r in cur.fetchall()}
    for col, sql in [
        ("retry_count", "ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0"),
        ("max_retries",  "ALTER TABLE jobs ADD COLUMN max_retries INTEGER DEFAULT 3"),
    ]:
        if col not in cols:
            cur.execute(sql)
            print(f"  DB 迁移: jobs.{col} 已补充")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Job polling
# ---------------------------------------------------------------------------

def poll_job(job_id: str, proc: subprocess.Popen, label: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT
    dots = 0
    while time.time() < deadline:
        r = httpx.get(f"{BASE}/jobs/{job_id}", timeout=10)
        if r.status_code != 200:
            fail(f"Job poll error {r.status_code}: {r.text}", proc)
        job = r.json()
        status = job.get("status")
        if status == "succeeded":
            print()
            return job
        if status == "failed":
            print()
            fail(f"{label} job failed: {job.get('error', '(no detail)')}", proc)
        print(".", end="", flush=True)
        dots += 1
        if dots % 40 == 0:
            print(f" [{status}]")
        time.sleep(POLL_INTERVAL)
    fail(f"{label} job timed out after {POLL_TIMEOUT}s", proc)


# ---------------------------------------------------------------------------
# Phase 1: print trending list
# ---------------------------------------------------------------------------

def print_trending_list(items: list[dict], snapshot_items: list[dict]) -> None:
    """Print ranked trending list with stars delta."""
    # Build rank→stars_delta map from snapshot
    delta_map: dict[str, int | None] = {
        si["repo_full_name"]: si.get("stars_delta_window")
        for si in snapshot_items
    }
    rank_map: dict[str, int] = {
        si["repo_full_name"]: si.get("rank", 0)
        for si in snapshot_items
    }

    # Sort items by rank from snapshot
    sorted_items = sorted(items, key=lambda x: rank_map.get(x["repo_full_name"], 999))

    print(f"\n  {'排名':<4}  {'项目':<42}  {'语言':<12}  {'今日新增⭐'}")
    print(f"  {'─'*4}  {'─'*42}  {'─'*12}  {'─'*10}")
    for item in sorted_items:
        repo = item["repo_full_name"]
        rank = rank_map.get(repo, "?")
        lang = item.get("primary_language") or "—"
        icon = LANG_ICON.get(lang, "  ")
        delta = delta_map.get(repo)
        delta_str = f"+{delta}" if delta else "—"
        print(f"  #{rank:<3}  {repo:<42}  {icon} {lang:<10}  {delta_str}")


# ---------------------------------------------------------------------------
# Phase 2: parse user selection
# ---------------------------------------------------------------------------

def parse_selection(raw: str, max_rank: int) -> list[int]:
    """Parse '1,3,5' or '1-5' or 'all' into a list of ranks."""
    raw = raw.strip().lower()
    if not raw or raw == "all":
        return list(range(1, max_rank + 1))
    ranks = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                ranks.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        else:
            try:
                ranks.add(int(part))
            except ValueError:
                pass
    return sorted(r for r in ranks if 1 <= r <= max_rank)


# ---------------------------------------------------------------------------
# Phase 2: analyze selected projects
# ---------------------------------------------------------------------------

def analyze_project(pid: str, repo: str, proc: subprocess.Popen, step_base: int) -> None:
    step(step_base, f"GitHub REST API 拉指标：{repo}")
    r = httpx.post(f"{BASE}/projects/{pid}/metrics:refresh", timeout=15)
    if r.status_code != 202:
        fail(f"metrics refresh 失败: {r.text}", proc)
    poll_job(r.json()["job_id"], proc, "metrics_refresh")
    ok("指标采集完成")

    step(step_base + 1, f"生成商业分析 + 评分：{repo}")
    r = httpx.post(f"{BASE}/projects/{pid}/biz-profiles:generate",
                   json={"model": "rule-v1"}, timeout=15)
    if r.status_code != 202:
        fail(f"biz generate 失败: {r.text}", proc)
    poll_job(r.json()["job_id"], proc, "biz_generate")
    ok("分析完成")

    step(step_base + 2, f"最终结果：{repo}")
    r = httpx.get(f"{BASE}/projects/{pid}", timeout=10)
    if r.status_code != 200:
        fail(f"project detail 失败: {r.text}", proc)
    print_project(r.json())


def print_project(data: dict) -> None:
    p = data.get("project", {})
    m = data.get("latest_metrics") or {}
    b = data.get("latest_biz_profile") or {}
    s = data.get("latest_score") or {}

    print(f"\n  {'─'*58}")
    print(f"  📦  {p.get('repo_full_name', 'N/A')}")
    print(f"  🔗  {p.get('repo_url', 'N/A')}")

    if m:
        print(f"\n  📊  GitHub 指标 (as of {m.get('as_of', 'N/A')})")
        print(f"       ⭐  Stars           : {m.get('stars', 'N/A')}")
        print(f"       🍴  Forks           : {m.get('forks', 'N/A')}")
        print(f"       📝  Commits (30d)   : {m.get('commits_30d', 'N/A')}")
        print(f"       👥  Contributors(90d): {m.get('contributors_90d', 'N/A')}")

    if b:
        print(f"\n  💼  商业分析")
        print(f"       分类    : {b.get('category', 'N/A')}")
        print(f"       价值主张 : {', '.join(b.get('value_props') or []) or 'N/A'}")
        print(f"       买家     : {b.get('buyer', 'N/A')}")
        print(f"       变现路径 : {', '.join(b.get('monetization_candidates') or []) or 'N/A'}")

    if s:
        bd = s.get("breakdown", {})
        print(f"\n  🏆  评分  {s.get('total', 'N/A'):.2f}/10  [{s.get('grade', '?')}]")
        for k, v in bd.items():
            filled = round(v or 0)
            bar = "█" * filled + "░" * (10 - filled)
            print(f"       {k:<14} {bar} {v:.2f}")

    print(f"  {'─'*58}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Trend2Biz 一键端到端 API 脚本")
    parser.add_argument("--token", default="", help="GitHub Personal Access Token（可选）")
    parser.add_argument("--lang", default="all", help="语言过滤，默认 all")
    parser.add_argument("--since", default="daily",
                        choices=["daily", "weekly", "monthly"], help="时间窗口")
    parser.add_argument("--top", type=int, default=0,
                        help="直接分析前 N 名，跳过交互（0 = 交互式选择）")
    args = parser.parse_args()

    # 读取 token：--token > 环境变量 > .env
    if not args.token and not os.environ.get("GITHUB_TOKEN"):
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("GITHUB_TOKEN=") and not line.startswith("#"):
                    os.environ["GITHUB_TOKEN"] = line.split("=", 1)[1].strip()
                    break
    token = args.token or os.environ.get("GITHUB_TOKEN", "")
    env = {}
    if token:
        env["GITHUB_TOKEN"] = token
        ok(f"使用 GitHub Token（前 4 位：{token[:4]}****）")
    else:
        warn("未设置 GITHUB_TOKEN，GitHub API 限速 60 req/h")

    # ── 启动服务 ────────────────────────────────────────────────────────────
    step(1, "启动 API 服务器")
    ensure_db_schema()
    proc = start_server(env)
    wait_for_server(proc)
    ok(f"Server 就绪  http://127.0.0.1:{SERVER_PORT}")

    try:
        # ── 阶段一：抓 Trending 榜单 ─────────────────────────────────────
        step(2, f"抓取 GitHub Trending（{args.since} / {args.lang}）")
        r = httpx.post(f"{BASE}/trending/snapshots:fetch",
                       json={"since": args.since, "language": args.lang}, timeout=15)
        if r.status_code != 202:
            fail(f"fetch snapshot 失败 {r.status_code}: {r.text}", proc)
        ok(f"job_id = {r.json()['job_id']}，等待完成...")
        poll_job(r.json()["job_id"], proc, "trending_fetch")
        ok("Trending 抓取完成")

        # 获取快照中的排名数据
        from datetime import date
        snap_r = httpx.get(f"{BASE}/trending/snapshots",
                           params={"since": args.since, "language": args.lang,
                                   "date": str(date.today()), "limit": 50}, timeout=10)
        snapshot_items = snap_r.json().get("items", []) if snap_r.status_code == 200 else []

        # 获取项目列表（含 project_id）
        r = httpx.get(f"{BASE}/projects", params={"limit": 50}, timeout=10)
        if r.status_code != 200:
            fail(f"list projects 失败: {r.text}", proc)
        all_items = r.json().get("items", [])
        if not all_items:
            fail("没有入库的项目，请检查 Trending 解析结果", proc)

        # 构建 rank→project 映射
        rank_map = {si.get("rank"): si["repo_full_name"] for si in snapshot_items}
        name_to_item = {it["repo_full_name"]: it for it in all_items}
        max_rank = max((si.get("rank", 0) for si in snapshot_items), default=len(all_items))

        # ── 显示完整榜单 ────────────────────────────────────────────────
        step(3, f"今日 GitHub Trending 榜单（共 {max_rank} 个）")
        print_trending_list(all_items, snapshot_items)

        # ── 阶段二：选择要深入分析的项目 ──────────────────────────────
        if args.top > 0:
            selected_ranks = list(range(1, min(args.top, max_rank) + 1))
            print(f"\n  → 自动选择前 {args.top} 名")
        else:
            print(f"\n  请输入要分析的排名（例：1  或  1,3,5  或  1-3  或  all）")
            print(f"  直接回车 = 分析第 1 名", end="")
            try:
                raw = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                raw = "1"
            selected_ranks = parse_selection(raw or "1", max_rank)

        if not selected_ranks:
            fail("没有选择任何项目", proc)

        print(f"\n  将分析：{', '.join(f'#{r}' for r in selected_ranks)}")

        # ── 对选中项目跑完整分析 ─────────────────────────────────────
        for seq, rank in enumerate(selected_ranks):
            repo_name = rank_map.get(rank)
            if not repo_name:
                warn(f"排名 #{rank} 未找到对应项目，跳过")
                continue
            item = name_to_item.get(repo_name)
            if not item:
                warn(f"{repo_name} 未在项目库中，跳过")
                continue
            analyze_project(item["project_id"], repo_name, proc,
                            step_base=4 + seq * 3)

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f"\n{'═' * 62}")
    print("  ✅  全流程完成！")
    print(f"{'═' * 62}\n")


if __name__ == "__main__":
    main()
