from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceArtifact:
    input_method: str
    filename: str | None = None
    local_file_path: str | None = None
    source_url: str | None = None
    pasted_text: str | None = None
    content_type: str | None = None
    raw_content_sample: bytes | None = None
    size_bytes: int = 0
    requested_source_type: str | None = None
    created_by: str | None = None

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        sample = data.pop("raw_content_sample", None)
        data["raw_content_sample_hex"] = sample[:64].hex() if isinstance(sample, bytes) else None
        return data


@dataclass(frozen=True)
class SourceFingerprint:
    file_extension: str | None
    magic_signature: str | None
    mime_type: str | None
    url_kind: str | None
    content_kind: str | None
    detected_source_type: str | None
    final_source_type: str | None
    parser_adapter: str | None
    confidence: int
    warnings: list[str] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)
    requested_source_type: str | None = None
    source_type_overridden: bool = False
    override_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntakeProfile:
    final_source_type: str | None
    adapter_name: str | None
    entity_name: str | None = None
    protocol_name: str | None = None
    category: str | None = None
    sub_category: str | None = None
    expected_roles: list[str] = field(default_factory=list)
    chain_scope: list[str] = field(default_factory=list)
    detected_columns: list[dict[str, str]] = field(default_factory=list)
    sheet_count: int = 0
    parsed_sheet_names: list[str] = field(default_factory=list)
    skipped_sheet_names: list[str] = field(default_factory=list)
    table_count: int = 0
    warnings: list[str] = field(default_factory=list)
    confidence: int = 0
    recommended_action: str = "run_extraction"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidatePreview:
    address: str
    normalized_address: str
    source_type: str
    entity_name: str | None = None
    source_network: str | None = None
    chain_guess: str | None = None
    chain_slug: str | None = None
    chain_id: int | None = None
    address_family: str | None = None
    suggested_role: str | None = None
    confidence_initial: int = 0
    status: str = "needs_review"
    source_input_type: str | None = None
    source_sheet: str | None = None
    source_row: int | None = None
    source_page: int | None = None
    source_url: str | None = None
    file_path: str | None = None
    evidence_type: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw_reference: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParsedSource:
    document_text: str
    document_title: str | None
    content_type: str | None
    metadata: dict[str, Any]
    table_preview: list[dict[str, Any]]
    candidates: list[CandidatePreview]
    evidence_preview: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    fatal_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntakePreview:
    preview_id: str
    staged_artifact_id: str | None
    artifact: SourceArtifact
    fingerprint: SourceFingerprint
    profile: IntakeProfile
    table_preview: list[dict[str, Any]]
    candidates_preview: list[CandidatePreview]
    evidence_preview: list[dict[str, Any]]
    warnings: list[str]
    fatal_errors: list[str]
    can_save_job: bool
    can_run_extraction: bool

    def to_response(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "staged_artifact_id": self.staged_artifact_id,
            "requested_source_type": self.fingerprint.requested_source_type,
            "final_source_type": self.fingerprint.final_source_type,
            "adapter_name": self.fingerprint.parser_adapter,
            "fingerprint_confidence": self.fingerprint.confidence,
            "override_reason": self.fingerprint.override_reason,
            "warnings": self.warnings,
            "fatal_errors": self.fatal_errors,
            "can_save_job": self.can_save_job,
            "can_run_extraction": self.can_run_extraction,
            "artifact": self.artifact.to_json(),
            "fingerprint": self.fingerprint.to_json(),
            "profile": self.profile.to_json(),
            "table_preview": self.table_preview,
            "candidates_preview": [candidate.to_json() for candidate in self.candidates_preview],
            "evidence_preview": self.evidence_preview,
        }
