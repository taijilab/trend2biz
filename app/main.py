from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import time

import httpx
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
from app.services.github_metrics import GithubMetricsError, RateLimitError, fetch_readme, fetch_repo_metrics
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

# ---------------------------------------------------------------------------
# Version / Build
# ---------------------------------------------------------------------------

APP_VERSION = "0.7.0"

def _git_short_hash() -> str:
    # Vercel 部署时无 .git 目录，优先读 Vercel 注入的 commit SHA
    vercel_sha = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
    if vercel_sha:
        return vercel_sha[:7]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            cwd=pathlib.Path(__file__).parent.parent,
        ).decode().strip()
    except Exception:
        return "unknown"

APP_BUILD = _git_short_hash()

app = FastAPI(title=settings.app_name)

_STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_STATIC_DIR), html=True), name="web")


def _migrate_add_missing_columns() -> None:
    """Idempotent column migrations for databases created before schema additions."""
    migrations = [
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 3",
    ]
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
                conn.commit()
                logger.info("migration ok: %s", sql[:60])
            except Exception as exc:
                logger.warning("migration skipped (%s): %s", sql[:60], exc)


@app.on_event("startup")
def startup() -> None:
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        logger.error("DB create_all failed: %s", exc)
    _migrate_add_missing_columns()


@app.get("/ping")
def ping() -> dict:
    return {"pong": True}


@app.get("/api/v1/version")
def version() -> dict:
    return {"version": APP_VERSION, "build": APP_BUILD}


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


def _call_llm_openrouter(api_key: str, prompt: str) -> Optional[str]:
    """Call OpenRouter API (Deepseek-V3) for Chinese description generation."""
    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek/deepseek-chat-v3-0324:free",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 256,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        logger.warning("OpenRouter LLM call failed: %s", e)
        return None


def _call_llm_zhipu(api_key: str, prompt: str) -> Optional[str]:
    """Call Zhipu AI (GLM-4-Flash) for Chinese description generation."""
    try:
        r = httpx.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 256,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip() or None
    except Exception as e:
        logger.warning("Zhipu LLM call failed: %s", e)
        return None


def _translate_to_zh_mymemory(text: str) -> Optional[str]:
    """Fallback: translate via MyMemory free API."""
    try:
        r = httpx.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text[:500], "langpair": "en|zh-CN"},
            timeout=10,
        )
        result = r.json().get("responseData", {}).get("translatedText")
        return result if result else None
    except Exception:
        return None


def enrich_description_zh(
    repo_full_name: str,
    description: Optional[str],
    readme: Optional[str],
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> Optional[str]:
    """Generate a rich Chinese project description using an LLM (if API key is set).

    Supported providers: "anthropic" (default), "openrouter" (Deepseek-V3), "zhipu" (GLM-4-Flash).
    Falls back to MyMemory translation when no key is available.
    """
    # Already Chinese?
    if description and any('\u4e00' <= c <= '\u9fff' for c in description):
        return description

    effective_key = api_key or settings.anthropic_api_key
    effective_provider = provider or "anthropic"

    if effective_key and (description or readme):
        context_parts = []
        if description:
            context_parts.append(f"GitHub 简介：{description}")
        if readme:
            context_parts.append(f"README（节选）：\n{readme}")
        context = "\n\n".join(context_parts)
        prompt = (
            f"你是一名技术产品分析师。请根据以下开源项目资料，用中文写出 2～3 句准确、简洁的项目介绍。"
            f"要求：说清楚项目是什么、解决什么问题、适合哪类用户使用，不要夸大，不要翻译腔。\n\n"
            f"项目：{repo_full_name}\n\n{context}"
        )

        if effective_provider == "openrouter":
            result = _call_llm_openrouter(effective_key, prompt)
            if result:
                return result
        elif effective_provider == "zhipu":
            result = _call_llm_zhipu(effective_key, prompt)
            if result:
                return result
        else:  # anthropic
            try:
                import anthropic  # lazy import — optional dependency
                client = anthropic.Anthropic(api_key=effective_key)
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
                result = msg.content[0].text.strip()
                if result:
                    return result
            except Exception as e:
                logger.warning("Anthropic description enrichment failed for %s: %s", repo_full_name, e)

    # Fallback: simple translation of the short description
    if description and description.strip():
        return _translate_to_zh_mymemory(description)
    return None


def generate_biz_profile_llm(
    repo_name: str,
    description: Optional[str],
    readme: Optional[str],
    language: Optional[str],
    api_key: str,
    provider: str,
) -> Optional[dict]:
    """Call an LLM to generate a complete structured biz profile (llm-v1).

    Returns a dict compatible with infer_biz_profile() output, or None on failure.
    The LLM response is expected to be a JSON object with all biz profile fields
    plus description_zh and bd_pitch stored in explanations.
    """
    context_parts = []
    if description:
        context_parts.append(f"GitHub 简介：{description}")
    if readme:
        context_parts.append(f"README（节选）：\n{readme[:2000]}")
    if language:
        context_parts.append(f"主要编程语言：{language}")
    context = "\n\n".join(context_parts) if context_parts else "（无描述）"

    categories = (
        "agent / security / data-platform / observability / fintech / biotech / "
        "robotics-iot / edu-tech / media-tech / low-code / enterprise-saas / "
        "infra / devops / devtools / developer-tools"
    )

    prompt = (
        f"你是一名开源商业化分析师。请根据以下 GitHub 项目信息，生成结构化商业分析报告。\n\n"
        f"项目：{repo_name}\n{context}\n\n"
        f"请严格只返回以下 JSON 对象（不加 markdown 代码块，不加任何额外文字）：\n"
        f'{{\n'
        f'  "category": "从以下选一：{categories}",\n'
        f'  "scenarios": ["2-3个中文使用场景"],\n'
        f'  "value_props": ["2-3个核心价值主张"],\n'
        f'  "delivery_forms": ["交付形式，可选 OSS/SaaS/SDK/API/On-premise，选1-3个"],\n'
        f'  "monetization_candidates": ["1-3个变现路径"],\n'
        f'  "buyer": "目标购买决策者（中文）",\n'
        f'  "sales_motion": "PLG 或 Enterprise",\n'
        f'  "confidence": 0.0到1.0之间的置信度数字,\n'
        f'  "description_zh": "2-3句中文项目介绍：说清楚是什么、解决什么问题、适合谁用",\n'
        f'  "bd_pitch": "3句话BD话术：①合作价值主张 ②对方核心痛点 ③我方可提供资源"\n'
        f'}}'
    )

    raw: Optional[str] = None
    if provider == "openrouter":
        try:
            r = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek/deepseek-chat-v3-0324:free",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 800,
                },
                timeout=60,
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip() or None
        except Exception as e:
            logger.warning("OpenRouter llm-v1 call failed for %s: %s", repo_name, e)
    elif provider == "zhipu":
        try:
            r = httpx.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 800,
                },
                timeout=60,
            )
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip() or None
        except Exception as e:
            logger.warning("Zhipu llm-v1 call failed for %s: %s", repo_name, e)
    else:  # anthropic
        try:
            import anthropic  # lazy import
            client = anthropic.Anthropic(api_key=api_key)
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip() or None
        except Exception as e:
            logger.warning("Anthropic llm-v1 call failed for %s: %s", repo_name, e)

    if not raw:
        return None

    # Parse JSON — handle possible markdown code-block wrapping
    data: Optional[dict] = None
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group())
            except Exception:
                pass

    if not data or not isinstance(data.get("category"), str):
        logger.warning("llm-v1: JSON parse failed for %s, raw=%s", repo_name, raw[:200])
        return None

    return {
        "category": data["category"],
        "user_persona": "AI",
        "scenarios": data.get("scenarios") or ["通用研发效率"],
        "value_props": data.get("value_props") or ["效率提升"],
        "delivery_forms": data.get("delivery_forms") or ["OSS"],
        "monetization_candidates": data.get("monetization_candidates") or [],
        "buyer": data.get("buyer", "研发工程师"),
        "sales_motion": data.get("sales_motion", "PLG"),
        "confidence": float(data.get("confidence", 0.8)),
        "explanations": {
            "method": "llm-v1",
            "signals": [repo_name, description or ""],
            "description_zh": data.get("description_zh"),
            "bd_pitch": data.get("bd_pitch"),
        },
    }


def generate_biz_and_score(
    db: Session,
    project: Project,
    model: str = "rule-v1",
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
) -> tuple[BizProfile, ProjectScore]:
    biz_data: Optional[dict] = None

    if model == "llm-v1" and api_key:
        readme = fetch_readme(project.repo_full_name, token=settings.github_token)
        biz_data = generate_biz_profile_llm(
            repo_name=project.repo_full_name,
            description=project.description,
            readme=readme,
            language=project.primary_language,
            api_key=api_key,
            provider=provider or "anthropic",
        )
        if not biz_data:
            logger.warning("llm-v1 failed for %s, falling back to rule-v1", project.repo_full_name)
            model = "rule-v1"

    if biz_data is None:
        biz_data = infer_biz_profile(project.repo_full_name, project.description, project.primary_language)
        readme = fetch_readme(project.repo_full_name, token=settings.github_token)
        desc_zh = enrich_description_zh(project.repo_full_name, project.description, readme, api_key=api_key, provider=provider)
        if desc_zh:
            biz_data["explanations"]["description_zh"] = desc_zh

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


def _do_biz_generate(db: Session, project_id: str, model: str, api_key: Optional[str] = None, provider: Optional[str] = None) -> None:
    project = db.get(Project, project_id)
    if not project:
        raise ValueError(f"project {project_id} not found")
    generate_biz_and_score(db, project, model=model, api_key=api_key, provider=provider)


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


def run_biz_generate_job(job_id: str, project_id: str, model: str, api_key: Optional[str] = None, provider: Optional[str] = None) -> None:
    _run_job_with_retry(job_id, _do_biz_generate, project_id, model, api_key, provider)


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


@app.get(f"{settings.api_prefix}/trending/snapshots/dates")
def get_snapshot_dates(
    since: str = Query(..., pattern="^(daily|weekly|monthly)$"),
    language: str = "all",
    db: Session = Depends(get_db),
):
    """Return all dates (desc) for which a snapshot exists for the given since+language."""
    rows = db.execute(
        select(TrendingSnapshot.snapshot_date)
        .where(
            TrendingSnapshot.since == since,
            TrendingSnapshot.language == language,
        )
        .order_by(desc(TrendingSnapshot.snapshot_date))
        .limit(60)
    ).scalars().all()
    return {"dates": [str(d) for d in rows]}


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

    snap_items = db.execute(
        select(TrendingSnapshotItem)
        .where(TrendingSnapshotItem.snapshot_id == snapshot.snapshot_id)
        .order_by(TrendingSnapshotItem.rank.asc())
        .limit(limit)
    ).scalars().all()

    # Batch-load latest biz profiles and scores for all project IDs in this snapshot
    project_ids = [i.project_id for i in snap_items if i.project_id]
    biz_by_pid: dict = {}
    score_by_pid: dict = {}
    if project_ids:
        for b in db.execute(
            select(BizProfile).where(BizProfile.project_id.in_(project_ids))
            .order_by(desc(BizProfile.created_at))
        ).scalars().all():
            if b.project_id not in biz_by_pid:
                biz_by_pid[b.project_id] = b
        for s in db.execute(
            select(ProjectScore).where(ProjectScore.project_id.in_(project_ids))
            .order_by(desc(ProjectScore.created_at))
        ).scalars().all():
            if s.project_id not in score_by_pid:
                score_by_pid[s.project_id] = s

    def _item_biz(pid: Optional[str]) -> Optional[dict]:
        b = biz_by_pid.get(pid) if pid else None
        if not b:
            return None
        expl = b.explanations or {}
        return {
            "category": b.category,
            "scenarios": b.scenarios,
            "value_props": b.value_props,
            "delivery_forms": b.delivery_forms,
            "monetization_candidates": b.monetization_candidates,
            "buyer": b.buyer,
            "sales_motion": b.sales_motion,
            "confidence": b.confidence,
            "description_zh": expl.get("description_zh"),
            "bd_pitch": expl.get("bd_pitch"),
        }

    def _item_score(pid: Optional[str]) -> Optional[dict]:
        s = score_by_pid.get(pid) if pid else None
        return {"total": s.total_score, "grade": s.grade} if s else None

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
                project_id=i.project_id,
                latest_biz=_item_biz(i.project_id),
                latest_score=_item_score(i.project_id),
            )
            for i in snap_items
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
                latest_biz_profile={
                    "category": biz.category,
                    "scenarios": biz.scenarios,
                    "monetization_candidates": biz.monetization_candidates,
                    "description_zh": (biz.explanations or {}).get("description_zh"),
                    "bd_pitch": (biz.explanations or {}).get("bd_pitch"),
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
            "scenarios": biz.scenarios,
            "value_props": biz.value_props,
            "buyer": biz.buyer,
            "monetization_candidates": biz.monetization_candidates,
            "description_zh": (biz.explanations or {}).get("description_zh"),
            "bd_pitch": (biz.explanations or {}).get("bd_pitch"),
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
    # api_key is passed in memory only — NOT stored in the job payload / DB
    background_tasks.add_task(run_biz_generate_job, job.job_id, project_id, model, payload.api_key, payload.provider)
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
    expl = (biz.explanations or {}) if biz else {}
    desc_zh = expl.get("description_zh") if biz else None
    bd_pitch = expl.get("bd_pitch") if biz else None

    grade = score.grade if score else "N/A"
    total_score = score.total_score if score else None
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    highlights_html = "".join(f"<li>{h}</li>" for h in (score.highlights or [])) if score else ""
    risks_html = "".join(f"<li>{r}</li>" for r in (score.risks or [])) if score else ""
    followups_html = "".join(f"<li>{f}</li>" for f in (score.followups or [])) if score else ""

    # Score breakdown cells with signal text
    score_dim_labels = {"market": "市场", "traction": "牵引力", "moat": "护城河", "team": "团队", "monetization": "商业化", "risk": "风险"}
    score_values = {
        "market": score.market_score, "traction": score.traction_score,
        "moat": score.moat_score, "team": score.team_score,
        "monetization": score.monetization_score, "risk": score.risk_score,
    } if score else {}
    signals_text = (score.explanations or {}).get("signals_text", {}) if score else {}
    score_cells_html = "".join(
        f'<div class="score-item"><div class="val">{v:.1f}</div>'
        f'<div class="lbl">{score_dim_labels.get(k, k)}</div>'
        f'<div class="sig">{signals_text.get(k, "")}</div></div>'
        for k, v in score_values.items()
        if v is not None
    )

    biz_tags_html = ""
    if biz:
        for item in (biz.monetization_candidates or [])[:3]:
            biz_tags_html += f'<span class="tag">{item}</span>'
        for item in (biz.delivery_forms or [])[:3]:
            biz_tags_html += f'<span class="tag" style="background:#dcfce7;color:#166534">{item}</span>'

    desc_section = (
        f'<div class="card"><h2>项目介绍</h2>'
        f'<div class="desc">{desc_zh}</div></div>'
    ) if desc_zh else (
        f'<div class="card"><h2>项目介绍</h2>'
        f'<p style="color:#374151;font-size:14px">{project.description or ""}</p></div>'
    ) if project.description else ""

    bd_section = (
        f'<div class="card"><h2>BD 话术</h2><div class="bd-pitch">{bd_pitch}</div></div>'
    ) if bd_pitch else ""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{project.repo_full_name} — Trend2Biz 商业分析报告</title>
  <style>
    body{{font-family:system-ui,sans-serif;max-width:860px;margin:40px auto;padding:0 20px;color:#1a1a2e;background:#f8fafc}}
    .header{{background:linear-gradient(135deg,#0f3460 0%,#16213e 100%);color:#fff;padding:28px 32px;border-radius:12px;margin-bottom:20px}}
    .header h1{{margin:0 0 6px;font-size:22px;font-weight:700}}
    .header .meta{{font-size:13px;opacity:.8}}
    .card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:20px 24px;margin-bottom:16px}}
    .card h2{{font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin:0 0 12px}}
    .grade-badge{{display:inline-block;padding:4px 14px;border-radius:20px;font-weight:700;font-size:14px}}
    .grade-S{{background:#f59e0b;color:#000}}.grade-A{{background:#22c55e;color:#000}}
    .grade-B{{background:#3b82f6;color:#fff}}.grade-C{{background:#6b7280;color:#fff}}
    .score-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}
    .score-item{{text-align:center;padding:10px;background:#f8fafc;border-radius:8px}}
    .score-item .val{{font-size:22px;font-weight:700;color:#0f3460}}
    .score-item .lbl{{font-size:11px;color:#64748b;margin-top:3px}}
    .score-item .sig{{font-size:10px;color:#94a3b8;margin-top:2px;line-height:1.4}}
    ul{{margin:0;padding-left:18px}}li{{margin-bottom:6px;font-size:14px;line-height:1.6}}
    .tags{{display:flex;flex-wrap:wrap;gap:8px}}
    .tag{{padding:3px 10px;border-radius:16px;font-size:12px;background:#e0f2fe;color:#0369a1}}
    .desc{{font-size:14px;color:#374151;line-height:1.7;background:#f0fdf4;padding:14px 16px;border-radius:8px;border-left:3px solid #22c55e}}
    .bd-pitch{{font-size:14px;color:#374151;line-height:1.7;background:#fefce8;padding:14px 16px;border-radius:8px;border-left:3px solid #f59e0b}}
    .two-col{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
    .footer{{text-align:center;font-size:12px;color:#94a3b8;margin-top:28px}}
  </style>
</head>
<body>
  <div class="header">
    <h1>{project.repo_full_name}</h1>
    <div class="meta">
      <a href="{project.repo_url}" style="color:#93c5fd">{project.repo_url}</a>
      &nbsp;·&nbsp; 生成时间：{generated_at}
    </div>
  </div>

  {desc_section}

  <div class="card">
    <h2>评分概览</h2>
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px">
      <span class="grade-badge grade-{grade}">{grade}</span>
      <span style="font-size:28px;font-weight:700;color:#0f3460">{f"{total_score:.1f}" if total_score else "N/A"}</span>
      <span style="color:#64748b;font-size:14px">/ 10.0</span>
    </div>
    <div class="score-grid">{score_cells_html}</div>
  </div>

  <div class="card">
    <h2>商业画像</h2>
    <div class="tags" style="margin-bottom:10px">
      <span class="tag" style="background:#ede9fe;color:#5b21b6">{biz.category if biz else "N/A"}</span>
      {biz_tags_html}
    </div>
    <p style="font-size:13px;color:#475569;margin:0">
      <strong>买方：</strong>{biz.buyer if biz else "N/A"} &nbsp;·&nbsp;
      <strong>运动：</strong>{biz.sales_motion if biz else "N/A"} &nbsp;·&nbsp;
      <strong>置信度：</strong>{f"{biz.confidence:.0%}" if biz and biz.confidence else "N/A"}
    </p>
  </div>

  {bd_section}

  <div class="two-col">
    <div class="card"><h2>亮点</h2><ul>{highlights_html}</ul></div>
    <div class="card"><h2>风险</h2><ul>{risks_html}</ul></div>
  </div>

  <div class="card"><h2>追问清单</h2><ul>{followups_html}</ul></div>

  <div class="footer">Trend2Biz · 数据来源 GitHub Trending · 分析仅供参考</div>
</body>
</html>"""

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
