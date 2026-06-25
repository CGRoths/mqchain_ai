from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.intake import AddressCandidate, SourceVerification, utcnow


VERIFIED_STATUSES = {"verified", "approved", "active"}
REJECTED_STATUSES = {"rejected"}
TRUST_LEVELS = {
    "official_verified",
    "official_likely",
    "third_party_officially_referenced",
    "third_party_audit",
    "third_party_exchange_reported",
    "third_party_unverified",
    "manual_verified",
    "manual_unverified",
    "unknown",
    "rejected",
}
CORE_PROTOCOL_ROLES = {
    "address_provider",
    "factory_contract",
    "governance_contract",
    "lending_market",
    "lending_pool",
    "oracle",
    "protocol_configurator",
    "protocol_contract",
    "rewards_contract",
    "router_contract",
    "treasury",
}
ROLE_TRUST_POLICY = {
    "cex_reserve_wallet": {
        "official_verified",
        "third_party_officially_referenced",
        "third_party_exchange_reported",
        "third_party_audit",
        "manual_verified",
    },
    "core_protocol_contract": {"official_verified", "official_likely", "manual_verified"},
}


@dataclass(frozen=True)
class VerificationGate:
    allowed: bool
    reason: str | None
    verification: SourceVerification | None
    address_class: str


def record_source_verification(
    db: Session,
    *,
    verification_scope: str,
    source_trust: str,
    verification_status: str = "verified",
    verified_by: str | None,
    verified_at: datetime | None = None,
    source_job_id: int | None = None,
    source_document_id: int | None = None,
    candidate_id: int | None = None,
    candidate_group_key: str | None = None,
    entity_name: str | None = None,
    entity_id: int | None = None,
    protocol_name: str | None = None,
    source_url: str | None = None,
    source_origin: str | None = None,
    official_referrer_url: str | None = None,
    file_path: str | None = None,
    input_method: str | None = None,
    evidence_shape: str | None = None,
    verification_reason: str | None = None,
    verification_evidence_json: dict[str, Any] | None = None,
) -> SourceVerification:
    if source_trust not in TRUST_LEVELS:
        raise ValueError(f"unsupported_source_trust:{source_trust}")
    if verification_status in VERIFIED_STATUSES and not verified_by:
        raise ValueError("verified_by_required")
    verification = SourceVerification(
        source_job_id=source_job_id,
        source_document_id=source_document_id,
        candidate_id=candidate_id,
        candidate_group_key=candidate_group_key,
        entity_name=entity_name,
        entity_id=entity_id,
        protocol_name=protocol_name,
        source_url=source_url,
        source_origin=source_origin,
        official_referrer_url=official_referrer_url,
        file_path=file_path,
        input_method=input_method,
        evidence_shape=evidence_shape,
        verification_scope=verification_scope,
        verification_status=verification_status,
        source_trust=source_trust,
        verified_by=verified_by,
        verified_at=verified_at or (utcnow() if verified_by else None),
        verification_reason=verification_reason,
        verification_evidence_json=verification_evidence_json or {},
    )
    db.add(verification)
    db.flush()
    return verification


def find_source_verification_for_candidate(db: Session, candidate: AddressCandidate) -> SourceVerification | None:
    group_key = build_candidate_group_key(candidate)
    queries = []
    if candidate.id is not None:
        queries.append(select(SourceVerification).where(SourceVerification.candidate_id == candidate.id))
    if group_key:
        queries.append(select(SourceVerification).where(SourceVerification.candidate_group_key == group_key))
    if candidate.source_document_id is not None:
        queries.append(select(SourceVerification).where(SourceVerification.source_document_id == candidate.source_document_id))
    if candidate.source_job_id is not None:
        queries.append(select(SourceVerification).where(SourceVerification.source_job_id == candidate.source_job_id))
    for stmt in queries:
        verification = db.scalars(stmt.order_by(SourceVerification.updated_at.desc(), SourceVerification.id.desc())).first()
        if verification is not None:
            return verification
    return None


def verification_gate_for_candidate(db: Session, candidate: AddressCandidate) -> VerificationGate:
    address_class = address_class_for_candidate(candidate)
    verification = find_source_verification_for_candidate(db, candidate)
    if verification is None:
        return VerificationGate(False, "missing_source_verification", None, address_class)
    if verification.verification_status in REJECTED_STATUSES or verification.source_trust == "rejected":
        return VerificationGate(False, "source_verification_rejected", verification, address_class)
    if verification.verification_status not in VERIFIED_STATUSES:
        return VerificationGate(False, "source_verification_not_verified", verification, address_class)
    if not verification.verified_by or not verification.verified_at:
        return VerificationGate(False, "source_verification_incomplete", verification, address_class)
    allowed = ROLE_TRUST_POLICY.get(address_class)
    if not allowed:
        return VerificationGate(False, f"source_verification_not_auto_approvable_{address_class}", verification, address_class)
    if verification.source_trust not in allowed:
        return VerificationGate(False, f"source_trust_not_allowed_{verification.source_trust}", verification, address_class)
    return VerificationGate(True, None, verification, address_class)


def source_verification_payload(verification: SourceVerification | None) -> dict[str, Any] | None:
    if verification is None:
        return None
    return {
        "id": verification.id,
        "source_job_id": verification.source_job_id,
        "source_document_id": verification.source_document_id,
        "candidate_id": verification.candidate_id,
        "candidate_group_key": verification.candidate_group_key,
        "verification_scope": verification.verification_scope,
        "verification_status": verification.verification_status,
        "source_trust": verification.source_trust,
        "verified_by": verification.verified_by,
        "verified_at": verification.verified_at.isoformat() if verification.verified_at else None,
        "verification_reason": verification.verification_reason,
        "source_origin": verification.source_origin,
        "official_referrer_url": verification.official_referrer_url,
        "evidence_shape": verification.evidence_shape,
    }


def address_class_for_candidate(candidate: AddressCandidate) -> str:
    role = (candidate.suggested_role or "").strip().lower()
    source_input_type = (candidate.source_input_type or "").strip().lower()
    if "loose" in source_input_type:
        return "loose_address_context"
    if role == "cex_por_wallet":
        return "cex_reserve_wallet"
    if role == "cex_hot_wallet":
        return "cex_hot_wallet"
    if role == "cex_cold_wallet":
        return "cex_cold_wallet"
    if role in CORE_PROTOCOL_ROLES:
        return "core_protocol_contract"
    if "wallet" in role:
        return "generic_wallet"
    return "unknown_candidate"


def build_candidate_group_key(candidate: AddressCandidate) -> str:
    return json.dumps(
        {
            "entity_name": candidate.entity_name,
            "chain_slug": candidate.chain_slug,
            "normalized_address": candidate.normalized_address,
            "suggested_role": candidate.suggested_role,
            "address_class": address_class_for_candidate(candidate),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
