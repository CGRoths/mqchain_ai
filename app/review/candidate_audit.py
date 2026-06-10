from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.ingestion.network_normalizer import NetworkNormalizer
from app.models.intake import AddressCandidate, AddressEvidence
from app.review.official_auto_approval import OFFICIAL_EVIDENCE_TYPES


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
OFFICIAL_CORE_EVIDENCE_TYPES = {
    *OFFICIAL_EVIDENCE_TYPES,
    "official_github_deployment",
    "official_docs_deployment",
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
        review_bucket = _review_bucket(candidate, address_class)
        if review_bucket.startswith("auto_approvable"):
            auto_approvable_count += 1
        elif review_bucket.startswith("needs_review"):
            needs_review_count += 1
        else:
            blocked_count += 1

        _increment_counts(counts, candidate, address_class, review_bucket, chain_slug)
        unique_key = (candidate.entity_name, chain_slug, candidate.normalized_address, candidate.suggested_role, address_class)
        unique_keys[unique_key] += 1
        unique_address_classes[unique_key] = address_class
        if len(sample_candidates) < limit_samples:
            sample_candidates.append(_candidate_sample(candidate, evidence_count, address_class, review_bucket, chain_slug))

    duplicate_groups_top = _duplicate_groups(unique_keys, limit_samples)
    duplicate_samples = duplicate_groups_top
    duplicate_group_count = sum(1 for count in unique_keys.values() if count > 1)
    duplicate_row_count = sum(count - 1 for count in unique_keys.values() if count > 1)
    duplicate_count = duplicate_row_count
    unique_candidate_count = len(unique_keys)
    max_duplicate_group_size = max(unique_keys.values(), default=0)
    count_by_unique_address_class = Counter(unique_address_classes.values())
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
    if candidate.evidence_type in OFFICIAL_CORE_EVIDENCE_TYPES and role in CORE_PROTOCOL_ROLES:
        return "core_protocol_contract"
    if "wallet" in role:
        return "generic_wallet"
    return "unknown_candidate"


def _review_bucket(candidate: AddressCandidate, address_class: str) -> str:
    if not candidate.entity_name:
        return "blocked_missing_entity"
    if not candidate.source_network:
        return "blocked_missing_network"
    if not candidate.suggested_role:
        return "blocked_missing_role"
    if not candidate.evidence:
        return "blocked_missing_evidence"
    if not candidate.address or not candidate.normalized_address:
        return "blocked_missing_address"
    if address_class == "explorer_link_only":
        return "blocked_explorer_link_only"
    if address_class == "unknown_candidate":
        return "blocked_unknown"
    if address_class in {"staking_deposit_wallet", "staking_withdrawal_wallet"}:
        return "needs_review_staking_mapping"
    if address_class in {"cex_hot_wallet", "cex_cold_wallet"}:
        return "needs_review_hot_cold_wallet"
    if address_class == "cex_reserve_wallet" and _is_official_enough(candidate):
        return "auto_approvable_cex_reserve" if candidate.confidence_initial >= 90 else "needs_review_official_low_confidence"
    if address_class == "core_protocol_contract" and _is_official_enough(candidate):
        return "auto_approvable_official_core" if candidate.confidence_initial >= 90 else "needs_review_official_low_confidence"
    if address_class == "protocol_relation_dependency" or address_class == "external_dependency":
        return "needs_review_relation_dependency"
    if candidate.confidence_initial < 75:
        return "blocked_low_confidence"
    return "needs_review_generic"


def _is_official_enough(candidate: AddressCandidate) -> bool:
    evidence_types = {candidate.evidence_type, *(evidence.evidence_type for evidence in candidate.evidence)}
    if evidence_types & OFFICIAL_CORE_EVIDENCE_TYPES:
        return True
    if "audited_wallet" in evidence_types:
        return True
    evidence_text = " ".join(str(evidence_type or "").lower() for evidence_type in evidence_types)
    if any(marker in evidence_text for marker in ("proof of reserves", "por", "audit", "audited", "hacken")):
        return True
    return (
        candidate.source_type == "por_pdf"
        and candidate.source_input_type == "pdf_audited_wallet_table"
        and "audited_wallet" in evidence_types
    )


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


def _increment_counts(counts: dict[str, Counter | defaultdict], candidate: AddressCandidate, address_class: str, review_bucket: str, chain_slug: str | None) -> None:
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
    counts["review_bucket"][review_bucket] += 1
    if not candidate.entity_name:
        counts["missing"]["entity"] += 1
    if not candidate.source_network:
        counts["missing"]["network"] += 1
    if not candidate.suggested_role:
        counts["missing"]["role"] += 1
    if not candidate.address or not candidate.normalized_address:
        counts["missing"]["address"] += 1


def _candidate_sample(candidate: AddressCandidate, evidence_count: int, address_class: str, review_bucket: str, chain_slug: str | None) -> dict:
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
