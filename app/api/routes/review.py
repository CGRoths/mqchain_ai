from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.api.deps import DBSession
from app.models.intake import (
    AddressCandidate,
    AddressEvidence,
    ApprovalEvent,
    ApprovedAddress,
    ApprovedAddressEvidence,
    ApprovedAddressRole,
    Entity,
    SourceSnapshot,
    SourceVerification,
)
from app.review.approval_registry import approve_candidate_groups, get_unique_candidate_groups
from app.review.candidate_audit import audit_candidates
from app.review.official_auto_approval import auto_approve_official_candidates
from app.review.source_verification import record_source_verification, source_verification_payload
from app.review.snapshot_diff import create_source_snapshot, diff_source_snapshot, mark_missing_in_latest


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
    source_snapshot_id: int | None = None
    new_only: bool = False


class CandidateGroupsRequest(BaseModel):
    source_job_id: int | None = None
    approval_readiness: str | None = None


class SourceVerificationRequest(BaseModel):
    source_job_id: int | None = None
    source_document_id: int | None = None
    candidate_id: int | None = None
    candidate_group_key: str | None = None
    source_sheet: str | None = None
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


class SourceSnapshotRequest(BaseModel):
    source_job_id: int
    snapshot_type: str = "reserve_snapshot"
    snapshot_period: str | None = None
    snapshot_date: str | None = None
    previous_snapshot_id: int | None = None
    created_by: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class SnapshotDiffRequest(BaseModel):
    source_job_id: int
    source_snapshot_id: int | None = None


class MarkMissingRequest(BaseModel):
    source_job_id: int
    source_snapshot_id: int
    dry_run: bool = True


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
        source_snapshot_id=payload.source_snapshot_id,
        new_only=payload.new_only,
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
            source_sheet=payload.source_sheet,
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


@api_router.post("/source-snapshots")
def create_snapshot(payload: SourceSnapshotRequest, db: DBSession) -> dict[str, Any]:
    try:
        snapshot = create_source_snapshot(
            db,
            source_job_id=payload.source_job_id,
            snapshot_type=payload.snapshot_type,
            snapshot_period=payload.snapshot_period,
            snapshot_date=payload.snapshot_date,
            previous_snapshot_id=payload.previous_snapshot_id,
            created_by=payload.created_by,
            metadata_json=payload.metadata_json,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": [str(exc)]}) from exc
    db.commit()
    db.refresh(snapshot)
    return _source_snapshot_row(snapshot)


@api_router.post("/source-snapshots/diff")
def snapshot_diff(payload: SnapshotDiffRequest, db: DBSession) -> dict[str, Any]:
    return diff_source_snapshot(db, payload.source_job_id, payload.source_snapshot_id)


@api_router.post("/source-snapshots/mark-missing")
def mark_missing(payload: MarkMissingRequest, db: DBSession) -> dict[str, Any]:
    try:
        return mark_missing_in_latest(
            db,
            source_job_id=payload.source_job_id,
            source_snapshot_id=payload.source_snapshot_id,
            dry_run=payload.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"fatal_errors": [str(exc)]}) from exc


@api_router.get("/source-verifications")
def source_verifications(
    db: DBSession,
    source_job_id: int | None = None,
    source_sheet: str | None = None,
    entity_name: str | None = None,
    source_trust: str | None = None,
    verification_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    stmt = select(SourceVerification).order_by(SourceVerification.created_at.desc(), SourceVerification.id.desc())
    if source_job_id is not None:
        stmt = stmt.where(SourceVerification.source_job_id == source_job_id)
    if source_sheet:
        stmt = stmt.where(SourceVerification.source_sheet == source_sheet)
    if entity_name:
        stmt = stmt.where(SourceVerification.entity_name.ilike(f"%{entity_name}%"))
    if source_trust:
        stmt = stmt.where(SourceVerification.source_trust == source_trust)
    if verification_status:
        stmt = stmt.where(SourceVerification.verification_status == verification_status)
    return [_source_verification_row(item) for item in db.scalars(stmt.limit(min(limit, 500)).offset(offset))]


@api_router.get("/approval-events")
def approval_events(
    db: DBSession,
    source_job_id: int | None = None,
    candidate_group_key: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    stmt = select(ApprovalEvent).order_by(ApprovalEvent.created_at.desc(), ApprovalEvent.id.desc())
    if source_job_id is not None:
        stmt = (
            stmt.outerjoin(ApprovedAddressEvidence, ApprovalEvent.approved_address_id == ApprovedAddressEvidence.approved_address_id)
            .where(ApprovedAddressEvidence.source_job_id == source_job_id)
            .distinct()
        )
    if candidate_group_key:
        stmt = stmt.where(ApprovalEvent.candidate_group_key == candidate_group_key)
    if action:
        stmt = stmt.where(ApprovalEvent.action == action)
    if actor:
        stmt = stmt.where(ApprovalEvent.actor.ilike(f"%{actor}%"))
    return [_approval_event_row(item) for item in db.scalars(stmt.limit(min(limit, 500)).offset(offset))]


@api_router.get("/global-search")
def global_search(db: DBSession, q: str, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    query = q.strip()
    if not query:
        return {"approved_addresses": [], "candidates": [], "evidence": []}
    limit = min(limit, 200)
    pattern = f"%{query}%"

    approved = _search_approved_addresses(db, pattern=pattern, limit=limit)
    candidates_stmt = (
        select(AddressCandidate)
        .where(
            or_(
                AddressCandidate.entity_name.ilike(pattern),
                AddressCandidate.source_network.ilike(pattern),
                AddressCandidate.chain_slug.ilike(pattern),
                AddressCandidate.address.ilike(pattern),
                AddressCandidate.normalized_address.ilike(pattern),
                AddressCandidate.suggested_role.ilike(pattern),
            )
        )
        .order_by(AddressCandidate.updated_at.desc(), AddressCandidate.id.desc())
        .limit(limit)
    )
    candidates = [_candidate_row(item) for item in db.scalars(candidates_stmt)]

    evidence_stmt = (
        select(AddressEvidence, AddressCandidate)
        .join(AddressCandidate, AddressCandidate.id == AddressEvidence.candidate_id)
        .where(
            or_(
                AddressEvidence.evidence_type.ilike(pattern),
                AddressEvidence.source_type.ilike(pattern),
                AddressEvidence.source_url.ilike(pattern),
                AddressEvidence.file_path.ilike(pattern),
                AddressCandidate.entity_name.ilike(pattern),
                AddressCandidate.chain_slug.ilike(pattern),
                AddressCandidate.address.ilike(pattern),
                AddressCandidate.normalized_address.ilike(pattern),
            )
        )
        .order_by(AddressEvidence.id.desc())
        .limit(limit)
    )
    evidence = [_evidence_search_row(item, candidate) for item, candidate in db.execute(evidence_stmt)]
    return {"approved_addresses": approved, "candidates": candidates, "evidence": evidence}


def _source_verification_row(verification: SourceVerification) -> dict[str, Any]:
    return {
        **(source_verification_payload(verification) or {}),
        "entity_name": verification.entity_name,
        "source_sheet": verification.source_sheet,
        "source_origin": verification.source_origin,
        "source_url": verification.source_url,
        "evidence_shape": verification.evidence_shape,
        "verification_reason": verification.verification_reason,
        "created_at": verification.created_at.isoformat() if verification.created_at else None,
        "updated_at": verification.updated_at.isoformat() if verification.updated_at else None,
    }


def _source_snapshot_row(snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "source_job_id": snapshot.source_job_id,
        "source_document_id": snapshot.source_document_id,
        "entity_name": snapshot.entity_name,
        "source_origin": snapshot.source_origin,
        "source_url": snapshot.source_url,
        "official_referrer_url": snapshot.official_referrer_url,
        "snapshot_type": snapshot.snapshot_type,
        "snapshot_period": snapshot.snapshot_period,
        "snapshot_date": snapshot.snapshot_date,
        "file_hash": snapshot.file_hash,
        "content_hash": snapshot.content_hash,
        "previous_snapshot_id": snapshot.previous_snapshot_id,
        "created_by": snapshot.created_by,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
        "metadata_json": snapshot.metadata_json or {},
    }


def _approval_event_row(event: ApprovalEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "approved_address_id": event.approved_address_id,
        "candidate_group_key": event.candidate_group_key,
        "action": event.action,
        "actor": event.actor,
        "reason": event.reason,
        "dry_run": event.dry_run,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "payload_json": event.payload_json or {},
    }


def _search_approved_addresses(db: DBSession, *, pattern: str, limit: int) -> list[dict[str, Any]]:
    evidence_count = func.count(ApprovedAddressEvidence.id).label("evidence_count")
    stmt = (
        select(Entity, ApprovedAddress, ApprovedAddressRole, evidence_count)
        .join(ApprovedAddress, ApprovedAddress.entity_id == Entity.id)
        .join(ApprovedAddressRole, ApprovedAddressRole.approved_address_id == ApprovedAddress.id)
        .outerjoin(ApprovedAddressEvidence, ApprovedAddressEvidence.approved_address_id == ApprovedAddress.id)
        .where(
            or_(
                Entity.entity_name.ilike(pattern),
                ApprovedAddress.chain_slug.ilike(pattern),
                ApprovedAddress.address.ilike(pattern),
                ApprovedAddress.normalized_address.ilike(pattern),
                ApprovedAddress.address_class.ilike(pattern),
                ApprovedAddressRole.role.ilike(pattern),
            )
        )
        .group_by(Entity.id, ApprovedAddress.id, ApprovedAddressRole.id)
        .order_by(Entity.entity_name.asc(), ApprovedAddress.chain_slug.asc(), ApprovedAddress.normalized_address.asc())
        .limit(limit)
    )
    return [_approved_address_row(entity, approved, role, count) for entity, approved, role, count in db.execute(stmt)]


def _approved_address_row(entity: Entity, approved: ApprovedAddress, role: ApprovedAddressRole, evidence_count: int) -> dict[str, Any]:
    return {
        "entity_name": entity.entity_name,
        "chain_slug": approved.chain_slug,
        "address": approved.address,
        "normalized_address": approved.normalized_address,
        "address_class": approved.address_class,
        "role": role.role,
        "source_trust_status": approved.source_trust_status,
        "confidence_score": approved.confidence_score,
        "evidence_count": int(evidence_count or 0),
        "first_approved_at": approved.first_approved_at.isoformat() if approved.first_approved_at else None,
    }


def _candidate_row(candidate: AddressCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "source_job_id": candidate.source_job_id,
        "entity_name": candidate.entity_name,
        "chain_slug": candidate.chain_slug,
        "address": candidate.address,
        "normalized_address": candidate.normalized_address,
        "suggested_role": candidate.suggested_role,
        "status": candidate.status,
        "confidence_initial": candidate.confidence_initial,
        "evidence_type": candidate.evidence_type,
    }


def _evidence_search_row(evidence: AddressEvidence, candidate: AddressCandidate) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "candidate_id": evidence.candidate_id,
        "source_job_id": candidate.source_job_id,
        "entity_name": candidate.entity_name,
        "chain_slug": candidate.chain_slug,
        "address": candidate.address,
        "evidence_type": evidence.evidence_type,
        "source_type": evidence.source_type,
        "source_url": evidence.source_url,
        "file_path": evidence.file_path,
        "payload_preview": evidence.payload or {},
    }
