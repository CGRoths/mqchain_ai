from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import DBSession
from app.review.candidate_audit import audit_candidates
from app.review.official_auto_approval import auto_approve_official_candidates


api_router = APIRouter(prefix="/review", tags=["review"])


class AutoApproveOfficialRequest(BaseModel):
    source_job_id: int | None = None
    dry_run: bool = True


class AutoApproveOfficialResponse(BaseModel):
    dry_run: bool
    matched: int
    approved: int
    skipped: int
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    source_job_id: int | None = None


class CandidateAuditRequest(BaseModel):
    source_job_id: int | None = None
    limit_samples: int = 20


@api_router.post("/auto-approve-official", response_model=AutoApproveOfficialResponse)
def auto_approve_official(payload: AutoApproveOfficialRequest, db: DBSession) -> dict:
    return auto_approve_official_candidates(
        db,
        source_job_id=payload.source_job_id,
        dry_run=payload.dry_run,
        approved_by="api",
    )


@api_router.post("/candidate-audit")
def candidate_audit(payload: CandidateAuditRequest, db: DBSession) -> dict:
    return audit_candidates(
        db,
        source_job_id=payload.source_job_id,
        limit_samples=payload.limit_samples,
    )
