from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

InputMethod = Literal["upload", "url", "github", "paste", "onchain_root"]


class PreviewRequest(BaseModel):
    input_method: InputMethod
    source_url: str | None = None
    pasted_text: str | None = None
    requested_source_type: str | None = None
    content_type: str | None = None
    created_by: str | None = None


class SaveJobRequest(BaseModel):
    preview_id: str | None = None
    staged_artifact_id: str | None = None
    created_by: str | None = None


class CandidatePreviewRead(BaseModel):
    address: str
    normalized_address: str
    entity_name: str | None = None
    source_network: str | None = None
    chain_guess: str | None = None
    chain_slug: str | None = None
    chain_id: int | None = None
    address_family: str | None = None
    suggested_role: str | None = None
    confidence_initial: int = 0
    status: str = "needs_review"
    source_type: str
    source_input_type: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    source_page: int | None = None
    source_url: str | None = None
    file_path: str | None = None
    evidence_type: str | None = None
    warnings: list[str] = Field(default_factory=list)
    raw_reference: dict[str, Any] = Field(default_factory=dict)


class IntakePreviewRead(BaseModel):
    preview_id: str
    staged_artifact_id: str | None = None
    requested_source_type: str | None = None
    final_source_type: str | None = None
    adapter_name: str | None = None
    fingerprint_confidence: int = 0
    override_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    fatal_errors: list[str] = Field(default_factory=list)
    can_save_job: bool = False
    can_run_extraction: bool = False
    artifact: dict[str, Any] = Field(default_factory=dict)
    fingerprint: dict[str, Any] = Field(default_factory=dict)
    profile: dict[str, Any] = Field(default_factory=dict)
    table_preview: list[dict[str, Any]] = Field(default_factory=list)
    candidates_preview: list[CandidatePreviewRead] = Field(default_factory=list)
    evidence_preview: list[dict[str, Any]] = Field(default_factory=list)


class SourceJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    preview_id: str
    staged_artifact_id: str | None
    input_method: str
    requested_source_type: str | None
    final_source_type: str
    adapter_name: str
    status: str
    created_at: datetime
    updated_at: datetime


class RunExtractionResponse(BaseModel):
    source_job_id: int
    extracted_candidates: int
    status: str
    final_source_type: str
    adapter_name: str
    fatal_errors: list[str] = Field(default_factory=list)


class CandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_job_id: int
    source_document_id: int
    address: str
    normalized_address: str
    entity_name: str | None
    source_network: str | None
    chain_guess: str | None
    chain_slug: str | None
    chain_id: int | None
    address_family: str | None
    suggested_role: str | None
    confidence_initial: int
    status: str
    source_type: str
    source_input_type: str | None
    source_sheet: str | None
    source_row: int | None
    source_page: int | None
    source_url: str | None
    file_path: str | None
    evidence_type: str | None
    warnings: list[str]
    raw_reference: dict[str, Any]


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    candidate_id: int
    source_document_id: int
    evidence_type: str
    source_type: str
    final_source_type: str
    adapter_name: str
    source_url: str | None
    file_path: str | None
    payload: dict[str, Any]
    confidence_reason: str | None


class SourceDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_job_id: int
    canonical_source_url: str | None
    file_path: str | None
    content_type: str | None
    document_title: str | None
    text_hash: str
    metadata_json: dict[str, Any]
    created_at: datetime
