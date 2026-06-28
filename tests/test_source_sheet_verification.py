from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"
os.environ["MQCHAIN_AI_STAGED_ARTIFACT_DIR"] = "./data/test_staged_artifacts"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.database import Base, SessionLocal, engine, init_db
from app.main import app
from app.models.intake import AddressCandidate, AddressEvidence, IntakePreview, SourceDocument, SourceJob, SourceVerification
from app.review.official_auto_approval import auto_approve_official_candidates
from app.review.source_verification import (
    address_class_for_candidate,
    record_source_verification,
    verification_gate_for_candidate,
    verify_source_sheets_from_candidates,
)


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_sheet_metadata_requires_sheet_verification_not_source_job_verification() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(db, job, source_sheet="Bybit", source_trust_hint="official_verified")
        record_source_verification(
            db,
            verification_scope="source_job",
            verification_status="verified",
            source_trust="official_verified",
            verified_by="pytest",
            source_job_id=job.id,
            entity_name="Bybit",
            evidence_shape="excel_wallet_list",
        )
        db.commit()

        gate = verification_gate_for_candidate(db, candidate)

        assert gate.allowed is False
        assert gate.reason == "missing_source_verification"
        assert db.scalar(select(func.count(SourceVerification.id))) == 1


def test_bulk_sheet_verification_endpoint_enables_candidate_gate(client: TestClient) -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(db, job, source_sheet="Bybit", source_trust_hint="official_verified")
        source_job_id = job.id
        candidate_id = candidate.id

    dry = client.post(
        "/api/review/verify-source-sheets-from-manifest",
        json={"source_job_id": source_job_id, "verified_by": "CRAY", "dry_run": True},
    )
    assert dry.status_code == 200, dry.text
    assert dry.json()["verifications_created"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(SourceVerification.id))) == 0

    apply = client.post(
        "/api/review/verify-source-sheets-from-manifest",
        json={"source_job_id": source_job_id, "verified_by": "CRAY", "dry_run": False},
    )
    assert apply.status_code == 200, apply.text
    body = apply.json()
    assert body["verifications_created"] == 1
    assert body["created"][0]["source_sheet"] == "Bybit"

    with SessionLocal() as db:
        candidate = db.get(AddressCandidate, candidate_id)
        verification = db.scalar(select(SourceVerification))
        gate = verification_gate_for_candidate(db, candidate)

        assert verification.verification_scope == "source_sheet"
        assert verification.source_sheet == "Bybit"
        assert verification.source_trust == "official_verified"
        assert verification.verified_by == "CRAY"
        assert verification.verification_reason == "bulk_sheet_verification_from_manifest_or_sheet_metadata"
        assert verification.verification_evidence_json["row_count"] == 1
        assert gate.allowed is True


def test_bulk_sheet_verification_allows_different_sheet_trust_values() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, source_sheet="Official", source_trust_hint={"trust_level": "official_checked"})
        _candidate(
            db,
            job,
            source_sheet="Audit",
            source_trust_hint="audit",
            address="0x2222222222222222222222222222222222222222",
            normalized_address="0x2222222222222222222222222222222222222222",
        )

        result = verify_source_sheets_from_candidates(db, job.id, verified_by="CRAY", dry_run=False)
        rows = list(db.scalars(select(SourceVerification).order_by(SourceVerification.source_sheet.asc())))

        assert result["sheets_scanned"] == 2
        assert result["verifications_created"] == 2
        assert [(row.source_sheet, row.source_trust) for row in rows] == [
            ("Audit", "third_party_audit"),
            ("Official", "official_likely"),
        ]


def test_missing_sheet_source_trust_hint_is_skipped() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, source_sheet="NoTrust", source_trust_hint=None)

        result = verify_source_sheets_from_candidates(db, job.id, verified_by="CRAY", dry_run=False)

        assert result["verifications_created"] == 0
        assert result["sheets_skipped"] == 1
        assert result["skipped_reasons"]["missing_source_trust_hint"] == 1
        assert db.scalar(select(func.count(SourceVerification.id))) == 0


def test_bulk_sheet_verification_is_idempotent() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, source_sheet="Bybit", source_trust_hint="official_verified")

        first = verify_source_sheets_from_candidates(db, job.id, verified_by="CRAY", dry_run=False)
        second = verify_source_sheets_from_candidates(db, job.id, verified_by="CRAY", dry_run=False)

        assert first["verifications_created"] == 1
        assert second["verifications_created"] == 0
        assert second["sheets_skipped"] == 1
        assert second["skipped_reasons"]["existing_source_sheet_verification"] == 1
        assert db.scalar(select(func.count(SourceVerification.id))) == 1


def test_bulk_sheet_verification_does_not_change_roles_or_scoring() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        reserve = _candidate(db, job, source_sheet="Bybit", source_trust_hint="official_verified", suggested_role="cex_por_wallet", confidence_initial=87)
        staking = _candidate(
            db,
            job,
            source_sheet="Bybit Staking",
            source_trust_hint="official_verified",
            suggested_role="staking_delegator",
            confidence_initial=92,
            address="0x3333333333333333333333333333333333333333",
            normalized_address="0x3333333333333333333333333333333333333333",
        )
        before = {
            reserve.id: (reserve.suggested_role, reserve.confidence_initial),
            staking.id: (staking.suggested_role, staking.confidence_initial),
        }

        verify_source_sheets_from_candidates(db, job.id, verified_by="CRAY", dry_run=False)
        db.refresh(reserve)
        db.refresh(staking)

        assert {
            reserve.id: (reserve.suggested_role, reserve.confidence_initial),
            staking.id: (staking.suggested_role, staking.confidence_initial),
        } == before
        assert address_class_for_candidate(reserve) == "cex_reserve_wallet"
        assert address_class_for_candidate(staking) != "cex_reserve_wallet"


def test_staking_delegator_sheet_verification_does_not_auto_approve_as_reserve() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        staking = _candidate(db, job, source_sheet="Staking", source_trust_hint="official_verified", suggested_role="staking_delegator")

        verify_source_sheets_from_candidates(db, job.id, verified_by="CRAY", dry_run=False)
        gate = verification_gate_for_candidate(db, staking)
        result = auto_approve_official_candidates(db, source_job_id=job.id, dry_run=False)
        db.refresh(staking)

        assert address_class_for_candidate(staking) == "unknown_candidate"
        assert gate.allowed is False
        assert gate.reason == "source_verification_not_auto_approvable_unknown_candidate"
        assert result["approved"] == 0
        assert staking.status == "needs_review"


def _candidate(
    db,
    job: SourceJob,
    *,
    source_sheet: str,
    source_trust_hint: Any,
    entity_name: str = "Bybit",
    suggested_role: str = "cex_por_wallet",
    confidence_initial: int = 90,
    address: str = "0x1111111111111111111111111111111111111111",
    normalized_address: str = "0x1111111111111111111111111111111111111111",
) -> AddressCandidate:
    document = SourceDocument(
        source_job_id=job.id,
        canonical_source_url=f"https://example.com/{source_sheet.lower()}",
        file_path="source.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        document_title="source.xlsx",
        text_hash=str(uuid4()).replace("-", "")[:64],
        metadata_json={},
    )
    db.add(document)
    db.flush()
    sheet_profile = {
        "entity_hint": entity_name,
        "source_url": document.canonical_source_url,
        "official_referrer_url": f"https://official.example.com/{source_sheet.lower()}",
        "source_origin": f"{entity_name} source manifest",
        "provenance_type": "official_registry",
        "evidence_shape": "excel_wallet_list",
        "snapshot_date": "2026-06-29",
    }
    if source_trust_hint is not None:
        sheet_profile["source_trust"] = source_trust_hint
    raw_reference = {
        "source_sheet": source_sheet,
        "sheet_entity_hint": entity_name,
        "sheet_source_url": document.canonical_source_url,
        "sheet_source_origin": sheet_profile["source_origin"],
        "sheet_official_referrer_url": sheet_profile["official_referrer_url"],
        "sheet_provenance_type": sheet_profile["provenance_type"],
        "sheet_evidence_shape": sheet_profile["evidence_shape"],
        "sheet_snapshot_date": sheet_profile["snapshot_date"],
        "source_evidence": {"sheet_profiles": {source_sheet: sheet_profile}},
    }
    candidate = AddressCandidate(
        source_job_id=job.id,
        source_document_id=document.id,
        address=address,
        normalized_address=normalized_address,
        entity_name=entity_name,
        source_network="Ethereum",
        chain_guess="evm",
        chain_slug="ethereum",
        chain_id=1,
        address_family="evm",
        suggested_role=suggested_role,
        confidence_initial=confidence_initial,
        status="needs_review",
        source_type="excel_upload",
        source_input_type="xlsx_multi_sheet_registry",
        source_sheet=source_sheet,
        source_row=2,
        source_url=document.canonical_source_url,
        file_path=document.file_path,
        evidence_type="excel_wallet_list",
        warnings=[],
        raw_reference=raw_reference,
    )
    db.add(candidate)
    db.flush()
    db.add(
        AddressEvidence(
            candidate_id=candidate.id,
            source_document_id=document.id,
            evidence_type="excel_wallet_list",
            source_type="excel_upload",
            final_source_type="excel_upload",
            adapter_name="excel_csv_adapter",
            source_url=candidate.source_url,
            file_path=document.file_path,
            payload={"raw_reference": candidate.raw_reference, "source_evidence": raw_reference["source_evidence"]},
            confidence_reason="structured_network_column",
        )
    )
    db.commit()
    db.refresh(candidate)
    return candidate


def _source_job(db) -> SourceJob:
    preview = IntakePreview(
        id=str(uuid4()),
        source_artifact_json={},
        fingerprint_json={},
        profile_json={},
        preview_json={},
        warnings=[],
        fatal_errors=[],
    )
    db.add(preview)
    db.flush()
    job = SourceJob(
        preview_id=preview.id,
        input_method="upload",
        source_url=None,
        final_source_type="excel_upload",
        adapter_name="excel_csv_adapter",
        fingerprint_json={},
        source_artifact_json={},
        profile_json={},
        preview_json={},
        status="needs_review",
    )
    db.add(job)
    db.flush()
    return job
