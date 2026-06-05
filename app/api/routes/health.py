from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DBSession

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
def health_db(db: DBSession) -> dict[str, str]:
    db.execute(text("select 1"))
    return {"status": "ok", "database": "ok"}
