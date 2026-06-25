from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.intake import AddressCandidate, utcnow
from app.review.source_verification import source_verification_payload, verification_gate_for_candidate


APPROVABLE_STATUSES = {"needs_review"}
APPROVED_STATUS = "approved"
# Kept for import compatibility; evidence type is no longer approval authority.
OFFICIAL_EVIDENCE_TYPES: set[str] = set()
BLOCKED_EVIDENCE_TYPES = {"official_github_relation"}
BLOCKED_SOURCE_INPUT_TYPES = {"github_typescript_relation_map"}
BLOCKED_ROLES = {"external_dependency", "unknown"}
BLOCKED_MARKERS = {
    "loose_fallback",
    "loose_address_extractor",
    "relation_file",
    "github_typescript_relation_map",
    "external_or_related",
    "storage_slot",
}
STORAGE_SLOT_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


def auto_approve_official_candidates(
    db: Session,
    source_job_id: int | None = None,
    *,
    dry_run: bool = False,
    approved_by: str | None = None,
) -> dict:
    stmt = select(AddressCandidate).options(selectinload(AddressCandidate.evidence)).order_by(AddressCandidate.id.asc())
    if source_job_id is not None:
        stmt = stmt.where(AddressCandidate.source_job_id == source_job_id)
    candidates = list(db.scalars(stmt))

    matched: list[AddressCandidate] = []
    skipped_reasons: Counter[str] = Counter()
    for candidate in candidates:
        reason = _skip_reason(db, candidate)
        if reason:
            skipped_reasons[reason] += 1
            continue
        matched.append(candidate)

    if not dry_run:
        now = utcnow()
        for candidate in matched:
            gate = verification_gate_for_candidate(db, candidate)
            candidate.status = APPROVED_STATUS
            candidate.approved_at = now
            candidate.approved_by = approved_by or "system"
            candidate.approval_method = "source_verification_auto_approval"
            payload = source_verification_payload(gate.verification)
            candidate.approval_notes = f"Auto-approved with source verification {payload.get('id') if payload else '(missing)'}"
        if matched:
            db.commit()

    return {
        "dry_run": dry_run,
        "matched": len(matched),
        "approved": 0 if dry_run else len(matched),
        "skipped": sum(skipped_reasons.values()),
        "skipped_reasons": dict(sorted(skipped_reasons.items())),
        "source_job_id": source_job_id,
    }


def _skip_reason(db: Session, candidate: AddressCandidate) -> str | None:
    if candidate.status not in APPROVABLE_STATUSES:
        return "status_not_needs_review"
    if not candidate.entity_name:
        return "missing_entity"
    if not candidate.source_network:
        return "missing_network"
    if not candidate.address or not candidate.normalized_address:
        return "missing_address"
    if not candidate.suggested_role:
        return "missing_role"
    if candidate.suggested_role in BLOCKED_ROLES:
        return "blocked_role"
    if candidate.confidence_initial < 90:
        return "confidence_below_90"
    scoring_reason = _scoring_skip_reason(candidate)
    if scoring_reason:
        return scoring_reason
    if not candidate.evidence:
        return "missing_evidence"
    if _is_storage_slot(candidate):
        return "storage_slot_like_address"
    if _has_blocked_metadata(candidate):
        return "blocked_source_metadata"
    if candidate.suggested_role == "token_contract" and _relations_only(candidate):
        return "relation_token_contract"
    gate = verification_gate_for_candidate(db, candidate)
    if not gate.allowed:
        return gate.reason or "source_verification_not_allowed"
    return None


def _scoring_skip_reason(candidate: AddressCandidate) -> str | None:
    raw = candidate.raw_reference or {}
    readiness = raw.get("approval_readiness")
    if readiness and readiness not in {"auto_ready_official_verified", "approved"}:
        return f"scoring_{readiness}"
    discovery = raw.get("discovery_permission")
    if isinstance(discovery, dict):
        readiness = discovery.get("approval_readiness")
        if readiness and readiness not in {"auto_ready_official_verified", "approved"}:
            return f"scoring_{readiness}"
        if int(discovery.get("discovery_depth") or 0) <= 0:
            return "scoring_discovery_depth_0"
    source_trust = raw.get("scored_source_trust") or raw.get("source_trust_level")
    if source_trust in {"third_party_unverified", "manual_unverified", "unknown"}:
        return f"scoring_untrusted_source_{source_trust}"
    return None


def _has_blocked_metadata(candidate: AddressCandidate) -> bool:
    values = _candidate_metadata_values(candidate)
    if candidate.source_input_type in BLOCKED_SOURCE_INPUT_TYPES:
        return True
    for value in values:
        normalized = str(value).strip().lower()
        if normalized in BLOCKED_MARKERS:
            return True
        if any(marker in normalized for marker in BLOCKED_MARKERS):
            return True
    return False


def _relations_only(candidate: AddressCandidate) -> bool:
    paths = [candidate.file_path or "", *_evidence_values(candidate, "file_path")]
    return bool(paths) and all("relations.ts" in path.replace("\\", "/").lower() for path in paths if path)


def _is_storage_slot(candidate: AddressCandidate) -> bool:
    if STORAGE_SLOT_RE.fullmatch(candidate.address or "") or STORAGE_SLOT_RE.fullmatch(candidate.normalized_address or ""):
        return True
    raw = candidate.raw_reference or {}
    for key in ("raw_key", "column_name", "contract_name", "role_source", "original_role_text"):
        value = raw.get(key)
        if value and "storage" in str(value).lower() and "slot" in str(value).lower():
            return True
    return False


def _candidate_metadata_values(candidate: AddressCandidate) -> list[Any]:
    values: list[Any] = [
        candidate.source_input_type,
        candidate.evidence_type,
        candidate.file_path,
        candidate.suggested_role,
        candidate.raw_reference,
        candidate.warnings,
    ]
    for evidence in candidate.evidence:
        values.extend([evidence.evidence_type, evidence.file_path, evidence.payload])
    return _flatten(values)


def _evidence_values(candidate: AddressCandidate, key: str) -> list[str]:
    values: list[str] = []
    for evidence in candidate.evidence:
        value = getattr(evidence, key, None)
        if value:
            values.append(str(value))
    return values


def _flatten(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[Any] = []
        for key, item in value.items():
            result.append(key)
            result.extend(_flatten(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_flatten(item))
        return result
    return [value]
