from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base
from app.models.intake import TimestampMixin


def _bigint() -> BigInteger:
    return BigInteger().with_variant(Integer, "sqlite")


class KeyPrefixDict(TimestampMixin, Base):
    __tablename__ = "mq_kv_key_prefix_dict"
    __table_args__ = (
        CheckConstraint("prefix_code >= 0 AND prefix_code <= 32767", name="ck_mq_kv_key_prefix_dict_prefix_code_signed_safe"),
        CheckConstraint("codec_status IN ('active', 'planned', 'experimental', 'disabled')", name="ck_mq_kv_key_prefix_dict_codec_status"),
    )

    prefix_code: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    chain_id: Mapped[int] = mapped_column(SmallInteger, nullable=False, index=True)
    chain_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    chain_name: Mapped[str] = mapped_column(String(255), nullable=False)
    chain_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    address_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    codec: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    codec_status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    payload_len: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    evm_chain_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    slip44_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    native_symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class RoleDict(TimestampMixin, Base):
    __tablename__ = "mq_kv_role_dict"
    __table_args__ = (
        CheckConstraint("role_id >= 0 AND role_id <= 32767", name="ck_mq_kv_role_dict_role_id_signed_safe"),
    )

    role_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    role_code: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    category_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    role_group: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    metric_usage_default: Mapped[str] = mapped_column(String(128), nullable=False)
    boundary_class: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    default_quality_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    default_flags: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)


class RoleProposal(TimestampMixin, Base):
    __tablename__ = "mq_kv_role_proposals"

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    proposed_role_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    role_group: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_job_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_jobs.id"), nullable=True, index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    example_addresses_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Protocol(TimestampMixin, Base):
    __tablename__ = "mq_protocols"
    __table_args__ = (UniqueConstraint("protocol_slug", name="uq_mq_protocols_protocol_slug"),)

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    protocol_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    protocol_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sub_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class LabelBatch(TimestampMixin, Base):
    __tablename__ = "mq_label_batches"

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    source_job_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_jobs.id"), nullable=True, index=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=True, index=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("mq_entities.id"), nullable=True, index=True)
    protocol_id: Mapped[int | None] = mapped_column(_bigint(), ForeignKey("mq_protocols.id"), nullable=True, index=True)
    role_id: Mapped[int | None] = mapped_column(ForeignKey("mq_kv_role_dict.role_id"), nullable=True, index=True)
    source_type: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence_default: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    quality_tier_default: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    status_default: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    flags_default: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    effective_from_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    effective_to_block: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    label_action: Mapped[str | None] = mapped_column(String(128), nullable=True)
    supersedes_batch_id: Mapped[int | None] = mapped_column(_bigint(), ForeignKey("mq_label_batches.id"), nullable=True, index=True)
    batch_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dictionary_version: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending", index=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class LabelBatchEvidence(TimestampMixin, Base):
    __tablename__ = "mq_label_batch_evidence"

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    batch_id: Mapped[int] = mapped_column(_bigint(), ForeignKey("mq_label_batches.id"), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_id: Mapped[int | None] = mapped_column(ForeignKey("mq_source_documents.id"), nullable=True, index=True)
    evidence_hash: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    storage_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class KVIndexManifest(TimestampMixin, Base):
    __tablename__ = "mq_kv_index_manifest"

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    index_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    rocksdb_path: Mapped[str] = mapped_column(Text, nullable=False)
    column_family: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    key_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    value_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    dictionary_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    total_keys: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_committed_batch_id: Mapped[int | None] = mapped_column(_bigint(), ForeignKey("mq_label_batches.id"), nullable=True, index=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="building", index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KVIndexShard(TimestampMixin, Base):
    __tablename__ = "mq_kv_index_shards"
    __table_args__ = (UniqueConstraint("manifest_id", "prefix_code", "shard_id", name="uq_mq_kv_index_shards_manifest_prefix_shard"),)

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    manifest_id: Mapped[int] = mapped_column(_bigint(), ForeignKey("mq_kv_index_manifest.id"), nullable=False, index=True)
    prefix_code: Mapped[int | None] = mapped_column(SmallInteger, nullable=True, index=True)
    chain_code: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    shard_id: Mapped[int] = mapped_column(Integer, nullable=False)
    shard_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    key_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    min_key_hex: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_key_hex: Mapped[str | None] = mapped_column(Text, nullable=True)
    shard_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="building", index=True)


class DictionaryVersion(TimestampMixin, Base):
    __tablename__ = "mq_dictionary_versions"
    __table_args__ = (UniqueConstraint("version_name", name="uq_mq_dictionary_versions_version_name"),)

    id: Mapped[int] = mapped_column(_bigint(), primary_key=True)
    version_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    key_prefix_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role_dict_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="active", index=True)
