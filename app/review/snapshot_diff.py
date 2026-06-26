from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.intake import (
    AddressCandidate,
    ApprovedAddress,
    ApprovedAddressObservation,
    ApprovedAddressRole,
    Entity,
    SourceDocument,
    SourceJob,
    SourceSnapshot,
)
from app.review.candidate_audit import classify_candidate_address_class
from app.review.source_verification import find_source_verification_for_candidate


def create_source_snapshot(
    db: Session,
    *,
    source_job_id: int,
    snapshot_type: str,
    snapshot_period: str | None = None,
    snapshot_date: str | None = None,
    previous_snapshot_id: int | None = None,
    created_by: str | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> SourceSnapshot:
    job = db.get(SourceJob, source_job_id)
    if job is None:
        raise ValueError("source_job_not_found")
    document = db.scalars(select(SourceDocument).where(SourceDocument.source_job_id == source_job_id).order_by(SourceDocument.id.asc())).first()
    evidence = dict((job.source_artifact_json or {}).get("source_evidence") or {})
    snapshot = SourceSnapshot(
        source_job_id=source_job_id,
        source_document_id=document.id if document else None,
        entity_name=(metadata_json or {}).get("entity_name") or evidence.get("entity_hint") or (job.profile_json or {}).get("entity_name"),
        source_origin=(metadata_json or {}).get("source_origin") or evidence.get("source_origin"),
        source_url=(metadata_json or {}).get("source_url") or evidence.get("source_url") or job.source_url,
        official_referrer_url=(metadata_json or {}).get("official_referrer_url") or evidence.get("official_referrer_url"),
        snapshot_type=snapshot_type,
        snapshot_period=snapshot_period,
        snapshot_date=snapshot_date,
        file_hash=(job.source_artifact_json or {}).get("sha256") or (job.fingerprint_json or {}).get("sha256"),
        content_hash=document.text_hash if document else None,
        previous_snapshot_id=previous_snapshot_id,
        created_by=created_by or job.created_by,
        metadata_json=metadata_json or {},
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def diff_source_snapshot(db: Session, source_job_id: int, snapshot_id: int | None = None) -> dict[str, Any]:
    snapshot = db.get(SourceSnapshot, snapshot_id) if snapshot_id else _latest_snapshot_for_job(db, source_job_id)
    candidates = list(db.scalars(select(AddressCandidate).where(AddressCandidate.source_job_id == source_job_id).order_by(AddressCandidate.id.asc())))
    counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = {}
    current_keys: set[tuple[str | None, str | None, str | None, str | None, str | None]] = set()

    for candidate in candidates:
        row = _candidate_diff_row(db, candidate)
        classification = row["classification"]
        counts[classification] += 1
        current_keys.add(_candidate_key(candidate, row["address_class"]))
        samples.setdefault(classification, []).append(row)

    previous = _previous_snapshot(db, snapshot)
    if previous is not None:
        previous_observations = list(
            db.scalars(
                select(ApprovedAddressObservation).where(
                    ApprovedAddressObservation.source_snapshot_id == previous.id,
                    ApprovedAddressObservation.observed_status != "missing_in_latest",
                )
            )
        )
        for observation in previous_observations:
            key = (
                observation.entity_name,
                observation.chain_slug,
                observation.normalized_address,
                observation.role,
                observation.address_class,
            )
            if key in current_keys:
                counts["still_present"] += 1
                continue
            counts["missing_in_latest"] += 1
            samples.setdefault("missing_in_latest", []).append(_missing_observation_row(observation))

    return {
        "source_job_id": source_job_id,
        "snapshot_id": snapshot.id if snapshot else snapshot_id,
        "previous_snapshot_id": previous.id if previous else None,
        "total_candidates": len(candidates),
        "existing_approved": counts["unchanged_existing"] + counts["new_role_for_existing_address"] + counts["changed_address_class"],
        "new_addresses": counts["new_address"],
        "unchanged_existing": counts["unchanged_existing"],
        "new_roles": counts["new_role_for_existing_address"],
        "missing_in_latest": counts["missing_in_latest"],
        "conflicts": counts["conflict_entity"] + counts["invalid_missing_fields"],
        "rejected": counts["rejected"],
        "ready_for_approval": counts["new_address"] + counts["new_role_for_existing_address"],
        "counts": dict(sorted(counts.items())),
        "samples": {key: value[:20] for key, value in samples.items()},
    }


def mark_missing_in_latest(db: Session, *, source_job_id: int, source_snapshot_id: int, dry_run: bool = True) -> dict[str, Any]:
    snapshot = db.get(SourceSnapshot, source_snapshot_id)
    if snapshot is None:
        raise ValueError("source_snapshot_not_found")
    diff = diff_source_snapshot(db, source_job_id, source_snapshot_id)
    rows = diff["samples"].get("missing_in_latest", [])
    result = {"dry_run": dry_run, "source_snapshot_id": source_snapshot_id, "missing_marked": 0}
    if dry_run:
        return {**result, "missing_marked": len(rows)}
    for row in rows:
        approved = db.get(ApprovedAddress, row.get("approved_address_id"))
        if approved is None:
            continue
        observation = ApprovedAddressObservation(
            approved_address_id=approved.id,
            source_snapshot_id=snapshot.id,
            source_job_id=source_job_id,
            source_document_id=snapshot.source_document_id,
            entity_name=row.get("entity_name"),
            chain_slug=row.get("chain_slug"),
            normalized_address=row.get("normalized_address"),
            role=row.get("role"),
            address_class=row.get("address_class"),
            observed_status="missing_in_latest",
            snapshot_date=snapshot.snapshot_date,
            snapshot_period=snapshot.snapshot_period,
            source_url=snapshot.source_url,
            source_origin=snapshot.source_origin,
            payload_json={"previous_observation_id": row.get("observation_id")},
        )
        approved.latest_snapshot_id = snapshot.id
        approved.latest_snapshot_status = "missing_in_latest"
        approved.lifecycle_status = "missing_in_latest"
        metadata = dict(approved.metadata_json or {})
        metadata["latest_snapshot_status"] = "missing_in_latest"
        metadata["missing_since_snapshot_id"] = snapshot.id
        approved.metadata_json = metadata
        db.add(observation)
        result["missing_marked"] += 1
    db.commit()
    return result


def _candidate_diff_row(db: Session, candidate: AddressCandidate) -> dict[str, Any]:
    address_class = classify_candidate_address_class(candidate)
    base = {
        "candidate_id": candidate.id,
        "entity_name": candidate.entity_name,
        "chain_slug": candidate.chain_slug,
        "normalized_address": candidate.normalized_address,
        "role": candidate.suggested_role,
        "address_class": address_class,
        "source_sheet": candidate.source_sheet,
    }
    if not candidate.entity_name or not candidate.chain_slug or not candidate.normalized_address or not candidate.suggested_role:
        return {**base, "classification": "invalid_missing_fields", "reason": "missing_entity_chain_address_or_role"}

    approved_with_entity = _approved_for_candidate(db, candidate)
    if approved_with_entity is None:
        conflicting = _approved_by_chain_address(db, candidate.chain_slug, candidate.normalized_address)
        if conflicting is not None:
            entity, approved = conflicting
            return {**base, "classification": "conflict_entity", "approved_address_id": approved.id, "approved_entity_name": entity.entity_name}
        return {**base, "classification": "new_address"}

    approved, role = approved_with_entity
    if approved.address_class != address_class:
        return {**base, "classification": "changed_address_class", "approved_address_id": approved.id, "approved_address_class": approved.address_class}
    if role is None:
        return {**base, "classification": "new_role_for_existing_address", "approved_address_id": approved.id}
    return {**base, "classification": "unchanged_existing", "approved_address_id": approved.id, "approved_address_role_id": role.id}


def _approved_for_candidate(db: Session, candidate: AddressCandidate) -> tuple[ApprovedAddress, ApprovedAddressRole | None] | None:
    row = db.execute(
        select(ApprovedAddress, ApprovedAddressRole)
        .join(Entity, Entity.id == ApprovedAddress.entity_id)
        .outerjoin(
            ApprovedAddressRole,
            (ApprovedAddressRole.approved_address_id == ApprovedAddress.id)
            & (ApprovedAddressRole.role == (candidate.suggested_role or "")),
        )
        .where(
            Entity.entity_name == candidate.entity_name,
            ApprovedAddress.chain_slug == candidate.chain_slug,
            ApprovedAddress.normalized_address == candidate.normalized_address,
        )
    ).first()
    return (row[0], row[1]) if row else None


def _approved_by_chain_address(db: Session, chain_slug: str | None, normalized_address: str | None) -> tuple[Entity, ApprovedAddress] | None:
    if not chain_slug or not normalized_address:
        return None
    row = db.execute(
        select(Entity, ApprovedAddress)
        .join(ApprovedAddress, ApprovedAddress.entity_id == Entity.id)
        .where(ApprovedAddress.chain_slug == chain_slug, ApprovedAddress.normalized_address == normalized_address)
    ).first()
    return (row[0], row[1]) if row else None


def _candidate_key(candidate: AddressCandidate, address_class: str) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    return (candidate.entity_name, candidate.chain_slug, candidate.normalized_address, candidate.suggested_role, address_class)


def _missing_observation_row(observation: ApprovedAddressObservation) -> dict[str, Any]:
    return {
        "observation_id": observation.id,
        "approved_address_id": observation.approved_address_id,
        "entity_name": observation.entity_name,
        "chain_slug": observation.chain_slug,
        "normalized_address": observation.normalized_address,
        "role": observation.role,
        "address_class": observation.address_class,
    }


def _latest_snapshot_for_job(db: Session, source_job_id: int) -> SourceSnapshot | None:
    return db.scalars(
        select(SourceSnapshot).where(SourceSnapshot.source_job_id == source_job_id).order_by(SourceSnapshot.created_at.desc(), SourceSnapshot.id.desc())
    ).first()


def _previous_snapshot(db: Session, snapshot: SourceSnapshot | None) -> SourceSnapshot | None:
    if snapshot is None:
        return None
    if snapshot.previous_snapshot_id:
        return db.get(SourceSnapshot, snapshot.previous_snapshot_id)
    if not snapshot.entity_name or not snapshot.snapshot_type:
        return None
    return db.scalars(
        select(SourceSnapshot)
        .where(
            SourceSnapshot.id != snapshot.id,
            SourceSnapshot.entity_name == snapshot.entity_name,
            SourceSnapshot.snapshot_type == snapshot.snapshot_type,
        )
        .order_by(SourceSnapshot.created_at.desc(), SourceSnapshot.id.desc())
    ).first()
