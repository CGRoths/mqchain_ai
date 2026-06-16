from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import health, intake, label_batches, registry, review
from app.core.config import settings
from app.db.database import init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.app_debug, lifespan=lifespan)

app.include_router(health.router)
app.include_router(intake.router)
app.include_router(intake.api_router, prefix=settings.api_prefix)
app.include_router(review.api_router, prefix=settings.api_prefix)
app.include_router(registry.api_router, prefix=settings.api_prefix)
app.include_router(label_batches.api_router, prefix=settings.api_prefix)
