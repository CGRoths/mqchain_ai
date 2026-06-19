from __future__ import annotations

from app.ingestion.address_recognizer import ADDRESS_RE, DEFAULT_RECOGNIZER
from app.ingestion.network_normalizer import NormalizedNetwork


def clean_wallet_address(value: str | None) -> str | None:
    return DEFAULT_RECOGNIZER.clean_address(value)


def infer_address_family(address: str) -> str | None:
    match = DEFAULT_RECOGNIZER.detect_format(address)
    if match.address_family in {"evm20", "xdc"}:
        return "evm"
    if match.address_family == "base58_32" and match.exact_chain == "solana":
        return "solana"
    if match.address_family == "btc_legacy" or match.address_family == "btc_bech32":
        return "btc"
    if match.address_family == "cosmos_bech32":
        return "cosmos"
    return match.address_family


def normalize_address(address: str, network: NormalizedNetwork) -> str:
    hint = _network_hint(network)
    resolved = DEFAULT_RECOGNIZER.resolve_with_context(address, hint)
    return resolved.normalized_address or address


def valid_address_for_network(address: str, network: NormalizedNetwork) -> bool:
    hint = _network_hint(network)
    resolved = DEFAULT_RECOGNIZER.resolve_with_context(address, hint)
    if resolved.resolution_method == "no_format_match":
        return False
    if resolved.resolution_method == "network_format_conflict":
        return False
    return resolved.normalized_address is not None


def _network_hint(network: NormalizedNetwork) -> str | int | None:
    if network.canonical_chain:
        return network.canonical_chain
    if network.chain_id is not None:
        return network.chain_id
    if network.chain_guess:
        return network.raw_network
    return None
