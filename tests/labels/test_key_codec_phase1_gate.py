from __future__ import annotations

import os

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"

import pytest
from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine, init_db
from app.labels.chain_registry_seed import KEY_PREFIX_SEEDS, active_key_prefixes, seed_compact_label_dictionaries
from app.labels.key_codec import AddressCodecError, encode_address_key
from app.models.compact_label import KeyPrefixDict
from scripts.compact_label_phase1_report import build_phase1_report


@pytest.fixture(autouse=True)
def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


def test_phase1_gate_has_fixture_for_every_active_prefix() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        report = build_phase1_report(db, pytest_result="not_run_in_unit")

    assert report["total_prefix_rows"] == 39
    assert report["active_prefix_count"] == 32
    assert report["planned_prefix_count"] == 7
    assert report["experimental_prefix_count"] == 0
    assert report["disabled_prefix_count"] == 0
    assert report["active_prefixes_with_key_hex_fixture"] == report["active_prefix_count"]
    assert report["active_prefixes_with_invalid_address_test"] == report["active_prefix_count"]
    assert report["active_prefixes_with_round_trip_test"] == report["active_prefix_count"]
    assert report["gate_passed"] is True
    assert all(fixture["test_status"] == "pass" for fixture in report["fixtures"])


def test_same_evm_address_uses_different_prefix_for_each_active_evm_chain() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        report = build_phase1_report(db)

    evm = [fixture for fixture in report["fixtures"] if fixture["codec"] == "evm_hex_20"]
    required = {"ethereum", "polygon", "base", "arbitrum_one", "optimism", "bsc"}
    assert required <= {fixture["chain_code"] for fixture in evm}
    assert {fixture["payload_hex"] for fixture in evm} == {"11" * 20}
    assert len({fixture["prefix_hex"] for fixture in evm}) == len(evm)
    assert len({fixture["full_key_hex"] for fixture in evm}) == len(evm)


def test_planned_prefixes_are_not_active() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        planned_rows = db.scalars(select(KeyPrefixDict).where(KeyPrefixDict.codec_status == "planned")).all()

    assert {row.chain_code for row in planned_rows} == {"xrp", "cosmos", "osmosis", "kujira", "celestia", "injective", "ton"}
    assert all(row.is_active is False for row in planned_rows)


def test_key_codec_rejects_invalid_fixture_for_every_active_prefix() -> None:
    seeds_by_prefix = {seed["prefix_code"]: seed for seed in KEY_PREFIX_SEEDS}
    for prefix in active_key_prefixes():
        invalid = seeds_by_prefix[prefix.prefix_code]["invalid_address"]
        with pytest.raises(AddressCodecError):
            encode_address_key(prefix, invalid)


def test_family_specific_payload_shapes() -> None:
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        report = build_phase1_report(db)

    by_family = {fixture["address_family"]: fixture for fixture in report["fixtures"]}
    assert by_family["btc_p2pkh"]["payload_hex"].startswith("00")
    assert by_family["btc_p2pkh"]["payload_len"] == 21
    assert by_family["btc_p2sh"]["payload_hex"].startswith("05")
    assert by_family["btc_bech32_v0_p2wpkh"]["payload_hex"].startswith("00")
    assert by_family["btc_bech32_v0_p2wpkh"]["payload_len"] == 21
    assert by_family["btc_bech32_v0_p2wsh"]["payload_len"] == 33
    assert by_family["btc_bech32m_v1_p2tr"]["payload_hex"].startswith("01")
    assert by_family["tron_base58check_21"]["payload_hex"].startswith("41")
    assert by_family["solana_account_32"]["payload_len"] == 32
    assert by_family["aptos_address_32"]["payload_len"] == 32
    assert by_family["sui_address_32"]["payload_len"] == 32
