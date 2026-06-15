from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.labels.key_codec import KeyPrefix, make_base58_address, make_base58check_address, make_btc_witness_address
from app.models.compact_label import KeyPrefixDict, RoleDict


SAMPLE_EVM_ADDRESS = "0x1111111111111111111111111111111111111111"
INVALID_EVM_ADDRESS = "0x1234"


def _active_seed(
    *,
    prefix_code: int,
    chain_id: int,
    chain_code: str,
    chain_name: str,
    chain_family: str,
    address_family: str,
    codec: str,
    payload_len: int,
    sample_address: str,
    invalid_address: str,
    evm_chain_id: int | None = None,
    slip44_id: int | None = None,
    native_symbol: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "prefix_code": prefix_code,
        "chain_id": chain_id,
        "chain_code": chain_code,
        "chain_name": chain_name,
        "chain_family": chain_family,
        "address_family": address_family,
        "codec": codec,
        "codec_status": "active",
        "payload_len": payload_len,
        "evm_chain_id": evm_chain_id,
        "slip44_id": slip44_id,
        "native_symbol": native_symbol,
        "description": description,
        "is_active": True,
        "sample_address": sample_address,
        "invalid_address": invalid_address,
    }


def _planned_seed(
    *,
    prefix_code: int,
    chain_id: int,
    chain_code: str,
    chain_name: str,
    chain_family: str,
    address_family: str,
    codec: str,
    payload_len: int | None,
    native_symbol: str | None = None,
    description: str | None = None,
    codec_status: str = "planned",
) -> dict[str, Any]:
    return {
        "prefix_code": prefix_code,
        "chain_id": chain_id,
        "chain_code": chain_code,
        "chain_name": chain_name,
        "chain_family": chain_family,
        "address_family": address_family,
        "codec": codec,
        "codec_status": codec_status,
        "payload_len": payload_len,
        "evm_chain_id": None,
        "slip44_id": None,
        "native_symbol": native_symbol,
        "description": description,
        "is_active": False,
        "sample_address": None,
        "invalid_address": None,
    }


def _evm_seed(prefix_code: int, chain_id: int, chain_code: str, chain_name: str, evm_chain_id: int, native_symbol: str) -> dict[str, Any]:
    return _active_seed(
        prefix_code=prefix_code,
        chain_id=chain_id,
        chain_code=chain_code,
        chain_name=chain_name,
        chain_family="evm",
        address_family="evm_20",
        codec="evm_hex_20",
        payload_len=20,
        sample_address=SAMPLE_EVM_ADDRESS,
        invalid_address=INVALID_EVM_ADDRESS,
        evm_chain_id=evm_chain_id,
        native_symbol=native_symbol,
        description=f"{chain_name} EVM account/contract address",
    )


KEY_PREFIX_SEEDS: list[dict[str, Any]] = [
    _active_seed(
        prefix_code=16,
        chain_id=1,
        chain_code="btc",
        chain_name="Bitcoin",
        chain_family="bitcoin",
        address_family="btc_p2pkh",
        codec="btc_base58check",
        payload_len=21,
        sample_address=make_base58check_address(b"\x00" + b"\x11" * 20),
        invalid_address=make_base58check_address(b"\x05" + b"\x11" * 20),
        slip44_id=0,
        native_symbol="BTC",
        description="Bitcoin legacy P2PKH address",
    ),
    _active_seed(
        prefix_code=17,
        chain_id=1,
        chain_code="btc",
        chain_name="Bitcoin",
        chain_family="bitcoin",
        address_family="btc_p2sh",
        codec="btc_base58check",
        payload_len=21,
        sample_address=make_base58check_address(b"\x05" + b"\x22" * 20),
        invalid_address=make_base58check_address(b"\x00" + b"\x22" * 20),
        slip44_id=0,
        native_symbol="BTC",
        description="Bitcoin legacy P2SH address",
    ),
    _active_seed(
        prefix_code=18,
        chain_id=1,
        chain_code="btc",
        chain_name="Bitcoin",
        chain_family="bitcoin",
        address_family="btc_bech32_v0_p2wpkh",
        codec="btc_bech32",
        payload_len=21,
        sample_address=make_btc_witness_address(0, b"\x33" * 20),
        invalid_address=make_btc_witness_address(0, b"\x33" * 32),
        slip44_id=0,
        native_symbol="BTC",
        description="Bitcoin SegWit v0 P2WPKH address",
    ),
    _active_seed(
        prefix_code=19,
        chain_id=1,
        chain_code="btc",
        chain_name="Bitcoin",
        chain_family="bitcoin",
        address_family="btc_bech32_v0_p2wsh",
        codec="btc_bech32",
        payload_len=33,
        sample_address=make_btc_witness_address(0, b"\x44" * 32),
        invalid_address=make_btc_witness_address(0, b"\x44" * 20),
        slip44_id=0,
        native_symbol="BTC",
        description="Bitcoin SegWit v0 P2WSH address",
    ),
    _active_seed(
        prefix_code=20,
        chain_id=1,
        chain_code="btc",
        chain_name="Bitcoin",
        chain_family="bitcoin",
        address_family="btc_bech32m_v1_p2tr",
        codec="btc_bech32m",
        payload_len=33,
        sample_address=make_btc_witness_address(1, b"\x55" * 32),
        invalid_address=make_btc_witness_address(0, b"\x55" * 32),
        slip44_id=0,
        native_symbol="BTC",
        description="Bitcoin Taproot v1 P2TR address",
    ),
    *[
        _evm_seed(prefix, chain_id, code, name, evm_id, symbol)
        for prefix, chain_id, code, name, evm_id, symbol in [
            (100, 10, "ethereum", "Ethereum", 1, "ETH"),
            (101, 11, "bsc", "BNB Smart Chain", 56, "BNB"),
            (102, 12, "polygon", "Polygon", 137, "MATIC"),
            (103, 13, "arbitrum_one", "Arbitrum One", 42161, "ETH"),
            (104, 14, "optimism", "Optimism", 10, "ETH"),
            (105, 15, "base", "Base", 8453, "ETH"),
            (106, 16, "avalanche_c", "Avalanche C-Chain", 43114, "AVAX"),
            (107, 17, "fantom", "Fantom", 250, "FTM"),
            (108, 18, "gnosis", "Gnosis", 100, "XDAI"),
            (109, 19, "linea", "Linea", 59144, "ETH"),
            (110, 20, "scroll", "Scroll", 534352, "ETH"),
            (111, 21, "zksync_era", "zkSync Era", 324, "ETH"),
            (112, 22, "blast", "Blast", 81457, "ETH"),
            (113, 23, "mantle", "Mantle", 5000, "MNT"),
            (114, 24, "celo", "Celo", 42220, "CELO"),
            (115, 25, "moonbeam", "Moonbeam", 1284, "GLMR"),
            (116, 26, "moonriver", "Moonriver", 1285, "MOVR"),
            (117, 27, "cronos", "Cronos", 25, "CRO"),
            (118, 28, "metis", "Metis", 1088, "METIS"),
            (119, 29, "manta_pacific", "Manta Pacific", 169, "ETH"),
            (120, 30, "mode", "Mode", 34443, "ETH"),
            (121, 31, "zora", "Zora", 7777777, "ETH"),
            (122, 32, "taiko", "Taiko", 167000, "ETH"),
        ]
    ],
    _active_seed(
        prefix_code=200,
        chain_id=40,
        chain_code="solana",
        chain_name="Solana",
        chain_family="solana",
        address_family="solana_account_32",
        codec="solana_base58_32",
        payload_len=32,
        sample_address=make_base58_address(b"\x77" * 32),
        invalid_address="1111",
        slip44_id=501,
        native_symbol="SOL",
        description="Solana 32-byte account address",
    ),
    _active_seed(
        prefix_code=201,
        chain_id=41,
        chain_code="tron",
        chain_name="Tron",
        chain_family="tron",
        address_family="tron_base58check_21",
        codec="tron_base58check_21",
        payload_len=21,
        sample_address=make_base58check_address(b"\x41" + b"\x66" * 20),
        invalid_address=make_base58check_address(b"\x00" + b"\x66" * 20),
        slip44_id=195,
        native_symbol="TRX",
        description="Tron base58check account address with 0x41 version byte",
    ),
    _active_seed(
        prefix_code=202,
        chain_id=42,
        chain_code="aptos",
        chain_name="Aptos",
        chain_family="move",
        address_family="aptos_address_32",
        codec="aptos_hex_32",
        payload_len=32,
        sample_address="0x" + "88" * 32,
        invalid_address="0x" + "88" * 31,
        native_symbol="APT",
        description="Aptos 32-byte account address",
    ),
    _active_seed(
        prefix_code=203,
        chain_id=43,
        chain_code="sui",
        chain_name="Sui",
        chain_family="move",
        address_family="sui_address_32",
        codec="sui_hex_32",
        payload_len=32,
        sample_address="0x" + "99" * 32,
        invalid_address="0x" + "99" * 31,
        native_symbol="SUI",
        description="Sui 32-byte account address",
    ),
    _planned_seed(prefix_code=300, chain_id=50, chain_code="xrp", chain_name="XRP Ledger", chain_family="xrp", address_family="xrp_classic_account", codec="xrp_classic", payload_len=20, native_symbol="XRP"),
    _planned_seed(prefix_code=310, chain_id=60, chain_code="cosmos", chain_name="Cosmos Hub", chain_family="cosmos", address_family="cosmos_bech32_account", codec="cosmos_bech32", payload_len=20, native_symbol="ATOM"),
    _planned_seed(prefix_code=311, chain_id=61, chain_code="osmosis", chain_name="Osmosis", chain_family="cosmos", address_family="osmosis_bech32_account", codec="cosmos_bech32", payload_len=20, native_symbol="OSMO"),
    _planned_seed(prefix_code=312, chain_id=62, chain_code="kujira", chain_name="Kujira", chain_family="cosmos", address_family="kujira_bech32_account", codec="cosmos_bech32", payload_len=20, native_symbol="KUJI"),
    _planned_seed(prefix_code=313, chain_id=63, chain_code="celestia", chain_name="Celestia", chain_family="cosmos", address_family="celestia_bech32_account", codec="cosmos_bech32", payload_len=20, native_symbol="TIA"),
    _planned_seed(prefix_code=314, chain_id=64, chain_code="injective", chain_name="Injective", chain_family="cosmos", address_family="injective_bech32_account", codec="cosmos_bech32", payload_len=20, native_symbol="INJ"),
    _planned_seed(prefix_code=330, chain_id=70, chain_code="ton", chain_name="TON", chain_family="ton", address_family="ton_user_friendly", codec="ton_user_friendly", payload_len=36, native_symbol="TON"),
]


ROLE_SEEDS: list[dict[str, Any]] = [
    *[
        {
            "role_id": role_id,
            "role_code": role_code,
            "category_code": category,
            "role_group": group,
            "metric_usage_default": metric_usage,
            "boundary_class": boundary,
            "default_quality_tier": 1,
            "default_flags": flags,
            "description": description,
            "is_active": True,
        }
        for role_id, role_code, category, group, metric_usage, boundary, flags, description in [
            (100, "cex_por_cold_wallet", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01000101, "Official CEX proof-of-reserves cold wallet"),
            (101, "cex_hot_wallet", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01000001, "CEX hot wallet"),
            (102, "cex_cold_wallet", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01000001, "CEX cold wallet"),
            (103, "cex_deposit_wallet", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01000001, "CEX deposit wallet"),
            (104, "cex_sweep_wallet", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01000001, "CEX sweep wallet"),
            (105, "cex_consolidation_wallet", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01000001, "CEX consolidation wallet"),
            (106, "cex_gas_supplier", "cex", "cex_ops", "supporting_context", "supporting", 0b01000000, "CEX gas supplier"),
            (107, "cex_safe_multisig", "cex", "cex_wallet", "cex_boundary", "cex_boundary", 0b01001001, "CEX Safe multisig"),
            (108, "custody_wallet", "cex", "custody", "cex_boundary", "cex_boundary", 0b01000001, "Custody wallet"),
            (109, "staking_wallet", "cex", "staking", "supporting_context", "staking_boundary", 0b01000001, "Staking wallet"),
            (200, "protocol_factory", "protocol", "protocol_core", "protocol_identity", "protocol_boundary", 0b01000011, "Protocol factory"),
            (201, "protocol_registry", "protocol", "protocol_core", "protocol_identity", "protocol_boundary", 0b01000011, "Protocol registry"),
            (202, "protocol_router", "protocol", "protocol_core", "protocol_identity", "protocol_boundary", 0b01000011, "Protocol router"),
            (203, "protocol_pool", "protocol", "protocol_core", "protocol_identity", "protocol_boundary", 0b01000011, "Protocol pool"),
            (204, "protocol_vault", "protocol", "protocol_core", "protocol_identity", "protocol_boundary", 0b01000011, "Protocol vault"),
            (205, "protocol_treasury", "protocol", "protocol_ops", "protocol_identity", "protocol_boundary", 0b01000011, "Protocol treasury"),
            (206, "protocol_oracle", "protocol", "protocol_core", "supporting_context", "supporting", 0b01000010, "Protocol oracle"),
            (207, "protocol_configurator", "protocol", "protocol_core", "protocol_identity", "protocol_boundary", 0b01000010, "Protocol configurator"),
            (208, "protocol_data_provider", "protocol", "protocol_core", "supporting_context", "supporting", 0b01000010, "Protocol data provider"),
            (209, "protocol_incentives_controller", "protocol", "protocol_core", "supporting_context", "supporting", 0b01000010, "Protocol incentives controller"),
            (300, "aave_pool", "protocol", "aave", "protocol_identity", "protocol_boundary", 0b01000011, "Aave pool"),
            (301, "aave_pool_addresses_provider", "protocol", "aave", "protocol_identity", "protocol_boundary", 0b01000011, "Aave pool addresses provider"),
            (302, "aave_a_token", "protocol", "aave", "supporting_context", "asset_container", 0b01010010, "Aave aToken"),
            (303, "aave_variable_debt_token", "protocol", "aave", "supporting_context", "asset_container", 0b01010010, "Aave variable debt token"),
            (304, "aave_stable_debt_token", "protocol", "aave", "supporting_context", "asset_container", 0b01010010, "Aave stable debt token"),
            (400, "dex_factory", "dex", "dex_core", "protocol_identity", "protocol_boundary", 0b01000011, "DEX factory"),
            (401, "dex_router", "dex", "dex_core", "protocol_identity", "protocol_boundary", 0b01000011, "DEX router"),
            (402, "dex_pool", "dex", "dex_core", "protocol_identity", "protocol_boundary", 0b01000011, "DEX pool"),
            (403, "dex_pair", "dex", "dex_core", "protocol_identity", "protocol_boundary", 0b01000011, "DEX pair"),
            (404, "lp_token", "dex", "dex_token", "supporting_context", "asset_container", 0b01010010, "LP token"),
            (500, "bridge_vault", "bridge", "bridge_core", "protocol_identity", "bridge_boundary", 0b01000011, "Bridge vault"),
            (501, "bridge_router", "bridge", "bridge_core", "protocol_identity", "bridge_boundary", 0b01000011, "Bridge router"),
            (502, "bridge_relayer", "bridge", "bridge_ops", "supporting_context", "supporting", 0b01000010, "Bridge relayer"),
            (503, "bridge_messenger", "bridge", "bridge_core", "supporting_context", "supporting", 0b01000010, "Bridge messenger"),
        ]
    ]
]


def key_prefix_from_seed(seed: dict[str, Any]) -> KeyPrefix:
    return KeyPrefix(
        prefix_code=int(seed["prefix_code"]),
        chain_code=str(seed["chain_code"]),
        chain_name=str(seed["chain_name"]),
        chain_family=str(seed["chain_family"]),
        address_family=str(seed["address_family"]),
        codec=str(seed["codec"]),
        codec_status=str(seed["codec_status"]),
        payload_len=seed.get("payload_len"),
        is_active=bool(seed["is_active"]),
        evm_chain_id=seed.get("evm_chain_id"),
        slip44_id=seed.get("slip44_id"),
        native_symbol=seed.get("native_symbol"),
        description=seed.get("description"),
    )


def active_key_prefixes() -> list[KeyPrefix]:
    return [key_prefix_from_seed(seed) for seed in KEY_PREFIX_SEEDS if seed["is_active"] and seed["codec_status"] == "active"]


def seed_compact_label_dictionaries(db: Session) -> dict[str, int]:
    key_prefix_count = 0
    role_count = 0
    for seed in KEY_PREFIX_SEEDS:
        payload = {key: value for key, value in seed.items() if key not in {"sample_address", "invalid_address"}}
        db.merge(KeyPrefixDict(**payload))
        key_prefix_count += 1
    for seed in ROLE_SEEDS:
        db.merge(RoleDict(**seed))
        role_count += 1
    db.flush()
    return {"key_prefix_count": key_prefix_count, "role_count": role_count}
