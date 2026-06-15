"""compact label control schema

Revision ID: 20260607_0003
Revises: 20260607_0002
Create Date: 2026-06-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260607_0003"
down_revision = "20260607_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "mq_kv_key_prefix_dict" not in existing_tables:
        op.create_table(
            "mq_kv_key_prefix_dict",
            sa.Column("prefix_code", sa.SmallInteger(), nullable=False),
            sa.Column("chain_id", sa.SmallInteger(), nullable=False),
            sa.Column("chain_code", sa.String(length=128), nullable=False),
            sa.Column("chain_name", sa.String(length=255), nullable=False),
            sa.Column("chain_family", sa.String(length=128), nullable=False),
            sa.Column("address_family", sa.String(length=128), nullable=False),
            sa.Column("codec", sa.String(length=128), nullable=False),
            sa.Column("codec_status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("payload_len", sa.SmallInteger(), nullable=True),
            sa.Column("evm_chain_id", sa.BigInteger(), nullable=True),
            sa.Column("slip44_id", sa.Integer(), nullable=True),
            sa.Column("native_symbol", sa.String(length=32), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("prefix_code >= 0 AND prefix_code <= 32767", name="ck_mq_kv_key_prefix_dict_prefix_code_signed_safe"),
            sa.CheckConstraint("codec_status IN ('active', 'planned', 'experimental', 'disabled')", name="ck_mq_kv_key_prefix_dict_codec_status"),
            sa.PrimaryKeyConstraint("prefix_code"),
        )
        for column in ("chain_id", "chain_code", "chain_family", "address_family", "codec", "codec_status", "evm_chain_id", "is_active"):
            op.create_index(f"ix_mq_kv_key_prefix_dict_{column}", "mq_kv_key_prefix_dict", [column])

    if "mq_kv_role_dict" not in existing_tables:
        op.create_table(
            "mq_kv_role_dict",
            sa.Column("role_id", sa.SmallInteger(), nullable=False),
            sa.Column("role_code", sa.String(length=128), nullable=False),
            sa.Column("category_code", sa.String(length=128), nullable=False),
            sa.Column("role_group", sa.String(length=128), nullable=False),
            sa.Column("metric_usage_default", sa.String(length=128), nullable=False),
            sa.Column("boundary_class", sa.String(length=128), nullable=False),
            sa.Column("default_quality_tier", sa.SmallInteger(), nullable=False),
            sa.Column("default_flags", sa.Integer(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("role_id >= 0 AND role_id <= 32767", name="ck_mq_kv_role_dict_role_id_signed_safe"),
            sa.PrimaryKeyConstraint("role_id"),
            sa.UniqueConstraint("role_code", name="uq_mq_kv_role_dict_role_code"),
        )
        for column in ("role_code", "category_code", "role_group", "boundary_class", "is_active"):
            op.create_index(f"ix_mq_kv_role_dict_{column}", "mq_kv_role_dict", [column])

    if "mq_kv_role_proposals" not in existing_tables:
        op.create_table(
            "mq_kv_role_proposals",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("proposed_role_code", sa.String(length=128), nullable=False),
            sa.Column("category_code", sa.String(length=128), nullable=True),
            sa.Column("role_group", sa.String(length=128), nullable=True),
            sa.Column("source_job_id", sa.Integer(), nullable=True),
            sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("example_addresses_json", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("reviewed_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("proposed_role_code", "category_code", "role_group", "source_job_id", "status"):
            op.create_index(f"ix_mq_kv_role_proposals_{column}", "mq_kv_role_proposals", [column])

    if "mq_protocols" not in existing_tables:
        op.create_table(
            "mq_protocols",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("protocol_slug", sa.String(length=255), nullable=False),
            sa.Column("protocol_name", sa.String(length=255), nullable=False),
            sa.Column("category", sa.String(length=128), nullable=True),
            sa.Column("sub_category", sa.String(length=128), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("protocol_slug", name="uq_mq_protocols_protocol_slug"),
        )
        for column in ("protocol_slug", "protocol_name", "category"):
            op.create_index(f"ix_mq_protocols_{column}", "mq_protocols", [column])

    if "mq_label_batches" not in existing_tables:
        op.create_table(
            "mq_label_batches",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("source_job_id", sa.Integer(), nullable=True),
            sa.Column("source_document_id", sa.Integer(), nullable=True),
            sa.Column("entity_id", sa.Integer(), nullable=True),
            sa.Column("protocol_id", _bigint(), nullable=True),
            sa.Column("role_id", sa.SmallInteger(), nullable=True),
            sa.Column("source_type", sa.String(length=128), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_name", sa.String(length=255), nullable=True),
            sa.Column("confidence_default", sa.SmallInteger(), nullable=True),
            sa.Column("quality_tier_default", sa.SmallInteger(), nullable=True),
            sa.Column("status_default", sa.SmallInteger(), nullable=True),
            sa.Column("flags_default", sa.Integer(), nullable=True),
            sa.Column("imported_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("accepted_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("rejected_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("conflict_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("effective_from_block", sa.BigInteger(), nullable=True),
            sa.Column("effective_to_block", sa.BigInteger(), nullable=True),
            sa.Column("label_action", sa.String(length=128), nullable=True),
            sa.Column("supersedes_batch_id", _bigint(), nullable=True),
            sa.Column("batch_hash", sa.String(length=128), nullable=False),
            sa.Column("evidence_hash", sa.String(length=128), nullable=True),
            sa.Column("storage_uri", sa.Text(), nullable=True),
            sa.Column("parser_version", sa.String(length=128), nullable=True),
            sa.Column("dictionary_version", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="pending"),
            sa.Column("created_by", sa.String(length=255), nullable=True),
            sa.Column("approved_by", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
            sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
            sa.ForeignKeyConstraint(["entity_id"], ["mq_entities.id"]),
            sa.ForeignKeyConstraint(["protocol_id"], ["mq_protocols.id"]),
            sa.ForeignKeyConstraint(["role_id"], ["mq_kv_role_dict.role_id"]),
            sa.ForeignKeyConstraint(["supersedes_batch_id"], ["mq_label_batches.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("source_job_id", "source_document_id", "entity_id", "protocol_id", "role_id", "source_type", "supersedes_batch_id", "batch_hash", "dictionary_version", "status"):
            op.create_index(f"ix_mq_label_batches_{column}", "mq_label_batches", [column])

    if "mq_label_batch_evidence" not in existing_tables:
        op.create_table(
            "mq_label_batch_evidence",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("batch_id", _bigint(), nullable=False),
            sa.Column("evidence_type", sa.String(length=128), nullable=False),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("source_document_id", sa.Integer(), nullable=True),
            sa.Column("evidence_hash", sa.String(length=128), nullable=False),
            sa.Column("storage_uri", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["batch_id"], ["mq_label_batches.id"]),
            sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("batch_id", "evidence_type", "source_document_id", "evidence_hash"):
            op.create_index(f"ix_mq_label_batch_evidence_{column}", "mq_label_batch_evidence", [column])

    if "mq_kv_index_manifest" not in existing_tables:
        op.create_table(
            "mq_kv_index_manifest",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("index_name", sa.String(length=255), nullable=False),
            sa.Column("rocksdb_path", sa.Text(), nullable=False),
            sa.Column("column_family", sa.String(length=128), nullable=False),
            sa.Column("key_schema_version", sa.SmallInteger(), nullable=False),
            sa.Column("value_schema_version", sa.SmallInteger(), nullable=False),
            sa.Column("dictionary_version", sa.String(length=128), nullable=False),
            sa.Column("total_keys", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("last_committed_batch_id", _bigint(), nullable=True),
            sa.Column("manifest_hash", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="building"),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["last_committed_batch_id"], ["mq_label_batches.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("index_name", "column_family", "dictionary_version", "last_committed_batch_id", "status"):
            op.create_index(f"ix_mq_kv_index_manifest_{column}", "mq_kv_index_manifest", [column])

    if "mq_kv_index_shards" not in existing_tables:
        op.create_table(
            "mq_kv_index_shards",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("manifest_id", _bigint(), nullable=False),
            sa.Column("prefix_code", sa.SmallInteger(), nullable=True),
            sa.Column("chain_code", sa.String(length=128), nullable=True),
            sa.Column("shard_id", sa.Integer(), nullable=False),
            sa.Column("shard_key", sa.String(length=255), nullable=False),
            sa.Column("key_count", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("min_key_hex", sa.Text(), nullable=True),
            sa.Column("max_key_hex", sa.Text(), nullable=True),
            sa.Column("shard_hash", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="building"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["manifest_id"], ["mq_kv_index_manifest.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("manifest_id", "prefix_code", "shard_id", name="uq_mq_kv_index_shards_manifest_prefix_shard"),
        )
        for column in ("manifest_id", "prefix_code", "chain_code", "shard_key", "status"):
            op.create_index(f"ix_mq_kv_index_shards_{column}", "mq_kv_index_shards", [column])

    if "mq_dictionary_versions" not in existing_tables:
        op.create_table(
            "mq_dictionary_versions",
            sa.Column("id", _bigint(), nullable=False),
            sa.Column("version_name", sa.String(length=128), nullable=False),
            sa.Column("key_prefix_hash", sa.String(length=128), nullable=False),
            sa.Column("role_dict_hash", sa.String(length=128), nullable=False),
            sa.Column("entity_hash", sa.String(length=128), nullable=False),
            sa.Column("protocol_hash", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False, server_default="active"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("version_name", name="uq_mq_dictionary_versions_version_name"),
        )
        op.create_index("ix_mq_dictionary_versions_version_name", "mq_dictionary_versions", ["version_name"])
        op.create_index("ix_mq_dictionary_versions_status", "mq_dictionary_versions", ["status"])

    _add_column_if_missing("mq_address_registry", "is_active", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    _add_column_if_missing("mq_address_registry", "compact_prefix_code", sa.Column("compact_prefix_code", sa.Integer(), nullable=True))
    _add_column_if_missing("mq_address_registry", "address_key", sa.Column("address_key", sa.LargeBinary(), nullable=True))
    _add_column_if_missing("mq_address_registry", "address_type_code", sa.Column("address_type_code", sa.String(length=128), nullable=True))
    _add_column_if_missing("mq_address_registry", "label_batch_id", sa.Column("label_batch_id", _bigint(), nullable=True))
    _create_index_if_missing("mq_address_registry", "ix_mq_address_registry_is_active", ["is_active"])
    _create_index_if_missing("mq_address_registry", "ix_mq_address_registry_compact_prefix_code", ["compact_prefix_code"])
    _create_index_if_missing("mq_address_registry", "ix_mq_address_registry_address_type_code", ["address_type_code"])
    _create_index_if_missing("mq_address_registry", "ix_mq_address_registry_label_batch_id", ["label_batch_id"])
    _create_index_if_missing(
        "mq_address_registry",
        "uq_mq_address_registry_active_compact_key",
        ["compact_prefix_code", "address_key"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for column in ("label_batch_id", "address_type_code", "address_key", "compact_prefix_code", "is_active"):
        _drop_column_if_present("mq_address_registry", column)

    for table_name in (
        "mq_dictionary_versions",
        "mq_kv_index_shards",
        "mq_kv_index_manifest",
        "mq_label_batch_evidence",
        "mq_label_batches",
        "mq_protocols",
        "mq_kv_role_proposals",
        "mq_kv_role_dict",
        "mq_kv_key_prefix_dict",
    ):
        if table_name in existing_tables:
            op.drop_table(table_name)


def _bigint() -> sa.TypeEngine:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _add_column_if_missing(table_name: str, column_name: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    try:
        columns = {item["name"] for item in inspector.get_columns(table_name)}
    except sa.exc.NoSuchTableError:
        return
    if column_name not in columns:
        op.add_column(table_name, column)


def _drop_column_if_present(table_name: str, column_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    try:
        columns = {item["name"] for item in inspector.get_columns(table_name)}
    except sa.exc.NoSuchTableError:
        return
    if column_name in columns:
        op.drop_column(table_name, column_name)


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], unique: bool = False, **kwargs) -> None:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    except sa.exc.NoSuchTableError:
        return
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=unique, **kwargs)
