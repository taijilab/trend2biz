from __future__ import annotations

import os
import pathlib
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# ---------------------------------------------------------------------------
# Point settings at an in-memory DB BEFORE any app module is imported.
# StaticPool ensures all connections share the same in-memory database.
# ---------------------------------------------------------------------------
os.environ["DATABASE_URL"] = "sqlite://"

from app.database import Base, get_db  # noqa: E402
from app.main import app              # noqa: E402


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture(scope="function")
def db_engine():
    engine = _make_engine()
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="function")
def client(db_engine) -> Generator:
    """FastAPI TestClient backed by an isolated in-memory SQLite database."""
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def override_get_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    # Patch the module-level engine & SessionLocal used by background job runners
    import app.database as _db
    import app.main as _main
    _orig_engine = _db.engine
    _orig_session = _db.SessionLocal
    _db.engine = db_engine
    _db.SessionLocal = TestSession
    _main.SessionLocal = TestSession  # background jobs import this directly

    # Ensure tables exist on our test engine (startup may still target original)
    Base.metadata.create_all(bind=db_engine)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    _db.engine = _orig_engine
    _db.SessionLocal = _orig_session
    _main.SessionLocal = _orig_session


# ---------------------------------------------------------------------------
# HTML fixture helpers
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def trending_fixture_html() -> str:
    return (FIXTURES_DIR / "trending_daily_all.html").read_text(encoding="utf-8")
