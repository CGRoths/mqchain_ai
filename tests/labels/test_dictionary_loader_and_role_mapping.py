from __future__ import annotations

import os

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"

import pytest
from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine, init_db
from app.labels.chain_registry_seed import seed_compact_label_dictionaries
from app.labels.dictionary_loader import DictionaryMismatchError, freeze_dictionary_version, load_dictionary_snapshot
from app.labels.role_mapping import map_role_or_propose
from app.models.compact_label import KeyPrefixDict, RoleProposal


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


def test_dictionary_version_mismatch_fails_fast() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        freeze_dictionary_version(db, "phase1")
        row = db.scalar(select(KeyPrefixDict).where(KeyPrefixDict.chain_code == "ethereum"))
        assert row is not None
        row.native_symbol = "DRIFT"
        db.flush()

        with pytest.raises(DictionaryMismatchError, match="dictionary_hash_mismatch:key_prefix_hash"):
            load_dictionary_snapshot(db, "phase1")


def test_dictionary_version_loads_when_hashes_match() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        version = freeze_dictionary_version(db, "phase1")

        snapshot = load_dictionary_snapshot(db, "phase1")

    assert snapshot.key_prefix_hash == version.key_prefix_hash
    assert snapshot.role_dict_hash == version.role_dict_hash


def test_role_mapping_reuses_known_role_and_proposes_unknown_role() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)

        known = map_role_or_propose(db, "factory_contract", source_job_id=1, example_addresses=["0x" + "1" * 40])
        unknown = map_role_or_propose(db, "emission_manager", source_job_id=1, example_addresses=["0x" + "2" * 40])
        repeated = map_role_or_propose(db, "emission_manager", source_job_id=1, example_addresses=["0x" + "3" * 40])
        proposal = db.get(RoleProposal, unknown.proposal_id)

    assert known.can_commit is True
    assert known.role_code == "protocol_factory"
    assert known.proposal_id is None
    assert unknown.can_commit is False
    assert unknown.status == "proposal_created"
    assert repeated.proposal_id == unknown.proposal_id
    assert proposal is not None
    assert proposal.proposed_role_code == "emission_manager"
    assert proposal.status == "pending"
    assert proposal.candidate_count == 2
    assert proposal.example_addresses_json == ["0x" + "2" * 40, "0x" + "3" * 40]
