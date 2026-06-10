from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    settings.ensure_data_dirs()
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_optional_review_columns()


def _ensure_optional_review_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    try:
        columns = {column["name"] for column in inspector.get_columns("mq_address_candidates")}
    except Exception:
        return
    additions = {
        "approved_at": "DATETIME",
        "approved_by": "VARCHAR(255)",
        "approval_method": "VARCHAR(128)",
        "approval_notes": "TEXT",
    }
    missing = [(name, ddl) for name, ddl in additions.items() if name not in columns]
    if not missing:
        return
    with engine.begin() as conn:
        for name, ddl in missing:
            conn.execute(text(f"ALTER TABLE mq_address_candidates ADD COLUMN {name} {ddl}"))
