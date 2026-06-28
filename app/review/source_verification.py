from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

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
OFFICIAL_REGISTRY_ENTRY_SOURCE_INPUT_TYPES = {
    "github_solidity_address_book",
    "github_typescript_address_map",
    "github_json_deployment_registry",
    "github_yaml_deployment_registry",
    "github_markdown_deployment_table",
    "official_github_deployment_table",
    "docs_html_deployment_table",
    "docs_markdown_deployment_table",
    "json_deployment_registry",
    "yaml_deployment_registry",
    "structured_deployment_registry",
    "standardized_registry_upload",
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
    "official_registry_entry": {"official_verified", "official_likely", "manual_verified"},
}


@dataclass(frozen=True)
class VerificationGate:
    allowed: bool
    reason: str | None
    verification: SourceVerification | None
    address_class: str


TRUST_HINT_ALIASES = {
    "official": "official_likely",
    "official_likely": "official_likely",
    "official_checked": "official_likely",
    "official_verified": "official_verified",
    "audit": "third_party_audit",
    "third_party_audit": "third_party_audit",
    "exchange_reported": "third_party_exchange_reported",
    "third_party_exchange_reported": "third_party_exchange_reported",
    "manual_verified": "manual_verified",
    "manual_unverified": "manual_unverified",
}


def verify_source_sheets_from_candidates(
    db: Session,
    source_job_id: int,
    verified_by: str,
    dry_run: bool = True,
    update_existing: bool = False,
) -> dict[str, Any]:
    if not verified_by or not str(verified_by).strip():
        raise ValueError("verified_by_required")

    candidates = list(
        db.scalars(
            select(AddressCandidate)
            .options(selectinload(AddressCandidate.evidence))
            .where(AddressCandidate.source_job_id == source_job_id)
            .order_by(AddressCandidate.id.asc())
        )
    )

    by_sheet: dict[str, list[AddressCandidate]] = {}
    skipped_reasons: dict[str, int] = {}
    for candidate in candidates:
        sheet = (candidate.source_sheet or "").strip()
        if not sheet:
            _increment_skip(skipped_reasons, "missing_source_sheet")
            continue
        by_sheet.setdefault(sheet, []).append(candidate)

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "source_job_id": source_job_id,
        "sheets_scanned": len(by_sheet),
        "verifications_created": 0,
        "verifications_updated": 0,
        "sheets_skipped": 0,
        "created": [],
        "updated": [],
        "skipped_reasons": {},
        "sheet_summary": [],
    }

    now = utcnow()
    for source_sheet, sheet_candidates in sorted(by_sheet.items()):
        representative = sheet_candidates[0]
        metadata = _sheet_metadata(representative, source_sheet)
        trust_hint = _first_value(
            metadata.get("source_trust_hint"),
            metadata.get("sheet_source_trust"),
            metadata.get("source_trust"),
        )
        source_trust = normalize_sheet_source_trust(trust_hint)
        summary = _sheet_summary(source_job_id, source_sheet, sheet_candidates, metadata, trust_hint, source_trust)

        if source_trust is None:
            summary["status"] = "skipped"
            summary["skip_reason"] = "missing_source_trust_hint"
            result["sheets_skipped"] += 1
            _increment_skip(skipped_reasons, "missing_source_trust_hint")
            result["sheet_summary"].append(summary)
            continue

        existing = _existing_sheet_verification(db, source_job_id, source_sheet)
        if existing is not None and not update_existing:
            summary["status"] = "skipped"
            summary["skip_reason"] = "existing_source_sheet_verification"
            summary["verification_id"] = existing.id
            result["sheets_skipped"] += 1
            _increment_skip(skipped_reasons, "existing_source_sheet_verification")
            result["sheet_summary"].append(summary)
            continue

        payload = _sheet_verification_payload(source_sheet, sheet_candidates, metadata, trust_hint)
        if existing is not None:
            summary["status"] = "would_update" if dry_run else "updated"
            summary["verification_id"] = existing.id
            result["verifications_updated"] += 1
            result["updated"].append(summary.copy())
            result["sheet_summary"].append(summary)
            if not dry_run:
                existing.source_trust = source_trust
                existing.verified_by = str(verified_by).strip()
                existing.verified_at = now
                existing.source_url = summary["source_url"]
                existing.official_referrer_url = summary["official_referrer_url"]
                existing.source_origin = summary["source_origin"]
                existing.evidence_shape = summary["evidence_shape"]
                existing.verification_evidence_json = payload
            continue

        summary["status"] = "would_create" if dry_run else "created"
        result["verifications_created"] += 1
        result["created"].append(summary.copy())
        result["sheet_summary"].append(summary)
        if not dry_run:
            verification = record_source_verification(
                db,
                verification_scope="source_sheet",
                verification_status="verified",
                source_trust=source_trust,
                verified_by=str(verified_by).strip(),
                verified_at=now,
                source_job_id=source_job_id,
                source_document_id=representative.source_document_id,
                source_sheet=source_sheet,
                entity_name=summary["entity_name"],
                protocol_name=summary["protocol_name"],
                source_url=summary["source_url"],
                source_origin=summary["source_origin"],
                official_referrer_url=summary["official_referrer_url"],
                file_path=representative.file_path,
                input_method=representative.source_type,
                evidence_shape=summary["evidence_shape"],
                verification_reason="bulk_sheet_verification_from_manifest_or_sheet_metadata",
                verification_evidence_json=payload,
            )
            summary["verification_id"] = verification.id
            result["created"][-1]["verification_id"] = verification.id

    result["skipped_reasons"] = dict(sorted(skipped_reasons.items()))
    if not dry_run:
        db.commit()
    return result


def normalize_sheet_source_trust(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, dict):
        value = _first_value(
            value.get("source_trust_hint"),
            value.get("sheet_source_trust"),
            value.get("source_trust"),
            value.get("trust_level"),
            value.get("level"),
        )
        if value is None:
            return None
    key = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")
    return TRUST_HINT_ALIASES.get(key)


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
    source_sheet: str | None = None,
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
        source_sheet=source_sheet,
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
    requires_sheet_verification = _requires_sheet_verification(candidate)
    if candidate.source_document_id is not None and candidate.source_sheet:
        queries.append(
            select(SourceVerification).where(
                SourceVerification.source_document_id == candidate.source_document_id,
                SourceVerification.source_sheet == candidate.source_sheet,
            )
        )
    if candidate.source_job_id is not None and candidate.source_sheet:
        queries.append(
            select(SourceVerification).where(
                SourceVerification.source_job_id == candidate.source_job_id,
                SourceVerification.source_sheet == candidate.source_sheet,
            )
        )
    if not requires_sheet_verification and candidate.source_document_id is not None:
        queries.append(select(SourceVerification).where(SourceVerification.source_document_id == candidate.source_document_id))
    if not requires_sheet_verification and candidate.source_job_id is not None:
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
        "source_sheet": verification.source_sheet,
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
    if _is_official_registry_entry_source(candidate):
        return "official_registry_entry"
    if "wallet" in role:
        return "generic_wallet"
    return "unknown_candidate"


def _is_official_registry_entry_source(candidate: AddressCandidate) -> bool:
    source_input_type = (candidate.source_input_type or "").strip().lower()
    return source_input_type in OFFICIAL_REGISTRY_ENTRY_SOURCE_INPUT_TYPES


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


def _requires_sheet_verification(candidate: AddressCandidate) -> bool:
    if not candidate.source_sheet:
        return False
    raw_reference = candidate.raw_reference if isinstance(candidate.raw_reference, dict) else {}
    source_evidence = raw_reference.get("source_evidence") if isinstance(raw_reference.get("source_evidence"), dict) else {}
    sheet_profiles = source_evidence.get("sheet_profiles") if isinstance(source_evidence.get("sheet_profiles"), dict) else {}
    if sheet_profiles:
        return True
    return any(
        raw_reference.get(key) not in {None, ""}
        for key in (
            "sheet_entity_hint",
            "sheet_source_url",
            "sheet_source_origin",
            "sheet_provenance_type",
            "sheet_evidence_shape",
            "sheet_snapshot_date",
        )
    )


def _sheet_metadata(candidate: AddressCandidate, source_sheet: str) -> dict[str, Any]:
    raw_reference = candidate.raw_reference if isinstance(candidate.raw_reference, dict) else {}
    profile = _sheet_profile(raw_reference, source_sheet)
    return {
        "entity_name": _first_value(profile.get("entity_name"), profile.get("entity_hint"), raw_reference.get("sheet_entity_hint"), candidate.entity_name),
        "protocol_name": _first_value(profile.get("protocol_name"), raw_reference.get("sheet_protocol_name"), raw_reference.get("protocol_name")),
        "source_url": _first_value(profile.get("source_url"), raw_reference.get("sheet_source_url"), candidate.source_url),
        "official_referrer_url": _first_value(profile.get("official_referrer_url"), raw_reference.get("sheet_official_referrer_url")),
        "source_origin": _first_value(profile.get("source_origin"), raw_reference.get("sheet_source_origin")),
        "provenance_type": _first_value(profile.get("provenance_type"), raw_reference.get("sheet_provenance_type")),
        "evidence_shape": _first_value(profile.get("evidence_shape"), raw_reference.get("sheet_evidence_shape"), candidate.evidence_type),
        "snapshot_date": _first_value(profile.get("snapshot_date"), raw_reference.get("sheet_snapshot_date")),
        "operator_note": _first_value(profile.get("operator_note"), raw_reference.get("sheet_operator_note")),
        "source_trust_hint": _first_value(
            profile.get("source_trust_hint"),
            profile.get("source_trust"),
            profile.get("sheet_source_trust"),
            raw_reference.get("source_trust_hint"),
            raw_reference.get("sheet_source_trust"),
            raw_reference.get("source_trust"),
        ),
        "sheet_source_trust": _first_value(profile.get("sheet_source_trust"), raw_reference.get("sheet_source_trust")),
        "source_trust": _first_value(profile.get("source_trust"), raw_reference.get("source_trust")),
    }


def _sheet_profile(raw_reference: dict[str, Any], source_sheet: str) -> dict[str, Any]:
    source_evidence = raw_reference.get("source_evidence") if isinstance(raw_reference.get("source_evidence"), dict) else {}
    sheet_profiles = source_evidence.get("sheet_profiles") if isinstance(source_evidence.get("sheet_profiles"), dict) else {}
    direct = sheet_profiles.get(source_sheet)
    if isinstance(direct, dict):
        return direct
    normalized_sheet = _normalize_sheet_name(source_sheet)
    for key, profile in sheet_profiles.items():
        if _normalize_sheet_name(str(key)) == normalized_sheet and isinstance(profile, dict):
            return profile
    return {}


def _sheet_summary(
    source_job_id: int,
    source_sheet: str,
    candidates: list[AddressCandidate],
    metadata: dict[str, Any],
    trust_hint: Any,
    source_trust: str | None,
) -> dict[str, Any]:
    representative = candidates[0]
    return {
        "source_job_id": source_job_id,
        "source_document_id": representative.source_document_id,
        "source_sheet": source_sheet,
        "row_count": len(candidates),
        "entity_name": metadata.get("entity_name"),
        "protocol_name": metadata.get("protocol_name"),
        "source_url": metadata.get("source_url"),
        "official_referrer_url": metadata.get("official_referrer_url"),
        "source_origin": metadata.get("source_origin"),
        "provenance_type": metadata.get("provenance_type"),
        "evidence_shape": metadata.get("evidence_shape"),
        "snapshot_date": metadata.get("snapshot_date"),
        "source_trust_hint": trust_hint,
        "source_trust": source_trust,
        "evidence_types": _evidence_types(candidates),
    }


def _sheet_verification_payload(
    source_sheet: str,
    candidates: list[AddressCandidate],
    metadata: dict[str, Any],
    trust_hint: Any,
) -> dict[str, Any]:
    return {
        "source_sheet": source_sheet,
        "row_count": len(candidates),
        "evidence_types": _evidence_types(candidates),
        "source_url": metadata.get("source_url"),
        "official_referrer_url": metadata.get("official_referrer_url"),
        "source_origin": metadata.get("source_origin"),
        "provenance_type": metadata.get("provenance_type"),
        "evidence_shape": metadata.get("evidence_shape"),
        "snapshot_date": metadata.get("snapshot_date"),
        "operator_note": metadata.get("operator_note"),
        "source_trust_hint": trust_hint,
        "candidate_ids": [candidate.id for candidate in candidates],
    }


def _existing_sheet_verification(db: Session, source_job_id: int, source_sheet: str) -> SourceVerification | None:
    return db.scalars(
        select(SourceVerification)
        .where(
            SourceVerification.source_job_id == source_job_id,
            SourceVerification.source_sheet == source_sheet,
        )
        .order_by(SourceVerification.updated_at.desc(), SourceVerification.id.desc())
    ).first()


def _evidence_types(candidates: list[AddressCandidate]) -> list[str]:
    values: set[str] = set()
    for candidate in candidates:
        if candidate.evidence_type:
            values.add(candidate.evidence_type)
        for evidence in candidate.evidence:
            if evidence.evidence_type:
                values.add(evidence.evidence_type)
    return sorted(values)


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _increment_skip(skipped_reasons: dict[str, int], reason: str) -> None:
    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1


def _normalize_sheet_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value).strip().lower()).strip()
