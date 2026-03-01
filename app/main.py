from __future__ import annotations

import base64
import csv
import io
import json
import logging
import os
import pathlib
import re
import subprocess
import sys
import time

import httpx
from datetime import date, datetime, timedelta
from typing import Optional

from dateutil.parser import isoparse
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, desc, func, or_, select, text
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
    RescheduleReq,
    RetryJobReq,
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
from app.services.github_metrics import GithubMetricsError, RateLimitError, fetch_readme, fetch_repo_metrics, fetch_star_history
from app.services.scoring import compute_score
from app.services.trending import TrendingParseError, fetch_trending_html, parse_trending_html


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

APP_VERSION = "0.9.0"

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

# ---------------------------------------------------------------------------
# Runtime key cache (survives restarts via .keys.json)
# ---------------------------------------------------------------------------

_KEYS_FILE = pathlib.Path(__file__).parent.parent / ".keys.json"

def _load_keys() -> dict:
    try:
        if _KEYS_FILE.exists():
            return json.loads(_KEYS_FILE.read_text())
    except Exception:
        pass
    return {}

def _save_keys(data: dict) -> None:
    try:
        _KEYS_FILE.write_text(json.dumps(data))
    except Exception as exc:
        logger.warning("could not persist .keys.json: %s", exc)

_runtime_keys: dict = _load_keys()


def _effective_github_token() -> Optional[str]:
    """Return GitHub token: runtime cache > env/config."""
    return _runtime_keys.get("github_token") or settings.github_token


def _effective_llm_key() -> Optional[str]:
    """Return LLM API key: runtime cache > env/config."""
    return _runtime_keys.get("llm_api_key") or settings.anthropic_api_key


def _effective_llm_provider() -> Optional[str]:
    return _runtime_keys.get("llm_provider") or "anthropic"


# ---------------------------------------------------------------------------
# Access-token middleware
# ---------------------------------------------------------------------------

class _AccessTokenMiddleware(BaseHTTPMiddleware):
    """If ACCESS_TOKEN is configured, protect all /api/v1/* and /web/* paths."""

    async def dispatch(self, request: Request, call_next):
        required = settings.access_token
        if not required:
            return await call_next(request)
        # Exempt health/ping endpoints and static assets that are not /web/
        path = request.url.path
        if path in ("/ping", "/health", "/"):
            return await call_next(request)
        # Check Authorization header or ?token= query param
        auth_header = request.headers.get("Authorization", "")
        token_param = request.query_params.get("token", "")
        provided = ""
        if auth_header.startswith("Bearer "):
            provided = auth_header[7:]
        elif token_param:
            provided = token_param
        if provided != required:
            return Response(
                content=json.dumps({"detail": "Unauthorized — access token required"}),
                status_code=401,
                media_type="application/json",
            )
        return await call_next(request)


app = FastAPI(title=settings.app_name)
app.add_middleware(_AccessTokenMiddleware)

_STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=str(_STATIC_DIR), html=True), name="web")


def _migrate_add_missing_columns() -> None:
    """Idempotent column migrations for databases created before schema additions."""
    migrations = [
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS max_retries INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_login VARCHAR(255)",
        "ALTER TABLE projects ADD COLUMN IF NOT EXISTS owner_type VARCHAR(50)",
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
    from app.scheduler import start_scheduler  # noqa: PLC0415
    start_scheduler()


@app.on_event("shutdown")
def shutdown() -> None:
    from app.scheduler import stop_scheduler  # noqa: PLC0415
    stop_scheduler()


@app.get("/ping")
def ping() -> dict:
    return {"pong": True}


@app.get("/api/v1/version")
def version() -> dict:
    return {
        "version": APP_VERSION,
        "build": APP_BUILD,
        "server_has_key": bool(_effective_llm_key()),
        "server_has_github_token": bool(_effective_github_token()),
        "access_protected": bool(settings.access_token),
    }


# ---------------------------------------------------------------------------
# Settings API (runtime key management)
# ---------------------------------------------------------------------------

@app.post("/api/v1/settings/github-token")
def set_github_token(body: dict) -> dict:
    """Store or clear the GitHub API token at runtime (persisted to .keys.json)."""
    token = (body.get("token") or "").strip()
    if token:
        _runtime_keys["github_token"] = token
    else:
        _runtime_keys.pop("github_token", None)
    _save_keys(_runtime_keys)
    return {"saved": True, "has_token": bool(_effective_github_token())}


@app.get("/api/v1/settings/github-token-status")
def github_token_status() -> dict:
    token = _effective_github_token()
    masked = None
    if token:
        masked = token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
    # Optionally check rate limit
    rate_info: dict = {}
    if token:
        try:
            r = httpx.get(
                "https://api.github.com/rate_limit",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                timeout=8,
            )
            if r.status_code == 200:
                core = r.json().get("resources", {}).get("core", {})
                rate_info = {"remaining": core.get("remaining"), "limit": core.get("limit")}
        except Exception:
            pass
    return {"has_token": bool(token), "masked": masked, "rate_limit": rate_info}


@app.post("/api/v1/settings/llm-key")
def set_llm_key(body: dict) -> dict:
    """Store or clear the server-side LLM API key (persisted to .keys.json)."""
    key = (body.get("api_key") or "").strip()
    provider = (body.get("provider") or "anthropic").strip()
    if key:
        _runtime_keys["llm_api_key"] = key
        _runtime_keys["llm_provider"] = provider
    else:
        _runtime_keys.pop("llm_api_key", None)
        _runtime_keys.pop("llm_provider", None)
    _save_keys(_runtime_keys)
    return {"saved": True, "has_key": bool(_effective_llm_key()), "provider": _effective_llm_provider()}


@app.get("/api/v1/settings/llm-key-status")
def llm_key_status() -> dict:
    key = _effective_llm_key()
    masked = None
    if key:
        masked = key[:6] + "..." + key[-4:] if len(key) > 10 else "***"
    return {
        "has_key": bool(key),
        "masked": masked,
        "provider": _effective_llm_provider(),
        "source": "runtime" if _runtime_keys.get("llm_api_key") else ("env" if settings.anthropic_api_key else "none"),
    }


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
    data = fetch_repo_metrics(project.repo_full_name, token=_effective_github_token())
    project.description = data.get("description") or project.description
    project.primary_language = data.get("primary_language") or project.primary_language
    project.license_spdx = data.get("license_spdx") or project.license_spdx
    if data.get("owner_login"):
        project.owner_login = data["owner_login"]
    if data.get("owner_type"):
        project.owner_type = data["owner_type"]
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

    effective_key = api_key or _effective_llm_key()
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

    # Use server-side LLM key as fallback when caller didn't supply one
    effective_api_key = api_key or _effective_llm_key()
    effective_provider = provider or _effective_llm_provider() or "anthropic"

    if model == "llm-v1" and effective_api_key:
        readme = fetch_readme(project.repo_full_name, token=_effective_github_token())
        biz_data = generate_biz_profile_llm(
            repo_name=project.repo_full_name,
            description=project.description,
            readme=readme,
            language=project.primary_language,
            api_key=effective_api_key,
            provider=effective_provider,
        )
        if not biz_data:
            logger.warning("llm-v1 failed for %s, falling back to rule-v1", project.repo_full_name)
            model = "rule-v1"

    if biz_data is None:
        biz_data = infer_biz_profile(project.repo_full_name, project.description, project.primary_language)
        readme = fetch_readme(project.repo_full_name, token=_effective_github_token())
        desc_zh = enrich_description_zh(project.repo_full_name, project.description, readme, api_key=effective_api_key, provider=effective_provider)
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

    # Backfill star history from GitHub Stargazers API (star-history.com technique).
    # Run when we lack records older than 7 days — indicates no full history yet.
    week_ago = metric_date - timedelta(days=7)
    has_old_record = db.execute(
        select(RepoMetricDaily.metric_id).where(
            and_(RepoMetricDaily.project_id == project_id, RepoMetricDaily.metric_date < week_ago)
        ).limit(1)
    ).scalar_one_or_none()
    if not has_old_record:
        try:
            history = fetch_star_history(project.repo_full_name, token=_effective_github_token())
            for date_str, stars in history:
                hist_date = date.fromisoformat(date_str)
                if hist_date == metric_date:
                    continue  # today's record already created by refresh_metrics_for_project
                exists = db.execute(
                    select(RepoMetricDaily.metric_id).where(
                        and_(RepoMetricDaily.project_id == project_id, RepoMetricDaily.metric_date == hist_date)
                    ).limit(1)
                ).scalar_one_or_none()
                if not exists:
                    db.add(RepoMetricDaily(
                        project_id=project_id,
                        metric_date=hist_date,
                        stars=stars,
                        captured_at=datetime.utcnow(),
                    ))
        except Exception as exc:
            logger.warning("star history backfill failed for %s: %s", project.repo_full_name, exc)

    # Cross-validate API stars vs scraped stars_total_hint from most recent snapshot
    latest_m = db.execute(
        select(RepoMetricDaily).where(RepoMetricDaily.project_id == project_id).order_by(desc(RepoMetricDaily.metric_date)).limit(1)
    ).scalar_one_or_none()
    if latest_m and latest_m.stars:
        snapshot_item = db.execute(
            select(TrendingSnapshotItem)
            .join(TrendingSnapshot, TrendingSnapshotItem.snapshot_id == TrendingSnapshot.snapshot_id)
            .where(
                TrendingSnapshotItem.project_id == project_id,
                TrendingSnapshotItem.stars_total_hint.isnot(None),
            )
            .order_by(desc(TrendingSnapshot.snapshot_date))
            .limit(1)
        ).scalar_one_or_none()
        if snapshot_item and snapshot_item.stars_total_hint:
            hint = snapshot_item.stars_total_hint
            api_stars = latest_m.stars
            if hint > 0:
                deviation = abs(api_stars - hint) / hint
                if deviation > 0.20:
                    logger.warning(
                        "stars accuracy warning for %s: scraped_hint=%d api=%d deviation=%.1f%%",
                        project.repo_full_name, hint, api_stars, deviation * 100,
                    )


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


@app.get(f"{settings.api_prefix}/jobs")
def list_jobs(
    status: Optional[str] = Query(None, pattern="^(queued|running|succeeded|failed)$"),
    job_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """List jobs with optional status/type filter, newest first."""
    q = select(Job).order_by(desc(Job.created_at))
    if status:
        q = q.where(Job.status == status)
    if job_type:
        q = q.where(Job.job_type == job_type)
    jobs = db.execute(q.limit(limit)).scalars().all()
    return {
        "jobs": [
            {
                "job_id": j.job_id,
                "job_type": j.job_type,
                "status": j.status,
                "retry_count": j.retry_count,
                "max_retries": j.max_retries,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
                "error": j.error,
                "payload": j.payload,
            }
            for j in jobs
        ]
    }


@app.post(f"{settings.api_prefix}/jobs/{{job_id}}:retry", response_model=JobResp, status_code=202)
def retry_job(job_id: str, req: RetryJobReq = RetryJobReq(), background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    """Create a new copy of a failed job and re-queue it, optionally with a delay (minutes)."""
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail=f"job status is {job.status!r}; only failed jobs can be retried")

    payload = job.payload or {}
    new_job = create_job(db, job.job_type, payload)
    db.commit()

    delay = max(0, req.delay_minutes)
    jt = job.job_type

    def _dispatch(runner, *args):
        if delay > 0:
            from app.scheduler import schedule_one_time  # noqa: PLC0415
            schedule_one_time(runner, *args, delay_minutes=delay)
        else:
            background_tasks.add_task(runner, *args)

    if jt == "trending_fetch":
        snap_date = date.fromisoformat(payload["date"]) if "date" in payload else date.today()
        _dispatch(run_snapshot_fetch_job, new_job.job_id,
                  payload.get("since", "daily"), payload.get("language", "all"),
                  payload.get("spoken"), snap_date)
    elif jt == "metrics_refresh":
        metric_date = date.fromisoformat(payload["date"]) if "date" in payload else date.today()
        _dispatch(run_metrics_refresh_job, new_job.job_id,
                  payload.get("project_id"), metric_date)
    elif jt == "biz_generate":
        _dispatch(run_biz_generate_job, new_job.job_id,
                  payload.get("project_id"), payload.get("model", "rule-v1"))
    elif jt == "score_batch":
        _dispatch(run_score_batch_job, new_job.job_id,
                  payload.get("snapshot_id"), payload.get("biz_model", "rule-v1"))
    else:
        raise HTTPException(status_code=422, detail=f"cannot retry job_type {jt!r}")

    return JobResp(job_id=new_job.job_id, status=new_job.status)


@app.get(f"{settings.api_prefix}/scheduler/status")
def scheduler_status():
    """Return APScheduler state and next run times for each scheduled job."""
    from app.scheduler import get_scheduler_status  # noqa: PLC0415
    return get_scheduler_status()


@app.post(f"{settings.api_prefix}/scheduler/reschedule")
def scheduler_reschedule(req: RescheduleReq):
    """Change the UTC hour:minute for a scheduled cron job."""
    from app.scheduler import reschedule_job  # noqa: PLC0415
    ok = reschedule_job(req.job_id, req.hour, req.minute)
    if not ok:
        raise HTTPException(status_code=404, detail="scheduler job not found or scheduler not running")
    return {"ok": True, "job_id": req.job_id, "hour": req.hour, "minute": req.minute}


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
            "followups":  score.followups  or [],
            "highlights": score.highlights or [],
            "risks":      score.risks      or [],
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
    import json as _json
    from collections import defaultdict

    project = db.get(Project, payload.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    score = latest_score(db, project.project_id)
    biz   = latest_biz(db, project.project_id)
    expl  = (biz.explanations or {}) if biz else {}

    # ── metrics ──────────────────────────────────────────────────────────────
    metrics_rows = db.execute(
        select(RepoMetricDaily)
        .where(RepoMetricDaily.project_id == project.project_id)
        .order_by(RepoMetricDaily.metric_date.asc())
    ).scalars().all()

    latest_m = metrics_rows[-1] if metrics_rows else None
    cur_stars   = latest_m.stars            if latest_m else None
    cur_forks   = latest_m.forks            if latest_m else None
    cur_issues  = latest_m.open_issues      if latest_m else None
    cur_commits = latest_m.commits_30d      if latest_m else None
    cur_contribs= latest_m.contributors_90d if latest_m else None
    bus_factor  = latest_m.bus_factor_top1_share if latest_m else None

    # ── star history ─────────────────────────────────────────────────────────
    star_dates: list[str] = []
    star_values: list[int] = []
    star_source = ""

    star_dates  = [str(r.metric_date) for r in metrics_rows if r.stars is not None]
    star_values = [r.stars            for r in metrics_rows if r.stars is not None]
    if star_dates:
        star_source = "GitHub Stargazers API"

    if len(star_dates) <= 1:
        try:
            history = fetch_star_history(project.repo_full_name, token=_effective_github_token(), max_samples=5)
            if len(history) > len(star_dates):
                star_dates  = [h[0] for h in history]
                star_values = [h[1] for h in history]
                star_source = "GitHub Stargazers API (预览)"
        except Exception:
            pass

    if not star_dates:
        trending_rows = db.execute(
            select(TrendingSnapshotItem, TrendingSnapshot.snapshot_date)
            .join(TrendingSnapshot, TrendingSnapshotItem.snapshot_id == TrendingSnapshot.snapshot_id)
            .where(TrendingSnapshotItem.project_id == project.project_id)
            .where(TrendingSnapshotItem.stars_total_hint.isnot(None))
            .order_by(TrendingSnapshot.snapshot_date.asc())
        ).all()
        star_dates  = [str(row[1]) for row in trending_rows]
        star_values = [row[0].stars_total_hint for row in trending_rows]
        if star_dates:
            star_source = "Trending 快照"

    # ── monthly growth table ──────────────────────────────────────────────────
    def monthly_growth(dates, values):
        monthly: dict[str, int] = {}
        for d, v in zip(dates, values):
            monthly[d[:7]] = v
        rows = []
        keys = sorted(monthly)
        for i, m in enumerate(keys):
            v   = monthly[m]
            pv  = monthly[keys[i - 1]] if i > 0 else v
            delta = v - pv
            mom   = (delta / pv * 100) if pv > 0 else 0.0
            rows.append({"month": m, "stars": v, "delta": delta, "mom": mom})
        return rows[-12:]

    growth_rows = monthly_growth(star_dates, star_values)

    # ── score helpers ─────────────────────────────────────────────────────────
    def s2g(v):
        if v is None: return "N/A"
        if v >= 8.5:  return "S"
        if v >= 7.0:  return "A"
        if v >= 5.5:  return "B"
        if v >= 4.0:  return "C"
        return "D"

    def grade_color(g):
        return {"S": "#f59e0b", "A": "#22c55e", "B": "#3b82f6", "C": "#6b7280", "D": "#ef4444"}.get(g, "#94a3b8")

    def grade_badge(g):
        c = grade_color(g)
        tc = "#000" if g in ("S", "A") else "#fff"
        return f'<span style="display:inline-block;padding:3px 12px;border-radius:16px;font-weight:700;font-size:13px;background:{c};color:{tc}">{g}</span>'

    def fmt_num(v):
        if v is None: return "—"
        return f"{v/1000:.1f}k" if v >= 1000 else str(v)

    def pct(v):
        if v is None: return "—"
        return f"{v:.0%}"

    # ── YC dimensions ────────────────────────────────────────────────────────
    yc_dims = [
        ("Traction & Growth",    "牵引力与增长",  0.30, score.traction_score    if score else None),
        ("Problem & Market",     "问题与市场",    0.20, score.market_score       if score else None),
        ("Product & Technology", "产品与技术",    0.20, score.moat_score         if score else None),
        ("Business Model",       "商业模式",      0.15, score.monetization_score if score else None),
        ("Team & Community",     "团队与社区",    0.05, score.team_score         if score else None),
        ("Risk (inverted)",      "风险(反转)",    0.10, (10 - score.risk_score)  if score and score.risk_score else None),
    ]
    yc_score_100 = None
    if score:
        parts = [(w * v) for _, _, w, v in yc_dims if v is not None]
        ws    = sum(w for _, _, w, v in yc_dims if v is not None)
        yc_score_100 = round(sum(parts) / ws * 10, 1) if ws > 0 else None

    # ── recommendation ────────────────────────────────────────────────────────
    if yc_score_100 is None:
        rec = "数据不足，暂无建议"
        rec_color = "#64748b"
    elif yc_score_100 >= 80:
        rec = "强烈推荐跟进 🚀"
        rec_color = "#22c55e"
    elif yc_score_100 >= 65:
        rec = "值得持续观察 👀"
        rec_color = "#3b82f6"
    elif yc_score_100 >= 50:
        rec = "暂不推荐，等待更多信号 ⏳"
        rec_color = "#f59e0b"
    else:
        rec = "不推荐，商业潜力有限 ❌"
        rec_color = "#ef4444"

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    signals      = (score.explanations or {}).get("signals_text", {}) if score else {}

    # ── section: 项目概况 ─────────────────────────────────────────────────────
    first_date = star_dates[0][:7] if star_dates else "—"
    _otype = project.owner_type or "—"
    _ologin = project.owner_login or (project.repo_full_name.split("/")[0] if "/" in project.repo_full_name else "—")
    _owner_html = (
        f'<a href="https://github.com/{_ologin}" style="color:#0f3460">{_ologin}</a>'
        f' <span style="color:#94a3b8;font-size:11px">({_otype})</span>'
    )
    overview_rows = [
        ("仓库", f'<a href="{project.repo_url}" style="color:#0f3460">{project.repo_full_name}</a>'),
        ("所属账号", _owner_html),
        ("账号类型", "企业/组织 (Organization)" if _otype == "Organization" else ("个人 (User)" if _otype == "User" else "—")),
        ("主语言", project.primary_language or "—"),
        ("首次上榜", first_date),
        ("当前 Stars", fmt_num(cur_stars)),
        ("Forks", fmt_num(cur_forks)),
        ("Open Issues", fmt_num(cur_issues)),
        ("贡献者 (90d)", fmt_num(cur_contribs)),
        ("Commits (30d)", fmt_num(cur_commits)),
        ("主维护者集中度", f"{bus_factor:.0%}" if bus_factor else "—"),
        ("商业赛道", biz.category if biz else "—"),
        ("变现形式", "、".join(biz.monetization_candidates or []) if biz else "—"),
        ("销售动力", biz.sales_motion if biz else "—"),
        ("License", expl.get("license") or "—"),
    ]
    overview_html = "".join(
        f'<tr><td style="color:#64748b;width:130px;padding:6px 8px;vertical-align:top;font-size:13px">{k}</td>'
        f'<td style="padding:6px 8px;font-size:13px;font-weight:500">{v}</td></tr>'
        for k, v in overview_rows
    )

    # ── section: Star 增长数据表 ──────────────────────────────────────────────
    growth_table_html = ""
    if growth_rows:
        rows_html = ""
        for r in growth_rows:
            delta_str = f'+{fmt_num(r["delta"])}' if r["delta"] >= 0 else fmt_num(r["delta"])
            mom_str   = f'+{r["mom"]:.1f}%' if r["mom"] >= 0 else f'{r["mom"]:.1f}%'
            mom_color = "#22c55e" if r["mom"] > 10 else ("#f59e0b" if r["mom"] > 0 else "#64748b")
            rows_html += (
                f'<tr><td>{r["month"]}</td><td>{fmt_num(r["stars"])}</td>'
                f'<td>{delta_str}</td>'
                f'<td style="color:{mom_color};font-weight:600">{mom_str}</td></tr>'
            )
        growth_table_html = f"""
        <div style="margin-top:14px;overflow-x:auto">
          <table style="width:100%;border-collapse:collapse;font-size:12px">
            <thead><tr style="background:#f8fafc;color:#64748b">
              <th style="padding:6px 8px;text-align:left">月份</th>
              <th style="padding:6px 8px;text-align:left">Stars</th>
              <th style="padding:6px 8px;text-align:left">月增量</th>
              <th style="padding:6px 8px;text-align:left">MoM%</th>
            </tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""

    # ── section: Chart.js ────────────────────────────────────────────────────
    star_source_note = f' <span style="font-size:10px;font-weight:400;color:#94a3b8">· {star_source}</span>' if star_source else ""
    star_chart_section = ""
    chart_script = ""
    if star_dates:
        star_chart_section = (
            f'<div class="card"><h2>📈 Star 增长趋势{star_source_note}</h2>'
            f'<canvas id="starChart" style="max-height:220px"></canvas>'
            f'{growth_table_html}</div>'
        )
        _lbl = _json.dumps(star_dates)
        _dat = _json.dumps(star_values)
        # Build JS without f-string to avoid brace-escaping issues
        _js = (
            'new Chart(document.getElementById("starChart"),{'
            'type:"line",'
            'data:{labels:' + _lbl + ','
            'datasets:[{label:"Stars",data:' + _dat + ','
            'borderColor:"#0f3460",backgroundColor:"rgba(15,52,96,0.08)",'
            'borderWidth:2,pointRadius:3,tension:0.3,fill:true}]},'
            'options:{responsive:true,'
            'plugins:{legend:{display:false},'
            'tooltip:{callbacks:{label:function(c){'
            'var v=c.parsed.y;return(v>=1000?(v/1000).toFixed(1)+"k":""+v)+" \u2605";'
            '}}}},'   # closes: function, callbacks, tooltip, plugins
            'scales:{'
            'x:{ticks:{maxTicksLimit:8,font:{size:10}}},'
            'y:{ticks:{font:{size:10},callback:function(v){'
            'return v>=1000?(v/1000).toFixed(0)+"k":v;'
            '}}}}'    # closes: callback func, y.ticks, y, scales
            '}});'    # closes: options, Chart config object, new Chart() call
        )
        chart_script = (
            '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>'
            '<script>window.addEventListener("DOMContentLoaded",function(){'
            'var el=document.getElementById("starChart");if(!el)return;'
            + _js +
            '});</script>'
        )

    # ── section: traction ─────────────────────────────────────────────────────
    fork_rate = f"{cur_forks/cur_stars:.1%}" if cur_stars and cur_forks and cur_stars > 0 else "—"
    star_mom  = f'+{growth_rows[-1]["mom"]:.1f}%' if growth_rows else "—"
    traction_grade = s2g(score.traction_score if score else None)
    traction_bullets = [
        f"Star 月增长率：{star_mom}",
        f"Fork/Star 比：{fork_rate}（高说明开发者真正在用）",
        f"近 90 天活跃贡献者：{fmt_num(cur_contribs)}",
        f"近 30 天 Commits：{fmt_num(cur_commits)}",
        f"主维护者集中度：{'高（单点风险）' if bus_factor and bus_factor > 0.5 else '较分散（健康）' if bus_factor else '—'}",
    ] + (score.highlights or [])[:3]

    # ── section: problem & market ────────────────────────────────────────────
    market_grade = s2g(score.market_score if score else None)
    scenarios_str = "、".join(biz.scenarios or []) if biz else "—"
    market_base   = getattr(biz, "market_base", None) if biz else None
    market_bullets = [
        f"目标用户：{biz.buyer if biz else '—'}",
        f"核心应用场景：{scenarios_str}",
        f"市场基础评分：{market_base:.1f}/10" if market_base else "市场规模：待评估",
        f"项目描述：{(project.description or '—')[:120]}",
    ] + [signals.get("market", "")]

    # ── section: product & technology ────────────────────────────────────────
    moat_grade = s2g(score.moat_score if score else None)
    delivery   = "、".join(biz.delivery_forms or []) if biz else "—"
    moat_bullets = [
        f"主要编程语言：{project.primary_language or '—'}",
        f"交付形态：{delivery}",
        f"护城河信号：{signals.get('moat', '—')}",
        f"置信度：{pct(biz.confidence) if biz else '—'}（基于 {'AI 分析' if biz and biz.model_name != 'rule-v1' else '规则匹配'}）",
    ]

    # ── section: business model ───────────────────────────────────────────────
    biz_grade  = s2g(score.monetization_score if score else None)
    mono_items = biz.monetization_candidates or [] if biz else []
    form_items = biz.delivery_forms          or [] if biz else []
    _motion_desc_zh = {
        "PLG": "产品驱动增长 — 用户自助发现、体验并付费",
        "Enterprise": "企业直销 — 依靠专职销售团队拓展企业合同",
    }.get(biz.sales_motion if biz else "", biz.sales_motion if biz else "—")

    biz_table_rows = ""
    for item in mono_items[:5]:
        feasibility = "⭐⭐⭐" if item in ["SaaS", "Cloud", "API", "Enterprise"] else "⭐⭐"
        biz_table_rows += f'<tr><td>{item}</td><td>{feasibility}</td><td>适合 {biz.buyer or "技术团队"}</td></tr>'
    if not biz_table_rows:
        biz_table_rows = '<tr><td colspan="3" style="color:#94a3b8">暂无分析数据，建议运行 AI 分析</td></tr>'

    # ── section: team & community ────────────────────────────────────────────
    team_grade  = s2g(score.team_score if score else None)
    bd_pitch    = expl.get("bd_pitch") or ""

    # owner / company context
    _owner_login = project.owner_login or (project.repo_full_name.split("/")[0] if "/" in project.repo_full_name else None)
    _owner_type  = project.owner_type or "Unknown"
    if _owner_type == "Organization":
        _owner_label = f"组织维护（GitHub Org: [{_owner_login}](https://github.com/{_owner_login})）"
    elif _owner_type == "User":
        _owner_label = f"个人维护（GitHub User: [{_owner_login}](https://github.com/{_owner_login})）"
    else:
        _owner_label = f"维护者：{_owner_login or '—'}"

    _n = cur_contribs or 0
    _team_health = (
        f"多团队规模（{_n} 人活跃），外部贡献充分，单点风险低" if _n >= 30 else
        f"中等规模（{_n} 人活跃），有一定外部贡献" if _n >= 10 else
        f"小团队（{_n} 人活跃），主要由核心作者驱动" if _n >= 3 else
        f"极小团队（{_n} 人活跃），高度依赖核心作者" if _n > 0 else
        "贡献者数据待采集"
    )
    team_bullets = [
        _owner_label,
        f"近 90 天贡献者数：{fmt_num(cur_contribs)}",
        f"主维护者集中度：{pct(bus_factor)}（>50% 表示单点风险）" if bus_factor else "主维护者集中度：数据待采集",
        f"团队健康度：{_team_health}",
        f"销售动力评估：{_motion_desc_zh}",
    ]

    # ── section: competitor analysis ─────────────────────────────────────────
    # tuples: (name, kind, pos, diff, github_url)
    _comp_db: dict[str, list[tuple]] = {
        "agent": [
            ("LangChain",    "OSS",       "通用 LLM 链式框架", "生态最大；护城河弱，社区分裂",   "https://github.com/langchain-ai/langchain"),
            ("LlamaIndex",   "OSS+SaaS",  "RAG & Agent",       "数据连接强；偏检索场景",         "https://github.com/run-llama/llama_index"),
            ("AutoGen",      "OSS",       "多 Agent 编排",     "微软背书；适合复杂对话流",       "https://github.com/microsoft/autogen"),
            ("CrewAI",       "OSS",       "角色分工 Agent",    "上手快；功能较局限",             "https://github.com/crewAIInc/crewAI"),
        ],
        "developer-tools": [
            ("GitHub Copilot","SaaS",     "AI 代码补全",       "市场主导；封闭，价格高",         "https://github.com/github"),
            ("Cursor",        "SaaS",     "AI IDE",            "开发者口碑好；依赖 Claude/GPT",  "https://github.com/getcursor/cursor"),
            ("Tabnine",       "OSS+SaaS", "本地代码补全",      "隐私友好；增速放缓",             "https://github.com/codota/TabNine"),
        ],
        "observability": [
            ("Datadog",       "SaaS",     "全栈可观测",        "功能完整；价格贵，厂商锁定",     "https://github.com/DataDog"),
            ("Grafana",       "OSS+SaaS", "指标可视化",        "生态最大；配置复杂",             "https://github.com/grafana/grafana"),
            ("OpenTelemetry", "OSS",      "标准协议层",        "CNCF 标准；非竞品，可互补",      "https://github.com/open-telemetry/opentelemetry-specification"),
        ],
        "security": [
            ("Snyk",          "SaaS",     "代码安全扫描",      "CI/CD 集成好；企业价格高",       "https://github.com/snyk/snyk"),
            ("SonarQube",     "OSS+SaaS", "代码质量门禁",      "企业广用；UI 老旧",              "https://github.com/SonarSource/sonarqube"),
            ("Wiz",           "SaaS",     "云安全态势",        "估值 120亿+；功能专注云",        "https://github.com/wiz-sec"),
        ],
        "data": [
            ("dbt",           "OSS+SaaS", "数据转换",          "Analytics 工程标准；ELT 专用",   "https://github.com/dbt-labs/dbt-core"),
            ("Airbyte",       "OSS",      "数据集成",          "连接器最多；资源消耗大",         "https://github.com/airbytehq/airbyte"),
            ("Apache Spark",  "OSS",      "大数据批处理",      "生态成熟；运维复杂",             "https://github.com/apache/spark"),
        ],
        "infra": [
            ("Terraform",     "OSS",      "IaC 标准",          "用户最多；HCL 语言割裂",         "https://github.com/hashicorp/terraform"),
            ("Pulumi",        "OSS+SaaS", "编程语言 IaC",      "开发者友好；社区较小",           "https://github.com/pulumi/pulumi"),
            ("Ansible",       "OSS",      "配置管理",          "无 Agent；适合存量系统",         "https://github.com/ansible/ansible"),
        ],
        "fintech": [
            ("Stripe",        "SaaS",     "支付 API",          "开发者首选；费率不低",           "https://github.com/stripe"),
            ("Plaid",         "SaaS",     "银行数据连接",      "金融数据标准；监管敏感",         "https://github.com/plaid"),
        ],
        "edu-tech": [
            ("Coursera",      "SaaS",     "在线课程平台",      "内容最多；变现靠认证",           "https://github.com/coursera"),
            ("Duolingo",      "SaaS",     "语言学习",          "游戏化强；聚焦语言",             "https://github.com/duolingo"),
        ],
        "biotech": [
            ("Benchling",     "SaaS",     "生命科学 R&D 平台", "实验室标准；价格高",             "https://github.com/benchling"),
            ("Dotmatics",     "SaaS",     "科学数据管理",      "大药企首选；封闭",               ""),
        ],
    }
    _cat_key  = (biz.category or "").lower().replace(" ", "-") if biz else ""
    comp_list = _comp_db.get(_cat_key, [])
    if not comp_list:
        # Generic fallback: show placeholder
        comp_list = [("（待补充）", "—", "同类开源/商业竞品", "需结合项目具体赛道人工补充", "")]

    comp_rows_html = ""
    for name, kind, pos, diff, gh_url in comp_list:
        name_cell = (
            f'<a href="{gh_url}" target="_blank" rel="noopener" '
            f'style="font-weight:600;color:#1e40af;text-decoration:none">'
            f'{name}&nbsp;↗</a>'
            if gh_url else
            f'<span style="font-weight:600">{name}</span>'
        )
        comp_rows_html += (
            f'<tr><td>{name_cell}</td>'
            f'<td><span style="font-size:11px;background:#e0f2fe;color:#0369a1;padding:2px 7px;border-radius:10px">{kind}</span></td>'
            f'<td style="color:#374151">{pos}</td>'
            f'<td style="color:#64748b;font-size:12px">{diff}</td></tr>'
        )
    comp_section_html = f"""
    <div class="card">
      <h2>🏁 竞品分析 <span style="color:#94a3b8;font-size:10px;font-weight:400;text-transform:none">Competitive Landscape · {biz.category if biz else '—'} 赛道</span></h2>
      <table>
        <thead><tr><th>竞品</th><th>类型</th><th>定位</th><th>对比要点</th></tr></thead>
        <tbody>{comp_rows_html}</tbody>
      </table>
      <div style="margin-top:10px;font-size:12px;color:#94a3b8">⚑ 竞品数据基于规则库，建议结合 AI 分析获取更精准对比</div>
    </div>"""

    # ── section: risk ────────────────────────────────────────────────────────
    raw_risks = (score.risks or []) if score else []
    risk_score_inv = (10 - score.risk_score) if score and score.risk_score else None

    _risk_cat = [
        ("大厂竞争风险",   "medium", "大型云厂商或开源基金会可能推出竞品"),
        ("License 风险",  "low",    f"当前 License：{expl.get('license', '待确认')}"),
        ("维护者单点风险", "high" if bus_factor and bus_factor > 0.5 else "low",
         f"主维护者集中度 {pct(bus_factor)}，{'需引入更多 Contributor' if bus_factor and bus_factor > 0.5 else '贡献分布健康'}"),
        ("商业化转化风险", "medium", "从 OSS 用户转为付费用户需要明确的企业版价值主张"),
        ("技术过时风险",   "low",    f"主语言 {project.primary_language or '未知'}，{signals.get('moat', '技术细节待评估')}"),
    ]
    risk_level_color = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}
    risk_level_label = {"high": "高", "medium": "中", "low": "低"}
    risk_rows_html = ""
    for cat, lvl, desc in _risk_cat:
        c = risk_level_color.get(lvl, "#94a3b8")
        l = risk_level_label.get(lvl, lvl)
        risk_rows_html += (
            f'<tr><td style="font-weight:500">{cat}</td>'
            f'<td><span style="color:{c};font-weight:700">{l}</span></td>'
            f'<td style="color:#64748b;font-size:12px">{desc}</td></tr>'
        )
    for r in raw_risks[:3]:
        risk_rows_html += f'<tr><td colspan="3" style="color:#64748b;font-size:12px;padding-left:8px">· {r}</td></tr>'

    # ── section: YC 综合评分表 ────────────────────────────────────────────────
    yc_score_rows = ""
    for en, zh, w, v in yc_dims:
        g = s2g(v)
        c = grade_color(g)
        score_100 = round(v * 10, 0) if v is not None else None
        yc_score_rows += (
            f'<tr><td style="font-weight:500">{zh} <span style="color:#94a3b8;font-size:11px">{en}</span></td>'
            f'<td style="color:#64748b">{w:.0%}</td>'
            f'<td>{grade_badge(g) if g != "N/A" else "—"}</td>'
            f'<td style="font-weight:700;color:#0f3460">{int(score_100) if score_100 else "—"} / 100</td></tr>'
        )
    yc_total_row = (
        f'<tr style="background:#f0f9ff;font-weight:700">'
        f'<td>综合得分</td><td>100%</td><td>{grade_badge(score.grade) if score else "—"}</td>'
        f'<td style="font-size:18px;color:#0f3460">{yc_score_100 if yc_score_100 else "—"} / 100</td></tr>'
    )

    # ── section: 投资建议 ────────────────────────────────────────────────────
    followups = (score.followups or []) if score else []
    checklist_html = ""
    default_actions = [
        "联系维护团队了解商业化计划",
        "调研企业用户付费意愿",
        "监控 Star 增速是否持续",
        "评估 License 变更风险",
        "关注大厂竞品动态",
    ]
    for item in (followups or default_actions)[:6]:
        checklist_html += f'<li style="margin-bottom:6px">{item}</li>'

    timing = (
        "现在是介入好时机（增速强劲）" if yc_score_100 and yc_score_100 >= 75 else
        "等待商业化路径明确后介入" if yc_score_100 and yc_score_100 >= 55 else
        "暂时观察，等待更多市场验证"
    )
    bd_section_html = f'<div style="margin-top:10px;background:#fefce8;border-left:3px solid #f59e0b;padding:10px 14px;border-radius:6px;font-size:13px;line-height:1.7">{bd_pitch}</div>' if bd_pitch else ""

    # ── markdown content generation ──────────────────────────────────────────
    def _md_table(headers, rows):
        sep = "|".join("---" for _ in headers)
        lines = ["| " + " | ".join(headers) + " |", f"|{sep}|"]
        for r in rows:
            lines.append("| " + " | ".join(str(c) for c in r) + " |")
        return "\n".join(lines)

    _overview_md_rows = [
        ("仓库", project.repo_url),
        ("所属账号", f"{_ologin} ({_otype})"),
        ("主语言", project.primary_language or "—"),
        ("当前 Stars", fmt_num(cur_stars)),
        ("Forks", fmt_num(cur_forks)),
        ("Open Issues", fmt_num(cur_issues)),
        ("贡献者 (90d)", fmt_num(cur_contribs)),
        ("Commits (30d)", fmt_num(cur_commits)),
        ("商业赛道", biz.category if biz else "—"),
        ("变现形式", "、".join(biz.monetization_candidates or []) if biz else "—"),
        ("销售动力", biz.sales_motion if biz else "—"),
    ]
    _growth_md_rows = [(r["month"], fmt_num(r["stars"]),
                        f'+{fmt_num(r["delta"])}' if r["delta"] >= 0 else fmt_num(r["delta"]),
                        f'{r["mom"]:+.1f}%') for r in growth_rows]
    _biz_md_rows = [(item,
                     "⭐⭐⭐" if item in ["SaaS","Cloud","API","Enterprise"] else "⭐⭐",
                     biz.buyer or "技术团队") for item in mono_items[:5]] or [("暂无", "—", "—")]
    _comp_md_rows = [
        (f"[{n}]({gh})" if gh else n, k, p, d)
        for n, k, p, d, gh in comp_list
    ]
    _risk_md_rows = [(cat, risk_level_label.get(lvl, lvl), desc) for cat, lvl, desc in _risk_cat]
    _yc_md_rows   = [(f"{zh} ({en})", f"{w:.0%}", s2g(v),
                      f"{int(v*10)}/100" if v else "—") for en, zh, w, v in yc_dims]
    _yc_md_rows.append(("综合得分", "100%", score.grade if score else "—",
                         f"{yc_score_100}/100" if yc_score_100 else "—"))

    md_lines = [
        f"# {project.repo_full_name} — YC 开源投资分析报告",
        f"",
        f"> 生成时间：{generated_at} | {project.repo_url}",
        f"",
        f"## 投资建议",
        f"",
        f"**{rec}**  综合 YC 评分：**{yc_score_100 if yc_score_100 else 'N/A'} / 100**  等级：{score.grade if score else '—'}",
        f"",
        f"建议介入时机：{timing}",
        f"",
    ]
    if bd_pitch:
        md_lines += [f"> BD 话术：{bd_pitch}", ""]

    md_lines += [
        "## 项目概况", "",
        _md_table(["指标", "值"], _overview_md_rows), "",
        "## Star 增长趋势", "",
    ]
    if _growth_md_rows:
        md_lines += [_md_table(["月份", "Stars", "月增量", "MoM%"], _growth_md_rows), ""]
    else:
        md_lines += ["暂无增长数据", ""]

    def _yc_section_md(title_zh, title_en, grade_val, bullets):
        lines = [f"## {title_zh} ({title_en}) — 评级：{s2g(grade_val)}", ""]
        lines += [f"- {b}" for b in bullets if b and b != "—"]
        lines.append("")
        return lines

    md_lines += _yc_section_md("牵引力与增长", "Traction & Growth",
                                score.traction_score if score else None, traction_bullets)
    md_lines += _yc_section_md("问题与市场", "Problem & Market",
                                score.market_score if score else None, market_bullets)
    md_lines += _yc_section_md("产品与技术", "Product & Technology",
                                score.moat_score if score else None, moat_bullets)
    md_lines += [
        f"## 商业模式 (Business Model) — 评级：{s2g(score.monetization_score if score else None)}", "",
        _md_table(["变现方式", "可行性", "说明"], _biz_md_rows), "",
        f"推荐销售动力：{_motion_desc_zh}", "",
    ]
    md_lines += _yc_section_md("团队与社区", "Team & Community",
                                score.team_score if score else None, team_bullets)
    md_lines += [
        "## 竞品分析 (Competitive Landscape)", "",
        _md_table(["竞品", "类型", "定位", "对比要点"], _comp_md_rows), "",
        "## 风险评估 (Risk Assessment)", "",
        _md_table(["风险类型", "等级", "描述"], _risk_md_rows), "",
    ]
    if raw_risks:
        md_lines += [f"- {r}" for r in raw_risks[:3]] + [""]
    md_lines += [
        "## YC 综合评分", "",
        _md_table(["维度", "权重", "评级", "得分"], _yc_md_rows), "",
        "## 建议行动清单", "",
    ]
    for item in (followups or default_actions)[:6]:
        md_lines.append(f"- [ ] {item}")
    md_lines += [
        "",
        "---",
        "*Trend2Biz · YC OSS Investment Analysis · 数据来源 GitHub · 分析仅供参考*",
    ]
    import base64 as _b64
    md_content_b64 = _b64.b64encode("\n".join(md_lines).encode()).decode()

    # ── HTML card helper ──────────────────────────────────────────────────────
    def yc_card(title_en, title_zh, grade_val, bullets):
        g = s2g(grade_val)
        bullet_html = "".join(f'<li style="margin-bottom:6px;color:#374151">{b}</li>' for b in bullets if b and b != "—")
        return f"""
        <div class="card">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
            <h2 style="margin:0;flex:1">{title_zh} <span style="color:#94a3b8;font-size:10px;font-weight:400;text-transform:none">{title_en}</span></h2>
            {grade_badge(g) if g != "N/A" else ""}
          </div>
          <ul style="margin:0;padding-left:18px">{bullet_html}</ul>
        </div>"""

    # ── assemble HTML ─────────────────────────────────────────────────────────
    _repo_slug = project.repo_full_name.replace("/", "_")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{project.repo_full_name} — YC 投资分析报告</title>
  <style>
    body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#1a1a2e;background:#f8fafc}}
    .header{{background:linear-gradient(135deg,#0f3460 0%,#16213e 100%);color:#fff;padding:28px 32px;border-radius:12px;margin-bottom:20px}}
    .header h1{{margin:0 0 6px;font-size:22px;font-weight:700}}
    .header .meta{{font-size:13px;opacity:.8}}
    .card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:20px 24px;margin-bottom:16px}}
    .card h2{{font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:.05em;margin:0 0 12px}}
    table{{width:100%;border-collapse:collapse}}
    td,th{{padding:6px 8px;text-align:left;border-bottom:1px solid #f1f5f9;font-size:13px}}
    th{{background:#f8fafc;color:#64748b;font-weight:600}}
    tr:last-child td{{border-bottom:none}}
    .rec-box{{border:2px solid;border-radius:10px;padding:16px 20px;margin-bottom:16px}}
    .footer{{text-align:center;font-size:12px;color:#94a3b8;margin-top:28px;padding-bottom:40px}}
    .dl-bar{{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}}
    .dl-btn{{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:8px;
             font-size:13px;font-weight:600;cursor:pointer;border:none;text-decoration:none}}
    .dl-btn-md{{background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0}}
    .dl-btn-pdf{{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe}}
    .dl-btn:hover{{opacity:.85}}
    @media print{{
      .dl-bar,.footer{{display:none}}
      body{{background:#fff;margin:0}}
      .card{{break-inside:avoid;border:1px solid #ddd}}
      .rec-box{{break-inside:avoid}}
    }}
  </style>
</head>
<body>
  <div class="header">
    <h1>📊 {project.repo_full_name}</h1>
    <div class="meta">
      YC 开源项目投资分析报告 &nbsp;·&nbsp;
      <a href="{project.repo_url}" style="color:#93c5fd">{project.repo_url}</a>
      &nbsp;·&nbsp; {generated_at}
    </div>
  </div>

  <!-- 下载工具栏 -->
  <div class="dl-bar">
    <button class="dl-btn dl-btn-md" onclick="downloadMd()">⬇ 下载 Markdown</button>
    <button class="dl-btn dl-btn-pdf" onclick="window.print()">🖨 导出 PDF（打印）</button>
  </div>

  <!-- 投资建议（置顶） -->
  <div class="rec-box" style="border-color:{rec_color};background:{rec_color}18">
    <div style="font-size:18px;font-weight:700;color:{rec_color};margin-bottom:6px">{rec}</div>
    <div style="font-size:14px;color:#374151">
      综合 YC 评分：<strong style="font-size:20px;color:#0f3460">{yc_score_100 if yc_score_100 else 'N/A'}</strong> / 100
      &nbsp;·&nbsp; 等级：{grade_badge(score.grade) if score else '—'}
      &nbsp;·&nbsp; 建议介入时机：{timing}
    </div>
    {bd_section_html}
  </div>

  <!-- 1. 项目概况 -->
  <div class="card">
    <h2>📋 项目概况</h2>
    <table><tbody>{overview_html}</tbody></table>
  </div>

  <!-- 2. Star 增长趋势 -->
  {star_chart_section}

  <!-- 3. Traction & Growth -->
  {yc_card("Traction &amp; Growth", "📈 牵引力与增长", score.traction_score if score else None, traction_bullets)}

  <!-- 4. Problem & Market -->
  {yc_card("Problem &amp; Market", "🎯 问题与市场", score.market_score if score else None, market_bullets)}

  <!-- 5. Product & Technology -->
  {yc_card("Product &amp; Technology", "🛠 产品与技术", score.moat_score if score else None, moat_bullets)}

  <!-- 6. Business Model -->
  <div class="card">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
      <h2 style="margin:0;flex:1">💰 商业模式 <span style="color:#94a3b8;font-size:10px;font-weight:400;text-transform:none">Business Model</span></h2>
      {grade_badge(s2g(score.monetization_score if score else None))}
    </div>
    <table>
      <thead><tr><th>变现方式</th><th>可行性</th><th>说明</th></tr></thead>
      <tbody>{biz_table_rows}</tbody>
    </table>
    <div style="margin-top:10px;font-size:12px;color:#64748b">推荐销售动力：{_motion_desc_zh}</div>
  </div>

  <!-- 7. Team & Community -->
  {yc_card("Team &amp; Community", "👥 团队与社区", score.team_score if score else None, team_bullets)}

  <!-- 8. Competitive Landscape -->
  {comp_section_html}

  <!-- 9. Risk Assessment -->
  <div class="card">
    <h2>⚠️ 风险评估 <span style="color:#94a3b8;font-size:10px;font-weight:400;text-transform:none">Risk Assessment</span></h2>
    <table>
      <thead><tr><th>风险类型</th><th>等级</th><th>描述</th></tr></thead>
      <tbody>{risk_rows_html}</tbody>
    </table>
  </div>

  <!-- 10. YC 综合评分 -->
  <div class="card">
    <h2>🎯 YC 综合评分</h2>
    <table>
      <thead><tr><th>维度</th><th>权重</th><th>评级</th><th>得分</th></tr></thead>
      <tbody>{yc_score_rows}{yc_total_row}</tbody>
    </table>
  </div>

  <!-- 11. 建议行动 -->
  <div class="card">
    <h2>💡 建议行动清单</h2>
    <ul style="margin:0;padding-left:18px">{checklist_html}</ul>
  </div>

  <div class="footer">
    Trend2Biz · YC OSS Investment Analysis Skill · 数据来源 GitHub &amp; Trend2Biz DB · 分析仅供参考
  </div>

<script>
var _mdB64 = "{md_content_b64}";
function downloadMd() {{
  var bytes = Uint8Array.from(atob(_mdB64), function(c){{return c.charCodeAt(0);}});
  var md = new TextDecoder("utf-8").decode(bytes);
  var blob = new Blob([md], {{type:"text/markdown;charset=utf-8"}});
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "{_repo_slug}_yc_report.md";
  document.body.appendChild(a); a.click();
  setTimeout(function(){{document.body.removeChild(a);URL.revokeObjectURL(a.href);}}, 100);
}}
</script>
{chart_script}
</body>
</html>"""

    report = Report(project_id=project.project_id, score_id=score.score_id if score else None, format=payload.format, content=html)
    db.add(report)
    db.commit()
    return ReportOut(report_id=report.report_id, url=f"/reports/{report.report_id}")


@app.get("/reports/{report_id}", response_class=HTMLResponse)
def get_report(report_id: str, db: Session = Depends(get_db)):
    report = db.execute(select(Report).where(Report.report_id == report_id)).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return HTMLResponse(content=report.content)


@app.get(f"{settings.api_prefix}/reports/{{report_id}}/markdown", response_class=Response)
def get_report_markdown(report_id: str, db: Session = Depends(get_db)):
    """Return the markdown content embedded in the report HTML (for CLI / skill use)."""
    import re as _re
    report = db.execute(select(Report).where(Report.report_id == report_id)).scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    m = _re.search(r'var _mdB64 = "([A-Za-z0-9+/=]+)"', report.content)
    if not m:
        raise HTTPException(status_code=404, detail="Markdown not found in report")
    md_bytes = base64.b64decode(m.group(1))
    return Response(content=md_bytes, media_type="text/markdown; charset=utf-8")


@app.get(f"{settings.api_prefix}/projects/search")
def search_projects(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Full-text search across repo name, description, Chinese description, category, monetization."""
    like = f"%{q}%"
    # First find matching projects
    matched_projects = db.execute(
        select(Project).where(
            or_(
                Project.repo_full_name.ilike(like),
                Project.description.ilike(like),
            )
        ).order_by(desc(Project.last_seen_at)).limit(limit)
    ).scalars().all()
    pid_set = {p.project_id for p in matched_projects}

    # Also search biz profiles (category, monetization_candidates, description_zh stored in explanations)
    biz_matches = db.execute(
        select(BizProfile).where(
            or_(
                BizProfile.category.ilike(like),
            )
        ).order_by(desc(BizProfile.created_at))
    ).scalars().all()
    extra_pids = [b.project_id for b in biz_matches if b.project_id not in pid_set]
    if extra_pids:
        extra_projects = db.execute(
            select(Project).where(Project.project_id.in_(extra_pids[:limit]))
        ).scalars().all()
        matched_projects = list(matched_projects) + extra_projects

    items = []
    seen_pids: set = set()
    for p in matched_projects[:limit]:
        if p.project_id in seen_pids:
            continue
        seen_pids.add(p.project_id)
        score = latest_score(db, p.project_id)
        biz = latest_biz(db, p.project_id)
        items.append(ProjectListItem(
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
            } if biz else None,
        ))
    return {"items": items, "total": len(items)}


@app.get(f"{settings.api_prefix}/trending/snapshots/export")
def export_snapshot(
    since: str = Query(..., pattern="^(daily|weekly|monthly)$"),
    date_param: Optional[date] = Query(default=None, alias="date"),
    language: str = "all",
    fmt: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
):
    """Export snapshot items with analysis results as CSV or JSON download."""
    target_date = date_param or date.today()
    snapshot = db.execute(
        select(TrendingSnapshot).where(
            and_(
                TrendingSnapshot.snapshot_date == target_date,
                TrendingSnapshot.since == since,
                TrendingSnapshot.language == language,
            )
        )
    ).scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="snapshot not found")

    snap_items = db.execute(
        select(TrendingSnapshotItem)
        .where(TrendingSnapshotItem.snapshot_id == snapshot.snapshot_id)
        .order_by(TrendingSnapshotItem.rank.asc())
    ).scalars().all()

    project_ids = [i.project_id for i in snap_items if i.project_id]
    biz_by_pid: dict = {}
    score_by_pid: dict = {}
    for b in db.execute(select(BizProfile).where(BizProfile.project_id.in_(project_ids)).order_by(desc(BizProfile.created_at))).scalars().all():
        if b.project_id not in biz_by_pid:
            biz_by_pid[b.project_id] = b
    for s in db.execute(select(ProjectScore).where(ProjectScore.project_id.in_(project_ids)).order_by(desc(ProjectScore.created_at))).scalars().all():
        if s.project_id not in score_by_pid:
            score_by_pid[s.project_id] = s

    rows = []
    for item in snap_items:
        biz = biz_by_pid.get(item.project_id)
        score = score_by_pid.get(item.project_id)
        expl = (biz.explanations or {}) if biz else {}
        rows.append({
            "rank": item.rank,
            "repo": item.repo_full_name,
            "language": item.primary_language or "",
            "stars_today": item.stars_delta_window or "",
            "stars_total": item.stars_total_hint or "",
            "grade": score.grade if score else "",
            "score": score.total_score if score else "",
            "category": biz.category if biz else "",
            "monetization": ", ".join(biz.monetization_candidates or []) if biz else "",
            "description_zh": expl.get("description_zh", ""),
            "bd_pitch": expl.get("bd_pitch", ""),
        })

    filename = f"trend2biz-{target_date}-{since}.{fmt}"
    if fmt == "json":
        content = json.dumps(rows, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    else:  # csv
        buf = io.StringIO()
        if rows:
            writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


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
