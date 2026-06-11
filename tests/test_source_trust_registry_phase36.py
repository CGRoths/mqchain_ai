from __future__ import annotations

import os
import subprocess
import sys
from uuid import uuid4

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"
os.environ["MQCHAIN_AI_STAGED_ARTIFACT_DIR"] = "./data/test_staged_artifacts"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.database import Base, SessionLocal, engine, init_db
from app.main import app
from app.models.intake import (
    AddressCandidate,
    AddressEvidence,
    ApprovalEvent,
    ApprovedAddress,
    ApprovedAddressEvidence,
    ApprovedAddressRole,
    Entity,
    IntakePreview,
    SourceDocument,
    SourceJob,
)
from app.review.approval_registry import approve_candidate_groups, get_unique_candidate_groups
from app.review.candidate_audit import (
    audit_candidates,
    classify_approval_readiness,
    classify_candidate_address_class,
    classify_source_trust_status,
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


def test_source_trust_classification() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        hacken = _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF")
        okx = _candidate(db, job, evidence_type="Validator mapping from OKX ETH staking PoR CSV", suggested_role="staking_deposit_wallet")
        explorer = _candidate(db, job, evidence_type="TXT explorer link list", suggested_role="wallet_address_from_explorer_link")

        assert classify_source_trust_status(hacken) == "official_audit_confirmed"
        assert classify_source_trust_status(okx) == "official_staking_mapping"
        assert classify_source_trust_status(explorer) == "weak_reference"


def test_approval_readiness_classification() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        ready = _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)
        low = _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=70)
        staking = _candidate(db, job, evidence_type="Validator mapping from OKX ETH staking PoR CSV", suggested_role="staking_deposit_wallet")
        unknown = _candidate(db, job, evidence_type="Official CoinEx CET staking delegator list", suggested_role="unmapped_role")

        assert _readiness(ready) == "ready_for_approval_cex_reserve"
        assert _readiness(low) == "needs_review_official_low_confidence"
        assert _readiness(staking) == "needs_review_staking_mapping"
        assert _readiness(unknown) == "needs_review_unmapped_official_role"


def test_audit_reports_source_trust_and_readiness_counts() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)
        _candidate(db, job, evidence_type="Validator mapping from OKX ETH staking PoR CSV", suggested_role="staking_deposit_wallet")

        report = audit_candidates(db, source_job_id=job.id)

        assert report["count_by_source_trust_status"]["official_audit_confirmed"] == 1
        assert report["count_by_approval_readiness"]["ready_for_approval_cex_reserve"] == 1
        assert report["count_by_unique_source_trust_status"]["official_staking_mapping"] == 1
        assert report["count_by_unique_approval_readiness"]["needs_review_staking_mapping"] == 1
        assert report["count_by_review_bucket"]["ready_for_approval_cex_reserve"] == 1


def test_unique_candidate_grouping_collapses_duplicates() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)

        groups = get_unique_candidate_groups(db, source_job_id=job.id)

        assert len(groups) == 1
        assert len(groups[0].candidates) == 2
        assert groups[0].approval_readiness == "ready_for_approval_cex_reserve"


def test_dry_run_approval_does_not_mutate_db() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)

        result = approve_candidate_groups(db, source_job_id=job.id, dry_run=True)

        assert result["groups_approved"] == 1
        assert db.scalar(select(func.count(Entity.id))) == 0
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 0
        assert db.scalar(select(func.count(ApprovalEvent.id))) == 0


def test_apply_approval_creates_registry_rows_and_is_idempotent() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)

        first = approve_candidate_groups(db, source_job_id=job.id, dry_run=False, actor="test")
        second = approve_candidate_groups(db, source_job_id=job.id, dry_run=False, actor="test")

        assert first["groups_approved"] == 1
        assert first["addresses_created"] == 1
        assert first["roles_created"] == 1
        assert first["evidence_linked"] == 2
        assert first["events_written"] == 1
        assert second["groups_approved"] == 0
        assert second["groups_skipped"] == 1
        assert second["skipped_reasons"]["already_approved"] == 1
        assert db.scalar(select(func.count(Entity.id))) == 1
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 1
        assert db.scalar(select(func.count(ApprovedAddressRole.id))) == 1
        assert db.scalar(select(func.count(ApprovedAddressEvidence.id))) == 2
        assert db.scalar(select(func.count(ApprovalEvent.id))) == 1


def test_low_confidence_override_dry_run_does_not_mutate_db() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=70)

        result = approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_official_low_confidence",
            dry_run=True,
        )

        assert result["groups_scanned"] == 1
        assert result["groups_approved"] == 1
        assert result["override_groups_approved"] == 1
        assert result["override_readiness_allowed"] == "needs_review_official_low_confidence"
        assert db.scalar(select(func.count(Entity.id))) == 0
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 0
        assert db.scalar(select(func.count(ApprovalEvent.id))) == 0


def test_low_confidence_override_apply_approves_official_reserve_and_is_idempotent() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=70)

        first = approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_official_low_confidence",
            dry_run=False,
            actor="test",
        )
        second = approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_official_low_confidence",
            dry_run=False,
            actor="test",
        )
        event = db.scalar(select(ApprovalEvent))

        assert first["groups_approved"] == 1
        assert first["override_groups_approved"] == 1
        assert first["addresses_created"] == 1
        assert first["roles_created"] == 1
        assert first["evidence_linked"] == 1
        assert second["groups_approved"] == 0
        assert second["groups_skipped"] == 1
        assert second["override_groups_skipped"] == 1
        assert second["skipped_reasons"]["already_approved"] == 1
        assert event.reason == "manual_policy_override: official low-confidence reserve/core candidate"
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 1
        assert db.scalar(select(func.count(ApprovedAddressEvidence.id))) == 1


def test_low_confidence_override_does_not_approve_disallowed_classes() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", suggested_role="cex_hot_wallet", confidence_initial=70)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", suggested_role="cex_cold_wallet", confidence_initial=70, address="0x2222222222222222222222222222222222222222", normalized_address="0x2222222222222222222222222222222222222222")
        _candidate(db, job, evidence_type="Validator mapping from OKX ETH staking PoR CSV", suggested_role="staking_deposit_wallet", confidence_initial=70, address="0x3333333333333333333333333333333333333333", normalized_address="0x3333333333333333333333333333333333333333")
        _candidate(db, job, evidence_type="Official CoinEx CET staking delegator list", suggested_role="unmapped_role", confidence_initial=70, address="0x4444444444444444444444444444444444444444", normalized_address="0x4444444444444444444444444444444444444444")
        _candidate(db, job, evidence_type="TXT explorer link list", suggested_role="wallet_address_from_explorer_link", confidence_initial=70, address="0x5555555555555555555555555555555555555555", normalized_address="0x5555555555555555555555555555555555555555")

        result = approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_official_low_confidence",
            dry_run=False,
        )

        assert result["groups_approved"] == 0
        assert result["groups_skipped"] == 5
        assert result["skipped_reasons"]["readiness_needs_review_hot_cold_wallet"] == 2
        assert result["skipped_reasons"]["readiness_needs_review_staking_mapping"] == 1
        assert result["skipped_reasons"]["readiness_needs_review_unmapped_official_role"] == 1
        assert result["skipped_reasons"]["readiness_not_auto_approvable_explorer_link_only"] == 1
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 0


def test_export_returns_override_approved_rows(tmp_path) -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=70)
        approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_official_low_confidence",
            dry_run=False,
        )

    output = tmp_path / "approved_registry.csv"
    subprocess.run(
        [sys.executable, "scripts/export_approved_registry.py", "--output", str(output)],
        cwd=".",
        text=True,
        capture_output=True,
        check=True,
    )

    exported = output.read_text(encoding="utf-8")
    assert "Bybit" in exported
    assert "cex_reserve_wallet" in exported


def test_non_approvable_readiness_is_skipped() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Validator mapping from OKX ETH staking PoR CSV", suggested_role="staking_deposit_wallet")

        result = approve_candidate_groups(db, source_job_id=job.id, approval_readiness="needs_review_staking_mapping", dry_run=False)
        repeated = approve_candidate_groups(db, source_job_id=job.id, approval_readiness="needs_review_staking_mapping", dry_run=False)

        assert result["groups_scanned"] == 1
        assert result["groups_approved"] == 0
        assert result["groups_skipped"] == 1
        assert result["skipped_reasons"]["readiness_needs_review_staking_mapping"] == 1
        assert result["events_written"] == 1
        assert repeated["events_written"] == 0
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 0
        assert db.scalar(select(func.count(ApprovalEvent.id))) == 1


def test_review_and_registry_api_endpoints(client: TestClient) -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="Hacken Proof of Reserves audit PDF", confidence_initial=85)
        source_job_id = job.id

    response = client.post("/api/review/approve-candidate-groups", json={"source_job_id": source_job_id, "dry_run": False, "actor": "api-test"})
    assert response.status_code == 200, response.text
    assert response.json()["groups_approved"] == 1

    registry = client.get("/api/registry/approved-addresses", params={"entity_name": "Bybit"})
    assert registry.status_code == 200, registry.text
    rows = registry.json()
    assert len(rows) == 1
    assert rows[0]["role"] == "cex_por_wallet"
    assert rows[0]["evidence_count"] == 1


def test_export_approved_registry_works(tmp_path) -> None:
    output = tmp_path / "approved_registry.csv"
    result = subprocess.run(
        [sys.executable, "scripts/export_approved_registry.py", "--output", str(output)],
        cwd=".",
        text=True,
        capture_output=True,
        check=True,
    )

    assert str(output) in result.stdout
    assert output.read_text(encoding="utf-8").startswith("entity_name,chain_slug")


def _readiness(candidate: AddressCandidate) -> str:
    address_class = classify_candidate_address_class(candidate)
    trust = classify_source_trust_status(candidate)
    return classify_approval_readiness(candidate, trust, address_class, candidate.confidence_initial, len(candidate.evidence))


def _candidate(
    db,
    job: SourceJob,
    *,
    source_type: str = "excel_upload",
    evidence_type: str = "audited_wallet",
    source_input_type: str = "xlsx_multi_sheet_registry",
    entity_name: str | None = "Bybit",
    source_network: str | None = "Ethereum",
    chain_slug: str | None = "ethereum",
    suggested_role: str | None = "cex_por_wallet",
    confidence_initial: int = 85,
    address: str = "0x1111111111111111111111111111111111111111",
    normalized_address: str = "0x1111111111111111111111111111111111111111",
    raw_reference: dict | None = None,
    with_evidence: bool = True,
) -> AddressCandidate:
    document = SourceDocument(
        source_job_id=job.id,
        canonical_source_url="source",
        file_path="source.xlsx",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        document_title="source.xlsx",
        text_hash=str(uuid4()).replace("-", "")[:64],
        metadata_json={},
    )
    db.add(document)
    db.flush()
    candidate = AddressCandidate(
        source_job_id=job.id,
        source_document_id=document.id,
        address=address,
        normalized_address=normalized_address,
        entity_name=entity_name,
        source_network=source_network,
        chain_guess="evm" if source_network else None,
        chain_slug=chain_slug if source_network else None,
        chain_id=1 if source_network else None,
        address_family="evm" if address else None,
        suggested_role=suggested_role,
        confidence_initial=confidence_initial,
        status="needs_review",
        source_type=source_type,
        source_input_type=source_input_type,
        source_url="source",
        file_path=document.file_path,
        evidence_type=evidence_type,
        warnings=[],
        raw_reference=raw_reference or {"contract_name": suggested_role},
    )
    db.add(candidate)
    db.flush()
    if with_evidence:
        db.add(
            AddressEvidence(
                candidate_id=candidate.id,
                source_document_id=document.id,
                evidence_type=evidence_type,
                source_type=source_type,
                final_source_type=source_type,
                adapter_name="excel_csv_adapter",
                source_url="source",
                file_path=document.file_path,
                payload={"raw_reference": candidate.raw_reference},
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
