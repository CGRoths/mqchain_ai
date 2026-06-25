from __future__ import annotations

import os
from uuid import uuid4

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"
os.environ["MQCHAIN_AI_STAGED_ARTIFACT_DIR"] = "./data/test_staged_artifacts"

import pytest
from fastapi.testclient import TestClient

from app.db.database import Base, SessionLocal, engine, init_db
from app.main import app
from app.models.intake import AddressCandidate, AddressEvidence, IntakePreview, SourceDocument, SourceJob
from app.review.official_auto_approval import auto_approve_official_candidates
from app.review.source_verification import record_source_verification


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


def test_verified_github_deployment_candidate_gets_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(db, evidence_type="github_deployment_source", source_input_type="github_json_deployment_registry")
        _verify_candidate(db, candidate, source_trust="official_verified")

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["matched"] == 1
        assert result["approved"] == 1
        assert candidate.status == "approved"
        assert candidate.approved_at is not None
        assert candidate.approval_method == "source_verification_auto_approval"


def test_official_evidence_type_without_source_verification_is_blocked() -> None:
    with SessionLocal() as db:
        candidate = _candidate(db, evidence_type="official_github_deployment", source_input_type="github_json_deployment_registry")

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 0
        assert candidate.status == "needs_review"
        assert result["skipped_reasons"]["missing_source_verification"] == 1


def test_por_audit_candidate_gets_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            source_type="por_pdf",
            evidence_type="pdf_por_document",
            source_input_type="pdf_audited_wallet_table",
            suggested_role="cex_por_wallet",
        )
        _verify_candidate(db, candidate, source_trust="third_party_audit")

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 1
        assert candidate.status == "approved"


def test_sablier_official_docs_candidate_gets_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            entity_name="Sablier",
            evidence_type="docs_deployment_source",
            source_input_type="docs_html_deployment_table",
            suggested_role="protocol_contract",
        )
        _verify_candidate(db, candidate, source_trust="official_verified")

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 1
        assert candidate.status == "approved"


def test_compound_configuration_candidate_gets_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            entity_name="Compound",
            evidence_type="github_deployment_source",
            source_input_type="github_json_deployment_registry",
            suggested_role="lending_market",
            file_path="deployments/base/usdc/configuration.json",
            raw_reference={"contract_name": "comet", "market": "USDC"},
        )
        _verify_candidate(db, candidate, source_trust="official_verified")

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 1
        assert candidate.status == "approved"


def test_relation_external_dependency_candidate_does_not_get_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            source_input_type="github_typescript_relation_map",
            suggested_role="external_dependency",
            evidence_type="official_github_relation",
            file_path="deployments/base/usdc/relations.ts",
            raw_reference={"ownership_boundary": "external_or_related"},
        )

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 0
        assert candidate.status == "needs_review"
        assert result["skipped_reasons"]["blocked_role"] == 1


def test_loose_extracted_candidate_does_not_get_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            evidence_type="official_github_deployment",
            source_input_type="loose_address_extractor",
            raw_reference={"extractor_name": "loose_address_extractor"},
        )

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 0
        assert candidate.status == "needs_review"
        assert result["skipped_reasons"]["blocked_source_metadata"] == 1


def test_relations_token_contract_candidate_does_not_get_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            suggested_role="token_contract",
            file_path="deployments/base/usdc/relations.ts",
            raw_reference={"contract_name": "baseToken"},
        )

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 0
        assert candidate.status == "needs_review"
        assert result["skipped_reasons"]["relation_token_contract"] == 1


def test_storage_slot_like_candidate_does_not_get_approved() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            address="0x" + "a" * 64,
            normalized_address="0x" + "a" * 64,
            raw_reference={"raw_key": "STORAGE_SLOT"},
        )

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 0
        assert candidate.status == "needs_review"
        assert result["skipped_reasons"]["storage_slot_like_address"] == 1


def test_scoring_readiness_blocks_auto_approval_even_with_official_evidence() -> None:
    with SessionLocal() as db:
        candidate = _candidate(
            db,
            evidence_type="docs_deployment_source",
            raw_reference={
                "approval_readiness": "needs_review_unverified_source",
                "scored_source_trust": "third_party_unverified",
                "discovery_permission": {"discovery_depth": 0, "approval_readiness": "needs_review_unverified_source"},
            },
        )

        result = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)

        assert result["approved"] == 0
        assert candidate.status == "needs_review"
        assert result["skipped_reasons"]["scoring_needs_review_unverified_source"] == 1


def test_missing_role_network_or_entity_candidates_do_not_get_approved() -> None:
    with SessionLocal() as db:
        _candidate(db, entity_name=None)
        _candidate(db, source_network=None)
        _candidate(db, suggested_role=None)

        result = auto_approve_official_candidates(db, dry_run=False)

        assert result["approved"] == 0
        assert result["skipped_reasons"]["missing_entity"] == 1
        assert result["skipped_reasons"]["missing_network"] == 1
        assert result["skipped_reasons"]["missing_role"] == 1


def test_dry_run_writes_nothing_and_apply_is_idempotent() -> None:
    with SessionLocal() as db:
        candidate = _candidate(db)
        _verify_candidate(db, candidate, source_trust="official_verified")

        dry = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=True)
        db.refresh(candidate)
        first_apply = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)
        db.refresh(candidate)
        second_apply = auto_approve_official_candidates(db, source_job_id=candidate.source_job_id, dry_run=False)

        assert dry["matched"] == 1
        assert dry["approved"] == 0
        assert first_apply["approved"] == 1
        assert second_apply["approved"] == 0
        assert second_apply["skipped_reasons"]["status_not_needs_review"] == 1
        assert candidate.status == "approved"


def test_auto_approve_official_api_endpoint(client: TestClient) -> None:
    with SessionLocal() as db:
        candidate = _candidate(db)
        _verify_candidate(db, candidate, source_trust="official_verified")
        source_job_id = candidate.source_job_id

    dry = client.post("/api/review/auto-approve-official", json={"source_job_id": source_job_id, "dry_run": True})
    apply = client.post("/api/review/auto-approve-official", json={"source_job_id": source_job_id, "dry_run": False})

    assert dry.status_code == 200, dry.text
    assert dry.json()["matched"] == 1
    assert dry.json()["approved"] == 0
    assert apply.status_code == 200, apply.text
    assert apply.json()["approved"] == 1


def _candidate(
    db,
    *,
    source_type: str = "github_directory",
    evidence_type: str = "github_deployment_source",
    source_input_type: str = "github_json_deployment_registry",
    entity_name: str | None = "Compound",
    source_network: str | None = "Base",
    suggested_role: str | None = "lending_market",
    confidence_initial: int = 95,
    file_path: str | None = "deployments/base/usdc/configuration.json",
    address: str = "0x1111111111111111111111111111111111111111",
    normalized_address: str = "0x1111111111111111111111111111111111111111",
    raw_reference: dict | None = None,
) -> AddressCandidate:
    source_job = _source_job(db)
    document = SourceDocument(
        source_job_id=source_job.id,
        canonical_source_url="https://github.com/compound-finance/comet/tree/main/deployments/base/usdc",
        file_path=file_path,
        content_type="application/json",
        document_title="configuration.json",
        text_hash="a" * 64,
        metadata_json={},
    )
    db.add(document)
    db.flush()
    candidate = AddressCandidate(
        source_job_id=source_job.id,
        source_document_id=document.id,
        address=address,
        normalized_address=normalized_address,
        entity_name=entity_name,
        source_network=source_network,
        chain_guess="evm",
        chain_slug="base" if source_network else None,
        chain_id=8453 if source_network else None,
        address_family="evm",
        suggested_role=suggested_role,
        confidence_initial=confidence_initial,
        status="needs_review",
        source_type=source_type,
        source_input_type=source_input_type,
        source_url="https://github.com/compound-finance/comet/tree/main/deployments/base/usdc",
        file_path=file_path,
        evidence_type=evidence_type,
        warnings=[],
        raw_reference=raw_reference or {"contract_name": "comet"},
    )
    db.add(candidate)
    db.flush()
    evidence = AddressEvidence(
        candidate_id=candidate.id,
        source_document_id=document.id,
        evidence_type=evidence_type,
        source_type=source_type,
        final_source_type=source_type,
        adapter_name="github_adapter",
        source_url=candidate.source_url,
        file_path=file_path,
        payload={"raw_reference": candidate.raw_reference, "source_input_type": source_input_type},
        confidence_reason="structured_network_column",
    )
    db.add(evidence)
    db.commit()
    db.refresh(candidate)
    return candidate


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
        input_method="github",
        source_url="https://github.com/compound-finance/comet/tree/main/deployments/base/usdc",
        final_source_type="github_directory",
        adapter_name="github_adapter",
        fingerprint_json={},
        source_artifact_json={},
        profile_json={},
        preview_json={},
        status="needs_review",
    )
    db.add(job)
    db.flush()
    return job
