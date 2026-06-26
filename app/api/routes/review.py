from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import DBSession
from app.review.approval_registry import approve_candidate_groups, get_unique_candidate_groups
from app.review.candidate_audit import audit_candidates
from app.review.official_auto_approval import auto_approve_official_candidates
from app.review.source_verification import record_source_verification, source_verification_payload


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


class ApproveCandidateGroupsRequest(BaseModel):
    source_job_id: int | None = None
    approval_readiness: str | None = None
    allow_review_readiness: str | None = None
    dry_run: bool = True
    actor: str = "system"


class CandidateGroupsRequest(BaseModel):
    source_job_id: int | None = None
    approval_readiness: str | None = None


class SourceVerificationRequest(BaseModel):
    source_job_id: int | None = None
    source_document_id: int | None = None
    candidate_id: int | None = None
    candidate_group_key: str | None = None
    entity_name: str | None = None
    entity_id: int | None = None
    protocol_name: str | None = None
    source_url: str | None = None
    source_origin: str | None = None
    official_referrer_url: str | None = None
    file_path: str | None = None
    input_method: str | None = None
    evidence_shape: str | None = None
    verification_scope: str
    verification_status: str = "verified"
    source_trust: str
    verified_by: str | None = None
    verification_reason: str | None = None
    verification_evidence_json: dict[str, Any] = Field(default_factory=dict)


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


@api_router.post("/candidate-groups")
def candidate_groups(payload: CandidateGroupsRequest, db: DBSession) -> list[dict[str, Any]]:
    return [
        {
            "group_key": group.group_key,
            "entity_name": group.entity_name,
            "chain_slug": group.chain_slug,
            "normalized_address": group.normalized_address,
            "suggested_role": group.suggested_role,
            "address_class": group.address_class,
            "source_trust_status": group.source_trust_status,
            "approval_readiness": group.approval_readiness,
            "candidate_count": len(group.candidates),
            "candidate_ids": [candidate.id for candidate in group.candidates],
        }
        for group in get_unique_candidate_groups(
            db,
            source_job_id=payload.source_job_id,
            approval_readiness=payload.approval_readiness,
        )
    ]


@api_router.post("/approve-candidate-groups")
def approve_groups(payload: ApproveCandidateGroupsRequest, db: DBSession) -> dict:
    return approve_candidate_groups(
        db,
        source_job_id=payload.source_job_id,
        approval_readiness=payload.approval_readiness,
        allow_review_readiness=payload.allow_review_readiness,
        dry_run=payload.dry_run,
        actor=payload.actor,
    )


@api_router.post("/source-verifications")
def create_source_verification(payload: SourceVerificationRequest, db: DBSession) -> dict[str, Any]:
    try:
        verification = record_source_verification(
            db,
            verification_scope=payload.verification_scope,
            verification_status=payload.verification_status,
            source_trust=payload.source_trust,
            verified_by=payload.verified_by,
            source_job_id=payload.source_job_id,
            source_document_id=payload.source_document_id,
            candidate_id=payload.candidate_id,
            candidate_group_key=payload.candidate_group_key,
            entity_name=payload.entity_name,
            entity_id=payload.entity_id,
            protocol_name=payload.protocol_name,
            source_url=payload.source_url,
            source_origin=payload.source_origin,
            official_referrer_url=payload.official_referrer_url,
            file_path=payload.file_path,
            input_method=payload.input_method,
            evidence_shape=payload.evidence_shape,
            verification_reason=payload.verification_reason,
            verification_evidence_json=payload.verification_evidence_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": [str(exc)]}) from exc
    db.commit()
    db.refresh(verification)
    return {
        **(source_verification_payload(verification) or {}),
        "entity_name": verification.entity_name,
        "entity_id": verification.entity_id,
        "protocol_name": verification.protocol_name,
        "source_url": verification.source_url,
        "file_path": verification.file_path,
        "input_method": verification.input_method,
        "verification_evidence_json": verification.verification_evidence_json,
        "created_at": verification.created_at.isoformat() if verification.created_at else None,
        "updated_at": verification.updated_at.isoformat() if verification.updated_at else None,
    }
