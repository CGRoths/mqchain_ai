"""intake console v1 schema

Revision ID: 20260605_0001
Revises:
Create Date: 2026-06-05
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260605_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mq_staged_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=True),
        sa.Column("staged_path", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mq_staged_artifacts_sha256", "mq_staged_artifacts", ["sha256"])
    op.create_table(
        "mq_intake_previews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("staged_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("source_artifact_json", sa.JSON(), nullable=False),
        sa.Column("fingerprint_json", sa.JSON(), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("preview_json", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("fatal_errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["staged_artifact_id"], ["mq_staged_artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mq_intake_previews_staged_artifact_id", "mq_intake_previews", ["staged_artifact_id"])
    op.create_table(
        "mq_source_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("preview_id", sa.String(length=36), nullable=False),
        sa.Column("staged_artifact_id", sa.String(length=36), nullable=True),
        sa.Column("input_method", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("pasted_text", sa.Text(), nullable=True),
        sa.Column("requested_source_type", sa.String(length=128), nullable=True),
        sa.Column("final_source_type", sa.String(length=128), nullable=False),
        sa.Column("adapter_name", sa.String(length=128), nullable=False),
        sa.Column("fingerprint_json", sa.JSON(), nullable=False),
        sa.Column("source_artifact_json", sa.JSON(), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("preview_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["preview_id"], ["mq_intake_previews.id"]),
        sa.ForeignKeyConstraint(["staged_artifact_id"], ["mq_staged_artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mq_source_jobs_preview_id", "mq_source_jobs", ["preview_id"])
    op.create_index("ix_mq_source_jobs_staged_artifact_id", "mq_source_jobs", ["staged_artifact_id"])
    op.create_index("ix_mq_source_jobs_final_source_type", "mq_source_jobs", ["final_source_type"])
    op.create_index("ix_mq_source_jobs_adapter_name", "mq_source_jobs", ["adapter_name"])
    op.create_table(
        "mq_source_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=False),
        sa.Column("canonical_source_url", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("document_title", sa.Text(), nullable=True),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mq_address_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_job_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=512), nullable=False),
        sa.Column("normalized_address", sa.String(length=512), nullable=False),
        sa.Column("entity_name", sa.String(length=255), nullable=True),
        sa.Column("source_network", sa.String(length=128), nullable=True),
        sa.Column("chain_guess", sa.String(length=64), nullable=True),
        sa.Column("chain_slug", sa.String(length=128), nullable=True),
        sa.Column("chain_id", sa.Integer(), nullable=True),
        sa.Column("address_family", sa.String(length=64), nullable=True),
        sa.Column("suggested_role", sa.String(length=128), nullable=True),
        sa.Column("confidence_initial", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=128), nullable=False),
        sa.Column("source_input_type", sa.String(length=128), nullable=True),
        sa.Column("source_sheet", sa.String(length=255), nullable=True),
        sa.Column("source_row", sa.Integer(), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("evidence_type", sa.String(length=128), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("raw_reference", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
        sa.ForeignKeyConstraint(["source_job_id"], ["mq_source_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mq_candidate_contexts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("table_name", sa.String(length=255), nullable=True),
        sa.Column("raw_row_json", sa.JSON(), nullable=True),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("normalized_value", sa.Text(), nullable=True),
        sa.Column("parser_warnings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["mq_address_candidates.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mq_address_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=128), nullable=False),
        sa.Column("final_source_type", sa.String(length=128), nullable=False),
        sa.Column("adapter_name", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("confidence_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["mq_address_candidates.id"]),
        sa.ForeignKeyConstraint(["source_document_id"], ["mq_source_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "mq_address_registry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=True),
        sa.Column("normalized_address", sa.String(length=512), nullable=False),
        sa.Column("entity_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("mq_address_registry")
    op.drop_table("mq_address_evidence")
    op.drop_table("mq_candidate_contexts")
    op.drop_table("mq_address_candidates")
    op.drop_table("mq_source_documents")
    op.drop_index("ix_mq_source_jobs_adapter_name", table_name="mq_source_jobs")
    op.drop_index("ix_mq_source_jobs_final_source_type", table_name="mq_source_jobs")
    op.drop_index("ix_mq_source_jobs_staged_artifact_id", table_name="mq_source_jobs")
    op.drop_index("ix_mq_source_jobs_preview_id", table_name="mq_source_jobs")
    op.drop_table("mq_source_jobs")
    op.drop_index("ix_mq_intake_previews_staged_artifact_id", table_name="mq_intake_previews")
    op.drop_table("mq_intake_previews")
    op.drop_index("ix_mq_staged_artifacts_sha256", table_name="mq_staged_artifacts")
    op.drop_table("mq_staged_artifacts")
