from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
    )


class StagedArtifact(Base):
    __tablename__ = "mq_staged_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    staged_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    previews = relationship("IntakePreview", back_populates="staged_artifact")
    source_jobs = relationship("SourceJob", back_populates="staged_artifact")


class IntakePreview(Base):
    __tablename__ = "mq_intake_previews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    staged_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("mq_staged_artifacts.id"),
        nullable=True,
        index=True,
    )
    source_artifact_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fingerprint_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preview_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    fatal_errors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    staged_artifact = relationship("StagedArtifact", back_populates="previews")
    source_jobs = relationship("SourceJob", back_populates="preview")


class SourceJob(TimestampMixin, Base):
    __tablename__ = "mq_source_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    preview_id: Mapped[str] = mapped_column(ForeignKey("mq_intake_previews.id"), nullable=False, index=True)
    staged_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("mq_staged_artifacts.id"),
        nullable=True,
        index=True,
    )
    input_method: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pasted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_source_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    final_source_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    fingerprint_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_artifact_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    preview_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="new", index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    preview = relationship("IntakePreview", back_populates="source_jobs")
    staged_artifact = relationship("StagedArtifact", back_populates="source_jobs")
    documents = relationship("SourceDocument", back_populates="source_job")
    candidates = relationship("AddressCandidate", back_populates="source_job")


class SourceDocument(TimestampMixin, Base):
    __tablename__ = "mq_source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_job_id: Mapped[int] = mapped_column(ForeignKey("mq_source_jobs.id"), nullable=False, index=True)
    canonical_source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    source_job = relationship("SourceJob", back_populates="documents")
    contexts = relationship("CandidateContext", back_populates="source_document")
    evidence = relationship("AddressEvidence", back_populates="source_document")


class AddressCandidate(TimestampMixin, Base):
    __tablename__ = "mq_address_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_job_id: Mapped[int] = mapped_column(ForeignKey("mq_source_jobs.id"), nullable=False, index=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    normalized_address: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_network: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    chain_guess: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    chain_slug: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    address_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_role: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    confidence_initial: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="needs_review", index=True)
    source_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_input_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_sheet: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    raw_reference: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    source_job = relationship("SourceJob", back_populates="candidates")
    source_document = relationship("SourceDocument")
    contexts = relationship("CandidateContext", back_populates="candidate")
    evidence = relationship("AddressEvidence", back_populates="candidate")


class CandidateContext(TimestampMixin, Base):
    __tablename__ = "mq_candidate_contexts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("mq_address_candidates.id"), nullable=False, index=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=False, index=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_row_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    original_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    candidate = relationship("AddressCandidate", back_populates="contexts")
    source_document = relationship("SourceDocument", back_populates="contexts")


class AddressEvidence(TimestampMixin, Base):
    __tablename__ = "mq_address_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("mq_address_candidates.id"), nullable=False, index=True)
    source_document_id: Mapped[int] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    final_source_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    adapter_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    confidence_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate = relationship("AddressCandidate", back_populates="evidence")
    source_document = relationship("SourceDocument", back_populates="evidence")


class RegistryAddress(TimestampMixin, Base):
    __tablename__ = "mq_address_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    normalized_address: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
