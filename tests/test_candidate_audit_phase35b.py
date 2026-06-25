from __future__ import annotations

import os
from uuid import uuid4

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"
os.environ["MQCHAIN_AI_STAGED_ARTIFACT_DIR"] = "./data/test_staged_artifacts"

import pytest
from fastapi.testclient import TestClient

from app.db.database import Base, SessionLocal, engine, init_db
from app.ingestion.network_normalizer import NetworkNormalizer
from app.main import app
from app.models.intake import AddressCandidate, AddressEvidence, IntakePreview, SourceDocument, SourceJob
from app.review.candidate_audit import audit_candidates, classify_candidate_address_class
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


def test_audit_scans_all_candidates_and_counts_core_dimensions() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, suggested_role="staking_deposit_wallet", evidence_type="audited_wallet", source_input_type="xlsx_multi_sheet_registry")
        _candidate(db, job, suggested_role="staking_withdrawal_wallet", evidence_type="audited_wallet", source_input_type="xlsx_multi_sheet_registry")
        _candidate(db, job, suggested_role="cex_por_wallet", source_type="por_pdf", evidence_type="audited_wallet", source_input_type="pdf_audited_wallet_table")
        _candidate(db, job, suggested_role="wallet_address_from_explorer_link", evidence_type="source_extraction_context", source_input_type="xlsx_multi_sheet_registry")

        report = audit_candidates(db, source_job_id=job.id, limit_samples=2)

        assert report["total_candidates"] == 4
        assert report["total_evidence"] == 4
        assert report["evidence_per_candidate_ratio"] == 1
        assert report["source_job_count"] == 1
        assert report["source_job_ids"] == [job.id]
        assert report["count_by_suggested_role"]["staking_deposit_wallet"] == 1
        assert report["count_by_suggested_role"]["staking_withdrawal_wallet"] == 1
        assert report["count_by_evidence_type"]["audited_wallet"] == 3
        assert report["count_by_source_input_type"]["xlsx_multi_sheet_registry"] == 3
        assert report["count_by_status"]["needs_review"] == 4
        assert report["unique_candidate_count"] == 4
        assert len(report["sample_candidates"]) == 2


def test_count_by_address_class_and_classification_mapping() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        deposit = _candidate(db, job, suggested_role="staking_deposit_wallet")
        withdrawal = _candidate(db, job, suggested_role="staking_withdrawal_wallet")
        reserve = _candidate(db, job, suggested_role="cex_por_wallet", source_type="por_pdf", evidence_type="audited_wallet", source_input_type="pdf_audited_wallet_table")
        explorer = _candidate(db, job, suggested_role="wallet_address_from_explorer_link")

        report = audit_candidates(db, source_job_id=job.id)

        assert classify_candidate_address_class(deposit) == "staking_deposit_wallet"
        assert classify_candidate_address_class(withdrawal) == "staking_withdrawal_wallet"
        assert classify_candidate_address_class(reserve) == "cex_reserve_wallet"
        assert classify_candidate_address_class(explorer) == "explorer_link_only"
        assert report["count_by_address_class"]["staking_deposit_wallet"] == 1
        assert report["count_by_address_class"]["staking_withdrawal_wallet"] == 1
        assert report["count_by_address_class"]["cex_reserve_wallet"] == 1
        assert report["count_by_address_class"]["explorer_link_only"] == 1


def test_missing_fields_evidence_and_duplicates_are_counted() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, entity_name=None, source_network=None, suggested_role=None, address="", normalized_address="", with_evidence=False)
        _candidate(db, job, address="0x2222222222222222222222222222222222222222", normalized_address="0x2222222222222222222222222222222222222222")
        _candidate(db, job, address="0x2222222222222222222222222222222222222222", normalized_address="0x2222222222222222222222222222222222222222")

        report = audit_candidates(db, source_job_id=job.id)

        assert report["missing_entity_count"] == 1
        assert report["missing_network_count"] == 1
        assert report["missing_role_count"] == 1
        assert report["missing_address_count"] == 1
        assert report["missing_evidence_count"] == 1
        assert report["duplicate_count"] == 1
        assert report["duplicate_row_count"] == 1
        assert report["duplicate_group_count"] == 1
        assert report["max_duplicate_group_size"] == 2
        assert report["duplicate_samples"][0]["count"] == 2
        assert report["duplicate_groups_top"][0]["count"] == 2
        assert "candidates_missing_evidence" in report["warnings"]


def test_auto_approvable_preview_does_not_mutate_db() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        core = _candidate(db, job, suggested_role="lending_market", evidence_type="github_deployment_source", source_input_type="github_json_deployment_registry")
        reserve = _candidate(db, job, suggested_role="cex_por_wallet", source_type="por_pdf", evidence_type="pdf_por_document", source_input_type="pdf_audited_wallet_table")
        _verify_candidate(db, core, source_trust="official_verified")
        _verify_candidate(db, reserve, source_trust="third_party_audit")
        _candidate(db, job, suggested_role="cex_hot_wallet", evidence_type="audited_wallet", source_input_type="xlsx_multi_sheet_registry")
        _candidate(db, job, suggested_role="wallet_address_from_explorer_link", evidence_type="source_extraction_context")

        report = audit_candidates(db, source_job_id=job.id)
        db.refresh(core)
        db.refresh(reserve)

        assert report["auto_approvable_count"] == 2
        assert report["needs_review_count"] == 1
        assert report["blocked_count"] == 1
        assert report["count_by_review_bucket"]["ready_for_approval_core_protocol"] == 1
        assert report["count_by_review_bucket"]["ready_for_approval_cex_reserve"] == 1
        assert report["count_by_review_bucket"]["needs_review_hot_cold_wallet"] == 1
        assert report["count_by_review_bucket"]["not_auto_approvable_explorer_link_only"] == 1
        assert core.status == "needs_review"
        assert reserve.status == "needs_review"


def test_relation_dependency_and_low_confidence_buckets() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        relation = _candidate(
            db,
            job,
            suggested_role="token_contract",
            source_input_type="github_typescript_relation_map",
            raw_reference={"ownership_boundary": "external_or_related"},
        )
        low = _candidate(db, job, suggested_role="cex_por_wallet", confidence_initial=60)
        _verify_candidate(db, low, source_trust="third_party_audit")

        report = audit_candidates(db, source_job_id=job.id)

        assert classify_candidate_address_class(relation) == "protocol_relation_dependency"
        assert report["count_by_review_bucket"]["needs_review_generic_wallet"] == 1
        assert report["count_by_review_bucket"]["needs_review_official_low_confidence"] == 1


def test_network_labels_are_normalized_for_audit_counts() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, source_network="Ethereum / ETH Staking", chain_slug=None)
        _candidate(db, job, source_network="BTC", chain_slug=None, address="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080", normalized_address="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080")
        _candidate(db, job, source_network="BNB Chain", chain_slug=None)
        _candidate(db, job, source_network="TRX", chain_slug=None, address="TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj", normalized_address="TXLAQ63Xg1NAzckPwKHvzw7CSEmLMEqcdj")

        report = audit_candidates(db, source_job_id=job.id)

        assert report["count_by_source_network"]["Ethereum / ETH Staking"] == 1
        assert report["count_by_chain_slug"]["ethereum"] == 1
        assert report["count_by_chain_slug"]["bitcoin"] == 1
        assert report["count_by_chain_slug"]["bsc"] == 1
        assert report["count_by_chain_slug"]["tron"] == 1


@pytest.mark.parametrize(
    ("label", "chain_slug"),
    [
        ("Ethereum / ETH Staking", "ethereum"),
        ("Ethereum / EVM", "ethereum"),
        ("Ethereum / EVM (masked)", "ethereum"),
        ("ETH", "ethereum"),
        ("ETH/ERC20", "ethereum"),
        ("BNB Chain", "bsc"),
        ("BNB Smart Chain / BEP20", "bsc"),
        ("BTC", "bitcoin"),
        ("TRX", "tron"),
        ("XRP Ledger", "xrp"),
        ("Avalanche C-Chain / AVAXC", "avalanche-c"),
        ("MATIC", "polygon"),
        ("SOL", "solana"),
        ("LTC", "litecoin"),
        ("DOGE", "dogecoin"),
    ],
)
def test_required_network_aliases_normalize(label: str, chain_slug: str) -> None:
    assert NetworkNormalizer.normalize(label).canonical_chain == chain_slug


def test_duplicate_grouping_and_staking_compression_counts() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        for _ in range(3):
            _candidate(db, job, suggested_role="staking_deposit_wallet", address="0x3333333333333333333333333333333333333333", normalized_address="0x3333333333333333333333333333333333333333")
        for _ in range(2):
            _candidate(db, job, suggested_role="staking_withdrawal_wallet", address="0x4444444444444444444444444444444444444444", normalized_address="0x4444444444444444444444444444444444444444")

        report = audit_candidates(db, source_job_id=job.id)

        assert report["total_candidates"] == 5
        assert report["unique_candidate_count"] == 2
        assert report["duplicate_row_count"] == 3
        assert report["duplicate_group_count"] == 2
        assert report["max_duplicate_group_size"] == 3
        assert report["count_by_unique_address_class"]["staking_deposit_wallet"] == 1
        assert report["staking_unique_candidate_count"] == 2
        assert report["staking_raw_row_count"] == 5
        assert report["staking_duplicate_row_count"] == 3
        assert report["staking_group_samples"][0]["count"] == 3
        assert report["count_by_review_bucket"]["needs_review_staking_mapping"] == 5


def test_refined_review_buckets_and_no_mutation() -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        reserve = _candidate(db, job, suggested_role="cex_por_wallet", confidence_initial=60)
        hacken_reserve = _candidate(db, job, suggested_role="cex_por_wallet", evidence_type="pdf_por_document", confidence_initial=60)
        hot = _candidate(db, job, suggested_role="cex_hot_wallet", confidence_initial=60)
        cold = _candidate(db, job, suggested_role="cex_cold_wallet", confidence_initial=60)
        explorer = _candidate(db, job, suggested_role="wallet_address_from_explorer_link", confidence_initial=60)
        unknown = _candidate(db, job, suggested_role="mystery_role", confidence_initial=95)
        _verify_candidate(db, reserve, source_trust="third_party_audit")
        _verify_candidate(db, hacken_reserve, source_trust="third_party_audit")
        _verify_candidate(db, unknown, source_trust="official_verified")
        before = {candidate.id: candidate.status for candidate in [reserve, hacken_reserve, hot, cold, explorer, unknown]}

        report = audit_candidates(db, source_job_id=job.id)
        after = {candidate.id: db.get(AddressCandidate, candidate.id).status for candidate in [reserve, hacken_reserve, hot, cold, explorer, unknown]}

        assert report["count_by_review_bucket"]["needs_review_official_low_confidence"] == 2
        assert report["count_by_review_bucket"]["needs_review_hot_cold_wallet"] == 2
        assert report["count_by_review_bucket"]["not_auto_approvable_explorer_link_only"] == 1
        assert report["count_by_review_bucket"]["needs_review_unmapped_official_role"] == 1
        assert before == after


def test_candidate_audit_api_endpoint(client: TestClient) -> None:
    with SessionLocal() as db:
        job = _source_job(db)
        _candidate(db, job, suggested_role="staking_deposit_wallet")
        source_job_id = job.id

    response = client.post("/api/review/candidate-audit", json={"source_job_id": source_job_id, "limit_samples": 1})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_candidates"] == 1
    assert body["count_by_address_class"]["staking_deposit_wallet"] == 1
    assert len(body["sample_candidates"]) == 1


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
    confidence_initial: int = 95,
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
