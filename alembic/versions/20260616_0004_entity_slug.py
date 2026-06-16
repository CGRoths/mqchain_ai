"""entity slug for deterministic compact-label ownership

Revision ID: 20260616_0004
Revises: 20260607_0003
Create Date: 2026-06-16
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260616_0004"
down_revision = "20260607_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _add_column_if_missing("mq_entities", "entity_slug", sa.Column("entity_slug", sa.String(length=255), nullable=True))
    _create_index_if_missing("mq_entities", "ix_mq_entities_entity_slug", ["entity_slug"])
    _create_index_if_missing("mq_entities", "uq_mq_entities_entity_slug", ["entity_slug"], unique=True)


def downgrade() -> None:
    _drop_index_if_present("mq_entities", "uq_mq_entities_entity_slug")
    _drop_index_if_present("mq_entities", "ix_mq_entities_entity_slug")
    _drop_column_if_present("mq_entities", "entity_slug")


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


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str], unique: bool = False) -> None:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    except sa.exc.NoSuchTableError:
        return
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns, unique=unique)


def _drop_index_if_present(table_name: str, index_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    try:
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
    except sa.exc.NoSuchTableError:
        return
    if index_name in indexes:
        op.drop_index(index_name, table_name=table_name)
