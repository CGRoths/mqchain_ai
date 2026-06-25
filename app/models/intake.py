from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
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
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approval_method: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approval_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class SourceVerification(TimestampMixin, Base):
    __tablename__ = "mq_source_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_job_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_jobs.id"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=True, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("mq_address_candidates.id"), nullable=True, index=True)
    candidate_group_key: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    protocol_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_origin: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    official_referrer_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_method: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evidence_shape: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    verification_scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    verification_status: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_trust: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    verified_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class RegistryAddress(TimestampMixin, Base):
    __tablename__ = "mq_address_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chain_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    normalized_address: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


class Entity(TimestampMixin, Base):
    __tablename__ = "mq_entities"
    __table_args__ = (UniqueConstraint("entity_name", name="uq_mq_entities_entity_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String(128), nullable=True)

    approved_addresses = relationship("ApprovedAddress", back_populates="entity")


class ApprovedAddress(TimestampMixin, Base):
    __tablename__ = "mq_approved_addresses"
    __table_args__ = (UniqueConstraint("entity_id", "chain_slug", "normalized_address", name="uq_mq_approved_addresses_entity_chain_address"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("mq_entities.id"), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    normalized_address: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    source_network: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    chain_slug: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    address_class: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_trust_status: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    approval_readiness_at_approval: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    confidence_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="approved", index=True)
    first_approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    entity = relationship("Entity", back_populates="approved_addresses")
    roles = relationship("ApprovedAddressRole", back_populates="approved_address")
    evidence = relationship("ApprovedAddressEvidence", back_populates="approved_address")
    events = relationship("ApprovalEvent", back_populates="approved_address")


class ApprovedAddressRole(TimestampMixin, Base):
    __tablename__ = "mq_approved_address_roles"
    __table_args__ = (UniqueConstraint("approved_address_id", "role", name="uq_mq_approved_address_roles_address_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approved_address_id: Mapped[int] = mapped_column(ForeignKey("mq_approved_addresses.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role_confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="approved", index=True)

    approved_address = relationship("ApprovedAddress", back_populates="roles")


class ApprovedAddressEvidence(Base):
    __tablename__ = "mq_approved_address_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approved_address_id: Mapped[int] = mapped_column(ForeignKey("mq_approved_addresses.id"), nullable=False, index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("mq_address_candidates.id"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=True, index=True)
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_type: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_input_type: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_reference: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence_contribution: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    approved_address = relationship("ApprovedAddress", back_populates="evidence")


class ApprovalEvent(Base):
    __tablename__ = "mq_approval_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    approved_address_id: Mapped[int | None] = mapped_column(ForeignKey("mq_approved_addresses.id"), nullable=True, index=True)
    candidate_group_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system", index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    approved_address = relationship("ApprovedAddress", back_populates="events")
