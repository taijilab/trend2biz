from __future__ import annotations

import base64
import json
import logging
import pathlib
import sys
import time
from datetime import date, datetime
from typing import Optional

from dateutil.parser import isoparse
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, desc, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import (
    BizProfile,
    Job,
    Project,
    ProjectScore,
    RepoMetricDaily,
    Report,
    TrendingSnapshot,
    TrendingSnapshotItem,
    Watchlist,
)
from app.schemas import (
    BatchScoreIn,
    BizGenerateIn,
    BizProfileOut,
    BizProfileResp,
    JobDetailResp,
    JobResp,
    MetricPoint,
    MetricSeriesResp,
    ProjectListItem,
    ProjectListResp,
    ProjectOut,
    ReportIn,
    ReportOut,
    ScoreOut,
    ScoreResp,
    SnapshotFetchIn,
    SnapshotItemOut,
    SnapshotOut,
    SnapshotResp,
    WatchlistIn,
    WatchlistItem,
    WatchlistResp,
)
from app.services.biz import infer_biz_profile
from app.services.github_metrics import GithubMetricsError, RateLimitError, fetch_repo_metrics
from app.services.scoring import compute_score
from app.services.trending import fetch_trending_html, parse_trending_html


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False)


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


_setup_logging()
logger = logging.getLogger("trend2biz")

app = FastAPI(title=settings.app_name)

_STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_STATIC_DIR), html=True), name="web")


@app.on_event("startup")
def startup() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logger.error("DB create_all failed: %s", exc)


@app.get("/ping")
def ping() -> dict:
    return {"pong": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_or_create_project(db: Session, repo_full_name: str, repo_url: str, description: Optional[str], primary_language: Optional[str]) -> Project:
    project = db.execute(select(Project).where(Project.repo_full_name == repo_full_name)).scalar_one_or_none()
    if project:
        project.last_seen_at = datetime.utcnow()
        if description:
            project.description = description
        if primary_language:
            project.primary_language = primary_language
        return project
    project = Project(
        repo_full_name=repo_full_name,
        repo_url=repo_url,
        description=description,
        primary_language=primary_language,
        last_seen_at=datetime.utcnow(),
    )
    db.add(project)
    db.flush()
    return project


def latest_metric(db: Session, project_id: str) -> Optional[RepoMetricDaily]:
    return db.execute(
        select(RepoMetricDaily).where(RepoMetricDaily.project_id == project_id).order_by(desc(RepoMetricDaily.metric_date)).limit(1)
    ).scalar_one_or_none()


def latest_biz(db: Session, project_id: str) -> Optional[BizProfile]:
    return db.execute(
        select(BizProfile).where(BizProfile.project_id == project_id).order_by(desc(BizProfile.created_at)).limit(1)
    ).scalar_one_or_none()


def latest_score(db: Session, project_id: str) -> Optional[ProjectScore]:
    return db.execute(
        select(ProjectScore).where(ProjectScore.project_id == project_id).order_by(desc(ProjectScore.created_at)).limit(1)
    ).scalar_one_or_none()


def create_job(db: Session, job_type: str, payload: Optional[dict]) -> Job:
    job = Job(job_type=job_type, payload=payload, status="queued")
    db.add(job)
    db.flush()
    return job


def refresh_metrics_for_project(db: Session, project: Project, metric_date: date) -> RepoMetricDaily:
    data = fetch_repo_metrics(project.repo_full_name, token=settings.github_token)
    project.description = data.get("description") or project.description
    project.primary_language = data.get("primary_language") or project.primary_language
    project.license_spdx = data.get("license_spdx") or project.license_spdx
    if data.get("created_at_github"):
        project.created_at_github = isoparse(data["created_at_github"])
    if data.get("updated_at_github"):
        project.updated_at_github = isoparse(data["updated_at_github"])
    if data.get("pushed_at_github"):
        project.pushed_at_github = isoparse(data["pushed_at_github"])

    metric = db.execute(
        select(RepoMetricDaily).where(and_(RepoMetricDaily.project_id == project.project_id, RepoMetricDaily.metric_date == metric_date))
    ).scalar_one_or_none()

    if not metric:
        metric = RepoMetricDaily(project_id=project.project_id, metric_date=metric_date)
        db.add(metric)

    metric.stars = data.get("stars")
    metric.forks = data.get("forks")
    metric.watchers = data.get("watchers")
    metric.open_issues = data.get("open_issues")
    metric.commits_30d = data.get("commits_30d")
    metric.commits_90d = data.get("commits_90d")
    metric.contributors_90d = data.get("contributors_90d")
    metric.captured_at = datetime.utcnow()
    db.flush()
    return metric


def upsert_score(db: Session, project: Project, model: str, biz_id: Optional[str], metric_date: Optional[date], score_data: dict) -> ProjectScore:
    """Insert or replace score for (project, model, metric_date)."""
    existing = db.execute(
        select(ProjectScore).where(
            and_(
                ProjectScore.project_id == project.project_id,
                ProjectScore.model_name == model,
                ProjectScore.metric_date == metric_date,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.biz_profile_id = biz_id
        existing.created_at = datetime.utcnow()
        existing.market_score = score_data["market_score"]
        existing.traction_score = score_data["traction_score"]
        existing.moat_score = score_data["moat_score"]
        existing.team_score = score_data["team_score"]
        existing.monetization_score = score_data["monetization_score"]
        existing.risk_score = score_data["risk_score"]
        existing.total_score = score_data["total_score"]
        existing.grade = score_data["grade"]
        existing.highlights = score_data["highlights"]
        existing.risks = score_data["risks"]
        existing.followups = score_data["followups"]
        existing.explanations = score_data["explanations"]
        db.flush()
        return existing

    score = ProjectScore(
        project_id=project.project_id,
        model_name=model,
        biz_profile_id=biz_id,
        metric_date=metric_date,
        market_score=score_data["market_score"],
        traction_score=score_data["traction_score"],
        moat_score=score_data["moat_score"],
        team_score=score_data["team_score"],
        monetization_score=score_data["monetization_score"],
        risk_score=score_data["risk_score"],
        total_score=score_data["total_score"],
        grade=score_data["grade"],
        highlights=score_data["highlights"],
        risks=score_data["risks"],
        followups=score_data["followups"],
        explanations=score_data["explanations"],
    )
    db.add(score)
    db.flush()
    return score


def generate_biz_and_score(db: Session, project: Project, model: str = "rule-v1") -> tuple[BizProfile, ProjectScore]:
    biz_data = infer_biz_profile(project.repo_full_name, project.description, project.primary_language)
    biz = BizProfile(project_id=project.project_id, model_name=model, **biz_data)
    db.add(biz)
    db.flush()

    metric = latest_metric(db, project.project_id)
    metric_payload = {
        "stars": metric.stars if metric else 0,
        "commits_30d": metric.commits_30d if metric else 0,
        "contributors_90d": metric.contributors_90d if metric else 0,
    }
    score_data = compute_score(metric_payload, biz_data)
    score = upsert_score(
        db=db,
        project=project,
        model="yc-open-source-v1",
        biz_id=biz.biz_profile_id,
        metric_date=metric.metric_date if metric else None,
        score_data=score_data,
    )
    return biz, score


# ---------------------------------------------------------------------------
# Cursor pagination helpers
# ---------------------------------------------------------------------------

def _encode_cursor(last_seen_at: datetime, project_id: str) -> str:
    raw = f"{last_seen_at.isoformat()}|{project_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        # Add padding to handle base64 without padding
        padded = cursor + "==" * ((4 - len(cursor) % 4) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        ts_str, project_id = raw.split("|", 1)
        return datetime.fromisoformat(ts_str), project_id
    except Exception:
        raise HTTPException(status_code=400, detail="invalid cursor")


# ---------------------------------------------------------------------------
# Background job work functions (no DB commit — wrapper commits)
# ---------------------------------------------------------------------------

def _do_snapshot_fetch(db: Session, since: str, language: str, spoken: Optional[str], snapshot_date: date) -> None:
    html = fetch_trending_html(since=since, language=language, spoken=spoken)
    parsed = parse_trending_html(html)

    snapshot = db.execute(
        select(TrendingSnapshot).where(
            and_(
                TrendingSnapshot.snapshot_date == snapshot_date,
                TrendingSnapshot.since == since,
                TrendingSnapshot.language == language,
                TrendingSnapshot.spoken == spoken,
            )
        )
    ).scalar_one_or_none()
    if not snapshot:
        snapshot = TrendingSnapshot(snapshot_date=snapshot_date, since=since, language=language, spoken=spoken)
        db.add(snapshot)
        db.flush()

    db.query(TrendingSnapshotItem).filter(TrendingSnapshotItem.snapshot_id == snapshot.snapshot_id).delete()

    for item in parsed:
        project = get_or_create_project(
            db=db,
            repo_full_name=item.repo_full_name,
            repo_url=item.repo_url,
            description=item.description,
            primary_language=item.primary_language,
        )
        db.add(
            TrendingSnapshotItem(
                snapshot_id=snapshot.snapshot_id,
                rank=item.rank,
                project_id=project.project_id,
                repo_full_name=item.repo_full_name,
                repo_url=item.repo_url,
                description=item.description,
                primary_language=item.primary_language,
                stars_total_hint=item.stars_total_hint,
                forks_total_hint=item.forks_total_hint,
                stars_delta_window=item.stars_delta_window,
            )
        )


def _do_metrics_refresh(db: Session, project_id: str, metric_date: date) -> None:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")
    refresh_metrics_for_project(db, project, metric_date)


def _do_biz_generate(db: Session, project_id: str, model: str) -> None:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")
    generate_biz_and_score(db, project, model=model)


def _do_score_batch(db: Session, snapshot_id: str, biz_model: str) -> None:
    items = db.execute(select(TrendingSnapshotItem).where(TrendingSnapshotItem.snapshot_id == snapshot_id)).scalars().all()
    for item in items:
        project = db.get(Project, item.project_id)
        if not project:
            continue
        generate_biz_and_score(db, project, model=biz_model)


# ---------------------------------------------------------------------------
# Job runner with exponential-backoff retry
# ---------------------------------------------------------------------------

def _run_job_with_retry(job_id: str, work_fn, *args) -> None:
    """Execute work_fn(db, *args) with up to job.max_retries retries on failure."""
    # Set job to running
    init_db = SessionLocal()
    try:
        job = init_db.get(Job, job_id)
        if not job:
            return
        max_retries = job.max_retries
        job.status = "running"
        job.started_at = datetime.utcnow()
        init_db.commit()
    except Exception:
        init_db.close()
        return
    finally:
        init_db.close()

    last_exc: Optional[Exception] = None
    succeeded = False
    attempt = 0

    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait = 2 ** (attempt - 1)  # 1s, 2s, 4s
            logger.info("job %s retry %d/%d in %ds", job_id, attempt, max_retries, wait)
            time.sleep(wait)

        attempt_db = SessionLocal()
        try:
            work_fn(attempt_db, *args)
            attempt_db.commit()
            succeeded = True
            break
        except (GithubMetricsError, RateLimitError, Exception) as exc:
            attempt_db.rollback()
            last_exc = exc
            logger.warning("job %s attempt %d/%d failed: %s", job_id, attempt + 1, max_retries + 1, exc)
        finally:
            attempt_db.close()

    # Write final status
    final_db = SessionLocal()
    try:
        job = final_db.get(Job, job_id)
        if job:
            if succeeded:
                job.status = "succeeded"
                job.retry_count = attempt
                logger.info("job %s succeeded after %d attempt(s)", job_id, attempt + 1)
            else:
                job.status = "failed"
                job.error = str(last_exc) if last_exc else "unknown error"
                job.retry_count = attempt
                logger.error("job %s failed after %d attempt(s): %s", job_id, attempt + 1, last_exc)
            job.finished_at = datetime.utcnow()
            final_db.commit()
    finally:
        final_db.close()


# ---------------------------------------------------------------------------
# Background job runners
# ---------------------------------------------------------------------------

def run_snapshot_fetch_job(job_id: str, since: str, language: str, spoken: Optional[str], snapshot_date: date) -> None:
    _run_job_with_retry(job_id, _do_snapshot_fetch, since, language, spoken, snapshot_date)


def run_metrics_refresh_job(job_id: str, project_id: str, metric_date: date) -> None:
    _run_job_with_retry(job_id, _do_metrics_refresh, project_id, metric_date)


def run_biz_generate_job(job_id: str, project_id: str, model: str) -> None:
    _run_job_with_retry(job_id, _do_biz_generate, project_id, model)


def run_score_batch_job(job_id: str, snapshot_id: str, biz_model: str) -> None:
    _run_job_with_retry(job_id, _do_score_batch, snapshot_id, biz_model)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/web/", status_code=302)


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    db_ok = False
    db_error = None
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        db_error = str(exc)
        logger.error("DB health check failed: %s", exc)
    result: dict = {"name": settings.app_name, "status": "ok", "db": "ok" if db_ok else "error"}
    if db_error:
        result["db_error"] = db_error
    return result


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}", response_model=JobDetailResp)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return JobDetailResp(
        job_id=job.job_id,
        job_type=job.job_type,
        status=job.status,
        retry_count=job.retry_count,
        max_retries=job.max_retries,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        payload=job.payload,
    )


@app.get(f"{settings.api_prefix}/trending/snapshots", response_model=SnapshotResp)
def get_trending_snapshots(
    since: str = Query(..., pattern="^(daily|weekly|monthly)$"),
    date_param: Optional[date] = Query(default=None, alias="date"),
    language: str = "all",
    spoken: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    target_date = date_param or date.today()
    snapshot = db.execute(
        select(TrendingSnapshot).where(
            and_(
                TrendingSnapshot.snapshot_date == target_date,
                TrendingSnapshot.since == since,
                TrendingSnapshot.language == language,
                TrendingSnapshot.spoken == spoken,
            )
        )
    ).scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="snapshot not found")

    items = db.execute(
        select(TrendingSnapshotItem)
        .where(TrendingSnapshotItem.snapshot_id == snapshot.snapshot_id)
        .order_by(TrendingSnapshotItem.rank.asc())
        .limit(limit)
    ).scalars()

    return SnapshotResp(
        snapshot=SnapshotOut(
            snapshot_id=snapshot.snapshot_id,
            date=snapshot.snapshot_date,
            since=snapshot.since,
            language=snapshot.language,
            spoken=snapshot.spoken,
            captured_at=snapshot.captured_at,
        ),
        items=[
            SnapshotItemOut(
                rank=i.rank,
                repo_full_name=i.repo_full_name,
                repo_url=i.repo_url,
                description=i.description,
                primary_language=i.primary_language,
                stars_total_hint=i.stars_total_hint,
                forks_total_hint=i.forks_total_hint,
                stars_delta_window=i.stars_delta_window,
            )
            for i in items
        ],
    )


@app.post(f"{settings.api_prefix}/trending/snapshots:fetch", response_model=JobResp, status_code=202)
def fetch_snapshot(payload: SnapshotFetchIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    try:
        # Idempotency: reuse an in-progress job for the same date/since/language/spoken
        in_flight = db.execute(
            select(Job).where(Job.job_type == "trending_fetch", Job.status.in_(["queued", "running"]))
        ).scalars().all()
        for j in in_flight:
            p = j.payload or {}
            if (p.get("since") == payload.since
                    and p.get("language") == payload.language
                    and p.get("spoken") == payload.spoken
                    and p.get("date") == str(date.today())):
                # Abandon stale queued jobs (server restarted before task ran)
                age = datetime.utcnow() - j.created_at.replace(tzinfo=None)
                if j.status == "queued" and age.total_seconds() > 300:
                    j.status = "failed"
                    j.error = "abandoned: server restarted before task could start"
                    j.finished_at = datetime.utcnow()
                    db.commit()
                    break
                return JobResp(job_id=j.job_id, status=j.status)

        job = create_job(
            db=db,
            job_type="trending_fetch",
            payload={"since": payload.since, "language": payload.language, "spoken": payload.spoken, "date": str(date.today())},
        )
        db.commit()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("fetch_snapshot failed: %s", exc)
        db.rollback()
        raise HTTPException(status_code=503, detail=f"抓取启动失败: {exc}")
    background_tasks.add_task(run_snapshot_fetch_job, job.job_id, payload.since, payload.language, payload.spoken, date.today())
    return JobResp(job_id=job.job_id, status=job.status)


@app.get(f"{settings.api_prefix}/projects", response_model=ProjectListResp)
def list_projects(
    q: Optional[str] = None,
    language: Optional[str] = None,
    min_score: Optional[float] = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    stmt = select(Project)
    if q:
        stmt = stmt.where(Project.repo_full_name.ilike(f"%{q}%"))
    if language:
        stmt = stmt.where(Project.primary_language == language)
    if cursor:
        cursor_ts, cursor_id = _decode_cursor(cursor)
        stmt = stmt.where(
            (Project.last_seen_at < cursor_ts)
            | ((Project.last_seen_at == cursor_ts) & (Project.project_id > cursor_id))
        )

    # Fetch one extra to detect if there's a next page
    projects = db.execute(
        stmt.order_by(desc(Project.last_seen_at), Project.project_id).limit(limit + 1)
    ).scalars().all()

    has_more = len(projects) > limit
    page = projects[:limit]

    items: list[ProjectListItem] = []
    for p in page:
        score = latest_score(db, p.project_id)
        if min_score is not None and (not score or score.total_score < min_score):
            continue
        biz = latest_biz(db, p.project_id)
        items.append(
            ProjectListItem(
                project_id=p.project_id,
                repo_full_name=p.repo_full_name,
                primary_language=p.primary_language,
                latest_score={"total": score.total_score, "grade": score.grade} if score else None,
                latest_biz={
                    "category": biz.category,
                    "monetization_candidates": biz.monetization_candidates,
                }
                if biz
                else None,
            )
        )

    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last.last_seen_at, last.project_id)

    return ProjectListResp(items=items, next_cursor=next_cursor)


@app.get(f"{settings.api_prefix}/projects/{{project_id}}", response_model=ProjectOut)
def project_detail(project_id: str, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(status_code=404, detail="project not found")

    metric = latest_metric(db, project_id)
    biz = latest_biz(db, project_id)
    score = latest_score(db, project_id)

    return ProjectOut(
        project={
            "project_id": p.project_id,
            "repo_full_name": p.repo_full_name,
            "repo_url": p.repo_url,
            "license": p.license_spdx,
            "created_at": p.created_at_github,
        },
        latest_metrics={
            "as_of": metric.metric_date,
            "stars": metric.stars,
            "forks": metric.forks,
            "commits_30d": metric.commits_30d,
            "contributors_90d": metric.contributors_90d,
        }
        if metric
        else None,
        latest_biz_profile={
            "category": biz.category,
            "value_props": biz.value_props,
            "buyer": biz.buyer,
            "monetization_candidates": biz.monetization_candidates,
        }
        if biz
        else None,
        latest_score={
            "total": score.total_score,
            "grade": score.grade,
            "breakdown": {
                "market": score.market_score,
                "traction": score.traction_score,
                "moat": score.moat_score,
                "team": score.team_score,
                "monetization": score.monetization_score,
                "risk": score.risk_score,
            },
        }
        if score
        else None,
    )


@app.get(f"{settings.api_prefix}/projects/{{project_id}}/metrics", response_model=MetricSeriesResp)
def project_metrics(project_id: str, from_date: date = Query(alias="from"), to_date: date = Query(alias="to"), db: Session = Depends(get_db)):
    rows = db.execute(
        select(RepoMetricDaily)
        .where(
            and_(
                RepoMetricDaily.project_id == project_id,
                RepoMetricDaily.metric_date >= from_date,
                RepoMetricDaily.metric_date <= to_date,
            )
        )
        .order_by(RepoMetricDaily.metric_date.asc())
    ).scalars()
    return MetricSeriesResp(
        series=[
            MetricPoint(
                date=r.metric_date,
                stars=r.stars,
                forks=r.forks,
                commits_30d=r.commits_30d,
                contributors_90d=r.contributors_90d,
            )
            for r in rows
        ]
    )


@app.post(f"{settings.api_prefix}/projects/{{project_id}}/metrics:refresh", response_model=JobResp, status_code=202)
def refresh_project_metrics(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    job = create_job(db, "metrics_refresh", {"project_id": project_id})
    db.commit()
    background_tasks.add_task(run_metrics_refresh_job, job.job_id, project_id, date.today())
    return JobResp(job_id=job.job_id, status=job.status)


@app.get(f"{settings.api_prefix}/projects/{{project_id}}/biz-profiles", response_model=BizProfileResp)
def get_biz_profiles(project_id: str, latest: bool = True, version_id: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(BizProfile).where(BizProfile.project_id == project_id).order_by(desc(BizProfile.created_at))
    if version_id:
        stmt = select(BizProfile).where(BizProfile.biz_profile_id == version_id)
    items = db.execute(stmt.limit(1 if latest and not version_id else 20)).scalars().all()

    return BizProfileResp(
        items=[
            BizProfileOut(
                biz_profile_id=i.biz_profile_id,
                version=i.model_name,
                created_at=i.created_at,
                category=i.category,
                confidence=i.confidence,
            )
            for i in items
        ]
    )


@app.post(f"{settings.api_prefix}/projects/{{project_id}}/biz-profiles:generate", response_model=JobResp, status_code=202)
def generate_biz_profiles(project_id: str, payload: BizGenerateIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    model = payload.model or "rule-v1"
    job = create_job(db, "biz_generate", {"project_id": project_id, "model": model})
    db.commit()
    background_tasks.add_task(run_biz_generate_job, job.job_id, project_id, model)
    return JobResp(job_id=job.job_id, status=job.status)


@app.get(f"{settings.api_prefix}/projects/{{project_id}}/scores", response_model=ScoreResp)
def get_scores(project_id: str, latest: bool = True, score_model_id: Optional[str] = None, db: Session = Depends(get_db)):
    stmt = select(ProjectScore).where(ProjectScore.project_id == project_id).order_by(desc(ProjectScore.created_at))
    if score_model_id:
        stmt = stmt.where(ProjectScore.model_name == score_model_id)
    rows = db.execute(stmt.limit(1 if latest and not score_model_id else 20)).scalars().all()
    return ScoreResp(
        items=[
            ScoreOut(
                score_id=s.score_id,
                model_name=s.model_name,
                total=s.total_score,
                grade=s.grade,
                highlights=s.highlights,
                risks=s.risks,
                followups=s.followups,
                explanations=s.explanations,
            )
            for s in rows
        ]
    )


@app.post(f"{settings.api_prefix}/scores:batch", response_model=JobResp, status_code=202)
def batch_score(payload: BatchScoreIn, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    snapshot = db.get(TrendingSnapshot, payload.snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="snapshot not found")

    biz_model = payload.biz_profile_model or "rule-v1"
    job = create_job(db, "score_batch", payload.model_dump())
    db.commit()
    background_tasks.add_task(run_score_batch_job, job.job_id, payload.snapshot_id, biz_model)
    return JobResp(job_id=job.job_id, status=job.status)


@app.post(f"{settings.api_prefix}/reports:generate", response_model=ReportOut)
def generate_report(payload: ReportIn, db: Session = Depends(get_db)):
    project = db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    score = latest_score(db, project.project_id)
    biz = latest_biz(db, project.project_id)

    highlights_html = "".join(f"<li>{h}</li>" for h in (score.highlights or [])) if score else ""
    risks_html = "".join(f"<li>{r}</li>" for r in (score.risks or [])) if score else ""
    followups_html = "".join(f"<li>{f}</li>" for f in (score.followups or [])) if score else ""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{project.repo_full_name}</title></head>
<body>
<h1>{project.repo_full_name}</h1>
<p><a href="{project.repo_url}">{project.repo_url}</a></p>
<p>{project.description or ''}</p>
<h2>评分：{score.total_score if score else 'N/A'} ({score.grade if score else 'N/A'})</h2>
<h3>商业化分类：{biz.category if biz else 'N/A'}</h3>
<h3>亮点</h3><ul>{highlights_html}</ul>
<h3>风险</h3><ul>{risks_html}</ul>
<h3>追问清单</h3><ul>{followups_html}</ul>
</body></html>"""

    report = Report(project_id=project.project_id, score_id=score.score_id if score else None, format=payload.format, content=html)
    db.add(report)
    db.commit()
    return ReportOut(report_id=report.report_id, url=f"/reports/{report.report_id}")


@app.post(f"{settings.api_prefix}/watchlist", response_model=WatchlistItem)
def add_watchlist(payload: WatchlistIn, db: Session = Depends(get_db)):
    if not db.get(Project, payload.project_id):
        raise HTTPException(status_code=404, detail="project not found")
    watch = Watchlist(project_id=payload.project_id, note=payload.note)
    db.add(watch)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        watch = db.execute(select(Watchlist).where(Watchlist.project_id == payload.project_id)).scalar_one()
    return WatchlistItem(project_id=watch.project_id, note=watch.note, created_at=watch.created_at)


@app.get(f"{settings.api_prefix}/watchlist", response_model=WatchlistResp)
def get_watchlist(db: Session = Depends(get_db)):
    rows = db.execute(select(Watchlist).order_by(desc(Watchlist.created_at))).scalars().all()
    return WatchlistResp(items=[WatchlistItem(project_id=r.project_id, note=r.note, created_at=r.created_at) for r in rows])


@app.delete(f"{settings.api_prefix}/watchlist/{{project_id}}")
def delete_watchlist(project_id: str, db: Session = Depends(get_db)):
    row = db.execute(select(Watchlist).where(Watchlist.project_id == project_id)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="watchlist item not found")
    db.delete(row)
    db.commit()
    return {"deleted": True}
