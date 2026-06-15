"""review registry schema

Revision ID: 20260607_0002
Revises: 20260605_0001
Create Date: 2026-06-07
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260607_0002"
down_revision = "20260605_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    _add_column_if_missing("mq_address_candidates", "approved_at", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("mq_address_candidates", "approved_by", sa.Column("approved_by", sa.String(length=255), nullable=True))
    _add_column_if_missing("mq_address_candidates", "approval_method", sa.Column("approval_method", sa.String(length=128), nullable=True))
    _add_column_if_missing("mq_address_candidates", "approval_notes", sa.Column("approval_notes", sa.Text(), nullable=True))

    if "mq_entities" not in existing_tables:
        op.create_table(
            "mq_entities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("entity_name", sa.String(length=255), nullable=False),
            sa.Column("entity_type", sa.String(length=128), nullable=True),
            sa.Column("category", sa.String(length=128), nullable=True),
            sa.Column("sub_category", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("entity_name", name="uq_mq_entities_entity_name"),
        )
        op.create_index("ix_mq_entities_entity_name", "mq_entities", ["entity_name"])

    if "mq_approved_addresses" not in existing_tables:
        op.create_table(
            "mq_approved_addresses",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("address", sa.String(length=512), nullable=False),
            sa.Column("normalized_address", sa.String(length=512), nullable=False),
            sa.Column("source_network", sa.String(length=128), nullable=True),
            sa.Column("chain_slug", sa.String(length=128), nullable=False),
            sa.Column("address_class", sa.String(length=128), nullable=False),
            sa.Column("source_trust_status", sa.String(length=128), nullable=False),
            sa.Column("approval_readiness_at_approval", sa.String(length=128), nullable=False),
            sa.Column("confidence_score", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False),
            sa.Column("first_approved_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["entity_id"], ["mq_entities.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("entity_id", "chain_slug", "normalized_address", name="uq_mq_approved_addresses_entity_chain_address"),
        )
        for column in (
            "entity_id",
            "address",
            "normalized_address",
            "source_network",
            "chain_slug",
            "address_class",
            "source_trust_status",
            "approval_readiness_at_approval",
            "status",
        ):
            op.create_index(f"ix_mq_approved_addresses_{column}", "mq_approved_addresses", [column])

    if "mq_approved_address_roles" not in existing_tables:
        op.create_table(
            "mq_approved_address_roles",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("approved_address_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=128), nullable=False),
            sa.Column("role_confidence", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["approved_address_id"], ["mq_approved_addresses.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("approved_address_id", "role", name="uq_mq_approved_address_roles_address_role"),
        )
        op.create_index("ix_mq_approved_address_roles_approved_address_id", "mq_approved_address_roles", ["approved_address_id"])
        op.create_index("ix_mq_approved_address_roles_role", "mq_approved_address_roles", ["role"])
        op.create_index("ix_mq_approved_address_roles_status", "mq_approved_address_roles", ["status"])

    if "mq_approved_address_evidence" not in existing_tables:
        op.create_table(
            "mq_approved_address_evidence",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("approved_address_id", sa.Integer(), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=True),
            sa.Column("source_document_id", sa.Integer(), nullable=True),
            sa.Column("evidence_type", sa.String(length=128), nullable=False),
            sa.Column("source_type", sa.String(length=128), nullable=True),
            sa.Column("source_input_type", sa.String(length=128), nullable=True),
            sa.Column("source_job_id", sa.Integer(), nullable=True),
            sa.Column("source_url", sa.Text(), nullable=True),
            sa.Column("file_path", sa.Text(), nullable=True),
            sa.Column("raw_reference", sa.JSON(), nullable=True),
            sa.Column("confidence_contribution", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["approved_address_id"], ["mq_approved_addresses.id"]),
            sa.ForeignKeyConstraint(["candidate_id"], ["mq_address_candidates.id"]),
            sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in (
            "approved_address_id",
            "candidate_id",
            "source_document_id",
            "evidence_type",
            "source_type",
            "source_input_type",
            "source_job_id",
        ):
            op.create_index(f"ix_mq_approved_address_evidence_{column}", "mq_approved_address_evidence", [column])

    if "mq_approval_events" not in existing_tables:
        op.create_table(
            "mq_approval_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("approved_address_id", sa.Integer(), nullable=True),
            sa.Column("candidate_group_key", sa.String(length=512), nullable=False),
            sa.Column("action", sa.String(length=64), nullable=False),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("dry_run", sa.Boolean(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["approved_address_id"], ["mq_approved_addresses.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        for column in ("approved_address_id", "candidate_group_key", "action", "actor"):
            op.create_index(f"ix_mq_approval_events_{column}", "mq_approval_events", [column])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "mq_approval_events" in existing_tables:
        op.drop_table("mq_approval_events")
    if "mq_approved_address_evidence" in existing_tables:
        op.drop_table("mq_approved_address_evidence")
    if "mq_approved_address_roles" in existing_tables:
        op.drop_table("mq_approved_address_roles")
    if "mq_approved_addresses" in existing_tables:
        op.drop_table("mq_approved_addresses")
    if "mq_entities" in existing_tables:
        op.drop_table("mq_entities")

    for column in ("approval_notes", "approval_method", "approved_by", "approved_at"):
        _drop_column_if_present("mq_address_candidates", column)


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
