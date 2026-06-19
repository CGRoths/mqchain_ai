from __future__ import annotations

from app.ingestion.address_recognizer import UniversalAddressRecognizer
from app.ingestion.address_utils import infer_address_family, normalize_address, valid_address_for_network
from app.ingestion.network_normalizer import NetworkNormalizer


def test_evm_0x40_without_context_is_format_only_ambiguous() -> None:
    recognizer = UniversalAddressRecognizer()
    address = "0x1111111111111111111111111111111111111111"

    detected = recognizer.detect_format(address)
    resolved = recognizer.resolve_with_context(address)

    assert detected.address_family == "evm20"
    assert detected.exact_chain is None
    assert "ethereum" in detected.possible_chains
    assert "base" in detected.possible_chains
    assert resolved.resolved_chain == "evm_unknown"
    assert resolved.resolution_method == "format_only_ambiguous"
    assert "missing_network_context" in resolved.warnings


def test_evm_possible_chains_follow_network_normalizer_evm_families() -> None:
    detected = UniversalAddressRecognizer().detect_format("0x1111111111111111111111111111111111111111")
    expected = {
        canonical
        for canonical, _chain_id, chain_guess in NetworkNormalizer.NETWORKS.values()
        if chain_guess == "evm"
    }

    assert expected <= set(detected.possible_chains)
    assert detected.exact_chain is None


def test_evm_0x40_with_base_network_hint_resolves_base() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("0x1111111111111111111111111111111111111111", "base")

    assert resolved.resolved_chain == "base"
    assert resolved.chain_id == 8453
    assert resolved.address_family == "evm"
    assert resolved.confidence >= 90


def test_evm_0x40_with_chain_id_one_resolves_ethereum() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("0x1111111111111111111111111111111111111111", "1")

    assert resolved.resolved_chain == "ethereum"
    assert resolved.chain_id == 1


def test_evm_0x40_with_source_context_resolves_weakly() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context(
        "0x1111111111111111111111111111111111111111",
        source_context={"filename": "deployments/base/contracts.json", "heading_path": ["Deployments", "Base"]},
    )

    assert resolved.resolved_chain == "base"
    assert resolved.resolution_method == "source_context"
    assert "chain_inferred_from_source_context" in resolved.warnings


def test_evm_0x40_with_bitcoin_hint_conflicts() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("0x1111111111111111111111111111111111111111", "bitcoin")

    assert resolved.resolved_chain is None
    assert resolved.confidence <= 20
    assert any("network_format_conflict" in warning for warning in resolved.warnings)


def test_xdc_prefixed_address_resolves_xdc() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("xdcF29f049144467b3dc55e19205c30C1737942F23a")

    assert resolved.resolved_chain == "xdc"
    assert resolved.normalized_address == "xdcf29f049144467b3dc55e19205c30c1737942f23a"


def test_btc_bech32_address_resolves_bitcoin() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kygt080")

    assert resolved.resolved_chain == "bitcoin"
    assert resolved.address_family == "btc_bech32"


def test_tron_base58check_address_resolves_tron() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")

    assert resolved.resolved_chain == "tron"
    assert resolved.address_family == "tron"
    assert resolved.metadata["format"]["metadata"]["version"] == 0x41


def test_solana_base58_32_address_resolves_solana() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")

    assert resolved.resolved_chain == "solana"
    assert resolved.address_family == "base58_32"
    assert resolved.metadata["format"]["payload_len"] == 32


def test_cosmos_family_hrps_resolve_exact_chains() -> None:
    recognizer = UniversalAddressRecognizer()

    assert recognizer.resolve_with_context("cosmos1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqnrql8a").resolved_chain == "cosmos"
    assert recognizer.resolve_with_context("noble1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq2f5t4x").resolved_chain == "noble"
    assert recognizer.resolve_with_context("dydx1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqsq0u3e").resolved_chain == "dydx"


def test_hex64_without_context_is_ambiguous_hex32() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("0x" + "a" * 64)

    assert resolved.resolved_chain == "hex32_unknown"
    assert resolved.address_family == "hex32"
    assert {"aptos", "sui", "starknet"} <= set(resolved.possible_chains)
    assert "ambiguous_hex32_address" in resolved.warnings


def test_hex64_with_sui_hint_resolves_sui() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("0x" + "a" * 64, "sui")

    assert resolved.resolved_chain == "sui"
    assert resolved.address_family == "sui"
    assert resolved.normalized_address == "0x" + "a" * 64


def test_hedera_account_id_resolves_hedera() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("0.0.12345")

    assert resolved.resolved_chain == "hedera"
    assert resolved.address_family == "hedera_account_id"


def test_near_named_account_resolves_near() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("treasury.alice.near")

    assert resolved.resolved_chain == "near"
    assert resolved.normalized_address == "treasury.alice.near"


def test_tezos_tz1_and_kt1_resolve_tezos() -> None:
    recognizer = UniversalAddressRecognizer()

    assert recognizer.resolve_with_context("tz1VSUr8wwNhLAzempoch5d6hLRiTh8Cjcjb").resolved_chain == "tezos"
    assert recognizer.resolve_with_context("KT1RJ6PbjHpwc3M5rw5s2Nbmefwbuwbdxton").resolved_chain == "tezos"


def test_ton_raw_and_user_friendly_resolve_ton() -> None:
    recognizer = UniversalAddressRecognizer()

    assert recognizer.resolve_with_context("0:" + "a" * 64).resolved_chain == "ton"
    assert recognizer.resolve_with_context("EQ" + "A" * 46).resolved_chain == "ton"


def test_unknown_random_string_has_no_match() -> None:
    resolved = UniversalAddressRecognizer().resolve_with_context("not an address")

    assert resolved.resolved_chain is None
    assert resolved.resolution_method == "no_format_match"
    assert resolved.confidence == 0


def test_address_utils_wrappers_delegate_to_universal_recognizer() -> None:
    base = NetworkNormalizer.normalize("base")
    bitcoin = NetworkNormalizer.normalize("bitcoin")
    address = "0x1111111111111111111111111111111111111111"

    assert infer_address_family(address) == "evm"
    assert normalize_address(address.upper().replace("X", "x"), base) == address
    assert valid_address_for_network(address, base) is True
    assert valid_address_for_network(address, bitcoin) is False
