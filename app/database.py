from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


def _normalize_url(url: str) -> str:
    # SQLAlchemy 2.0 requires 'postgresql://' not 'postgres://'
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    # Supabase requires SSL
    if "supabase" in url and "sslmode" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "sslmode=require"
    return url


_db_url = _normalize_url(settings.database_url)
if _db_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
    engine = create_engine(_db_url, future=True, connect_args=connect_args)
else:
    # Use NullPool for serverless environments (Vercel): no persistent connection pool
    engine = create_engine(_db_url, future=True, poolclass=NullPool)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
