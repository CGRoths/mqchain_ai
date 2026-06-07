from __future__ import annotations

import re

from app.ingestion.network_normalizer import NormalizedNetwork


ADDRESS_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:0x[a-fA-F0-9]{40,64}|xdc[a-fA-F0-9]{40})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
LONG_0X_RE = re.compile(r"^0x[a-fA-F0-9]{40,64}$")
XDC_RE = re.compile(r"^xdc[a-fA-F0-9]{40}$")


def clean_wallet_address(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = str(value).strip().strip("`'\";,()[]{}")
    cleaned = re.sub(r"\s+", "", cleaned)
    if len(cleaned) < 8:
        return None
    return cleaned


def infer_address_family(address: str) -> str | None:
    lower = address.lower()
    if lower.startswith(("0x", "xdc")):
        return "evm"
    return None


def normalize_address(address: str, network: NormalizedNetwork) -> str:
    if network.canonical_chain == "xdc":
        return f"xdc{address[2:].lower()}" if address.lower().startswith("0x") else address.lower()
    return address.lower() if address.lower().startswith(("0x", "xdc")) else address


def valid_address_for_network(address: str, network: NormalizedNetwork) -> bool:
    if EVM_RE.fullmatch(address):
        return network.chain_guess not in {"aptos", "sui", "xrp", "ton", "tron", "btc", "bitcoin"}
    if LONG_0X_RE.fullmatch(address):
        return network.chain_guess in {None, "evm", "aptos", "sui", "starknet"}
    if XDC_RE.fullmatch(address):
        return network.canonical_chain in {None, "xdc"}
    return False
