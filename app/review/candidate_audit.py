from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.ingestion.network_normalizer import NetworkNormalizer
from app.models.intake import AddressCandidate, AddressEvidence
from app.review.source_verification import VERIFIED_STATUSES, find_source_verification_for_candidate, verification_gate_for_candidate


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


def audit_candidates(db: Session, source_job_id: int | None = None, limit_samples: int = 20) -> dict:
    stmt = select(AddressCandidate).options(selectinload(AddressCandidate.evidence)).order_by(AddressCandidate.id.asc())
    if source_job_id is not None:
        stmt = stmt.where(AddressCandidate.source_job_id == source_job_id)
    candidates = list(db.scalars(stmt))

    total_evidence = _evidence_count(db, source_job_id)
    source_job_ids = sorted({candidate.source_job_id for candidate in candidates})
    duplicate_samples: list[dict] = []
    sample_candidates: list[dict] = []
    unique_keys: Counter[tuple[str | None, str | None, str | None, str | None, str]] = Counter()
    unique_address_classes: dict[tuple[str | None, str | None, str | None, str | None, str], str] = {}
    unique_source_trust: dict[tuple[str | None, str | None, str | None, str | None, str], str] = {}
    unique_approval_readiness: dict[tuple[str | None, str | None, str | None, str | None, str], str] = {}
    counts = _empty_counters()
    missing_evidence_count = 0
    auto_approvable_count = 0
    needs_review_count = 0
    blocked_count = 0
    warnings: list[str] = []

    for candidate in candidates:
        evidence_count = len(candidate.evidence)
        if evidence_count == 0:
            missing_evidence_count += 1
        address_class = classify_candidate_address_class(candidate, _first_evidence_payload(candidate))
        chain_slug = _candidate_chain_slug(candidate)
        source_trust_status = _verified_source_trust_status(db, candidate) or classify_source_trust_status(candidate, _first_evidence_payload(candidate))
        approval_readiness = classify_approval_readiness(
            candidate,
            source_trust_status,
            address_class,
            candidate.confidence_initial,
            evidence_count,
        )
        review_bucket = approval_readiness
        if (approval_readiness.startswith("ready_for_approval") or approval_readiness == "auto_ready_official_verified") and verification_gate_for_candidate(db, candidate).allowed:
            auto_approvable_count += 1
        elif approval_readiness.startswith("needs_review"):
            needs_review_count += 1
        else:
            blocked_count += 1

        _increment_counts(counts, candidate, address_class, source_trust_status, approval_readiness, review_bucket, chain_slug)
        unique_key = (candidate.entity_name, chain_slug, candidate.normalized_address, candidate.suggested_role, address_class)
        unique_keys[unique_key] += 1
        unique_address_classes[unique_key] = address_class
        unique_source_trust[unique_key] = _stronger_source_trust(unique_source_trust.get(unique_key), source_trust_status)
        unique_approval_readiness[unique_key] = _stronger_approval_readiness(unique_approval_readiness.get(unique_key), approval_readiness)
        if len(sample_candidates) < limit_samples:
            sample_candidates.append(_candidate_sample(candidate, evidence_count, address_class, source_trust_status, approval_readiness, review_bucket, chain_slug))

    duplicate_groups_top = _duplicate_groups(unique_keys, limit_samples)
    duplicate_samples = duplicate_groups_top
    duplicate_group_count = sum(1 for count in unique_keys.values() if count > 1)
    duplicate_row_count = sum(count - 1 for count in unique_keys.values() if count > 1)
    duplicate_count = duplicate_row_count
    unique_candidate_count = len(unique_keys)
    max_duplicate_group_size = max(unique_keys.values(), default=0)
    count_by_unique_address_class = Counter(unique_address_classes.values())
    count_by_unique_source_trust_status = Counter(unique_source_trust.values())
    count_by_unique_approval_readiness = Counter(unique_approval_readiness.values())
    staking_stats = _staking_stats(unique_keys, limit_samples)

    if candidates and total_evidence == 0:
        warnings.append("no_evidence_rows_found")
    if missing_evidence_count:
        warnings.append("candidates_missing_evidence")

    total_candidates = len(candidates)
    return {
        "total_candidates": total_candidates,
        "total_evidence": total_evidence,
        "evidence_per_candidate_ratio": round(total_evidence / total_candidates, 4) if total_candidates else 0,
        "source_job_count": len(source_job_ids),
        "source_job_ids": source_job_ids,
        "unique_candidate_count": unique_candidate_count,
        "duplicate_row_count": duplicate_row_count,
        "duplicate_group_count": duplicate_group_count,
        "max_duplicate_group_size": max_duplicate_group_size,
        "count_by_source_job_id": _string_counter(counts["source_job_id"]),
        "count_by_entity_name": _string_counter(counts["entity_name"]),
        "count_by_source_network": _string_counter(counts["source_network"]),
        "count_by_chain_slug": _string_counter(counts["chain_slug"]),
        "count_by_suggested_role": _string_counter(counts["suggested_role"]),
        "count_by_evidence_type": _string_counter(counts["evidence_type"]),
        "count_by_source_input_type": _string_counter(counts["source_input_type"]),
        "count_by_status": _string_counter(counts["status"]),
        "count_by_confidence_bucket": _string_counter(counts["confidence_bucket"]),
        "count_by_address_class": _string_counter(counts["address_class"]),
        "count_by_unique_address_class": _string_counter(count_by_unique_address_class),
        "count_by_source_trust_status": _string_counter(counts["source_trust_status"]),
        "count_by_approval_readiness": _string_counter(counts["approval_readiness"]),
        "count_by_unique_source_trust_status": _string_counter(count_by_unique_source_trust_status),
        "count_by_unique_approval_readiness": _string_counter(count_by_unique_approval_readiness),
        "count_by_review_bucket": _string_counter(counts["review_bucket"]),
        "missing_entity_count": counts["missing"]["entity"],
        "missing_network_count": counts["missing"]["network"],
        "missing_role_count": counts["missing"]["role"],
        "missing_address_count": counts["missing"]["address"],
        "missing_evidence_count": missing_evidence_count,
        "duplicate_count": duplicate_count,
        "duplicate_samples": duplicate_samples,
        "duplicate_groups_top": duplicate_groups_top,
        "staking_unique_candidate_count": staking_stats["unique_candidate_count"],
        "staking_raw_row_count": staking_stats["raw_row_count"],
        "staking_duplicate_row_count": staking_stats["duplicate_row_count"],
        "staking_group_samples": staking_stats["group_samples"],
        "sample_candidates": sample_candidates,
        "auto_approvable_count": auto_approvable_count,
        "needs_review_count": needs_review_count,
        "blocked_count": blocked_count,
        "warnings": warnings,
    }


def classify_candidate_address_class(candidate, evidence_payload: dict | None = None) -> str:
    role = (candidate.suggested_role or "").strip().lower()
    source_input_type = (candidate.source_input_type or "").strip().lower()
    metadata = _flatten([candidate.raw_reference or {}, evidence_payload or {}, candidate.file_path or ""])
    metadata_text = " ".join(str(value).lower() for value in metadata)

    if source_input_type == "github_typescript_relation_map" or "external_or_related" in metadata_text:
        return "protocol_relation_dependency" if role != "external_dependency" else "external_dependency"
    if role == "external_dependency":
        return "external_dependency"
    if role == "token_contract" and source_input_type == "github_typescript_relation_map":
        return "external_dependency"
    if role == "cex_por_wallet":
        return "cex_reserve_wallet"
    if role == "cex_hot_wallet":
        return "cex_hot_wallet"
    if role == "cex_cold_wallet":
        return "cex_cold_wallet"
    if role == "staking_deposit_wallet":
        return "staking_deposit_wallet"
    if role == "staking_withdrawal_wallet":
        return "staking_withdrawal_wallet"
    if role == "wallet_address_from_explorer_link":
        return "explorer_link_only"
    if role in CORE_PROTOCOL_ROLES:
        return "core_protocol_contract"
    if _is_official_registry_entry_source(candidate):
        return "official_registry_entry"
    if "wallet" in role:
        return "generic_wallet"
    return "unknown_candidate"


def classify_source_trust_status(candidate, evidence_payload: dict | None = None) -> str:
    explicit_level = _explicit_source_trust_level(candidate, evidence_payload)
    if explicit_level:
        return source_trust_status_from_trust_level(explicit_level) or "unknown"
    evidence_text = _candidate_evidence_text(candidate, evidence_payload)
    if "fallback" in evidence_text or "inferred" in evidence_text:
        return "inferred"
    return "unknown"


def source_trust_status_from_trust_level(trust_level: str | None) -> str | None:
    return {
        "official_verified": "official_confirmed",
        "official_likely": "official_confirmed",
        "manual_verified": "official_confirmed",
        "third_party_officially_referenced": "official_reference",
        "third_party_exchange_reported": "exchange_reported",
        "third_party_audit": "third_party_audit",
        "third_party_unverified": "weak_reference",
        "manual_unverified": "inferred",
        "unknown": "unknown",
        "rejected": "rejected",
    }.get(str(trust_level or "").strip())


def _verified_source_trust_status(db: Session, candidate: AddressCandidate) -> str | None:
    verification = find_source_verification_for_candidate(db, candidate)
    if verification is None:
        return None
    if verification.verification_status == "rejected" or verification.source_trust == "rejected":
        return "rejected"
    if verification.verification_status not in VERIFIED_STATUSES:
        return None
    if not verification.verified_by or not verification.verified_at:
        return None
    return source_trust_status_from_trust_level(verification.source_trust)


def _explicit_source_trust_level(candidate, evidence_payload: dict | None = None) -> str | None:
    for source in (candidate.raw_reference or {}, evidence_payload or {}):
        verification = _nested_get(source, "source_verification")
        if isinstance(verification, dict) and verification.get("source_trust"):
            return str(verification["source_trust"])
        scored_level = _nested_get(source, "scored_source_trust")
        if scored_level:
            return str(scored_level)
        level = _nested_get(source, "source_trust_level")
        if level:
            return str(level)
        source_trust = _nested_get(source, "source_trust")
        if isinstance(source_trust, dict) and source_trust.get("trust_level"):
            return str(source_trust["trust_level"])
    return None


def _nested_get(source: dict, key: str):
    if key in source:
        return source.get(key)
    raw_reference = source.get("raw_reference")
    if isinstance(raw_reference, dict):
        return raw_reference.get(key)
    return None


def classify_approval_readiness(candidate, source_trust_status: str, address_class: str, confidence_initial: int | None, evidence_count: int) -> str:
    if not candidate.entity_name:
        return "invalid_missing_entity"
    if not candidate.source_network:
        return "invalid_missing_network"
    if not candidate.suggested_role:
        return "invalid_missing_role"
    if not candidate.address or not candidate.normalized_address:
        return "invalid_missing_address"
    if evidence_count <= 0:
        return "invalid_missing_evidence"
    # Preview-time embedded readiness can be stale. Final audit/approval readiness is
    # always derived from current DB-backed candidate fields and evidence links.
    if address_class == "explorer_link_only":
        return "not_auto_approvable_explorer_link_only"
    ready_trust = _is_ready_trust_for_address_class(source_trust_status, address_class)
    if address_class == "official_registry_entry":
        return "ready_for_approval_official_registry_entry" if ready_trust else "needs_review_official_registry_entry"
    if address_class == "unknown_candidate":
        return "needs_review_unmapped_official_role" if ready_trust else "not_auto_approvable_unknown"
    if address_class in {"staking_deposit_wallet", "staking_withdrawal_wallet"}:
        return "needs_review_staking_mapping"
    if address_class in {"cex_hot_wallet", "cex_cold_wallet"}:
        return "needs_review_hot_cold_wallet"
    score = int(confidence_initial or 0)
    if address_class == "cex_reserve_wallet" and ready_trust:
        return "ready_for_approval_cex_reserve" if score >= 85 else "needs_review_official_low_confidence"
    if address_class == "core_protocol_contract" and ready_trust:
        return "ready_for_approval_core_protocol" if score >= 85 else "needs_review_official_low_confidence"
    if address_class == "generic_wallet" and ready_trust:
        return "needs_review_generic_wallet"
    return "needs_review_generic_wallet"


def _review_bucket(candidate: AddressCandidate, address_class: str) -> str:
    source_trust_status = classify_source_trust_status(candidate, _first_evidence_payload(candidate))
    return classify_approval_readiness(candidate, source_trust_status, address_class, candidate.confidence_initial, len(candidate.evidence))


def _is_official_enough(candidate: AddressCandidate) -> bool:
    return classify_source_trust_status(candidate, _first_evidence_payload(candidate)) == "official_confirmed"


def _is_trusted_official_status(source_trust_status: str) -> bool:
    return source_trust_status in {
        "official_confirmed",
        "exchange_reported",
    }


def _is_ready_trust_for_address_class(source_trust_status: str, address_class: str) -> bool:
    if address_class == "cex_reserve_wallet":
        return source_trust_status in {"official_confirmed", "official_reference", "exchange_reported", "third_party_audit"}
    if address_class == "core_protocol_contract":
        return source_trust_status == "official_confirmed"
    if address_class == "official_registry_entry":
        return source_trust_status == "official_confirmed"
    return _is_trusted_official_status(source_trust_status)


def _is_official_registry_entry_source(candidate: AddressCandidate) -> bool:
    source_input_type = (candidate.source_input_type or "").strip().lower()
    return source_input_type in OFFICIAL_REGISTRY_ENTRY_SOURCE_INPUT_TYPES


def _candidate_evidence_text(candidate, evidence_payload: dict | None = None) -> str:
    values: list[Any] = [candidate.evidence_type, candidate.source_type, candidate.source_input_type, candidate.raw_reference or {}, evidence_payload or {}]
    for evidence in getattr(candidate, "evidence", []) or []:
        values.extend([evidence.evidence_type, evidence.source_type, evidence.payload or {}, evidence.confidence_reason])
    return " ".join(str(value).lower() for value in _flatten(values))


def _evidence_count(db: Session, source_job_id: int | None) -> int:
    stmt = select(func.count(AddressEvidence.id))
    if source_job_id is not None:
        stmt = stmt.join(AddressCandidate, AddressCandidate.id == AddressEvidence.candidate_id).where(AddressCandidate.source_job_id == source_job_id)
    return int(db.scalar(stmt) or 0)


def _empty_counters() -> dict[str, Counter | defaultdict]:
    return {
        "source_job_id": Counter(),
        "entity_name": Counter(),
        "source_network": Counter(),
        "chain_slug": Counter(),
        "suggested_role": Counter(),
        "evidence_type": Counter(),
        "source_input_type": Counter(),
        "status": Counter(),
        "confidence_bucket": Counter(),
        "address_class": Counter(),
        "source_trust_status": Counter(),
        "approval_readiness": Counter(),
        "review_bucket": Counter(),
        "missing": defaultdict(int),
    }


def _candidate_chain_slug(candidate: AddressCandidate) -> str | None:
    return candidate.chain_slug or NetworkNormalizer.normalize(candidate.source_network).canonical_chain


def _duplicate_groups(unique_keys: Counter, limit_samples: int) -> list[dict]:
    groups = []
    for key, count in unique_keys.most_common():
        if count <= 1:
            continue
        groups.append(_group_sample(key, count))
        if len(groups) >= limit_samples:
            break
    return groups


def _staking_stats(unique_keys: Counter, limit_samples: int) -> dict:
    staking_classes = {"staking_deposit_wallet", "staking_withdrawal_wallet"}
    staking_keys = Counter({key: count for key, count in unique_keys.items() if key[4] in staking_classes})
    raw_row_count = sum(staking_keys.values())
    unique_candidate_count = len(staking_keys)
    duplicate_row_count = sum(count - 1 for count in staking_keys.values() if count > 1)
    group_samples = [_group_sample(key, count) for key, count in staking_keys.most_common(limit_samples)]
    return {
        "unique_candidate_count": unique_candidate_count,
        "raw_row_count": raw_row_count,
        "duplicate_row_count": duplicate_row_count,
        "group_samples": group_samples,
    }


def _group_sample(key: tuple[str | None, str | None, str | None, str | None, str], count: int) -> dict:
    entity_name, chain_slug, normalized_address, suggested_role, address_class = key
    return {
        "entity_name": entity_name,
        "chain_slug": chain_slug,
        "normalized_address": normalized_address,
        "suggested_role": suggested_role,
        "address_class": address_class,
        "count": count,
    }


SOURCE_TRUST_RANK = {
    "rejected": -1,
    "unknown": 0,
    "inferred": 1,
    "weak_reference": 2,
    "third_party_audit": 3,
    "official_reference": 3,
    "exchange_reported": 3,
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


def _stronger_source_trust(current: str | None, new_value: str) -> str:
    if current is None:
        return new_value
    return new_value if SOURCE_TRUST_RANK.get(new_value, 0) > SOURCE_TRUST_RANK.get(current, 0) else current


def _stronger_approval_readiness(current: str | None, new_value: str) -> str:
    if current is None:
        return new_value
    return new_value if APPROVAL_READINESS_RANK.get(new_value, 0) > APPROVAL_READINESS_RANK.get(current, 0) else current


def _increment_counts(
    counts: dict[str, Counter | defaultdict],
    candidate: AddressCandidate,
    address_class: str,
    source_trust_status: str,
    approval_readiness: str,
    review_bucket: str,
    chain_slug: str | None,
) -> None:
    counts["source_job_id"][candidate.source_job_id] += 1
    counts["entity_name"][candidate.entity_name or "-"] += 1
    counts["source_network"][candidate.source_network or "-"] += 1
    counts["chain_slug"][chain_slug or "-"] += 1
    counts["suggested_role"][candidate.suggested_role or "-"] += 1
    counts["evidence_type"][candidate.evidence_type or "-"] += 1
    counts["source_input_type"][candidate.source_input_type or "-"] += 1
    counts["status"][candidate.status or "-"] += 1
    counts["confidence_bucket"][_confidence_bucket(candidate.confidence_initial)] += 1
    counts["address_class"][address_class] += 1
    counts["source_trust_status"][source_trust_status] += 1
    counts["approval_readiness"][approval_readiness] += 1
    counts["review_bucket"][review_bucket] += 1
    if not candidate.entity_name:
        counts["missing"]["entity"] += 1
    if not candidate.source_network:
        counts["missing"]["network"] += 1
    if not candidate.suggested_role:
        counts["missing"]["role"] += 1
    if not candidate.address or not candidate.normalized_address:
        counts["missing"]["address"] += 1


def _candidate_sample(
    candidate: AddressCandidate,
    evidence_count: int,
    address_class: str,
    source_trust_status: str,
    approval_readiness: str,
    review_bucket: str,
    chain_slug: str | None,
) -> dict:
    return {
        "id": candidate.id,
        "source_job_id": candidate.source_job_id,
        "entity_name": candidate.entity_name,
        "source_network": candidate.source_network,
        "chain_slug": chain_slug,
        "stored_chain_slug": candidate.chain_slug,
        "suggested_role": candidate.suggested_role,
        "address": candidate.address,
        "normalized_address": candidate.normalized_address,
        "evidence_type": candidate.evidence_type,
        "source_input_type": candidate.source_input_type,
        "status": candidate.status,
        "confidence_initial": candidate.confidence_initial,
        "address_class": address_class,
        "source_trust_status": source_trust_status,
        "approval_readiness": approval_readiness,
        "review_bucket": review_bucket,
        "evidence_count": evidence_count,
    }


def _confidence_bucket(value: int | None) -> str:
    score = int(value or 0)
    if score >= 90:
        return "90_100"
    if score >= 75:
        return "75_89"
    if score >= 50:
        return "50_74"
    return "0_49"


def _first_evidence_payload(candidate: AddressCandidate) -> dict | None:
    for evidence in candidate.evidence:
        return evidence.payload
    return None


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


def _string_counter(counter: Counter) -> dict[str, int]:
    return {str(key): value for key, value in sorted(counter.items(), key=lambda item: str(item[0]))}
