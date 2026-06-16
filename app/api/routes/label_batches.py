from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import DBSession
from app.labels.batch_commit_service import BatchCommitError, BatchCommitOptions, BatchCommitService
from app.labels.memory_kv_store import DEFAULT_MEMORY_KV_STORE
from app.models.compact_label import LabelBatch, LabelBatchEvidence
from app.schemas.label_batches import (
    LabelBatchEvidenceRead,
    LabelBatchFromCandidatesRequest,
    LabelBatchOperationResponse,
    LabelBatchRead,
)


api_router = APIRouter(prefix="/label-batches", tags=["label-batches"])


@api_router.post("/from-candidates", response_model=LabelBatchOperationResponse)
def from_candidates(payload: LabelBatchFromCandidatesRequest, db: DBSession) -> dict:
    return _run_candidate_batch(payload, db)


@api_router.post("/dry-run", response_model=LabelBatchOperationResponse)
def dry_run(payload: LabelBatchFromCandidatesRequest, db: DBSession) -> dict:
    payload.dry_run = True
    return _run_candidate_batch(payload, db)


@api_router.post("/commit", response_model=LabelBatchOperationResponse)
def commit(payload: LabelBatchFromCandidatesRequest, db: DBSession) -> dict:
    payload.dry_run = False
    return _run_candidate_batch(payload, db)


@api_router.get("", response_model=list[LabelBatchRead])
def list_batches(db: DBSession, limit: int = 100, offset: int = 0) -> list[LabelBatch]:
    return list(db.scalars(select(LabelBatch).order_by(LabelBatch.id.desc()).limit(limit).offset(offset)))


@api_router.get("/{batch_id}", response_model=LabelBatchRead)
def get_batch(batch_id: int, db: DBSession) -> LabelBatch:
    batch = db.get(LabelBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="label_batch_not_found")
    return batch


@api_router.get("/{batch_id}/evidence", response_model=list[LabelBatchEvidenceRead])
def batch_evidence(batch_id: int, db: DBSession) -> list[LabelBatchEvidence]:
    if db.get(LabelBatch, batch_id) is None:
        raise HTTPException(status_code=404, detail="label_batch_not_found")
    return list(db.scalars(select(LabelBatchEvidence).where(LabelBatchEvidence.batch_id == batch_id).order_by(LabelBatchEvidence.id.asc())))


def _run_candidate_batch(payload: LabelBatchFromCandidatesRequest, db: DBSession) -> dict:
    options = BatchCommitOptions(**payload.model_dump(exclude={"dry_run"}))
    service = BatchCommitService(db, DEFAULT_MEMORY_KV_STORE)
    try:
        if payload.dry_run:
            return service.dry_run_from_candidates(options)
        return service.commit_from_candidates(options)
    except BatchCommitError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
