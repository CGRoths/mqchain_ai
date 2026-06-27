from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
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
from app.review.source_verification import record_source_verification


REPO_ROOT = Path(__file__).resolve().parents[1]


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

        assert classify_source_trust_status(hacken) == "unknown"
        assert classify_source_trust_status(okx) == "unknown"
        assert classify_source_trust_status(explorer) == "unknown"


def test_approval_readiness_classification() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        verified_audit = _verification_payload("third_party_audit")
        verified_exchange = _verification_payload("third_party_exchange_reported")
        verified_official = _verification_payload("official_verified")
        ready = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85, raw_reference=verified_audit)
        low = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=70, raw_reference=verified_audit)
        staking = _candidate(db, job, evidence_type="excel_wallet_list", suggested_role="staking_deposit_wallet", raw_reference=verified_exchange)
        unknown = _candidate(db, job, evidence_type="manual_seed_context", suggested_role="unmapped_role", raw_reference=verified_official)

        assert _readiness(ready) == "ready_for_approval_cex_reserve"
        assert _readiness(low) == "needs_review_official_low_confidence"
        assert _readiness(staking) == "needs_review_staking_mapping"
        assert _readiness(unknown) == "needs_review_unmapped_official_role"


def test_audit_reports_source_trust_and_readiness_counts() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        reserve = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        staking = _candidate(db, job, evidence_type="excel_wallet_list", suggested_role="staking_deposit_wallet")
        _verify_candidate(db, reserve, source_trust="third_party_audit")
        _verify_candidate(db, staking, source_trust="third_party_exchange_reported")

        report = audit_candidates(db, source_job_id=job.id)

        assert report["count_by_source_trust_status"]["third_party_audit"] == 1
        assert report["count_by_approval_readiness"]["ready_for_approval_cex_reserve"] == 1
        assert report["count_by_unique_source_trust_status"]["exchange_reported"] == 1
        assert report["count_by_unique_approval_readiness"]["needs_review_staking_mapping"] == 1
        assert report["count_by_review_bucket"]["ready_for_approval_cex_reserve"] == 1


def test_unique_candidate_grouping_collapses_duplicates() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        first = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        second = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        _verify_candidate(db, first, source_trust="third_party_audit")
        _verify_candidate(db, second, source_trust="third_party_audit")

        groups = get_unique_candidate_groups(db, source_job_id=job.id)

        assert len(groups) == 1
        assert len(groups[0].candidates) == 2
        assert groups[0].approval_readiness == "ready_for_approval_cex_reserve"


def test_dry_run_approval_does_not_mutate_db() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        _verify_candidate(db, candidate, source_trust="third_party_audit")

        result = approve_candidate_groups(db, source_job_id=job.id, dry_run=True)

        assert result["groups_approved"] == 1
        assert db.scalar(select(func.count(Entity.id))) == 0
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 0
        assert db.scalar(select(func.count(ApprovalEvent.id))) == 0


def test_live_evidence_overrides_stale_embedded_missing_evidence_readiness() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(
            db,
            job,
            evidence_type="pdf_por_document",
            confidence_initial=85,
            raw_reference={
                "approval_readiness": "invalid_missing_evidence",
                "discovery_permission": {"approval_readiness": "invalid_missing_evidence"},
            },
        )
        _verify_candidate(db, candidate, source_trust="third_party_audit")

        readiness = classify_approval_readiness(
            candidate,
            "third_party_audit",
            classify_candidate_address_class(candidate),
            candidate.confidence_initial,
            len(candidate.evidence),
        )
        result = approve_candidate_groups(db, source_job_id=job.id, dry_run=True)

        assert len(candidate.evidence) == 1
        assert readiness == "ready_for_approval_cex_reserve"
        assert result["groups_approved"] == 1
        assert "readiness_invalid_missing_evidence" not in result["skipped_reasons"]


def test_official_structured_registry_role_is_approvable_without_core_role_mapping() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(
            db,
            job,
            entity_name="Grove",
            evidence_type="github_deployment_source",
            source_input_type="github_json_deployment_registry",
            suggested_role="alm_controller",
            confidence_initial=85,
            raw_reference={"contract_name": "alm_controller", "original_role_text": "alm_controller"},
        )
        _verify_candidate(db, candidate, source_trust="official_verified")

        report = audit_candidates(db, source_job_id=job.id)
        groups = get_unique_candidate_groups(db, source_job_id=job.id)
        result = approve_candidate_groups(db, source_job_id=job.id, dry_run=True)

        assert len(candidate.evidence) == 1
        assert classify_candidate_address_class(candidate) == "official_registry_entry"
        assert groups[0].approval_readiness == "ready_for_approval_official_registry_entry"
        assert groups[0].approval_readiness != "needs_review_unmapped_official_role"
        assert report["count_by_review_bucket"]["ready_for_approval_official_registry_entry"] == 1
        assert result["groups_approved"] == 1


def test_official_registry_external_role_preserves_label_without_owned_relationship() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(
            db,
            job,
            entity_name="Grove",
            evidence_type="github_deployment_source",
            source_input_type="github_json_deployment_registry",
            suggested_role="usdc",
            confidence_initial=85,
            address="0x2222222222222222222222222222222222222222",
            normalized_address="0x2222222222222222222222222222222222222222",
            raw_reference={"contract_name": "USDC", "original_role_text": "usdc"},
        )
        _verify_candidate(db, candidate, source_trust="official_verified")

        result = approve_candidate_groups(db, source_job_id=job.id, dry_run=False, actor="pytest")
        approved = db.scalar(select(ApprovedAddress))
        role = db.scalar(select(ApprovedAddressRole))

        assert result["groups_approved"] == 1
        assert approved.address_class == "official_registry_entry"
        assert role.role == "usdc"
        assert approved.metadata_json["source_role_label"] == "usdc"
        assert approved.metadata_json["source_role_labels"] == ["usdc"]
        assert approved.metadata_json["relationship_type"] == "officially_referenced_by_entity"
        assert approved.metadata_json["ownership_scope"] == "unknown_ownership"
        assert approved.metadata_json["relationship_type"] != "owned_by_entity"


def test_apply_approval_creates_registry_rows_and_is_idempotent() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        first_candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        second_candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        _verify_candidate(db, first_candidate, source_trust="third_party_audit")
        _verify_candidate(db, second_candidate, source_trust="third_party_audit")

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
        candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=70)
        _verify_candidate(db, candidate, source_trust="third_party_audit")

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
        candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=70)
        _verify_candidate(db, candidate, source_trust="third_party_audit")

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


def test_hot_cold_override_apply_requires_verified_source() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        candidate = _candidate(db, job, evidence_type="excel_wallet_list", suggested_role="cex_hot_wallet", confidence_initial=90)
        _verify_candidate(db, candidate, source_trust="official_verified")

        result = approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_hot_cold_wallet",
            dry_run=False,
            actor="test",
        )

        approved = db.scalar(select(ApprovedAddress))
        event = db.scalar(select(ApprovalEvent))

        assert result["groups_approved"] == 1
        assert result["override_groups_approved"] == 1
        assert approved.address_class == "cex_hot_wallet"
        assert event.reason == "manual_policy_override: official hot/cold wallet candidate"


def test_low_confidence_override_does_not_approve_disallowed_classes() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, evidence_type="pdf_por_document", suggested_role="cex_hot_wallet", confidence_initial=70)
        _candidate(db, job, evidence_type="pdf_por_document", suggested_role="cex_cold_wallet", confidence_initial=70, address="0x2222222222222222222222222222222222222222", normalized_address="0x2222222222222222222222222222222222222222")
        _candidate(db, job, evidence_type="excel_wallet_list", suggested_role="staking_deposit_wallet", confidence_initial=70, address="0x3333333333333333333333333333333333333333", normalized_address="0x3333333333333333333333333333333333333333")
        unknown = _candidate(db, job, evidence_type="manual_seed_context", suggested_role="unmapped_role", confidence_initial=70, address="0x4444444444444444444444444444444444444444", normalized_address="0x4444444444444444444444444444444444444444")
        _candidate(db, job, evidence_type="TXT explorer link list", suggested_role="wallet_address_from_explorer_link", confidence_initial=70, address="0x5555555555555555555555555555555555555555", normalized_address="0x5555555555555555555555555555555555555555")
        _verify_candidate(db, unknown, source_trust="official_verified")

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
        candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=70)
        _verify_candidate(db, candidate, source_trust="third_party_audit")
        result = approve_candidate_groups(
            db,
            source_job_id=job.id,
            allow_review_readiness="needs_review_official_low_confidence",
            dry_run=False,
        )
        assert result["groups_approved"] == 1
        assert db.scalar(select(func.count(ApprovedAddress.id))) == 1

    output = tmp_path / "approved_registry.csv"
    database_url = f"sqlite:///{(REPO_ROOT / 'data' / 'test_mqchain_ai.db').as_posix()}"
    subprocess.run(
        [sys.executable, "scripts/export_approved_registry.py", "--output", str(output)],
        cwd=str(REPO_ROOT),
        env={**os.environ, "MQCHAIN_AI_DATABASE_URL": database_url},
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
        candidate = _candidate(db, job, evidence_type="excel_wallet_list", suggested_role="staking_deposit_wallet")
        _verify_candidate(db, candidate, source_trust="third_party_exchange_reported")

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
        candidate = _candidate(db, job, evidence_type="pdf_por_document", confidence_initial=85)
        _verify_candidate(db, candidate, source_trust="third_party_audit")
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

    verifications = client.get(
        "/api/review/source-verifications",
        params={"source_job_id": source_job_id, "entity_name": "Bybit", "source_trust": "third_party_audit"},
    )
    assert verifications.status_code == 200, verifications.text
    verification_rows = verifications.json()
    assert len(verification_rows) == 1
    assert verification_rows[0]["entity_name"] == "Bybit"
    assert verification_rows[0]["verified_by"] == "pytest"

    events = client.get("/api/review/approval-events", params={"source_job_id": source_job_id, "actor": "api-test"})
    assert events.status_code == 200, events.text
    event_rows = events.json()
    assert len(event_rows) == 1
    assert event_rows[0]["action"] == "approved"
    assert event_rows[0]["payload_json"]["evidence_linked"] == 1

    search = client.get("/api/review/global-search", params={"q": "Bybit"})
    assert search.status_code == 200, search.text
    body = search.json()
    assert len(body["approved_addresses"]) == 1
    assert len(body["candidates"]) == 1
    assert len(body["evidence"]) == 1


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


def _verification_payload(source_trust: str) -> dict:
    return {
        "source_verification": {
            "verification_status": "verified",
            "source_trust": source_trust,
            "verified_by": "pytest",
            "verified_at": "2026-06-26T00:00:00Z",
        }
    }


def _verify_candidate(db, candidate: AddressCandidate, *, source_trust: str) -> None:
    record_source_verification(
        db,
        verification_scope="candidate",
        verification_status="verified",
        source_trust=source_trust,
        verified_by="pytest",
        source_job_id=candidate.source_job_id,
        source_document_id=candidate.source_document_id,
        candidate_id=candidate.id,
        entity_name=candidate.entity_name,
        source_url=candidate.source_url,
        input_method=candidate.source_type,
        evidence_shape=candidate.evidence_type,
        verification_reason="test fixture",
    )
    db.commit()


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
