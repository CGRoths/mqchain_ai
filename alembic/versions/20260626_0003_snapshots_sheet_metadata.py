"""add sheet scoped verification and source snapshots

Revision ID: 20260626_0003
Revises: 20260626_0002
Create Date: 2026-06-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260626_0003"
down_revision = "20260626_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mq_source_verifications", sa.Column("source_sheet", sa.String(length=255), nullable=True))
    op.create_index("ix_mq_source_verifications_source_sheet", "mq_source_verifications", ["source_sheet"])
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

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

    op.create_table(
        "mq_source_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("entity_name", sa.String(length=255), nullable=True),
        sa.Column("source_origin", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("official_referrer_url", sa.Text(), nullable=True),
        sa.Column("snapshot_type", sa.String(length=128), nullable=False),
        sa.Column("snapshot_period", sa.String(length=64), nullable=True),
        sa.Column("snapshot_date", sa.String(length=32), nullable=True),
        sa.Column("file_hash", sa.String(length=128), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("previous_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["previous_snapshot_id"], ["mq_source_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "source_job_id",
        "source_document_id",
        "entity_name",
        "source_origin",
        "snapshot_type",
        "snapshot_period",
        "snapshot_date",
        "file_hash",
        "content_hash",
        "previous_snapshot_id",
    ):
        op.create_index(f"ix_mq_source_snapshots_{column}", "mq_source_snapshots", [column])

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
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("latest_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("latest_snapshot_status", sa.String(length=64), nullable=True),
            sa.Column("lifecycle_status", sa.String(length=64), server_default="active", nullable=False),
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
            "latest_snapshot_id",
            "latest_snapshot_status",
            "lifecycle_status",
        ):
            op.create_index(f"ix_mq_approved_addresses_{column}", "mq_approved_addresses", [column])
    else:
        op.add_column("mq_approved_addresses", sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("mq_approved_addresses", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("mq_approved_addresses", sa.Column("latest_snapshot_id", sa.Integer(), nullable=True))
        op.add_column("mq_approved_addresses", sa.Column("latest_snapshot_status", sa.String(length=64), nullable=True))
        op.add_column("mq_approved_addresses", sa.Column("lifecycle_status", sa.String(length=64), server_default="active", nullable=False))
        for column in ("latest_snapshot_id", "latest_snapshot_status", "lifecycle_status"):
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
        for column in ("approved_address_id", "role", "status"):
            op.create_index(f"ix_mq_approved_address_roles_{column}", "mq_approved_address_roles", [column])

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
        for column in ("approved_address_id", "candidate_id", "source_document_id", "evidence_type", "source_type", "source_input_type", "source_job_id"):
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

    op.create_table(
        "mq_approved_address_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("approved_address_id", sa.Integer(), nullable=True),
        sa.Column("approved_address_role_id", sa.Integer(), nullable=True),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("source_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("source_verification_id", sa.Integer(), nullable=True),
        sa.Column("source_job_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("source_sheet", sa.String(length=255), nullable=True),
        sa.Column("entity_name", sa.String(length=255), nullable=True),
        sa.Column("chain_slug", sa.String(length=128), nullable=True),
        sa.Column("normalized_address", sa.String(length=512), nullable=True),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("address_class", sa.String(length=128), nullable=True),
        sa.Column("observed_status", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_date", sa.String(length=32), nullable=True),
        sa.Column("snapshot_period", sa.String(length=64), nullable=True),
        sa.Column("evidence_type", sa.String(length=128), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_origin", sa.String(length=255), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["approved_address_id"], ["mq_approved_addresses.id"]),
        sa.ForeignKeyConstraint(["approved_address_role_id"], ["mq_approved_address_roles.id"]),
        sa.ForeignKeyConstraint(["candidate_id"], ["mq_address_candidates.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
        sa.ForeignKeyConstraint(["source_snapshot_id"], ["mq_source_snapshots.id"]),
        sa.ForeignKeyConstraint(["source_verification_id"], ["mq_source_verifications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "approved_address_id",
        "approved_address_role_id",
        "candidate_id",
        "source_snapshot_id",
        "source_verification_id",
        "source_job_id",
        "source_document_id",
        "source_sheet",
        "entity_name",
        "chain_slug",
        "normalized_address",
        "role",
        "address_class",
        "observed_status",
        "snapshot_date",
        "snapshot_period",
        "evidence_type",
        "source_origin",
    ):
        op.create_index(f"ix_mq_approved_address_observations_{column}", "mq_approved_address_observations", [column])


def downgrade() -> None:
    for column in (
        "source_origin",
        "evidence_type",
        "snapshot_period",
        "snapshot_date",
        "observed_status",
        "address_class",
        "role",
        "normalized_address",
        "chain_slug",
        "entity_name",
        "source_sheet",
        "source_document_id",
        "source_job_id",
        "source_verification_id",
        "source_snapshot_id",
        "candidate_id",
        "approved_address_role_id",
        "approved_address_id",
    ):
        op.drop_index(f"ix_mq_approved_address_observations_{column}", table_name="mq_approved_address_observations")
    op.drop_table("mq_approved_address_observations")

    for column in ("lifecycle_status", "latest_snapshot_status", "latest_snapshot_id"):
        op.drop_index(f"ix_mq_approved_addresses_{column}", table_name="mq_approved_addresses")
    op.drop_column("mq_approved_addresses", "lifecycle_status")
    op.drop_column("mq_approved_addresses", "latest_snapshot_status")
    op.drop_column("mq_approved_addresses", "latest_snapshot_id")
    op.drop_column("mq_approved_addresses", "last_seen_at")
    op.drop_column("mq_approved_addresses", "first_seen_at")

    for column in (
        "previous_snapshot_id",
        "content_hash",
        "file_hash",
        "snapshot_date",
        "snapshot_period",
        "snapshot_type",
        "source_origin",
        "entity_name",
        "source_document_id",
        "source_job_id",
    ):
        op.drop_index(f"ix_mq_source_snapshots_{column}", table_name="mq_source_snapshots")
    op.drop_table("mq_source_snapshots")

    op.drop_index("ix_mq_source_verifications_source_sheet", table_name="mq_source_verifications")
    op.drop_column("mq_source_verifications", "source_sheet")
