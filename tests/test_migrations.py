from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

import app.main as main_mod


def test_migrate_add_missing_jobs_retry_columns_on_sqlite():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )

    # Simulate an older schema that does not have retry_count/max_retries.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE jobs (
                    job_id VARCHAR(36) PRIMARY KEY,
                    job_type VARCHAR(100) NOT NULL,
                    payload JSON,
                    status VARCHAR(20) NOT NULL DEFAULT 'queued',
                    created_at DATETIME,
                    started_at DATETIME,
                    finished_at DATETIME,
                    error TEXT
                )
                """
            )
        )

    old_engine = main_mod.engine
    try:
        main_mod.engine = engine
        main_mod._migrate_add_missing_columns()
    finally:
        main_mod.engine = old_engine

    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "retry_count" in cols
    assert "max_retries" in cols

    # Defaults should work for rows inserted without the new fields.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO jobs (job_id, job_type, payload, status)
                VALUES ('job-1', 'trending_fetch', '{}', 'queued')
                """
            )
        )
        row = conn.execute(
            text("SELECT retry_count, max_retries FROM jobs WHERE job_id='job-1'")
        ).one()
    assert row[0] == 0
    assert row[1] == 3
