from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.compact_label import DictionaryVersion, KeyPrefixDict, Protocol, RoleDict
from app.models.intake import Entity


class DictionaryMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class DictionarySnapshot:
    key_prefix_hash: str
    role_dict_hash: str
    entity_hash: str
    protocol_hash: str
    dictionary_version: str | None = None


def load_dictionary_snapshot(db: Session, version_name: str | None = None) -> DictionarySnapshot:
    snapshot = DictionarySnapshot(
        key_prefix_hash=_hash_key_prefixes(db),
        role_dict_hash=_hash_roles(db),
        entity_hash=_hash_entities(db),
        protocol_hash=_hash_protocols(db),
        dictionary_version=version_name,
    )
    if version_name is None:
        return snapshot
    expected = db.scalar(select(DictionaryVersion).where(DictionaryVersion.version_name == version_name))
    if expected is None:
        raise DictionaryMismatchError(f"dictionary_version_not_found:{version_name}")
    mismatches = []
    for field_name in ("key_prefix_hash", "role_dict_hash", "entity_hash", "protocol_hash"):
        if getattr(expected, field_name) != getattr(snapshot, field_name):
            mismatches.append(field_name)
    if mismatches:
        raise DictionaryMismatchError(f"dictionary_hash_mismatch:{','.join(mismatches)}")
    return snapshot


def freeze_dictionary_version(db: Session, version_name: str, *, status: str = "active") -> DictionaryVersion:
    snapshot = load_dictionary_snapshot(db)
    version = db.scalar(select(DictionaryVersion).where(DictionaryVersion.version_name == version_name))
    if version is None:
        version = DictionaryVersion(version_name=version_name)
        db.add(version)
    version.key_prefix_hash = snapshot.key_prefix_hash
    version.role_dict_hash = snapshot.role_dict_hash
    version.entity_hash = snapshot.entity_hash
    version.protocol_hash = snapshot.protocol_hash
    version.status = status
    db.flush()
    return version


def _hash_key_prefixes(db: Session) -> str:
    rows = db.scalars(select(KeyPrefixDict).order_by(KeyPrefixDict.prefix_code.asc())).all()
    return _canonical_hash(
        [
            {
                "prefix_code": row.prefix_code,
                "chain_id": row.chain_id,
                "chain_code": row.chain_code,
                "chain_name": row.chain_name,
                "chain_family": row.chain_family,
                "address_family": row.address_family,
                "codec": row.codec,
                "codec_status": row.codec_status,
                "payload_len": row.payload_len,
                "evm_chain_id": row.evm_chain_id,
                "slip44_id": row.slip44_id,
                "native_symbol": row.native_symbol,
                "is_active": row.is_active,
            }
            for row in rows
        ]
    )


def _hash_roles(db: Session) -> str:
    rows = db.scalars(select(RoleDict).order_by(RoleDict.role_id.asc())).all()
    return _canonical_hash(
        [
            {
                "role_id": row.role_id,
                "role_code": row.role_code,
                "category_code": row.category_code,
                "role_group": row.role_group,
                "metric_usage_default": row.metric_usage_default,
                "boundary_class": row.boundary_class,
                "default_quality_tier": row.default_quality_tier,
                "default_flags": row.default_flags,
                "is_active": row.is_active,
            }
            for row in rows
        ]
    )


def _hash_entities(db: Session) -> str:
    rows = db.scalars(select(Entity).order_by(Entity.id.asc())).all()
    return _canonical_hash(
        [
            {
                "id": row.id,
                "entity_name": row.entity_name,
                "entity_type": row.entity_type,
                "category": row.category,
                "sub_category": row.sub_category,
            }
            for row in rows
        ]
    )


def _hash_protocols(db: Session) -> str:
    rows = db.scalars(select(Protocol).order_by(Protocol.id.asc())).all()
    return _canonical_hash(
        [
            {
                "id": row.id,
                "protocol_slug": row.protocol_slug,
                "protocol_name": row.protocol_name,
                "category": row.category,
                "sub_category": row.sub_category,
            }
            for row in rows
        ]
    )


def _canonical_hash(payload: list[dict[str, Any]]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
