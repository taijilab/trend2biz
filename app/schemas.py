from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class SnapshotQuery(BaseModel):
    date: Optional[date] = None
    since: str = Field(pattern="^(daily|weekly|monthly)$")
    language: str = "all"
    spoken: Optional[str] = None
    limit: int = 50


class SnapshotFetchIn(BaseModel):
    since: str = Field(pattern="^(daily|weekly|monthly)$")
    language: str = "all"
    spoken: Optional[str] = None


class SnapshotItemOut(BaseModel):
    rank: int
    repo_full_name: str
    repo_url: str
    description: Optional[str]
    primary_language: Optional[str]
    stars_total_hint: Optional[int]
    forks_total_hint: Optional[int]
    stars_delta_window: Optional[int]
    project_id: Optional[str] = None
    latest_biz: Optional[dict] = None
    latest_score: Optional[dict] = None


class SnapshotOut(BaseModel):
    snapshot_id: str
    date: date
    since: str
    language: str
    spoken: Optional[str]
    captured_at: datetime


class SnapshotResp(BaseModel):
    snapshot: SnapshotOut
    items: list[SnapshotItemOut]


class JobResp(BaseModel):
    job_id: str
    status: str


class ProjectListItem(BaseModel):
    project_id: str
    repo_full_name: str
    primary_language: Optional[str]
    latest_score: Optional[dict]
    latest_biz_profile: Optional[dict]


class ProjectListResp(BaseModel):
    items: list[ProjectListItem]
    next_cursor: Optional[str] = None


class MetricPoint(BaseModel):
    date: date
    stars: Optional[int]
    forks: Optional[int]
    commits_30d: Optional[int]
    contributors_90d: Optional[int]


class MetricSeriesResp(BaseModel):
    series: list[MetricPoint]


class BizProfileOut(BaseModel):
    biz_profile_id: str
    version: str
    created_at: datetime
    category: Optional[str]
    confidence: Optional[float]


class BizProfileResp(BaseModel):
    items: list[BizProfileOut]


class ScoreOut(BaseModel):
    score_id: str
    model_name: str
    total: float
    grade: str
    highlights: Optional[list]
    risks: Optional[list]
    followups: Optional[list]
    explanations: Optional[dict]


class ScoreResp(BaseModel):
    items: list[ScoreOut]


class WatchlistIn(BaseModel):
    project_id: str
    note: Optional[str] = None


class WatchlistItem(BaseModel):
    project_id: str
    note: Optional[str]
    created_at: datetime


class WatchlistResp(BaseModel):
    items: list[WatchlistItem]


class BatchScoreIn(BaseModel):
    snapshot_id: str
    score_model_id: Optional[str] = None
    biz_profile_model: Optional[str] = None


class ReportIn(BaseModel):
    project_id: str
    format: str = "html"
    latest: bool = True


class ReportOut(BaseModel):
    report_id: str
    url: str


class BizGenerateIn(BaseModel):
    model: Optional[str] = "rule-v1"
    api_key: Optional[str] = None
    provider: Optional[str] = None  # "anthropic" | "openrouter" | "zhipu"


class ProjectOut(BaseModel):
    project: dict
    latest_metrics: Optional[dict]
    latest_biz_profile: Optional[dict]
    latest_score: Optional[dict]


class JobDetailResp(BaseModel):
    job_id: str
    job_type: str
    status: str
    retry_count: int
    max_retries: int
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error: Optional[str]
    payload: Optional[dict]
