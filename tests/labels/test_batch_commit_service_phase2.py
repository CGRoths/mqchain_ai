from __future__ import annotations

import os
from uuid import uuid4

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"

import pytest
from sqlalchemy import func, select

from app.db.database import Base, SessionLocal, engine, init_db
from app.labels.batch_commit_service import BatchCommitOptions, BatchCommitService
from app.labels.chain_registry_seed import seed_compact_label_dictionaries
from app.labels.kv_store import LABEL_CURRENT_CF
from app.labels.memory_kv_store import MemoryKVStore
from app.labels.value_codec import CurrentLabelValue, pack_current_value, unpack_current_value
from app.models.compact_label import LabelBatch, LabelBatchEvidence, Protocol, RoleProposal
from app.models.intake import AddressCandidate, AddressEvidence, Entity, IntakePreview, SourceDocument, SourceJob


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


def test_approved_candidate_dry_run_returns_key_and_value_preview() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        entity = Entity(entity_name="Bybit", entity_slug="bybit")
        protocol = Protocol(protocol_name="Bybit", protocol_slug="bybit")
        db.add_all([entity, protocol])
        db.flush()
        candidate = _candidate(db, entity_name="Bybit", suggested_role="cex_por_wallet")
        db.commit()

        result = BatchCommitService(db, store).dry_run_from_candidates(BatchCommitOptions(candidate_ids=[candidate.id]))

    entry = result["entries"][0]
    assert result["status"] == "ready"
    assert result["dry_run"] is True
    assert result["accepted_count"] == 1
    assert entry["key_hex"] == "0064" + "11" * 20
    assert entry["value_hex"] is None
    assert entry["batch_id"] is None
    assert entry["entity_id"] == entity.id
    assert entry["protocol_id"] == protocol.id
    assert entry["role_id"] == 100
    assert entry["label_status"] == 1


def test_dry_run_detects_same_key_conflict_against_memory_kv() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        candidate = _candidate(db, confidence_initial=70)
        db.commit()
        service = BatchCommitService(db, store)
        ready = service.dry_run_from_candidates(BatchCommitOptions(candidate_ids=[candidate.id]))
        key = bytes.fromhex(ready["entries"][0]["key_hex"])
        store.put(
            LABEL_CURRENT_CF,
            key,
            pack_current_value(
                CurrentLabelValue(
                    schema_version=1,
                    confidence_score=95,
                    label_status=1,
                    quality_tier=1,
                    entity_id=999,
                    protocol_id=999,
                    role_id=101,
                    flags=1 << 6,
                    batch_id=123,
                    first_seen_block_or_slot=0,
                    last_seen_block_or_slot=0,
                )
            ),
        )

        conflict = service.dry_run_from_candidates(BatchCommitOptions(candidate_ids=[candidate.id]))

    assert conflict["status"] == "blocked"
    assert conflict["conflict_count"] == 1
    assert conflict["conflicts"][0]["key_hex"] == ready["entries"][0]["key_hex"]
    assert "overlapping_label_conflict" in conflict["conflicts"][0]["reason"]
    assert "lower_confidence_would_overwrite_official_label" in conflict["conflicts"][0]["reason"]


def test_commit_creates_batch_evidence_and_writes_decodable_memory_kv_value() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        candidate = _candidate(db, entity_name="Bybit", suggested_role="cex_por_wallet")
        db.commit()

        result = BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate.id], created_by="test", approved_by="test"))
        batch = db.get(LabelBatch, result["batch_id"])
        evidence = db.scalar(select(LabelBatchEvidence).where(LabelBatchEvidence.batch_id == result["batch_id"]))
        value_bytes = store.get(LABEL_CURRENT_CF, bytes.fromhex(result["entries"][0]["key_hex"]))
        decoded = unpack_current_value(value_bytes or b"")

    assert result["status"] == "committed"
    assert batch is not None
    assert batch.status == "committed"
    assert batch.accepted_count == 1
    assert evidence is not None
    assert evidence.evidence_type == "candidate_evidence_bundle"
    assert value_bytes is not None
    assert decoded.entity_id == result["entries"][0]["entity_id"]
    assert decoded.protocol_id == result["entries"][0]["protocol_id"]
    assert decoded.role_id == 100
    assert decoded.label_status == 1
    assert decoded.quality_tier == 1
    assert decoded.flags == 69
    assert decoded.batch_id == result["batch_id"]
    assert decoded.batch_id > 0


def test_unknown_role_creates_proposal_and_blocks_commit() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        candidate = _candidate(db, suggested_role="emission_manager")
        db.commit()

        result = BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate.id]))
        proposal = db.scalar(select(RoleProposal).where(RoleProposal.proposed_role_code == "emission_manager"))

    assert result["status"] == "blocked"
    assert result["blocked_count"] == 1
    assert result["blockers"][0]["reason"] == "unknown_role_created_proposal"
    assert proposal is not None
    assert proposal.status == "pending"
    assert proposal.candidate_count == 1


def test_entity_existing_reuse_and_entity_missing_create_deterministic_slug() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        existing = Entity(entity_name="Existing Entity", entity_slug="existing-entity")
        db.add(existing)
        db.flush()
        existing_id = existing.id
        candidate_existing = _candidate(db, entity_name="Existing Entity", address="0x" + "2" * 40, normalized_address="0x" + "2" * 40)
        candidate_missing = _candidate(db, entity_name="New Entity", address="0x" + "3" * 40, normalized_address="0x" + "3" * 40)
        db.commit()

        reused = BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate_existing.id], protocol_name="Existing Entity"))
        created = BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate_missing.id], protocol_name="New Entity"))
        created_entity = db.scalar(select(Entity).where(Entity.entity_slug == "new-entity"))

    assert reused["entries"][0]["entity_id"] == existing_id
    assert reused["entries"][0]["entity_created"] is False
    assert created_entity is not None
    assert created_entity.entity_name == "New Entity"
    assert created["entries"][0]["entity_id"] == created_entity.id
    assert created["entries"][0]["entity_created"] is True


def test_protocol_existing_reuse_and_protocol_missing_create_deterministic_slug() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        existing_protocol = Protocol(protocol_name="Existing Protocol", protocol_slug="existing-protocol")
        db.add(existing_protocol)
        db.flush()
        existing_protocol_id = existing_protocol.id
        candidate_existing = _candidate(db, entity_name="Protocol Entity", address="0x" + "4" * 40, normalized_address="0x" + "4" * 40)
        candidate_missing = _candidate(db, entity_name="Protocol Entity", address="0x" + "5" * 40, normalized_address="0x" + "5" * 40)
        db.commit()

        reused = BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate_existing.id], protocol_name="Existing Protocol"))
        created = BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate_missing.id], protocol_name="Missing Protocol"))
        created_protocol = db.scalar(select(Protocol).where(Protocol.protocol_slug == "missing-protocol"))

    assert reused["entries"][0]["protocol_id"] == existing_protocol_id
    assert reused["entries"][0]["protocol_created"] is False
    assert created_protocol is not None
    assert created_protocol.protocol_name == "Missing Protocol"
    assert created["entries"][0]["protocol_id"] == created_protocol.id
    assert created["entries"][0]["protocol_created"] is True


def test_commit_does_not_insert_mass_rows_into_curated_registry() -> None:
    store = MemoryKVStore()
    with SessionLocal() as db:
        _seed(db)
        candidate = _candidate(db)
        db.commit()

        BatchCommitService(db, store).commit_from_candidates(BatchCommitOptions(candidate_ids=[candidate.id]))

        from app.models.intake import RegistryAddress

        assert db.scalar(select(func.count(RegistryAddress.id))) == 0


def _seed(db) -> None:
    seed_compact_label_dictionaries(db)
    db.flush()


def _candidate(
    db,
    *,
    entity_name: str = "Bybit",
    suggested_role: str = "cex_por_wallet",
    confidence_initial: int = 95,
    address: str = "0x1111111111111111111111111111111111111111",
    normalized_address: str = "0x1111111111111111111111111111111111111111",
) -> AddressCandidate:
    job = _source_job(db)
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
        source_network="Ethereum",
        chain_guess="evm",
        chain_slug="ethereum",
        chain_id=1,
        address_family="evm",
        suggested_role=suggested_role,
        confidence_initial=confidence_initial,
        status="approved",
        source_type="excel_upload",
        source_input_type="xlsx_multi_sheet_registry",
        source_url="source",
        file_path=document.file_path,
        evidence_type="audited_wallet",
        warnings=[],
        raw_reference={"row_protocol": entity_name, "row_category": "cex"},
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
    return job
