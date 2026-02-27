#!/usr/bin/env python3
"""
Trend2Biz 交互式仪表板

用法:
  python scripts/dashboard.py
  python scripts/dashboard.py --lang python --since weekly

功能:
  - 显示今日 GitHub Trending 完整榜单
  - 已做过商业分析的项目直接显示评分 + 商业分类
  - 用户选择项目，触发 GitHub 指标采集 + 商业评分
  - 分析完成后自动刷新榜单
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import pathlib
from datetime import date
from typing import Optional

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import httpx
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box

BASE = "http://127.0.0.1:8766/api/v1"
HEALTH = "http://127.0.0.1:8766"
SERVER_PORT = 8766
POLL_INTERVAL = 1.0
POLL_TIMEOUT = 120

console = Console(width=max(100, os.get_terminal_size().columns if sys.stdout.isatty() else 110))

LANG_ICON = {
    "Python": "🐍", "TypeScript": "🟦", "JavaScript": "🟨",
    "Go": "🐹", "Rust": "🦀", "Java": "☕", "C++": "⚙️",
    "C": "⚙️", "Ruby": "💎", "Swift": "🍎", "Kotlin": "🟪",
    "Shell": "🐚", "Dockerfile": "🐳", "Jupyter Notebook": "📓",
}

GRADE_COLOR = {"S": "bold magenta", "A": "bold green", "B": "yellow", "C": "dim"}


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def load_token() -> str:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("GITHUB_TOKEN=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return ""


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
    conn.commit()
    conn.close()


def start_server(token: str) -> subprocess.Popen:
    env = {**os.environ}
    if token:
        env["GITHUB_TOKEN"] = token
    cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
           "--port", str(SERVER_PORT), "--log-level", "warning"]
    return subprocess.Popen(
        cmd, cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def wait_for_server(proc: subprocess.Popen, timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(f"{HEALTH}/ping", timeout=2).status_code == 200:
                return True
        except httpx.RequestError:
            pass
        time.sleep(0.4)
    return False


# ---------------------------------------------------------------------------
# Job polling
# ---------------------------------------------------------------------------

def poll_job(job_id: str, label: str) -> bool:
    """Poll until succeeded/failed. Returns True on success."""
    deadline = time.time() + POLL_TIMEOUT
    while time.time() < deadline:
        try:
            r = httpx.get(f"{BASE}/jobs/{job_id}", timeout=10)
            if r.status_code == 200:
                status = r.json().get("status")
                if status == "succeeded":
                    return True
                if status == "failed":
                    return False
        except httpx.RequestError:
            pass
        time.sleep(POLL_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def fetch_or_load_snapshot(since: str, lang: str) -> list[dict]:
    """Return snapshot items for today. Fetch if not cached."""
    today = str(date.today())
    try:
        r = httpx.get(f"{BASE}/trending/snapshots",
                      params={"since": since, "language": lang, "date": today, "limit": 50},
                      timeout=10)
        if r.status_code == 200:
            return r.json().get("items", [])
    except httpx.RequestError:
        pass

    # Not cached — fetch
    with console.status("[bold cyan]正在抓取 GitHub Trending...", spinner="dots"):
        try:
            r = httpx.post(f"{BASE}/trending/snapshots:fetch",
                           json={"since": since, "language": lang}, timeout=15)
            if r.status_code == 202:
                job_id = r.json()["job_id"]
                poll_job(job_id, "trending_fetch")
        except httpx.RequestError:
            return []

    try:
        r = httpx.get(f"{BASE}/trending/snapshots",
                      params={"since": since, "language": lang, "date": today, "limit": 50},
                      timeout=10)
        if r.status_code == 200:
            return r.json().get("items", [])
    except httpx.RequestError:
        pass
    return []


def load_projects() -> dict[str, dict]:
    """Return {repo_full_name: project_item} for all projects."""
    try:
        r = httpx.get(f"{BASE}/projects", params={"limit": 200}, timeout=10)
        if r.status_code == 200:
            return {it["repo_full_name"]: it for it in r.json().get("items", [])}
    except httpx.RequestError:
        pass
    return {}


def load_project_detail(project_id: str) -> Optional[dict]:
    try:
        r = httpx.get(f"{BASE}/projects/{project_id}", timeout=10)
        if r.status_code == 200:
            return r.json()
    except httpx.RequestError:
        pass
    return None


def build_rows(snapshot_items: list[dict], projects: dict[str, dict]) -> list[dict]:
    """Merge snapshot items with project analysis data."""
    rows = []
    for si in snapshot_items:
        repo = si["repo_full_name"]
        proj = projects.get(repo, {})
        score_info = proj.get("latest_score")   # {"total": float, "grade": str}
        biz_info   = proj.get("latest_biz")      # {"category": str, "monetization_candidates": [...]}
        rows.append({
            "rank":         si.get("rank", 0),
            "repo":         repo,
            "lang":         si.get("primary_language") or "",
            "delta":        si.get("stars_delta_window"),
            "description":  si.get("description") or "",
            "project_id":   proj.get("project_id"),
            "analyzed":     bool(score_info),
            "score":        score_info["total"] if score_info else None,
            "grade":        score_info["grade"] if score_info else None,
            "category":     biz_info["category"] if biz_info else None,
            "monetization": biz_info["monetization_candidates"] if biz_info else [],
        })
    # Sort by rank, unknowns last
    rows.sort(key=lambda x: x["rank"] if x["rank"] else 999)
    return rows


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def build_table(rows: list[dict], since: str, lang: str) -> Table:
    analyzed_count = sum(1 for r in rows if r["analyzed"])
    today = date.today().strftime("%Y-%m-%d")

    title = f"[bold cyan]Trend2Biz[/] · {today}  GitHub Trending  [{since} / {lang}]"
    subtitle = f"[dim]已分析 {analyzed_count}/{len(rows)} 个项目[/]"

    table = Table(
        title=title,
        caption=subtitle,
        box=box.SIMPLE_HEAD,
        border_style="cyan",
        header_style="bold cyan",
        show_lines=False,
        expand=False,
        min_width=88,
    )

    table.add_column("项目",                       width=42, no_wrap=True)
    table.add_column("语言",                       width=12, no_wrap=True)
    table.add_column("⭐今日",   justify="right",  width=7,  no_wrap=True)
    table.add_column("状态",                       width=4,  no_wrap=True)
    table.add_column("评分",     justify="right",  width=8,  no_wrap=True)
    table.add_column("商业分析",                   width=26, no_wrap=True)

    for row in rows:
        rank_str = str(row["rank"]) if row["rank"] else "?"
        repo_str = f'[dim]#{rank_str:>2}[/] {row["repo"]}'

        lang_val = row["lang"]
        icon = LANG_ICON.get(lang_val, "  ")
        lang_str = f"{icon} {lang_val}" if lang_val else "[dim]—[/]"

        delta = row["delta"]
        delta_str = f"[green]+{delta}[/]" if delta else "[dim]—[/]"

        if row["analyzed"]:
            status_str = "[green]✅[/]"
            grade = row["grade"] or "?"
            score_val = row["score"]
            score_str = f"[{GRADE_COLOR.get(grade,'white')}]{score_val:.1f} {grade}[/]"
            cat = row["category"] or "—"
            mono = ", ".join((row["monetization"] or [])[:2]) or "—"
            biz_str = f"[cyan]{cat}[/] [dim]· {mono}[/]"
        else:
            status_str = "[dim]─[/]"
            score_str  = "[dim]待分析[/]"
            biz_str    = ""

        table.add_row(repo_str, lang_str, delta_str,
                      status_str, score_str, biz_str)

    return table


def print_table(rows: list[dict], since: str, lang: str) -> None:
    console.clear()
    console.print()
    console.print(build_table(rows, since, lang))
    console.print()


# ---------------------------------------------------------------------------
# Selection parsing
# ---------------------------------------------------------------------------

def parse_selection(raw: str, max_rank: int) -> list[int]:
    raw = raw.strip().lower()
    if not raw or raw == "all":
        return list(range(1, max_rank + 1))
    ranks: set[int] = set()
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
# Analysis
# ---------------------------------------------------------------------------

def analyze_project(row: dict) -> Optional[dict]:
    """Run metrics refresh + biz generate for a project. Returns updated project detail."""
    pid = row["project_id"]
    repo = row["repo"]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=20),
        console=console,
        transient=True,
    ) as progress:
        # Step 1: metrics
        t1 = progress.add_task(f"[cyan][1/2] {repo}  GitHub 指标采集...", total=None)
        try:
            r = httpx.post(f"{BASE}/projects/{pid}/metrics:refresh", timeout=15)
            if r.status_code == 202:
                poll_job(r.json()["job_id"], "metrics_refresh")
        except httpx.RequestError:
            pass
        progress.update(t1, description=f"[green][1/2] {repo}  指标采集完成 ✅")

        # Step 2: biz + score
        t2 = progress.add_task(f"[cyan][2/2] {repo}  商业分析生成...", total=None)
        try:
            r = httpx.post(f"{BASE}/projects/{pid}/biz-profiles:generate",
                           json={"model": "rule-v1"}, timeout=15)
            if r.status_code == 202:
                poll_job(r.json()["job_id"], "biz_generate")
        except httpx.RequestError:
            pass
        progress.update(t2, description=f"[green][2/2] {repo}  商业分析完成 ✅")

    return load_project_detail(pid)


def update_row(row: dict, detail: dict) -> None:
    """Mutate row in-place with fresh analysis data from project detail."""
    m = detail.get("latest_metrics") or {}
    b = detail.get("latest_biz_profile") or {}
    s = detail.get("latest_score") or {}

    row["analyzed"]     = bool(s)
    row["score"]        = s.get("total")
    row["grade"]        = s.get("grade")
    row["category"]     = b.get("category")
    row["monetization"] = b.get("monetization_candidates") or []


def print_detail(row: dict, detail: dict) -> None:
    """Print a compact detail card after analysis."""
    m = detail.get("latest_metrics") or {}
    b = detail.get("latest_biz_profile") or {}
    s = detail.get("latest_score") or {}
    bd = s.get("breakdown", {})

    grade = s.get("grade", "?")
    total = s.get("total", 0)
    color = GRADE_COLOR.get(grade, "white")

    lines = []
    if m:
        lines.append(
            f"[dim]Stars[/] [yellow]{m.get('stars','—')}[/]  "
            f"[dim]Forks[/] {m.get('forks','—')}  "
            f"[dim]Commits/30d[/] {m.get('commits_30d','—')}  "
            f"[dim]Contributors/90d[/] {m.get('contributors_90d','—')}"
        )
    if b:
        cat   = b.get("category", "—")
        buyer = b.get("buyer", "—")
        mono  = ", ".join(b.get("monetization_candidates") or []) or "—"
        lines.append(f"[cyan]{cat}[/]  买家: {buyer}  变现: [bold]{mono}[/]")
    if bd:
        bar_parts = []
        for k, v in bd.items():
            filled = round(v or 0)
            bar = "█" * filled + "░" * (10 - filled)
            bar_parts.append(f"{k[:4]}: {bar} {v:.1f}")
        lines.append("  ".join(bar_parts[:3]))
        lines.append("  ".join(bar_parts[3:]))

    body = "\n".join(lines)
    console.print(Panel(
        body,
        title=f"[bold]{row['repo']}[/]  [{color}]{total:.2f}/10 [{grade}][/]",
        border_style=color,
        padding=(0, 1),
    ))
    console.print()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Trend2Biz 交互式仪表板")
    parser.add_argument("--lang",  default="all")
    parser.add_argument("--since", default="daily", choices=["daily", "weekly", "monthly"])
    args = parser.parse_args()

    token = load_token()

    # Start server
    console.print("[bold cyan]Trend2Biz[/] 正在启动...", end=" ")
    ensure_db_schema()
    proc = start_server(token)
    if not wait_for_server(proc):
        console.print("[red]✗ 服务器启动失败[/]")
        proc.terminate()
        sys.exit(1)
    console.print("[green]✓[/]")
    if token:
        console.print(f"[dim]GitHub Token: {token[:4]}****[/]")

    try:
        # Load data
        with console.status("[bold cyan]加载今日 Trending 数据...", spinner="dots"):
            snapshot_items = fetch_or_load_snapshot(args.since, args.lang)
            projects = load_projects()

        if not snapshot_items:
            console.print("[red]未获取到 Trending 数据，请检查网络连接[/]")
            return

        rows = build_rows(snapshot_items, projects)
        rank_to_row = {r["rank"]: r for r in rows if r["rank"]}
        max_rank = max(rank_to_row.keys(), default=0)

        # Main interaction loop
        while True:
            print_table(rows, args.since, args.lang)

            analyzed = sum(1 for r in rows if r["analyzed"])
            prompt_parts = [
                f"[bold]输入排名进行商业分析[/]",
                "[dim]（例: 1  或  1,3,5  或  2-5  或  all  或  q 退出）[/]",
            ]
            console.print("  ".join(prompt_parts))

            try:
                raw = input("  > ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if raw.lower() in ("q", "quit", "exit", ""):
                break

            selected_ranks = parse_selection(raw, max_rank)
            if not selected_ranks:
                console.print("[yellow]  未识别的输入，请重试[/]")
                time.sleep(1)
                continue

            # Filter to those that have a project_id (in our DB)
            to_analyze = []
            for rank in selected_ranks:
                row = rank_to_row.get(rank)
                if not row:
                    console.print(f"[dim]  #{rank} 不在榜单中，跳过[/]")
                    continue
                if not row["project_id"]:
                    console.print(f"[dim]  #{rank} {row['repo']} 未入库，跳过[/]")
                    continue
                to_analyze.append(row)

            if not to_analyze:
                continue

            console.print()
            for row in to_analyze:
                console.print(f"[bold]分析 #{row['rank']} {row['repo']}[/]")
                detail = analyze_project(row)
                if detail:
                    update_row(row, detail)
                    print_detail(row, detail)
                else:
                    console.print(f"[red]  ✗ 分析失败[/]")

            console.print("[dim]按 Enter 刷新榜单...[/]", end="")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                break

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    console.print("\n[bold cyan]Trend2Biz[/] 已退出。")


if __name__ == "__main__":
    main()
