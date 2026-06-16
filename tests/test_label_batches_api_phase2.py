from __future__ import annotations

import os
from uuid import uuid4

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine, init_db
from app.labels.chain_registry_seed import seed_compact_label_dictionaries
from app.labels.memory_kv_store import DEFAULT_MEMORY_KV_STORE
from app.main import app
from app.models.intake import AddressCandidate, AddressEvidence, IntakePreview, SourceDocument, SourceJob


@pytest.fixture(autouse=True)
def reset_db() -> None:
    DEFAULT_MEMORY_KV_STORE.clear()
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)
    DEFAULT_MEMORY_KV_STORE.clear()


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def test_label_batch_api_dry_run_and_commit_from_candidates(client: TestClient) -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        candidate = _candidate(db)
        db.commit()
        candidate_id = candidate.id

    dry = client.post("/api/label-batches/from-candidates", json={"candidate_ids": [candidate_id], "dry_run": True})
    assert dry.status_code == 200, dry.text
    dry_body = dry.json()
    assert dry_body["status"] == "ready"
    assert dry_body["entries"][0]["key_hex"] == "0064" + "11" * 20
    assert dry_body["entries"][0]["value_hex"] is None

    commit = client.post("/api/label-batches/commit", json={"candidate_ids": [candidate_id], "created_by": "api-test", "approved_by": "api-test"})
    assert commit.status_code == 200, commit.text
    commit_body = commit.json()
    assert commit_body["status"] == "committed"
    assert commit_body["batch_id"] is not None
    assert commit_body["entries"][0]["value_hex"]

    detail = client.get(f"/api/label-batches/{commit_body['batch_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "committed"

    evidence = client.get(f"/api/label-batches/{commit_body['batch_id']}/evidence")
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()[0]["evidence_type"] == "candidate_evidence_bundle"


def _candidate(db) -> AddressCandidate:
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
        final_source_type="excel_upload",
        adapter_name="excel_csv_adapter",
        fingerprint_json={},
        source_artifact_json={},
        profile_json={},
        preview_json={},
        status="completed",
    )
    db.add(job)
    db.flush()
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
        address="0x1111111111111111111111111111111111111111",
        normalized_address="0x1111111111111111111111111111111111111111",
        entity_name="Bybit",
        source_network="Ethereum",
        chain_guess="evm",
        chain_slug="ethereum",
        chain_id=1,
        address_family="evm",
        suggested_role="cex_por_wallet",
        confidence_initial=95,
        status="approved",
        source_type="excel_upload",
        source_input_type="xlsx_multi_sheet_registry",
        source_url="source",
        file_path=document.file_path,
        evidence_type="audited_wallet",
        warnings=[],
        raw_reference={"row_protocol": "Bybit", "row_category": "cex"},
    )
    db.add(candidate)
    db.flush()
    db.add(
        AddressEvidence(
            candidate_id=candidate.id,
            source_document_id=document.id,
            evidence_type="audited_wallet",
            source_type="excel_upload",
            final_source_type="excel_upload",
            adapter_name="excel_csv_adapter",
            source_url="source",
            file_path=document.file_path,
            payload={"raw_reference": candidate.raw_reference},
            confidence_reason="structured_network_column",
        )
    )
    db.flush()
    return candidate
