from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ingestion.intake_models import CandidatePreview


@dataclass
class SourceDocument:
    source_document_key: str
    document_id: str | None = None
    source_url: str | None = None
    source_file_path: str | None = None
    filename: str | None = None
    content_type: str | None = None
    final_source_type: str | None = None
    adapter_name: str | None = None
    text: str | None = None
    raw_bytes: bytes | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawExtractedRow:
    extractor_name: str
    source_input_type: str
    evidence_type: str
    source_url: str | None = None
    source_file_path: str | None = None
    source_document_key: str | None = None
    table_name: str | None = None
    heading_path: list[str] = field(default_factory=list)
    section_heading: str | None = None
    row_number: int | None = None
    line_number: int | None = None
    json_path: list[str] | None = None
    column_name: str | None = None
    raw_key: str | None = None
    raw_value: Any | None = None
    raw_row: dict[str, Any] = field(default_factory=dict)
    extracted_address: str | None = None
    extracted_network: str | None = None
    extracted_contract_name: str | None = None
    extracted_wallet_label: str | None = None
    extracted_role_hint: str | None = None
    extracted_label_type: str | None = None
    confidence_source: str | int | None = None
    confidence_parser: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizedExtractedRow:
    address: str
    normalized_address: str
    evidence_type: str
    source_input_type: str
    confidence_initial: int
    entity_name: str | None = None
    protocol_name: str | None = None
    category: str | None = None
    sub_category: str | None = None
    network: str | None = None
    chain_id: int | None = None
    address_family: str | None = None
    contract_name: str | None = None
    wallet_label: str | None = None
    role: str | None = None
    label_type: str | None = None
    source_url: str | None = None
    source_file_path: str | None = None
    source_document_key: str | None = None
    confidence_source: str | int | None = None
    confidence_parser: int | None = None
    confidence_role: int | None = None
    source_identity: dict[str, Any] | None = None
    source_trust: dict[str, Any] | None = None
    source_trust_level: str | None = None
    source_trust_score: int | None = None
    source_identity_confidence: int | None = None
    warnings: list[str] = field(default_factory=list)
    raw_reference: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    source_documents: list[SourceDocument] = field(default_factory=list)
    raw_rows: list[RawExtractedRow] = field(default_factory=list)
    normalized_rows: list[NormalizedExtractedRow] = field(default_factory=list)
    table_preview: list[dict[str, Any]] = field(default_factory=list)
    candidates_preview: list[CandidatePreview] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)
    extractor_stats: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    source_identity: dict[str, Any] | None = None
    source_trust: dict[str, Any] | None = None
    source_trust_level: str | None = None
    source_trust_score: int | None = None
    source_identity_confidence: int | None = None
