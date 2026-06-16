from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.labels.entity_protocol_resolver import EntityProtocolResolutionError, resolve_entity, resolve_protocol, slugify
from app.labels.key_codec import AddressCodecError, KeyPrefix, encode_address_key
from app.labels.kv_store import KVStore, KVWrite, LABEL_CURRENT_CF
from app.labels.memory_kv_store import DEFAULT_MEMORY_KV_STORE
from app.labels.role_mapping import map_role_or_propose, normalize_role_code
from app.labels.value_codec import CurrentLabelValue, pack_current_value, unpack_current_value
from app.models.compact_label import KeyPrefixDict, LabelBatch, LabelBatchEvidence, Protocol, RoleDict
from app.models.intake import AddressCandidate, Entity


SCHEMA_VERSION = 1
STATUS_ACTIVE_CURRENT = 1
STATUS_PENDING_REVIEW = 7
STATUS_CONFLICT = 5
STATUS_DO_NOT_USE = 6
QUALITY_OFFICIAL_VERIFIED = 1
FLAG_IS_OFFICIAL_SOURCE = 1 << 6


class BatchCommitError(ValueError):
    pass


@dataclass(frozen=True)
class BatchCommitOptions:
    candidate_ids: list[int] | None = None
    source_job_id: int | None = None
    entity_name: str | None = None
    protocol_name: str | None = None
    role_code: str | None = None
    confidence: int | None = None
    label_status: int = STATUS_ACTIVE_CURRENT
    quality_tier: int | None = None
    flags: int | None = None
    effective_from_block: int | None = None
    effective_to_block: int | None = None
    dictionary_version: str | None = None
    trusted_operator_override: bool = False
    created_by: str | None = None
    approved_by: str | None = None


class BatchCommitService:
    def __init__(self, db: Session, kv_store: KVStore | None = None) -> None:
        self.db = db
        self.kv_store = kv_store or DEFAULT_MEMORY_KV_STORE

    def dry_run_from_candidates(self, options: BatchCommitOptions) -> dict[str, Any]:
        candidates = self._load_candidates(options)
        prepared = self._prepare_entries(candidates, options, create_missing=False, create_role_proposals=False)
        conflicts = self._detect_conflicts(prepared["entries"], options)
        return self._result(
            dry_run=True,
            status="blocked" if prepared["blockers"] or conflicts else "ready",
            candidates=candidates,
            entries=prepared["entries"],
            blockers=prepared["blockers"],
            conflicts=conflicts,
            batch=None,
        )

    def commit_from_candidates(self, options: BatchCommitOptions) -> dict[str, Any]:
        candidates = self._load_candidates(options)
        role_blockers = self._role_blockers(candidates, options, create_role_proposals=True)
        if role_blockers:
            self.db.commit()
            return self._result(
                dry_run=False,
                status="blocked",
                candidates=candidates,
                entries=[],
                blockers=role_blockers,
                conflicts=[],
                batch=None,
            )

        prepared = self._prepare_entries(candidates, options, create_missing=True, create_role_proposals=False)
        if prepared["blockers"]:
            self.db.rollback()
            return self._result(
                dry_run=False,
                status="blocked",
                candidates=candidates,
                entries=prepared["entries"],
                blockers=prepared["blockers"],
                conflicts=[],
                batch=None,
            )

        conflicts = self._detect_conflicts(prepared["entries"], options)
        if conflicts and not options.trusted_operator_override:
            self.db.rollback()
            return self._result(
                dry_run=False,
                status="blocked",
                candidates=candidates,
                entries=prepared["entries"],
                blockers=[],
                conflicts=conflicts,
                batch=None,
            )

        batch_hash = _hash_payload([_entry_hash_payload(entry) for entry in prepared["entries"]])
        evidence_payload = _batch_evidence_payload(candidates)
        evidence_hash = _hash_payload(evidence_payload)
        batch = LabelBatch(
            source_job_id=_same_or_none(candidate.source_job_id for candidate in candidates) or options.source_job_id,
            source_document_id=_same_or_none(candidate.source_document_id for candidate in candidates),
            entity_id=_same_or_none(entry["entity_id"] for entry in prepared["entries"]),
            protocol_id=_same_or_none(entry["protocol_id"] for entry in prepared["entries"]),
            role_id=_same_or_none(entry["role_id"] for entry in prepared["entries"]),
            source_type=_same_or_none(candidate.source_type for candidate in candidates),
            source_url=_same_or_none(candidate.source_url for candidate in candidates),
            source_name="candidate_batch",
            confidence_default=options.confidence,
            quality_tier_default=options.quality_tier,
            status_default=options.label_status,
            flags_default=options.flags,
            imported_count=len(candidates),
            accepted_count=len(prepared["entries"]),
            rejected_count=0,
            conflict_count=len(conflicts),
            effective_from_block=options.effective_from_block,
            effective_to_block=options.effective_to_block,
            label_action="upsert_current",
            batch_hash=batch_hash,
            evidence_hash=evidence_hash,
            parser_version="phase2_memory_kv",
            dictionary_version=options.dictionary_version,
            status="writing",
            created_by=options.created_by,
            approved_by=options.approved_by,
        )
        self.db.add(batch)
        self.db.flush()
        self.db.add(
            LabelBatchEvidence(
                batch_id=batch.id,
                evidence_type="candidate_evidence_bundle",
                source_url=batch.source_url,
                source_document_id=batch.source_document_id,
                evidence_hash=evidence_hash,
                summary=f"{len(candidates)} approved candidate(s) committed to memory KV",
                payload=evidence_payload,
            )
        )
        writes: list[KVWrite] = []
        for entry in prepared["entries"]:
            value = CurrentLabelValue(
                schema_version=SCHEMA_VERSION,
                confidence_score=entry["confidence"],
                label_status=entry["label_status"],
                quality_tier=entry["quality_tier"],
                entity_id=entry["entity_id"],
                protocol_id=entry["protocol_id"],
                role_id=entry["role_id"],
                flags=entry["flags"],
                batch_id=batch.id,
                first_seen_block_or_slot=entry["first_seen_block_or_slot"],
                last_seen_block_or_slot=entry["last_seen_block_or_slot"],
            )
            value_bytes = pack_current_value(value)
            entry["batch_id"] = batch.id
            entry["value_hex"] = value_bytes.hex()
            writes.append(KVWrite(LABEL_CURRENT_CF, bytes.fromhex(entry["key_hex"]), value_bytes))
        self.kv_store.write_batch(writes)
        batch.status = "committed"
        self.db.commit()
        return self._result(
            dry_run=False,
            status="committed",
            candidates=candidates,
            entries=prepared["entries"],
            blockers=[],
            conflicts=conflicts,
            batch=batch,
        )

    def _load_candidates(self, options: BatchCommitOptions) -> list[AddressCandidate]:
        stmt = select(AddressCandidate).options(selectinload(AddressCandidate.evidence)).order_by(AddressCandidate.id.asc())
        if options.candidate_ids:
            stmt = stmt.where(AddressCandidate.id.in_(options.candidate_ids))
        if options.source_job_id is not None:
            stmt = stmt.where(AddressCandidate.source_job_id == options.source_job_id)
        candidates = list(self.db.scalars(stmt))
        if not candidates:
            raise BatchCommitError("no_candidates_selected")
        return candidates

    def _role_blockers(self, candidates: list[AddressCandidate], options: BatchCommitOptions, *, create_role_proposals: bool) -> list[dict[str, Any]]:
        blockers = []
        for candidate in candidates:
            if candidate.status != "approved":
                blockers.append({"candidate_id": candidate.id, "reason": "candidate_not_approved", "status": candidate.status})
                continue
            role_text = options.role_code or candidate.suggested_role
            if create_role_proposals:
                result = map_role_or_propose(
                    self.db,
                    role_text,
                    source_job_id=candidate.source_job_id,
                    example_addresses=[candidate.normalized_address or candidate.address],
                    reason="unknown_role_blocks_compact_label_commit",
                )
                if not result.can_commit:
                    blockers.append(
                        {
                            "candidate_id": candidate.id,
                            "reason": "unknown_role_created_proposal",
                            "role_code": result.role_code,
                            "proposal_id": result.proposal_id,
                        }
                    )
                continue
            role_code = normalize_role_code(role_text)
            role = self.db.scalar(select(RoleDict).where(RoleDict.role_code == role_code, RoleDict.is_active.is_(True))) if role_code else None
            if role is None:
                blockers.append({"candidate_id": candidate.id, "reason": "unknown_role_requires_proposal", "role_code": role_code or "unknown"})
        return blockers

    def _prepare_entries(
        self,
        candidates: list[AddressCandidate],
        options: BatchCommitOptions,
        *,
        create_missing: bool,
        create_role_proposals: bool,
    ) -> dict[str, Any]:
        blockers = self._role_blockers(candidates, options, create_role_proposals=create_role_proposals)
        if blockers:
            return {"entries": [], "blockers": blockers}
        entries = []
        for candidate in candidates:
            try:
                prefix = self._prefix_for_candidate(candidate)
                encoded = encode_address_key(prefix, candidate.normalized_address or candidate.address)
                role = self._role_for_candidate(candidate, options)
                entity_info = self._entity_for_candidate(candidate, options, create_missing=create_missing)
                protocol_info = self._protocol_for_candidate(candidate, options, create_missing=create_missing)
            except (AddressCodecError, BatchCommitError, EntityProtocolResolutionError) as exc:
                blockers.append({"candidate_id": candidate.id, "reason": str(exc)})
                continue
            confidence = _uint8(options.confidence if options.confidence is not None else candidate.confidence_initial)
            quality_tier = _uint8(options.quality_tier if options.quality_tier is not None else role.default_quality_tier)
            flags = _uint16(options.flags if options.flags is not None else role.default_flags)
            entry = {
                "candidate_id": candidate.id,
                "source_job_id": candidate.source_job_id,
                "source_document_id": candidate.source_document_id,
                "address": candidate.address,
                "normalized_display": encoded.normalized_display,
                "prefix_code": encoded.prefix_code,
                "prefix_hex": encoded.prefix_hex,
                "key_hex": encoded.full_key_hex,
                "payload_hex": encoded.payload_hex,
                "entity_id": entity_info["id"],
                "entity_name": entity_info["name"],
                "entity_slug": entity_info["slug"],
                "entity_created": entity_info["created"],
                "protocol_id": protocol_info["id"],
                "protocol_name": protocol_info["name"],
                "protocol_slug": protocol_info["slug"],
                "protocol_created": protocol_info["created"],
                "role_id": role.role_id,
                "role_code": role.role_code,
                "confidence": confidence,
                "label_status": _uint8(options.label_status),
                "quality_tier": quality_tier,
                "flags": flags,
                "batch_id": None,
                "value_hex": None,
                "first_seen_block_or_slot": _uint32(options.effective_from_block or 0),
                "last_seen_block_or_slot": _uint32(options.effective_to_block or 0),
            }
            entries.append(entry)
        blockers.extend(_incoming_conflicts(entries))
        return {"entries": entries, "blockers": blockers}

    def _prefix_for_candidate(self, candidate: AddressCandidate) -> KeyPrefix:
        rows = self.db.scalars(
            select(KeyPrefixDict)
            .where(KeyPrefixDict.is_active.is_(True), KeyPrefixDict.codec_status == "active")
            .order_by(KeyPrefixDict.prefix_code.asc())
        ).all()
        if not rows:
            raise BatchCommitError("key_prefix_dictionary_not_seeded")
        chain_code = _chain_code(candidate)
        candidates = []
        if candidate.chain_id is not None:
            candidates.extend([row for row in rows if row.evm_chain_id == candidate.chain_id])
        if chain_code:
            candidates.extend([row for row in rows if row.chain_code == chain_code])
            if chain_code == "bitcoin":
                candidates.extend([row for row in rows if row.chain_code == "btc"])
        seen = set()
        for row in candidates:
            if row.prefix_code in seen:
                continue
            seen.add(row.prefix_code)
            prefix = _prefix_from_row(row)
            try:
                encode_address_key(prefix, candidate.normalized_address or candidate.address)
            except AddressCodecError:
                continue
            return prefix
        raise BatchCommitError(f"no_active_prefix_for_candidate:{candidate.id}:{candidate.chain_slug or candidate.chain_id}")

    def _role_for_candidate(self, candidate: AddressCandidate, options: BatchCommitOptions) -> RoleDict:
        role_code = normalize_role_code(options.role_code or candidate.suggested_role)
        role = self.db.scalar(select(RoleDict).where(RoleDict.role_code == role_code, RoleDict.is_active.is_(True))) if role_code else None
        if role is None:
            raise BatchCommitError(f"unknown_role:{role_code or 'unknown'}")
        return role

    def _entity_for_candidate(self, candidate: AddressCandidate, options: BatchCommitOptions, *, create_missing: bool) -> dict[str, Any]:
        name = options.entity_name or candidate.entity_name
        if not name:
            raise BatchCommitError("missing_entity_name")
        slug = slugify(name)
        existing = self.db.scalar(select(Entity).where(Entity.entity_slug == slug))
        if existing is None:
            existing = self.db.scalar(select(Entity).where(Entity.entity_name == name))
        if existing is not None:
            return {"id": existing.id, "name": existing.entity_name, "slug": existing.entity_slug or slug, "created": False}
        if not create_missing:
            return {"id": None, "name": name, "slug": slug, "created": True}
        resolved = resolve_entity(self.db, name, entity_slug=slug)
        return {"id": resolved.entity.id, "name": resolved.entity.entity_name, "slug": resolved.entity.entity_slug, "created": resolved.created}

    def _protocol_for_candidate(self, candidate: AddressCandidate, options: BatchCommitOptions, *, create_missing: bool) -> dict[str, Any]:
        raw_reference = candidate.raw_reference or {}
        name = options.protocol_name or raw_reference.get("row_protocol") or raw_reference.get("protocol_name") or candidate.entity_name
        if not name:
            raise BatchCommitError("missing_protocol_name")
        slug = slugify(name)
        existing = self.db.scalar(select(Protocol).where(Protocol.protocol_slug == slug))
        if existing is None:
            existing = self.db.scalar(select(Protocol).where(Protocol.protocol_name == name))
        if existing is not None:
            return {"id": existing.id, "name": existing.protocol_name, "slug": existing.protocol_slug, "created": False}
        if not create_missing:
            return {"id": None, "name": name, "slug": slug, "created": True}
        resolved = resolve_protocol(self.db, name, protocol_slug=slug, category=raw_reference.get("row_category"))
        return {"id": resolved.protocol.id, "name": resolved.protocol.protocol_name, "slug": resolved.protocol.protocol_slug, "created": resolved.created}

    def _detect_conflicts(self, entries: list[dict[str, Any]], options: BatchCommitOptions) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        for entry in entries:
            existing_bytes = self.kv_store.get(LABEL_CURRENT_CF, bytes.fromhex(entry["key_hex"]))
            if existing_bytes is None:
                continue
            existing = unpack_current_value(existing_bytes)
            reasons: list[str] = []
            if existing.entity_id != entry["entity_id"] or existing.role_id != entry["role_id"] or existing.label_status != entry["label_status"]:
                reasons.append("overlapping_label_conflict")
            if _is_official(existing) and entry["confidence"] < existing.confidence_score:
                reasons.append("lower_confidence_would_overwrite_official_label")
            if existing.label_status == STATUS_ACTIVE_CURRENT and entry["label_status"] in {STATUS_PENDING_REVIEW, STATUS_CONFLICT, STATUS_DO_NOT_USE}:
                reasons.append("status_downgrade_requires_trusted_override")
            if reasons:
                conflicts.append(
                    {
                        "key_hex": entry["key_hex"],
                        "existing": {
                            "entity_id": existing.entity_id,
                            "protocol_id": existing.protocol_id,
                            "role_id": existing.role_id,
                            "label_status": existing.label_status,
                            "quality_tier": existing.quality_tier,
                            "confidence": existing.confidence_score,
                            "flags": existing.flags,
                            "batch_id": existing.batch_id,
                        },
                        "incoming": {
                            "entity_id": entry["entity_id"],
                            "protocol_id": entry["protocol_id"],
                            "role_id": entry["role_id"],
                            "label_status": entry["label_status"],
                            "quality_tier": entry["quality_tier"],
                            "confidence": entry["confidence"],
                            "flags": entry["flags"],
                            "source_job_id": entry["source_job_id"],
                        },
                        "reason": ";".join(reasons),
                        "recommended_action": "require_trusted_operator_override" if not options.trusted_operator_override else "override_applied",
                    }
                )
        return conflicts

    def _result(
        self,
        *,
        dry_run: bool,
        status: str,
        candidates: list[AddressCandidate],
        entries: list[dict[str, Any]],
        blockers: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        batch: LabelBatch | None,
    ) -> dict[str, Any]:
        return {
            "dry_run": dry_run,
            "status": status,
            "batch_id": batch.id if batch is not None else None,
            "candidates_scanned": len(candidates),
            "accepted_count": len(entries) if not blockers else 0,
            "blocked_count": len(blockers),
            "conflict_count": len(conflicts),
            "blockers": blockers,
            "conflicts": conflicts,
            "entries": entries,
        }


def _prefix_from_row(row: KeyPrefixDict) -> KeyPrefix:
    return KeyPrefix(
        prefix_code=row.prefix_code,
        chain_code=row.chain_code,
        chain_name=row.chain_name,
        chain_family=row.chain_family,
        address_family=row.address_family,
        codec=row.codec,
        codec_status=row.codec_status,
        payload_len=row.payload_len,
        is_active=row.is_active,
        evm_chain_id=row.evm_chain_id,
        slip44_id=row.slip44_id,
        native_symbol=row.native_symbol,
        description=row.description,
    )


def _chain_code(candidate: AddressCandidate) -> str | None:
    raw = (candidate.chain_slug or candidate.source_network or "").strip().lower()
    if not raw:
        return None
    normalized = raw.replace("-", "_").replace(" ", "_")
    return {
        "arbitrum": "arbitrum_one",
        "avalanche_c": "avalanche_c",
        "avalanche": "avalanche_c",
        "zksync_era": "zksync_era",
        "bitcoin": "btc",
    }.get(normalized, normalized)


def _incoming_conflicts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        prior = seen.get(entry["key_hex"])
        if prior is None:
            seen[entry["key_hex"]] = entry
            continue
        if (
            prior["entity_id"],
            prior["role_id"],
            prior["label_status"],
        ) != (
            entry["entity_id"],
            entry["role_id"],
            entry["label_status"],
        ):
            blockers.append(
                {
                    "candidate_id": entry["candidate_id"],
                    "reason": "incoming_batch_duplicate_conflict",
                    "key_hex": entry["key_hex"],
                    "prior_candidate_id": prior["candidate_id"],
                }
            )
    return blockers


def _batch_evidence_payload(candidates: list[AddressCandidate]) -> dict[str, Any]:
    return {
        "candidate_ids": [candidate.id for candidate in candidates],
        "evidence": [
            {
                "candidate_id": candidate.id,
                "source_document_id": evidence.source_document_id,
                "evidence_type": evidence.evidence_type,
                "source_type": evidence.source_type,
                "final_source_type": evidence.final_source_type,
                "adapter_name": evidence.adapter_name,
                "source_url": evidence.source_url,
                "file_path": evidence.file_path,
                "payload": evidence.payload,
            }
            for candidate in candidates
            for evidence in candidate.evidence
        ],
    }


def _entry_hash_payload(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "candidate_id",
        "key_hex",
        "entity_id",
        "protocol_id",
        "role_id",
        "confidence",
        "label_status",
        "quality_tier",
        "flags",
        "first_seen_block_or_slot",
        "last_seen_block_or_slot",
    )
    return {key: entry.get(key) for key in keys}


def _hash_payload(payload: Any) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _same_or_none(values) -> Any:
    items = list(values)
    if not items:
        return None
    first = items[0]
    return first if all(item == first for item in items) else None


def _is_official(value: CurrentLabelValue) -> bool:
    return bool(value.flags & FLAG_IS_OFFICIAL_SOURCE) or value.quality_tier == QUALITY_OFFICIAL_VERIFIED


def _uint8(value: int | None) -> int:
    number = int(value or 0)
    if not 0 <= number <= 255:
        raise BatchCommitError(f"uint8_out_of_range:{number}")
    return number


def _uint16(value: int | None) -> int:
    number = int(value or 0)
    if not 0 <= number <= 65535:
        raise BatchCommitError(f"uint16_out_of_range:{number}")
    return number


def _uint32(value: int | None) -> int:
    number = int(value or 0)
    if not 0 <= number <= 0xFFFFFFFF:
        raise BatchCommitError(f"uint32_out_of_range:{number}")
    return number
