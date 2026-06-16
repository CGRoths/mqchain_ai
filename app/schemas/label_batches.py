from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LabelBatchFromCandidatesRequest(BaseModel):
    candidate_ids: list[int] | None = None
    source_job_id: int | None = None
    entity_name: str | None = None
    protocol_name: str | None = None
    role_code: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=255)
    label_status: int = Field(default=1, ge=0, le=255)
    quality_tier: int | None = Field(default=None, ge=0, le=255)
    flags: int | None = Field(default=None, ge=0, le=65535)
    effective_from_block: int | None = Field(default=None, ge=0)
    effective_to_block: int | None = Field(default=None, ge=0)
    dictionary_version: str | None = None
    trusted_operator_override: bool = False
    dry_run: bool = True
    created_by: str | None = None
    approved_by: str | None = None


class CompactValuePreview(BaseModel):
    candidate_id: int
    source_job_id: int
    source_document_id: int
    address: str
    normalized_display: str
    prefix_code: int
    prefix_hex: str
    key_hex: str
    payload_hex: str
    entity_id: int | None
    entity_name: str
    entity_slug: str
    entity_created: bool
    protocol_id: int | None
    protocol_name: str
    protocol_slug: str
    protocol_created: bool
    role_id: int
    role_code: str
    confidence: int
    label_status: int
    quality_tier: int
    flags: int
    batch_id: int | None
    value_hex: str | None
    first_seen_block_or_slot: int
    last_seen_block_or_slot: int


class LabelBatchOperationResponse(BaseModel):
    dry_run: bool
    status: str
    batch_id: int | None = None
    candidates_scanned: int
    accepted_count: int
    blocked_count: int
    conflict_count: int
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    entries: list[CompactValuePreview] = Field(default_factory=list)


class LabelBatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_job_id: int | None
    source_document_id: int | None
    entity_id: int | None
    protocol_id: int | None
    role_id: int | None
    source_type: str | None
    source_url: str | None
    imported_count: int
    accepted_count: int
    rejected_count: int
    conflict_count: int
    effective_from_block: int | None
    effective_to_block: int | None
    label_action: str | None
    batch_hash: str
    evidence_hash: str | None
    dictionary_version: str | None
    status: str


class LabelBatchEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    batch_id: int
    evidence_type: str
    source_url: str | None
    source_document_id: int | None
    evidence_hash: str
    summary: str | None
    payload: dict[str, Any]
