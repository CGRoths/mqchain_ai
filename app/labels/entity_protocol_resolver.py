from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.compact_label import Protocol
from app.models.intake import Entity


class EntityProtocolResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedEntity:
    entity: Entity
    created: bool


@dataclass(frozen=True)
class ResolvedProtocol:
    protocol: Protocol
    created: bool


def slugify(value: str | None) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    if not text:
        raise EntityProtocolResolutionError("empty_slug_source")
    return text


def resolve_entity(db: Session, entity_name: str | None, *, entity_slug: str | None = None) -> ResolvedEntity:
    if not entity_name and not entity_slug:
        raise EntityProtocolResolutionError("missing_entity_name")
    slug = slugify(entity_slug or entity_name)
    existing = db.scalar(select(Entity).where(Entity.entity_slug == slug))
    if existing is None and entity_name:
        existing = db.scalar(select(Entity).where(Entity.entity_name == entity_name))
    if existing is not None:
        if existing.entity_slug is None:
            existing.entity_slug = slug
            db.flush()
        return ResolvedEntity(existing, False)
    entity = Entity(entity_name=entity_name or slug, entity_slug=slug)
    db.add(entity)
    db.flush()
    return ResolvedEntity(entity, True)


def resolve_protocol(
    db: Session,
    protocol_name: str | None,
    *,
    protocol_slug: str | None = None,
    category: str | None = None,
    sub_category: str | None = None,
) -> ResolvedProtocol:
    if not protocol_name and not protocol_slug:
        raise EntityProtocolResolutionError("missing_protocol_name")
    slug = slugify(protocol_slug or protocol_name)
    existing = db.scalar(select(Protocol).where(Protocol.protocol_slug == slug))
    if existing is None and protocol_name:
        existing = db.scalar(select(Protocol).where(Protocol.protocol_name == protocol_name))
    if existing is not None:
        return ResolvedProtocol(existing, False)
    protocol = Protocol(
        protocol_slug=slug,
        protocol_name=protocol_name or slug,
        category=category,
        sub_category=sub_category,
        metadata_json={"created_by": "compact_label_batch_commit"},
    )
    db.add(protocol)
    db.flush()
    return ResolvedProtocol(protocol, True)
