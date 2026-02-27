#!/usr/bin/env python3
"""
Trend2Biz 一键端到端 API 脚本
使用 GitHub REST API 采集真实指标

用法:
  python scripts/run_e2e_api.py                     # 无 token，60 req/h 限速
  python scripts/run_e2e_api.py --token ghp_xxx      # 带 token，5000 req/h
  python scripts/run_e2e_api.py --token ghp_xxx --lang python --since weekly

流程:
  1. 启动 uvicorn（后台）
  2. POST /trending/snapshots:fetch  → 抓 GitHub Trending
  3. 等 job succeeded
  4. GET  /projects                  → 取第一个 project_id
  5. POST /projects/{id}/metrics:refresh → GitHub REST API 拉指标
  6. 等 job succeeded
  7. POST /projects/{id}/biz-profiles:generate → 生成商业分析 + 评分
  8. 等 job succeeded
  9. GET  /projects/{id}             → 打印最终结果
  10. 关闭 server
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import pathlib

# 确保 project root 在 path 里
ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import httpx

BASE = "http://127.0.0.1:8765/api/v1"
HEALTH = "http://127.0.0.1:8765"
SERVER_PORT = 8765
POLL_INTERVAL = 1.5   # 秒
POLL_TIMEOUT = 120    # 最多等 2 分钟

SEP = "━" * 62


def step(n: int, title: str) -> None:
    print(f"\n{SEP}")
    print(f"  步骤 {n}: {title}")
    print(SEP)


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
    """Start uvicorn in background, return the process."""
    cmd = [
        sys.executable, "-m", "uvicorn",
        "app.main:app",
        "--port", str(SERVER_PORT),
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


def wait_for_server(proc: subprocess.Popen, timeout: int = 20) -> None:
    """Poll /ping until server is up or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read().decode(errors="replace") if proc.stdout else ""
            fail(f"Server process exited early:\n{output}")
        try:
            r = httpx.get(f"{HEALTH}/ping", timeout=2)
            if r.status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.5)
    fail("Server did not start within timeout", proc)


# ---------------------------------------------------------------------------
# DB migration (idempotent)
# ---------------------------------------------------------------------------

def ensure_db_schema() -> None:
    """Add missing columns to existing SQLite DB without losing data."""
    import sqlite3
    db_path = ROOT / "trend2biz.db"
    if not db_path.exists():
        return  # fresh DB — create_all will handle it
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(jobs)")
    cols = {r[1] for r in cur.fetchall()}
    migrations = [
        ("retry_count", "ALTER TABLE jobs ADD COLUMN retry_count INTEGER DEFAULT 0"),
        ("max_retries",  "ALTER TABLE jobs ADD COLUMN max_retries INTEGER DEFAULT 3"),
    ]
    for col, sql in migrations:
        if col not in cols:
            cur.execute(sql)
            print(f"  DB 迁移: jobs.{col} 已补充")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Job polling
# ---------------------------------------------------------------------------

def poll_job(job_id: str, proc: subprocess.Popen, label: str) -> dict:
    """Poll GET /jobs/{job_id} until status is succeeded or failed."""
    deadline = time.time() + POLL_TIMEOUT
    dots = 0
    while time.time() < deadline:
        r = httpx.get(f"{BASE}/jobs/{job_id}", timeout=10)
        if r.status_code != 200:
            fail(f"Job poll error {r.status_code}: {r.text}", proc)
        job = r.json()
        status = job.get("status")
        if status == "succeeded":
            print()  # newline after dots
            return job
        if status == "failed":
            print()
            fail(f"{label} job failed: {job.get('error', '(no detail)')}", proc)
        # still running
        print(".", end="", flush=True)
        dots += 1
        if dots % 40 == 0:
            print(f" [{status}]")
        time.sleep(POLL_INTERVAL)
    fail(f"{label} job timed out after {POLL_TIMEOUT}s", proc)


# ---------------------------------------------------------------------------
# Pretty print
# ---------------------------------------------------------------------------

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
    parser.add_argument("--since", default="daily", choices=["daily", "weekly", "monthly"], help="时间窗口")
    parser.add_argument("--top", type=int, default=1, help="对前 N 个项目拉指标+分析，默认 1")
    args = parser.parse_args()

    # 优先级: --token 参数 > 环境变量 > .env 文件
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

    # ── Step 1: 启动服务 ──────────────────────────────────────────────────
    step(1, "启动 API 服务器")
    ensure_db_schema()
    proc = start_server(env)
    wait_for_server(proc)
    ok(f"Server 就绪  http://127.0.0.1:{SERVER_PORT}")

    try:
        # ── Step 2: 抓 Trending ──────────────────────────────────────────
        step(2, f"抓取 GitHub Trending（{args.since} / {args.lang}）")
        r = httpx.post(
            f"{BASE}/trending/snapshots:fetch",
            json={"since": args.since, "language": args.lang},
            timeout=15,
        )
        if r.status_code != 202:
            fail(f"fetch snapshot 失败 {r.status_code}: {r.text}", proc)
        job_id = r.json()["job_id"]
        ok(f"job_id = {job_id}，等待完成...")
        poll_job(job_id, proc, "trending_fetch")
        ok("Trending 抓取完成")

        # ── Step 3: 取项目列表 ──────────────────────────────────────────
        step(3, "获取已入库项目列表")
        r = httpx.get(f"{BASE}/projects", timeout=10)
        if r.status_code != 200:
            fail(f"list projects 失败: {r.text}", proc)
        items = r.json().get("items", [])
        if not items:
            fail("没有入库的项目，请检查 Trending 解析结果", proc)
        ok(f"共 {len(items)} 个项目")
        top_items = items[: args.top]
        for i, it in enumerate(top_items, 1):
            print(f"     #{i}  {it['repo_full_name']}")

        # ── Steps 4–6: 对每个项目跑完整链路 ─────────────────────────────
        for idx, item in enumerate(top_items, 1):
            pid = item["project_id"]
            repo = item["repo_full_name"]

            # Step 4: metrics:refresh
            step(4 + (idx - 1) * 3, f"GitHub REST API 拉指标：{repo}")
            r = httpx.post(f"{BASE}/projects/{pid}/metrics:refresh", timeout=15)
            if r.status_code != 202:
                fail(f"metrics refresh 失败: {r.text}", proc)
            job_id = r.json()["job_id"]
            ok(f"job_id = {job_id}，等待完成...")
            poll_job(job_id, proc, "metrics_refresh")
            ok("指标采集完成")

            # Step 5: biz-profiles:generate
            step(5 + (idx - 1) * 3, f"生成商业分析 + 评分：{repo}")
            r = httpx.post(
                f"{BASE}/projects/{pid}/biz-profiles:generate",
                json={"model": "rule-v1"},
                timeout=15,
            )
            if r.status_code != 202:
                fail(f"biz generate 失败: {r.text}", proc)
            job_id = r.json()["job_id"]
            ok(f"job_id = {job_id}，等待完成...")
            poll_job(job_id, proc, "biz_generate")
            ok("分析完成")

            # Step 6: 打印结果
            step(6 + (idx - 1) * 3, f"最终结果：{repo}")
            r = httpx.get(f"{BASE}/projects/{pid}", timeout=10)
            if r.status_code != 200:
                fail(f"project detail 失败: {r.text}", proc)
            print_project(r.json())

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
