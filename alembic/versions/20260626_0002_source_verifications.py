"""add source verification audit records

Revision ID: 20260626_0002
Revises: 20260605_0001
Create Date: 2026-06-26
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260626_0002"
down_revision = "20260605_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mq_source_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("candidate_id", sa.Integer(), nullable=True),
        sa.Column("candidate_group_key", sa.Text(), nullable=True),
        sa.Column("entity_name", sa.String(length=255), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("protocol_name", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_origin", sa.String(length=255), nullable=True),
        sa.Column("official_referrer_url", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("input_method", sa.String(length=64), nullable=True),
        sa.Column("evidence_shape", sa.String(length=128), nullable=True),
        sa.Column("verification_scope", sa.String(length=64), nullable=False),
        sa.Column("verification_status", sa.String(length=64), nullable=False),
        sa.Column("source_trust", sa.String(length=128), nullable=False),
        sa.Column("verified_by", sa.String(length=255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_reason", sa.Text(), nullable=True),
        sa.Column("verification_evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["mq_address_candidates.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "source_job_id",
        "source_document_id",
        "candidate_id",
        "candidate_group_key",
        "entity_name",
        "entity_id",
        "source_origin",
        "input_method",
        "evidence_shape",
        "verification_scope",
        "verification_status",
        "source_trust",
    ):
        op.create_index(f"ix_mq_source_verifications_{column}", "mq_source_verifications", [column])


def downgrade() -> None:
    for column in (
        "source_trust",
        "verification_status",
        "verification_scope",
        "evidence_shape",
        "input_method",
        "source_origin",
        "entity_id",
        "entity_name",
        "candidate_group_key",
        "candidate_id",
        "source_document_id",
        "source_job_id",
    ):
        op.drop_index(f"ix_mq_source_verifications_{column}", table_name="mq_source_verifications")
    op.drop_table("mq_source_verifications")
