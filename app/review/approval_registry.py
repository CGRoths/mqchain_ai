from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ingestion.network_normalizer import NetworkNormalizer
from app.review.candidate_audit import (
    classify_approval_readiness,
    classify_candidate_address_class,
    classify_source_trust_status,
)
from app.models.intake import (
    AddressCandidate,
    ApprovalEvent,
    ApprovedAddress,
    ApprovedAddressEvidence,
    ApprovedAddressRole,
    Entity,
)


DEFAULT_APPROVABLE_READINESS = {
    "auto_ready_official_verified",
    "ready_for_approval_cex_reserve",
    "ready_for_approval_core_protocol",
}
LOW_CONFIDENCE_OVERRIDE_READINESS = "needs_review_official_low_confidence"
LOW_CONFIDENCE_OVERRIDE_REASON = "manual_policy_override: official low-confidence reserve/core candidate"
LOW_CONFIDENCE_OVERRIDE_ADDRESS_CLASSES = {"cex_reserve_wallet", "core_protocol_contract"}
LOW_CONFIDENCE_OVERRIDE_SOURCE_TRUST = {
    "official_confirmed",
    "official_audit_confirmed",
    "official_published_list",
}

HOT_COLD_OVERRIDE_READINESS = "needs_review_hot_cold_wallet"
HOT_COLD_OVERRIDE_REASON = "manual_policy_override: official hot/cold wallet candidate"
HOT_COLD_OVERRIDE_ADDRESS_CLASSES = {"cex_hot_wallet", "cex_cold_wallet"}
HOT_COLD_OVERRIDE_SOURCE_TRUST = {
    "official_confirmed",
    "official_audit_confirmed",
    "official_published_list",
}
SUPPORTED_OVERRIDE_POLICIES = {
    LOW_CONFIDENCE_OVERRIDE_READINESS: {
        "reason": LOW_CONFIDENCE_OVERRIDE_REASON,
        "address_classes": LOW_CONFIDENCE_OVERRIDE_ADDRESS_CLASSES,
        "source_trust": LOW_CONFIDENCE_OVERRIDE_SOURCE_TRUST,
    },
    HOT_COLD_OVERRIDE_READINESS: {
        "reason": HOT_COLD_OVERRIDE_REASON,
        "address_classes": HOT_COLD_OVERRIDE_ADDRESS_CLASSES,
        "source_trust": HOT_COLD_OVERRIDE_SOURCE_TRUST,
    },
}


@dataclass
class CandidateGroup:
    group_key: str
    entity_name: str | None
    chain_slug: str | None
    normalized_address: str | None
    suggested_role: str | None
    address_class: str
    source_trust_status: str
    approval_readiness: str
    candidates: list[AddressCandidate]


def build_candidate_group_key(candidate) -> str:
    address_class = classify_candidate_address_class(candidate, _first_evidence_payload(candidate))
    chain_slug = _candidate_chain_slug(candidate)
    return json.dumps(
        {
            "entity_name": candidate.entity_name,
            "chain_slug": chain_slug,
            "normalized_address": candidate.normalized_address,
            "suggested_role": candidate.suggested_role,
            "address_class": address_class,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def get_unique_candidate_groups(db: Session, source_job_id: int | None = None, approval_readiness: str | None = None) -> list[CandidateGroup]:
    stmt = select(AddressCandidate).options(selectinload(AddressCandidate.evidence)).order_by(AddressCandidate.id.asc())
    if source_job_id is not None:
        stmt = stmt.where(AddressCandidate.source_job_id == source_job_id)
    grouped: dict[str, list[AddressCandidate]] = defaultdict(list)
    for candidate in db.scalars(stmt):
        grouped[build_candidate_group_key(candidate)].append(candidate)

    groups: list[CandidateGroup] = []
    for key, candidates in grouped.items():
        group = _candidate_group(key, candidates)
        if approval_readiness and group.approval_readiness != approval_readiness:
            continue
        groups.append(group)
    return groups


def approve_candidate_groups(
    db: Session,
    source_job_id: int | None = None,
    approval_readiness: str | None = None,
    allow_review_readiness: str | None = None,
    dry_run: bool = True,
    actor: str = "system",
) -> dict:
    groups = get_unique_candidate_groups(db, source_job_id=source_job_id, approval_readiness=approval_readiness)
    result = {
        "dry_run": dry_run,
        "source_job_id": source_job_id,
        "approval_readiness": approval_readiness,
        "override_readiness_allowed": allow_review_readiness,
        "groups_scanned": len(groups),
        "groups_approved": 0,
        "groups_skipped": 0,
        "override_groups_approved": 0,
        "override_groups_skipped": 0,
        "addresses_created": 0,
        "roles_created": 0,
        "evidence_linked": 0,
        "events_written": 0,
        "skipped_reasons": {},
    }
    skipped_reasons: Counter[str] = Counter()
    allowed = ({approval_readiness} & DEFAULT_APPROVABLE_READINESS) if approval_readiness else DEFAULT_APPROVABLE_READINESS

    for group in groups:
        override_reason = _override_skip_reason(group, allow_review_readiness)
        is_override = allow_review_readiness is not None and override_reason is None
        skip_reason = None if is_override else _skip_reason(group, allowed)
        if allow_review_readiness is not None and group.approval_readiness == allow_review_readiness and not is_override:
            result["override_groups_skipped"] += 1
            skip_reason = override_reason or skip_reason
        if skip_reason:
            result["groups_skipped"] += 1
            skipped_reasons[skip_reason] += 1
            if not dry_run and not _event_exists(db, group.group_key, "skipped", skip_reason):
                _record_event(db, None, group, "skipped", actor, skip_reason, dry_run, {})
                result["events_written"] += 1
            continue

        if dry_run:
            result["groups_approved"] += 1
            if is_override:
                result["override_groups_approved"] += 1
            continue

        entity, entity_created = _get_or_create_entity(db, group.entity_name or "")
        approved_address, address_created = _get_or_create_approved_address(db, entity, group)
        role, role_created = _get_or_create_role(db, approved_address, group)
        linked = _link_evidence(db, approved_address, group)

        if address_created:
            result["addresses_created"] += 1
        if role_created:
            result["roles_created"] += 1
        result["evidence_linked"] += linked

        if address_created or role_created or linked:
            result["groups_approved"] += 1
            if is_override:
                result["override_groups_approved"] += 1
            _record_event(
                db,
                approved_address.id,
                group,
                "approved",
                actor,
                _override_reason(allow_review_readiness) if is_override else "approved_candidate_group",
                dry_run,
                {"entity_created": entity_created, "address_created": address_created, "role_created": role_created, "evidence_linked": linked},
            )
            result["events_written"] += 1
        else:
            result["groups_skipped"] += 1
            if is_override:
                result["override_groups_skipped"] += 1
            skipped_reasons["already_approved"] += 1

    result["skipped_reasons"] = dict(sorted(skipped_reasons.items()))
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return result


def _candidate_group(key: str, candidates: list[AddressCandidate]) -> CandidateGroup:
    representative = candidates[0]
    address_class = classify_candidate_address_class(representative, _first_evidence_payload(representative))
    source_trust_status = _best_source_trust(candidates)
    readiness = _best_approval_readiness(candidates, source_trust_status, address_class)
    return CandidateGroup(
        group_key=key,
        entity_name=representative.entity_name,
        chain_slug=_candidate_chain_slug(representative),
        normalized_address=representative.normalized_address,
        suggested_role=representative.suggested_role,
        address_class=address_class,
        source_trust_status=source_trust_status,
        approval_readiness=readiness,
        candidates=candidates,
    )


SOURCE_TRUST_RANK = {
    "unknown": 0,
    "inferred": 1,
    "weak_reference": 2,
    "official_but_unmapped_role": 3,
    "official_published_list": 4,
    "official_staking_mapping": 5,
    "official_audit_confirmed": 6,
    "official_confirmed": 7,
}
APPROVAL_READINESS_RANK = {
    "invalid_missing_entity": 0,
    "invalid_missing_network": 0,
    "invalid_missing_role": 0,
    "invalid_missing_address": 0,
    "invalid_missing_evidence": 0,
    "blocked_conflict": 0,
    "blocked_missing_network": 0,
    "extract_only_low_confidence": 0,
    "not_auto_approvable_unknown": 1,
    "not_auto_approvable_explorer_link_only": 1,
    "needs_review_unverified_source": 1,
    "needs_review_generic_wallet": 2,
    "needs_review_third_party_audit": 2,
    "needs_review_third_party_official_reference": 2,
    "needs_review_manual_verified": 2,
    "needs_review_unmapped_official_role": 3,
    "needs_review_official_likely": 3,
    "needs_review_hot_cold_wallet": 4,
    "needs_review_staking_mapping": 5,
    "needs_review_official_low_confidence": 6,
    "ready_for_approval_core_protocol": 7,
    "ready_for_approval_cex_reserve": 7,
    "auto_ready_official_verified": 8,
}


def _best_source_trust(candidates: list[AddressCandidate]) -> str:
    best = "unknown"
    for candidate in candidates:
        trust = classify_source_trust_status(candidate, _first_evidence_payload(candidate))
        if SOURCE_TRUST_RANK.get(trust, 0) > SOURCE_TRUST_RANK.get(best, 0):
            best = trust
    return best


def _best_approval_readiness(candidates: list[AddressCandidate], source_trust_status: str, address_class: str) -> str:
    best = "invalid_missing_evidence"
    for candidate in candidates:
        readiness = classify_approval_readiness(candidate, source_trust_status, address_class, candidate.confidence_initial, len(candidate.evidence))
        if APPROVAL_READINESS_RANK.get(readiness, 0) > APPROVAL_READINESS_RANK.get(best, 0):
            best = readiness
    return best


def _skip_reason(group: CandidateGroup, allowed: set[str | None]) -> str | None:
    if group.approval_readiness not in allowed:
        return f"readiness_{group.approval_readiness}"
    if not group.entity_name:
        return "missing_entity"
    if not group.chain_slug:
        return "missing_chain_slug"
    if not group.normalized_address:
        return "missing_address"
    if not group.suggested_role:
        return "missing_role"
    return None


def _override_skip_reason(group: CandidateGroup, allow_review_readiness: str | None) -> str | None:
    if allow_review_readiness is None:
        return "override_not_requested"
    policy = SUPPORTED_OVERRIDE_POLICIES.get(allow_review_readiness)
    if policy is None:
        return f"override_readiness_not_supported_{allow_review_readiness}"
    if group.approval_readiness != allow_review_readiness:
        return f"readiness_{group.approval_readiness}"
    if group.address_class not in policy["address_classes"]:
        return f"override_address_class_{group.address_class}"
    if group.source_trust_status not in policy["source_trust"]:
        return f"override_source_trust_{group.source_trust_status}"
    if not group.entity_name:
        return "missing_entity"
    if not group.chain_slug or group.chain_slug == "-":
        return "missing_chain_slug"
    if not group.normalized_address:
        return "missing_address"
    if not group.suggested_role:
        return "missing_role"
    if sum(len(candidate.evidence) for candidate in group.candidates) < 1:
        return "missing_evidence"
    return None


def _override_reason(allow_review_readiness: str | None) -> str:
    if allow_review_readiness is None:
        return "approved_candidate_group"
    policy = SUPPORTED_OVERRIDE_POLICIES.get(allow_review_readiness)
    if policy is None:
        return "manual_policy_override"
    return str(policy["reason"])


def _get_or_create_entity(db: Session, entity_name: str) -> tuple[Entity, bool]:
    entity = db.scalar(select(Entity).where(Entity.entity_name == entity_name))
    if entity:
        return entity, False
    entity = Entity(entity_name=entity_name)
    db.add(entity)
    db.flush()
    return entity, True


def _get_or_create_approved_address(db: Session, entity: Entity, group: CandidateGroup) -> tuple[ApprovedAddress, bool]:
    approved = db.scalar(
        select(ApprovedAddress).where(
            ApprovedAddress.entity_id == entity.id,
            ApprovedAddress.chain_slug == group.chain_slug,
            ApprovedAddress.normalized_address == group.normalized_address,
        )
    )
    if approved:
        return approved, False
    representative = group.candidates[0]
    approved = ApprovedAddress(
        entity_id=entity.id,
        address=representative.address,
        normalized_address=representative.normalized_address,
        source_network=representative.source_network,
        chain_slug=group.chain_slug or "",
        address_class=group.address_class,
        source_trust_status=group.source_trust_status,
        approval_readiness_at_approval=group.approval_readiness,
        confidence_score=max(candidate.confidence_initial for candidate in group.candidates),
        status="approved",
        metadata_json={"candidate_group_key": group.group_key, "candidate_count": len(group.candidates)},
    )
    db.add(approved)
    db.flush()
    return approved, True


def _get_or_create_role(db: Session, approved_address: ApprovedAddress, group: CandidateGroup) -> tuple[ApprovedAddressRole, bool]:
    role = group.suggested_role or ""
    existing = db.scalar(
        select(ApprovedAddressRole).where(
            ApprovedAddressRole.approved_address_id == approved_address.id,
            ApprovedAddressRole.role == role,
        )
    )
    if existing:
        return existing, False
    created = ApprovedAddressRole(
        approved_address_id=approved_address.id,
        role=role,
        role_confidence=max(candidate.confidence_initial for candidate in group.candidates),
        status="approved",
    )
    db.add(created)
    db.flush()
    return created, True


def _link_evidence(db: Session, approved_address: ApprovedAddress, group: CandidateGroup) -> int:
    linked = 0
    for candidate in group.candidates:
        for evidence in candidate.evidence:
            existing = db.scalar(
                select(ApprovedAddressEvidence).where(
                    ApprovedAddressEvidence.approved_address_id == approved_address.id,
                    ApprovedAddressEvidence.candidate_id == candidate.id,
                    ApprovedAddressEvidence.source_document_id == evidence.source_document_id,
                    ApprovedAddressEvidence.evidence_type == evidence.evidence_type,
                )
            )
            if existing:
                continue
            db.add(
                ApprovedAddressEvidence(
                    approved_address_id=approved_address.id,
                    candidate_id=candidate.id,
                    source_document_id=evidence.source_document_id,
                    evidence_type=evidence.evidence_type,
                    source_type=evidence.source_type,
                    source_input_type=candidate.source_input_type,
                    source_job_id=candidate.source_job_id,
                    source_url=evidence.source_url,
                    file_path=evidence.file_path,
                    raw_reference=candidate.raw_reference,
                    confidence_contribution=candidate.confidence_initial,
                    payload_json=evidence.payload,
                )
            )
            linked += 1
    db.flush()
    return linked


def _record_event(
    db: Session,
    approved_address_id: int | None,
    group: CandidateGroup,
    action: str,
    actor: str,
    reason: str,
    dry_run: bool,
    payload: dict[str, Any],
) -> None:
    db.add(
        ApprovalEvent(
            approved_address_id=approved_address_id,
            candidate_group_key=group.group_key,
            action=action,
            actor=actor,
            reason=reason,
            dry_run=dry_run,
            payload_json={
                "approval_readiness": group.approval_readiness,
                "source_trust_status": group.source_trust_status,
                "address_class": group.address_class,
                "candidate_count": len(group.candidates),
                **payload,
            },
        )
    )
    db.flush()


def _event_exists(db: Session, candidate_group_key: str, action: str, reason: str) -> bool:
    return (
        db.scalar(
            select(ApprovalEvent.id).where(
                ApprovalEvent.candidate_group_key == candidate_group_key,
                ApprovalEvent.action == action,
                ApprovalEvent.reason == reason,
            )
        )
        is not None
    )


def _candidate_chain_slug(candidate: AddressCandidate) -> str | None:
    return candidate.chain_slug or NetworkNormalizer.normalize(candidate.source_network).canonical_chain


def _first_evidence_payload(candidate: AddressCandidate) -> dict | None:
    for evidence in candidate.evidence:
        return evidence.payload
    return None
