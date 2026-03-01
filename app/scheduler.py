"""APScheduler-based background scheduler for Trend2Biz.

Scheduled jobs (all UTC):
  00:10 — trending_fetch daily/all
  00:20 — trending_fetch weekly/all  +  monthly/all
  01:00 — metrics_refresh for top-200 projects
  01:30 — biz+score for unanalyzed projects in latest snapshot

Each job is idempotent: skips if a succeeded/queued/running job for the
same parameters already exists for today.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import desc, select

from app.database import SessionLocal
from app.models import BizProfile, Job, Project, RepoMetricDaily, TrendingSnapshot, TrendingSnapshotItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# APScheduler import (graceful fallback if not installed)
# ---------------------------------------------------------------------------

try:
    from apscheduler.executors.pool import ThreadPoolExecutor as _APSThreadPool
    from apscheduler.schedulers.background import BackgroundScheduler

    _HAS_SCHEDULER = True
except ImportError:  # pragma: no cover
    _HAS_SCHEDULER = False
    logger.warning("apscheduler not installed; background scheduling disabled")

_scheduler: Optional[Any] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _job_exists_today(db, job_type: str, match: dict) -> bool:
    """Return True if a non-failed job of the given type with matching payload
    keys already exists for today."""
    today_str = str(date.today())
    rows = db.execute(
        select(Job).where(
            Job.job_type == job_type,
            Job.status.in_(["queued", "running", "succeeded"]),
        )
    ).scalars().all()
    for j in rows:
        p = j.payload or {}
        # must have been created today
        created_str = (j.created_at.date().isoformat() if j.created_at else "")
        if created_str != today_str:
            continue
        if all(p.get(k) == v for k, v in match.items()):
            return True
    return False


def _create_and_run(job_type: str, payload: dict, runner, *runner_args) -> None:
    """Create a Job record and immediately run it (blocking within the scheduler thread)."""
    # Late import to avoid circular dependency at module load time
    from app.main import create_job  # noqa: PLC0415

    with SessionLocal() as db:
        job = create_job(db, job_type, payload)
        db.commit()
        job_id = job.job_id
    runner(job_id, *runner_args)


# ---------------------------------------------------------------------------
# Scheduled work functions
# ---------------------------------------------------------------------------

def _sched_trending_fetch(since: str, language: str) -> None:
    """Fetch trending for one since/language combination, idempotently."""
    from app.main import run_snapshot_fetch_job  # noqa: PLC0415

    today = date.today()
    match = {"since": since, "language": language, "date": str(today)}
    with SessionLocal() as db:
        if _job_exists_today(db, "trending_fetch", match):
            logger.info("scheduler: trending_fetch %s/%s already ran today, skipping", since, language)
            return
    _create_and_run(
        "trending_fetch",
        {"since": since, "language": language, "spoken": None, "date": str(today)},
        run_snapshot_fetch_job,
        since, language, None, today,
    )
    logger.info("scheduler: trending_fetch %s/%s done", since, language)


def _sched_daily_trending() -> None:
    """00:10 UTC — daily/all."""
    try:
        _sched_trending_fetch("daily", "all")
    except Exception as exc:
        logger.error("scheduler: daily trending failed: %s", exc)


def _sched_weekly_monthly_trending() -> None:
    """00:20 UTC — weekly/all + monthly/all."""
    for since in ("weekly", "monthly"):
        try:
            _sched_trending_fetch(since, "all")
        except Exception as exc:
            logger.error("scheduler: %s trending failed: %s", since, exc)


def _sched_metrics_refresh() -> None:
    """01:00 UTC — metrics_refresh for top-200 projects not yet refreshed today."""
    from app.main import run_metrics_refresh_job  # noqa: PLC0415

    today = date.today()
    try:
        with SessionLocal() as db:
            # Top-200 by latest star count; fallback to recently-created projects
            from sqlalchemy import func  # noqa: PLC0415
            subq = (
                select(
                    RepoMetricDaily.project_id,
                    func.max(RepoMetricDaily.stars).label("max_stars"),
                )
                .group_by(RepoMetricDaily.project_id)
                .order_by(desc("max_stars"))
                .limit(200)
                .subquery()
            )
            project_ids: list[str] = db.execute(select(subq.c.project_id)).scalars().all()

            if not project_ids:
                project_ids = db.execute(
                    select(Project.project_id).order_by(desc(Project.created_at)).limit(200)
                ).scalars().all()

            # Skip projects already refreshed today
            already_done: set[str] = set(
                db.execute(
                    select(RepoMetricDaily.project_id).where(
                        RepoMetricDaily.metric_date == today,
                        RepoMetricDaily.project_id.in_(project_ids),
                    )
                ).scalars().all()
            )
            todo = [pid for pid in project_ids if pid not in already_done]

            from app.main import create_job  # noqa: PLC0415
            job_rows: list[tuple[str, str]] = []
            for pid in todo:
                job = create_job(db, "metrics_refresh", {"project_id": pid, "date": str(today)})
                job_rows.append((job.job_id, pid))
            db.commit()

        logger.info("scheduler: metrics_refresh — %d projects to refresh", len(job_rows))
        for job_id, pid in job_rows:
            try:
                run_metrics_refresh_job(job_id, pid, today)
            except Exception as exc:
                logger.error("scheduler: metrics_refresh pid=%s failed: %s", pid, exc)
    except Exception as exc:
        logger.error("scheduler: metrics_refresh batch failed: %s", exc)


def _sched_biz_score() -> None:
    """01:30 UTC — biz+score for unanalyzed projects in latest daily snapshot."""
    from app.main import run_biz_generate_job  # noqa: PLC0415

    try:
        with SessionLocal() as db:
            latest_snap = db.execute(
                select(TrendingSnapshot)
                .where(TrendingSnapshot.since == "daily", TrendingSnapshot.language == "all")
                .order_by(desc(TrendingSnapshot.snapshot_date))
                .limit(1)
            ).scalar_one_or_none()
            if not latest_snap:
                logger.info("scheduler: biz_score — no snapshot found, skipping")
                return

            item_pids: list[str] = db.execute(
                select(TrendingSnapshotItem.project_id).where(
                    TrendingSnapshotItem.snapshot_id == latest_snap.snapshot_id
                )
            ).scalars().all()

            # Find which have no biz profile at all
            has_biz: set[str] = set(
                db.execute(
                    select(BizProfile.project_id).where(BizProfile.project_id.in_(item_pids))
                ).scalars().all()
            )
            todo = [pid for pid in item_pids if pid not in has_biz]

            from app.main import create_job  # noqa: PLC0415
            job_rows: list[tuple[str, str]] = []
            for pid in todo:
                job = create_job(db, "biz_generate", {"project_id": pid, "model": "rule-v1"})
                job_rows.append((job.job_id, pid))
            db.commit()

        logger.info("scheduler: biz_score — %d projects to analyse", len(job_rows))
        for job_id, pid in job_rows:
            try:
                run_biz_generate_job(job_id, pid, "rule-v1")
            except Exception as exc:
                logger.error("scheduler: biz_generate pid=%s failed: %s", pid, exc)
    except Exception as exc:
        logger.error("scheduler: biz_score batch failed: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Create and start the APScheduler instance (called from app startup)."""
    global _scheduler
    if not _HAS_SCHEDULER:
        return

    executors = {"default": _APSThreadPool(max_workers=4)}
    _scheduler = BackgroundScheduler(executors=executors, timezone="UTC")

    _scheduler.add_job(_sched_daily_trending,           "cron", hour=0,  minute=10, id="daily_trending",           replace_existing=True)
    _scheduler.add_job(_sched_weekly_monthly_trending,  "cron", hour=0,  minute=20, id="weekly_monthly_trending",  replace_existing=True)
    _scheduler.add_job(_sched_metrics_refresh,          "cron", hour=1,  minute=0,  id="metrics_refresh",          replace_existing=True)
    _scheduler.add_job(_sched_biz_score,                "cron", hour=1,  minute=30, id="biz_score",                replace_existing=True)

    _scheduler.start()
    logger.info("APScheduler started — 4 cron jobs scheduled (UTC)")


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")


def get_scheduler_status() -> dict:
    """Return scheduler state and next run times."""
    if not _HAS_SCHEDULER or not _scheduler:
        return {"enabled": False, "running": False, "jobs": []}

    jobs_info = []
    for job in _scheduler.get_jobs():
        nxt = job.next_run_time
        jobs_info.append({
            "id": job.id,
            "next_run": nxt.isoformat() if nxt else None,
        })

    return {
        "enabled": True,
        "running": bool(_scheduler.running),
        "jobs": jobs_info,
    }
