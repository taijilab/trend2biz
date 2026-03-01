from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Project(Base):
    __tablename__ = "projects"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repo_full_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    repo_url: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_language: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    license_spdx: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at_github: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at_github: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pushed_at_github: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_login: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    owner_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)   # "User" | "Organization"
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TrendingSnapshot(Base):
    __tablename__ = "trending_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_date", "since", "language", "spoken", name="uq_snapshot_dim"),)

    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_date: Mapped[date] = mapped_column(Date, index=True)
    since: Mapped[str] = mapped_column(Enum("daily", "weekly", "monthly", name="trending_since"), index=True)
    language: Mapped[str] = mapped_column(String(64), default="all")
    spoken: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    source: Mapped[str] = mapped_column(String(64), default="github_trending")

    items: Mapped[List[TrendingSnapshotItem]] = relationship(
        "TrendingSnapshotItem", back_populates="snapshot", cascade="all, delete-orphan"
    )


class TrendingSnapshotItem(Base):
    __tablename__ = "trending_snapshot_items"
    __table_args__ = (UniqueConstraint("snapshot_id", "rank", name="uq_snapshot_rank"),)

    item_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("trending_snapshots.snapshot_id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(Integer)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255))
    repo_url: Mapped[str] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    primary_language: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    stars_total_hint: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    forks_total_hint: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stars_delta_window: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    snapshot: Mapped[TrendingSnapshot] = relationship("TrendingSnapshot", back_populates="items")


class RepoMetricDaily(Base):
    __tablename__ = "repo_metrics_daily"
    __table_args__ = (UniqueConstraint("project_id", "metric_date", name="uq_metric_day"),)

    metric_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    metric_date: Mapped[date] = mapped_column(Date, index=True)
    stars: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    forks: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    watchers: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    open_issues: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    commits_30d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    commits_90d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prs_30d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    issues_30d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contributors_90d: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    bus_factor_top1_share: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BizProfile(Base):
    __tablename__ = "biz_profiles"

    biz_profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(100), default="rule-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_persona: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    scenarios: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    value_props: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    delivery_forms: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    monetization_candidates: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    buyer: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sales_motion: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    explanations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class ProjectScore(Base):
    __tablename__ = "project_scores"
    __table_args__ = (UniqueConstraint("project_id", "model_name", "metric_date", name="uq_score_day"),)

    score_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    model_name: Mapped[str] = mapped_column(String(100), default="yc-open-source-v1")
    biz_profile_id: Mapped[Optional[str]] = mapped_column(ForeignKey("biz_profiles.biz_profile_id"), nullable=True)
    metric_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    market_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    traction_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    moat_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    team_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    monetization_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_score: Mapped[float] = mapped_column(Float)
    grade: Mapped[str] = mapped_column(Enum("S", "A", "B", "C", name="project_grade"))
    highlights: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    risks: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    followups: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    explanations: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Report(Base):
    __tablename__ = "reports"

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    score_id: Mapped[Optional[str]] = mapped_column(ForeignKey("project_scores.score_id"), nullable=True)
    format: Mapped[str] = mapped_column(String(20), default="html")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    storage_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("project_id", name="uq_watch_project"),)

    watch_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.project_id", ondelete="CASCADE"), index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_type: Mapped[str] = mapped_column(String(100), index=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(Enum("queued", "running", "succeeded", "failed", name="job_status"), default="queued")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
