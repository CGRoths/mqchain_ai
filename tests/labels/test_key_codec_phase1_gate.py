from __future__ import annotations

import os

os.environ["MQCHAIN_AI_DATABASE_URL"] = "sqlite:///./data/test_mqchain_ai.db"

import pytest
from sqlalchemy import select

from app.db.database import Base, SessionLocal, engine, init_db
from app.labels.chain_registry_seed import KEY_PREFIX_SEEDS, active_key_prefixes, seed_compact_label_dictionaries
from app.labels.key_codec import AddressCodecError, KeyCodecError, encode_address_key
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


def test_xdc_prefixed_input_is_rejected_for_active_non_xdc_evm_prefixes() -> None:
    xdc_address = "xdcF29f049144467b3dc55e19205c30C1737942F23a"
    tested = []
    for prefix in active_key_prefixes():
        if prefix.codec != "evm_hex_20" or prefix.chain_code == "xdc":
            continue
        tested.append(prefix.chain_code)
        with pytest.raises(KeyCodecError, match="xdc_prefixed_address_requires_active_xdc_prefix"):
            encode_address_key(prefix, xdc_address)

    assert {"ethereum", "polygon", "base", "arbitrum_one", "optimism", "bsc"} <= set(tested)


def test_known_real_world_address_fixtures_encode_exact_keys() -> None:
    cases = [
        (
            "btc",
            "btc_p2pkh",
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
            "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
            "0062e907b15cbf27d5425399ebf6f0fb50ebb88f18",
            "00100062e907b15cbf27d5425399ebf6f0fb50ebb88f18",
        ),
        (
            "btc",
            "btc_p2sh",
            "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
            "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
            "05b472a266d0bd89c13706a4132ccfb16f7c3b9fcb",
            "001105b472a266d0bd89c13706a4132ccfb16f7c3b9fcb",
        ),
        (
            "ethereum",
            "evm_20",
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            "0064a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        ),
        (
            "polygon",
            "evm_20",
            "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
            "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
            "2791bca1f2de4661ed88a30c99a7a9449aa84174",
            "00662791bca1f2de4661ed88a30c99a7a9449aa84174",
        ),
        (
            "base",
            "evm_20",
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "0069833589fcd6edb6e08f4c7c32d4f71b54bda02913",
        ),
        (
            "solana",
            "solana_account_32",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
            "c6fa7af3bedbad3a3d65f36aabc97431b1bbe4c2d2f6e0e47ca60203452f5d61",
            "00c8c6fa7af3bedbad3a3d65f36aabc97431b1bbe4c2d2f6e0e47ca60203452f5d61",
        ),
        (
            "tron",
            "tron_base58check_21",
            "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
            "41a614f803b6fd780986a42c78ec9c7f77e6ded13c",
            "00c941a614f803b6fd780986a42c78ec9c7f77e6ded13c",
        ),
    ]

    for chain_code, address_family, address, normalized, payload_hex, full_key_hex in cases:
        prefix = _active_prefix(chain_code, address_family)
        encoded = encode_address_key(prefix, address)
        assert encoded.normalized_display == normalized
        assert encoded.payload_hex == payload_hex
        assert encoded.full_key_hex == full_key_hex


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


def _active_prefix(chain_code: str, address_family: str):
    for prefix in active_key_prefixes():
        if prefix.chain_code == chain_code and prefix.address_family == address_family:
            return prefix
    raise AssertionError(f"missing active prefix: {chain_code}:{address_family}")
