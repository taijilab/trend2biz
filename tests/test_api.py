"""
FastAPI 集成测试（全部使用 in-memory SQLite，无网络依赖）
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

def test_health_check(client):
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"


# ---------------------------------------------------------------------------
# Trending Snapshots
# ---------------------------------------------------------------------------

def test_fetch_snapshot_returns_202(client):
    """触发抓取任务应返回 202 + job_id（不真正调用网络）。"""
    with patch("app.main.run_snapshot_fetch_job"):
        resp = client.post(
            "/api/v1/trending/snapshots:fetch",
            json={"since": "daily", "language": "all", "spoken": None},
        )
    assert resp.status_code == 202
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"


def test_get_snapshot_not_found(client):
    """未抓取时查询快照应返回 404。"""
    resp = client.get("/api/v1/trending/snapshots?since=daily")
    assert resp.status_code == 404


def test_get_snapshot_invalid_since(client):
    """非法 since 参数应返回 422。"""
    resp = client.get("/api/v1/trending/snapshots?since=hourly")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def test_list_projects_empty(client):
    """项目库为空时应返回空列表。"""
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_project_not_found(client):
    resp = client.get("/api/v1/projects/nonexistent-id")
    assert resp.status_code == 404


def test_project_metrics_refresh_returns_202(client):
    """先创建 project，再触发 metrics refresh 应返回 202。"""
    # 注入一个 project
    from sqlalchemy.orm import Session
    from app.models import Project
    from app.database import get_db
    import datetime

    # 通过 watchlist 触发创建会比较复杂，直接插入
    override = client.app.dependency_overrides.get(get_db)
    if override:
        gen = override()
        db: Session = next(gen)
        project = Project(
            repo_full_name="test/testrepo",
            repo_url="https://github.com/test/testrepo",
            last_seen_at=datetime.datetime.utcnow(),
        )
        db.add(project)
        db.commit()
        project_id = project.project_id
        try:
            next(gen)
        except StopIteration:
            pass

        with patch("app.main.run_metrics_refresh_job"):
            resp = client.post(f"/api/v1/projects/{project_id}/metrics:refresh")
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def _create_project(client) -> str:
    """Helper: insert a project directly and return its project_id."""
    from sqlalchemy.orm import Session
    from app.models import Project
    from app.database import get_db
    import datetime

    override = client.app.dependency_overrides.get(get_db)
    gen = override()
    db: Session = next(gen)
    project = Project(
        repo_full_name="owner/watchable",
        repo_url="https://github.com/owner/watchable",
        last_seen_at=datetime.datetime.utcnow(),
    )
    db.add(project)
    db.commit()
    pid = project.project_id
    try:
        next(gen)
    except StopIteration:
        pass
    return pid


def test_watchlist_add_get_delete(client):
    pid = _create_project(client)

    # 添加
    resp = client.post("/api/v1/watchlist", json={"project_id": pid, "note": "看好这个项目"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["project_id"] == pid
    assert data["note"] == "看好这个项目"

    # 查询
    resp = client.get("/api/v1/watchlist")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(i["project_id"] == pid for i in items)

    # 删除
    resp = client.delete(f"/api/v1/watchlist/{pid}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # 删除后查询不到
    resp = client.get("/api/v1/watchlist")
    items = resp.json()["items"]
    assert not any(i["project_id"] == pid for i in items)


def test_watchlist_add_project_not_found(client):
    resp = client.post("/api/v1/watchlist", json={"project_id": "nonexistent", "note": ""})
    assert resp.status_code == 404


def test_watchlist_delete_not_found(client):
    resp = client.delete("/api/v1/watchlist/nonexistent")
    assert resp.status_code == 404


def test_watchlist_duplicate_add_is_idempotent(client):
    """重复添加同一项目到 watchlist 不应报错（应幂等）。"""
    pid = _create_project(client)
    client.post("/api/v1/watchlist", json={"project_id": pid, "note": "first"})
    resp = client.post("/api/v1/watchlist", json={"project_id": pid, "note": "second"})
    # 应返回 200 而不是 500
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scores & Biz
# ---------------------------------------------------------------------------

def test_biz_generate_returns_202(client):
    pid = _create_project(client)
    with patch("app.main.run_biz_generate_job"):
        resp = client.post(
            f"/api/v1/projects/{pid}/biz-profiles:generate",
            json={"model": "rule-v1"},
        )
    assert resp.status_code == 202
    assert "job_id" in resp.json()


def test_scores_empty_for_new_project(client):
    pid = _create_project(client)
    resp = client.get(f"/api/v1/projects/{pid}/scores")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_batch_score_snapshot_not_found(client):
    with patch("app.main.run_score_batch_job"):
        resp = client.post(
            "/api/v1/scores:batch",
            json={"snapshot_id": "nonexistent", "biz_profile_model": "rule-v1"},
        )
    assert resp.status_code == 404
