from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

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
    duplicate_keys: Counter[tuple[str | None, str | None, str | None, str | None]] = Counter()
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
        review_bucket = _review_bucket(candidate, address_class)
        if review_bucket.startswith("auto_approvable"):
            auto_approvable_count += 1
        elif review_bucket.startswith("needs_review"):
            needs_review_count += 1
        else:
            blocked_count += 1

        _increment_counts(counts, candidate, address_class, review_bucket)
        duplicate_keys[(candidate.normalized_address, candidate.chain_slug, candidate.entity_name, candidate.suggested_role)] += 1
        if len(sample_candidates) < limit_samples:
            sample_candidates.append(_candidate_sample(candidate, evidence_count, address_class, review_bucket))

    duplicate_count = 0
    duplicate_key_set = {key for key, count in duplicate_keys.items() if count > 1 and key[0]}
    for key in duplicate_key_set:
        count = duplicate_keys[key]
        duplicate_count += count - 1
        if len(duplicate_samples) < limit_samples:
            duplicate_samples.append(
                {
                    "normalized_address": key[0],
                    "chain_slug": key[1],
                    "entity_name": key[2],
                    "suggested_role": key[3],
                    "count": count,
                }
            )

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
        "count_by_review_bucket": _string_counter(counts["review_bucket"]),
        "missing_entity_count": counts["missing"]["entity"],
        "missing_network_count": counts["missing"]["network"],
        "missing_role_count": counts["missing"]["role"],
        "missing_address_count": counts["missing"]["address"],
        "missing_evidence_count": missing_evidence_count,
        "duplicate_count": duplicate_count,
        "duplicate_samples": duplicate_samples,
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
    if not candidate.address or not candidate.normalized_address:
        return "blocked_missing_address"
    if not candidate.evidence:
        return "blocked_missing_evidence"
    if candidate.confidence_initial < 90:
        return "blocked_low_confidence"
    if address_class == "explorer_link_only":
        return "blocked_explorer_link_only"
    if address_class == "cex_reserve_wallet" and _is_official_enough(candidate):
        return "auto_approvable_cex_reserve"
    if address_class == "core_protocol_contract" and _is_official_enough(candidate):
        return "auto_approvable_official_core"
    if address_class in {"staking_deposit_wallet", "staking_withdrawal_wallet"}:
        return "needs_review_staking_mapping"
    if address_class == "protocol_relation_dependency" or address_class == "external_dependency":
        return "needs_review_relation_dependency"
    if address_class in {"cex_hot_wallet", "cex_cold_wallet"}:
        return "needs_review_hot_cold_wallet"
    return "blocked_unknown"


def _is_official_enough(candidate: AddressCandidate) -> bool:
    evidence_types = {candidate.evidence_type, *(evidence.evidence_type for evidence in candidate.evidence)}
    if evidence_types & OFFICIAL_CORE_EVIDENCE_TYPES:
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


def _increment_counts(counts: dict[str, Counter | defaultdict], candidate: AddressCandidate, address_class: str, review_bucket: str) -> None:
    counts["source_job_id"][candidate.source_job_id] += 1
    counts["entity_name"][candidate.entity_name or "-"] += 1
    counts["source_network"][candidate.source_network or "-"] += 1
    counts["chain_slug"][candidate.chain_slug or "-"] += 1
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


def _candidate_sample(candidate: AddressCandidate, evidence_count: int, address_class: str, review_bucket: str) -> dict:
    return {
        "id": candidate.id,
        "source_job_id": candidate.source_job_id,
        "entity_name": candidate.entity_name,
        "source_network": candidate.source_network,
        "chain_slug": candidate.chain_slug,
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
