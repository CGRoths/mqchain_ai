from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.ingestion.network_normalizer import NetworkNormalizer


BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {char: index for index, char in enumerate(BASE58_ALPHABET)}
BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32_INDEX = {char: index for index, char in enumerate(BECH32_ALPHABET)}
RIPPLE_ALPHABET = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdefghijkmnopqrstuvwxyz"


EVM20_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HEX32_RE = re.compile(r"^0x[a-fA-F0-9]{1,64}$")
XDC_RE = re.compile(r"^xdc[a-fA-F0-9]{40}$", re.IGNORECASE)
BTC_BECH32_RE = re.compile(r"^bc1[ac-hj-np-z02-9]{11,87}$", re.IGNORECASE)
BTC_LEGACY_RE = re.compile(r"^[13][1-9A-HJ-NP-Za-km-z]{25,34}$")
LTC_RE = re.compile(r"^(?:ltc1[ac-hj-np-z02-9]{11,87}|[LM3][1-9A-HJ-NP-Za-km-z]{25,34})$", re.IGNORECASE)
DOGE_RE = re.compile(r"^[DA9][1-9A-HJ-NP-Za-km-z]{25,34}$")
TRON_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
SOLANA_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
ALGORAND_RE = re.compile(r"^[A-Z2-7]{58}$")
HEDERA_RE = re.compile(r"^0\.0\.\d{1,12}$")
NEAR_NAMED_RE = re.compile(r"^(?:[a-z0-9_-]+\.)*near$", re.IGNORECASE)
HEX64_RE = re.compile(r"^[a-fA-F0-9]{64}$")
COSMOS_BECH32_RE = re.compile(r"^(?P<hrp>[a-z][a-z0-9]{1,31})1[0-9a-z]{20,100}$", re.IGNORECASE)
SUBSTRATE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{45,64}$")
XRP_RE = re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")
TON_RAW_RE = re.compile(r"^(?P<workchain>-?1|0):(?P<account>[a-fA-F0-9]{64})$")
TON_USER_FRIENDLY_RE = re.compile(r"^(?:EQ|UQ|kQ)[A-Za-z0-9_-]{46}$")
TEZOS_RE = re.compile(r"^(?:tz1|tz2|tz3|KT1)[1-9A-HJ-NP-Za-km-z]{30,40}$")
AVALANCHE_X_RE = re.compile(r"^X-avax1[0-9a-z]{20,100}$", re.IGNORECASE)


ADDRESS_RE = re.compile(
    r"(?<![A-Za-z0-9:._-])(?:"
    r"0x[a-fA-F0-9]{40,64}|"
    r"xdc[a-fA-F0-9]{40}|"
    r"bc1[ac-hj-np-z02-9]{11,87}|"
    r"[13][1-9A-HJ-NP-Za-km-z]{25,34}|"
    r"ltc1[ac-hj-np-z02-9]{11,87}|[LM3][1-9A-HJ-NP-Za-km-z]{25,34}|"
    r"[DA9][1-9A-HJ-NP-Za-km-z]{25,34}|"
    r"T[1-9A-HJ-NP-Za-km-z]{33}|"
    r"r[1-9A-HJ-NP-Za-km-z]{24,34}|"
    r"(?:cosmos|noble|dydx)1[0-9a-z]{20,100}|"
    r"X-avax1[0-9a-z]{20,100}|"
    r"0\.0\.\d{1,12}|"
    r"(?:[a-z0-9_-]+\.)*near|"
    r"[a-fA-F0-9]{64}|"
    r"(?:tz1|tz2|tz3|KT1)[1-9A-HJ-NP-Za-km-z]{30,40}|"
    r"(?:EQ|UQ|kQ)[A-Za-z0-9_-]{46}|"
    r"(?:-?1|0):[a-fA-F0-9]{64}|"
    r"[1-9A-HJ-NP-Za-km-z]{32,44}"
    r")(?![A-Za-z0-9:._-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AddressFormatMatch:
    raw_address: str
    cleaned_address: str
    normalized_address: str | None
    address_family: str | None
    codec: str | None
    payload_len: int | None
    checksum_valid: bool | None
    exact_chain: str | None
    possible_chains: list[str]
    confidence: int
    reason: str | None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AddressNetworkResolution:
    raw_address: str
    normalized_address: str | None
    resolved_chain: str | None
    chain_id: int | None
    address_family: str | None
    resolution_method: str
    confidence: int
    possible_chains: list[str]
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class UniversalAddressRecognizer:
    def __init__(self) -> None:
        self.evm_chains = _chains_by_guess("evm")
        self.substrate_chains = _chains_by_guess("substrate")
        self.cosmos_chains = _chains_by_guess("cosmos")

    def clean_address(self, value: str | None) -> str | None:
        if not value:
            return None
        cleaned = str(value).strip().strip("`'\";,()[]{}<>")
        cleaned = re.sub(r"[\u200b\u200c\u200d\s]+", "", cleaned)
        if len(cleaned) < 3:
            return None
        return cleaned

    def detect_format(self, address: str | None) -> AddressFormatMatch:
        raw = "" if address is None else str(address)
        cleaned = self.clean_address(address)
        if not cleaned:
            return _no_match(raw, "empty_address")

        lower = cleaned.lower()
        if XDC_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, "xdc" + cleaned[3:].lower(), "xdc", "xdc_hex_20", 20, None, "xdc", ["xdc"], 98, "xdc_prefixed_evm20")
        if EVM20_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, "0x" + cleaned[2:].lower(), "evm20", "hex_0x_20", 20, None, None, self.evm_chains, 80, "ambiguous_evm20")
        if lower.startswith("0x") and HEX32_RE.fullmatch(cleaned):
            payload_hex = cleaned[2:].lower()
            padded = "0x" + payload_hex.rjust(64, "0")
            return AddressFormatMatch(
                raw,
                cleaned,
                padded,
                "hex32",
                "hex_0x_up_to_32",
                len(bytes.fromhex(payload_hex.rjust(64, "0"))),
                None,
                None,
                ["aptos", "sui", "starknet"],
                65,
                "ambiguous_hex32",
                warnings=["ambiguous_hex32_address", "possible_evm_storage_slot_or_move_address"],
                metadata={"hex_length": len(payload_hex)},
            )
        if HEDERA_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, cleaned, "hedera_account_id", "hedera_account", None, None, "hedera", ["hedera"], 96, "hedera_account_id")
        if TON_RAW_RE.fullmatch(cleaned):
            match = TON_RAW_RE.fullmatch(cleaned)
            metadata = {"workchain": int(match.group("workchain")) if match else None, "format": "raw"}
            return AddressFormatMatch(raw, cleaned, cleaned.lower(), "ton_account", "ton_raw", 36, None, "ton", ["ton"], 94, "ton_raw_address", metadata=metadata)
        if TON_USER_FRIENDLY_RE.fullmatch(cleaned):
            payload = _base64url_decode(cleaned)
            metadata = _ton_user_friendly_metadata(payload, cleaned)
            warnings = [] if payload else ["ton_user_friendly_payload_not_decoded"]
            return AddressFormatMatch(raw, cleaned, cleaned, "ton_account", "ton_user_friendly", len(payload) if payload else None, None, "ton", ["ton"], 88, "ton_user_friendly_address", warnings=warnings, metadata=metadata)
        if BTC_BECH32_RE.fullmatch(cleaned):
            checksum = _bech32_valid(cleaned)
            return _bech32_match(raw, cleaned, "bitcoin", "btc_bech32", "btc_bech32_or_bech32m", 86, checksum)
        if BTC_LEGACY_RE.fullmatch(cleaned):
            payload, checksum = _base58check_payload(cleaned)
            version = payload[0] if payload else None
            metadata = {"version": version}
            warnings = [] if checksum else ["base58check_checksum_not_validated_or_invalid"]
            exact_chain = "bitcoin" if checksum and version in {0x00, 0x05} else None
            return AddressFormatMatch(raw, cleaned, cleaned, "btc_legacy", "base58check", len(payload) if payload else None, checksum, exact_chain, ["bitcoin"], 84 if exact_chain else 58, "btc_base58_address", warnings=warnings, metadata=metadata)
        if LTC_RE.fullmatch(cleaned):
            payload, checksum = (None, _bech32_valid(cleaned)) if lower.startswith("ltc1") else _base58check_payload(cleaned)
            exact_chain = "litecoin" if checksum else None
            return AddressFormatMatch(raw, cleaned, lower if lower.startswith("ltc1") else cleaned, "litecoin", "bech32_or_base58check", len(payload) if payload else None, checksum, exact_chain, ["litecoin"], 82 if exact_chain else 58, "litecoin_address", warnings=[] if checksum else ["checksum_not_validated_or_invalid"])
        if DOGE_RE.fullmatch(cleaned):
            payload, checksum = _base58check_payload(cleaned)
            version = payload[0] if payload else None
            exact_chain = "dogecoin" if checksum and version in {0x1E, 0x16} else None
            return AddressFormatMatch(raw, cleaned, cleaned, "dogecoin", "base58check", len(payload) if payload else None, checksum, exact_chain, ["dogecoin"], 82 if exact_chain else 58, "dogecoin_address", warnings=[] if checksum else ["base58check_checksum_not_validated_or_invalid"], metadata={"version": version})
        if TRON_RE.fullmatch(cleaned):
            payload, checksum = _base58check_payload(cleaned)
            version_ok = bool(payload and payload[0] == 0x41)
            warnings = []
            if not checksum:
                warnings.append("base58check_checksum_not_validated_or_invalid")
            if payload and not version_ok:
                warnings.append("tron_version_byte_mismatch")
            exact_chain = "tron" if checksum and version_ok else None
            return AddressFormatMatch(raw, cleaned, cleaned, "tron", "tron_base58check", len(payload) if payload else None, checksum and version_ok, exact_chain, ["tron"], 92 if exact_chain else 58, "tron_base58check_address", warnings=warnings, metadata={"version": payload[0] if payload else None})
        if ALGORAND_RE.fullmatch(cleaned):
            payload, checksum = _algorand_payload(cleaned)
            return AddressFormatMatch(raw, cleaned, cleaned, "algorand", "algorand_base32", len(payload) if payload else None, checksum, "algorand", ["algorand"], 90 if checksum else 72, "algorand_address", warnings=[] if checksum else ["algorand_checksum_not_validated_or_invalid"])
        if AVALANCHE_X_RE.fullmatch(cleaned):
            checksum = _bech32_valid(cleaned.removeprefix("X-").removeprefix("x-"))
            return AddressFormatMatch(raw, cleaned, cleaned, "avalanche_x", "bech32", None, checksum, "avalanche-x", ["avalanche-x"], 84 if checksum else 70, "avalanche_x_bech32", warnings=[] if checksum else ["bech32_checksum_not_validated_or_invalid"])
        if TEZOS_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, cleaned, "tezos", "tezos_base58check", None, None, "tezos", ["tezos"], 82, "tezos_account_or_contract", warnings=["tezos_checksum_not_validated"])
        if XRP_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, cleaned, "xrp_classic", "xrp_base58check", None, None, "xrp", ["xrp"], 80, "xrp_classic_address", warnings=["xrp_checksum_not_validated"])
        solana_payload = _base58_decode(cleaned) if SOLANA_RE.fullmatch(cleaned) else None
        if solana_payload and len(solana_payload) == 32:
            return AddressFormatMatch(raw, cleaned, cleaned, "base58_32", "base58", 32, None, "solana", ["solana"], 82, "solana_base58_32", warnings=["base58_32_format"])
        cosmos = COSMOS_BECH32_RE.fullmatch(cleaned)
        if cosmos:
            hrp = cosmos.group("hrp").lower()
            chain = {"cosmos": "cosmos", "noble": "noble", "dydx": "dydx"}.get(hrp)
            checksum = _bech32_valid(cleaned)
            if not chain and not checksum:
                return _no_match(raw, "unknown_address_format")
            return AddressFormatMatch(
                raw,
                cleaned,
                lower,
                "cosmos_bech32",
                "bech32",
                None,
                checksum,
                chain,
                [chain] if chain else self.cosmos_chains,
                86 if chain and checksum else 70,
                "cosmos_family_bech32",
                warnings=[] if checksum else ["bech32_checksum_not_validated_or_invalid"],
                metadata={"hrp": hrp},
            )
        if NEAR_NAMED_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, lower, "near_account", "near_named_account", None, None, "near", ["near"], 92, "near_named_account")
        if HEX64_RE.fullmatch(cleaned):
            return AddressFormatMatch(raw, cleaned, lower, "hex32", "hex_32", 32, None, None, ["aptos", "sui", "starknet", "near"], 55, "ambiguous_raw_hex32", warnings=["missing_network_context", "ambiguous_raw_hex32"])
        if SUBSTRATE_RE.fullmatch(cleaned) and _base58_decode(cleaned):
            return AddressFormatMatch(raw, cleaned, cleaned, "substrate_ss58", "ss58_base58", None, None, None, self.substrate_chains, 58, "substrate_ss58_possible", warnings=["checksum_not_validated", "missing_network_context"])
        return _no_match(raw, "unknown_address_format")

    def resolve_with_context(
        self,
        address: str | None,
        network_hint: str | int | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> AddressNetworkResolution:
        match = self.detect_format(address)
        if not match.address_family:
            return AddressNetworkResolution(match.raw_address, None, None, None, None, "no_format_match", 0, [], match.warnings, {"format": match.to_dict()})

        hint = NetworkNormalizer.normalize(network_hint)
        hint_method = "network_hint" if network_hint not in {None, ""} else None
        if hint_method is None and source_context:
            context_hint = _network_from_context(source_context)
            if context_hint is not None:
                hint = context_hint
                hint_method = "source_context"

        if hint_method:
            compatible, family_override = _compatible(match, hint)
            if compatible:
                resolved_chain = hint.canonical_chain or (f"{hint.chain_guess}_unknown" if hint.chain_guess else match.exact_chain)
                confidence = 95 if hint_method == "network_hint" else min(85, max(match.confidence, 72))
                warnings = list(match.warnings)
                if hint_method == "source_context":
                    warnings.append("chain_inferred_from_source_context")
                return AddressNetworkResolution(
                    match.raw_address,
                    _normalize_for_chain(match, resolved_chain),
                    resolved_chain,
                    hint.chain_id,
                    family_override or _family_for_resolution(match, resolved_chain),
                    hint_method,
                    confidence,
                    match.possible_chains,
                    _dedupe(warnings),
                    {"format": match.to_dict(), "network_hint": hint.__dict__},
                )
            return AddressNetworkResolution(
                match.raw_address,
                match.normalized_address,
                None,
                hint.chain_id,
                match.address_family,
                "network_format_conflict",
                20,
                match.possible_chains,
                _dedupe([*match.warnings, f"network_format_conflict:{hint.canonical_chain or hint.chain_guess or hint.raw_network}"]),
                {"format": match.to_dict(), "network_hint": hint.__dict__},
            )

        if match.exact_chain:
            normalized = NetworkNormalizer.normalize(match.exact_chain)
            return AddressNetworkResolution(match.raw_address, match.normalized_address, match.exact_chain, normalized.chain_id, match.address_family, "format_exact", match.confidence, match.possible_chains, match.warnings, {"format": match.to_dict()})

        return AddressNetworkResolution(
            match.raw_address,
            match.normalized_address,
            _unknown_chain_for_match(match),
            None,
            match.address_family,
            "format_only_ambiguous",
            min(match.confidence, 65),
            match.possible_chains,
            _dedupe([*match.warnings, "missing_network_context"]),
            {"format": match.to_dict()},
        )


def _no_match(raw: str, reason: str) -> AddressFormatMatch:
    return AddressFormatMatch(raw, "", None, None, None, None, None, None, [], 0, reason, warnings=[reason])


def _bech32_match(raw: str, cleaned: str, chain: str, family: str, codec: str, confidence: int, checksum: bool) -> AddressFormatMatch:
    return AddressFormatMatch(raw, cleaned, cleaned.lower(), family, codec, None, checksum, chain, [chain], confidence if checksum else confidence - 16, f"{chain}_{codec}", warnings=[] if checksum else ["bech32_checksum_not_validated_or_invalid"])


def _chains_by_guess(chain_guess: str) -> list[str]:
    result: list[str] = []
    for canonical, _chain_id, guess in NetworkNormalizer.NETWORKS.values():
        if guess == chain_guess and canonical not in result:
            result.append(canonical)
    return result


def _base58_decode(value: str) -> bytes | None:
    number = 0
    try:
        for char in value:
            number = number * 58 + BASE58_INDEX[char]
    except KeyError:
        return None
    result = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + result


def _base58check_payload(value: str) -> tuple[bytes | None, bool]:
    decoded = _base58_decode(value)
    if not decoded or len(decoded) < 5:
        return decoded, False
    payload, checksum = decoded[:-4], decoded[-4:]
    return payload, hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def _bech32_valid(value: str) -> bool:
    if value.lower() != value and value.upper() != value:
        return False
    normalized = value.lower()
    if "1" not in normalized:
        return False
    hrp, data_part = normalized.rsplit("1", 1)
    if not hrp or len(data_part) < 6:
        return False
    try:
        data = [BECH32_INDEX[char] for char in data_part]
    except KeyError:
        return False
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    return polymod in {1, 0x2BC830A3}


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for index in range(5):
            if (top >> index) & 1:
                chk ^= generator[index]
    return chk


def _algorand_payload(value: str) -> tuple[bytes | None, bool]:
    try:
        decoded = base64.b32decode(value + "=" * ((8 - len(value) % 8) % 8))
    except Exception:
        return None, False
    if len(decoded) != 36:
        return decoded, False
    payload, checksum = decoded[:32], decoded[32:]
    try:
        digest = hashlib.new("sha512_256", payload).digest()
    except ValueError:
        return payload, False
    return payload, digest[-4:] == checksum


def _base64url_decode(value: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
    except Exception:
        return None


def _ton_user_friendly_metadata(payload: bytes | None, value: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"format": "user_friendly", "prefix": value[:2]}
    if payload and len(payload) >= 36:
        flags = payload[0]
        workchain = int.from_bytes(payload[1:2], "big", signed=True)
        metadata.update(
            {
                "workchain": workchain,
                "bounceable": flags in {0x11, 0x51},
                "testnet_only": bool(flags & 0x80),
            }
        )
    return metadata


def _compatible(match: AddressFormatMatch, network) -> tuple[bool, str | None]:
    canonical = network.canonical_chain
    guess = network.chain_guess
    if not canonical and not guess:
        return False, None
    if match.exact_chain:
        return canonical == match.exact_chain, None
    if match.address_family == "evm20":
        if guess == "evm":
            return True, "evm"
        if canonical == "hedera":
            return True, "hedera_evm_alias"
        return False, None
    if match.address_family == "hex32":
        if guess in {"aptos", "sui", "starknet"}:
            return True, guess
        if canonical == "near":
            return True, "near_implicit_account"
        return False, None
    if match.address_family == "substrate_ss58":
        return guess == "substrate", "substrate"
    if match.address_family == "cosmos_bech32":
        return guess == "cosmos", "cosmos"
    if match.address_family == "base58_32":
        return canonical == "solana", "solana"
    return canonical in match.possible_chains or guess in match.possible_chains, None


def _family_for_resolution(match: AddressFormatMatch, resolved_chain: str | None) -> str | None:
    if match.address_family == "evm20":
        return "evm"
    if match.address_family == "hex32" and resolved_chain in {"aptos", "sui", "starknet"}:
        return resolved_chain
    return match.address_family


def _normalize_for_chain(match: AddressFormatMatch, resolved_chain: str | None) -> str | None:
    cleaned = match.cleaned_address
    if not cleaned:
        return match.normalized_address
    lower = cleaned.lower()
    if resolved_chain == "xdc" and lower.startswith("0x"):
        return "xdc" + lower[2:]
    if resolved_chain in {"aptos", "sui", "starknet"} and lower.startswith("0x"):
        return "0x" + lower[2:].rjust(64, "0")
    if resolved_chain == "near":
        return lower
    if lower.startswith(("0x", "xdc")):
        return match.normalized_address
    return match.normalized_address or cleaned


def _unknown_chain_for_match(match: AddressFormatMatch) -> str | None:
    if match.address_family == "evm20":
        return "evm_unknown"
    if match.address_family == "hex32":
        return "hex32_unknown"
    if match.address_family == "base58_32":
        return "base58_32_unknown"
    if match.address_family == "substrate_ss58":
        return "substrate_unknown"
    if match.address_family == "cosmos_bech32":
        return "cosmos_unknown"
    return None


def _network_from_context(source_context: dict[str, Any]) -> Any:
    values = " ".join(_flatten_context(source_context))
    normalized = re.sub(r"[^a-z0-9]+", " ", values.lower())
    keys = sorted(NetworkNormalizer.NETWORKS, key=len, reverse=True)
    has_specific_match = any(_context_key_matches(key, normalized) for key in keys if key not in GENERIC_CONTEXT_NETWORK_KEYS)
    for key in keys:
        if has_specific_match and key in GENERIC_CONTEXT_NETWORK_KEYS:
            continue
        if _context_key_matches(key, normalized):
            return NetworkNormalizer.normalize(key)
    return None


GENERIC_CONTEXT_NETWORK_KEYS = {
    "erc20",
    "eth",
    "eth erc20",
    "ethereum evm",
    "ethereum evm masked",
    "ethereum mainnet",
    "mainnet",
}


def _context_key_matches(key: str, normalized_context: str) -> bool:
    key_norm = re.sub(r"[^a-z0-9]+", " ", key.lower()).strip()
    return bool(key_norm and re.search(rf"(?<![a-z0-9]){re.escape(key_norm)}(?![a-z0-9])", normalized_context))


def _flatten_context(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_flatten_context(item))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for item in value:
            result.extend(_flatten_context(item))
        return result
    return [str(value)]


def _dedupe(values: list[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


DEFAULT_RECOGNIZER = UniversalAddressRecognizer()
