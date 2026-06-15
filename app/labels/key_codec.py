from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable


BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {char: idx for idx, char in enumerate(BASE58_ALPHABET)}
BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
BECH32_INDEX = {char: idx for idx, char in enumerate(BECH32_ALPHABET)}
HEX_20_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
HEX_32_RE = re.compile(r"^0x[a-fA-F0-9]{64}$")


class AddressCodecError(ValueError):
    pass


@dataclass(frozen=True)
class KeyPrefix:
    prefix_code: int
    chain_code: str
    chain_name: str
    chain_family: str
    address_family: str
    codec: str
    codec_status: str
    payload_len: int | None
    is_active: bool
    evm_chain_id: int | None = None
    slip44_id: int | None = None
    native_symbol: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class EncodedAddressKey:
    original_address: str
    prefix_code: int
    prefix_hex: str
    payload: bytes
    payload_hex: str
    full_key: bytes
    full_key_hex: str
    normalized_display: str
    address_family: str
    warnings: tuple[str, ...] = ()

    @property
    def payload_len(self) -> int:
        return len(self.payload)


def encode_address_key(prefix: KeyPrefix, address: str) -> EncodedAddressKey:
    _ensure_active_prefix(prefix)
    original = str(address or "").strip()
    if not original:
        raise AddressCodecError("empty_address")

    normalized, payload = _encode_payload(prefix, original)
    if prefix.payload_len is not None and len(payload) != prefix.payload_len:
        raise AddressCodecError(f"payload_length_mismatch:{len(payload)}:{prefix.payload_len}")
    prefix_bytes = _prefix_bytes(prefix.prefix_code)
    full_key = prefix_bytes + payload
    return EncodedAddressKey(
        original_address=original,
        prefix_code=prefix.prefix_code,
        prefix_hex=prefix_bytes.hex(),
        payload=payload,
        payload_hex=payload.hex(),
        full_key=full_key,
        full_key_hex=full_key.hex(),
        normalized_display=normalized,
        address_family=prefix.address_family,
    )


def decode_full_key(prefixes: Iterable[KeyPrefix], full_key: bytes) -> EncodedAddressKey:
    if len(full_key) < 3:
        raise AddressCodecError("key_too_short")
    prefix_code = int.from_bytes(full_key[:2], "big")
    by_code = {prefix.prefix_code: prefix for prefix in prefixes}
    prefix = by_code.get(prefix_code)
    if prefix is None:
        raise AddressCodecError(f"unknown_prefix_code:{prefix_code}")
    payload = full_key[2:]
    if prefix.payload_len is not None and len(payload) != prefix.payload_len:
        raise AddressCodecError(f"payload_length_mismatch:{len(payload)}:{prefix.payload_len}")
    normalized = _decode_payload(prefix, payload)
    return EncodedAddressKey(
        original_address=normalized,
        prefix_code=prefix.prefix_code,
        prefix_hex=full_key[:2].hex(),
        payload=payload,
        payload_hex=payload.hex(),
        full_key=full_key,
        full_key_hex=full_key.hex(),
        normalized_display=normalized,
        address_family=prefix.address_family,
    )


def make_btc_witness_address(version: int, program: bytes, *, hrp: str = "bc") -> str:
    spec = "bech32" if version == 0 else "bech32m"
    data = [version, *_convertbits(program, 8, 5, True)]
    return _bech32_encode(hrp, data, spec)


def make_base58_address(payload: bytes) -> str:
    return _base58_encode(payload)


def make_base58check_address(payload: bytes) -> str:
    return _base58check_encode(payload)


def _ensure_active_prefix(prefix: KeyPrefix) -> None:
    if not prefix.is_active or prefix.codec_status != "active":
        raise AddressCodecError(f"inactive_prefix:{prefix.chain_code}:{prefix.address_family}")
    if not 0 <= int(prefix.prefix_code) <= 32767:
        raise AddressCodecError(f"prefix_code_out_of_v1_range:{prefix.prefix_code}")


def _encode_payload(prefix: KeyPrefix, address: str) -> tuple[str, bytes]:
    if prefix.codec == "evm_hex_20":
        if not HEX_20_RE.fullmatch(address):
            raise AddressCodecError("invalid_evm_address")
        payload = bytes.fromhex(address[2:])
        return "0x" + payload.hex(), payload
    if prefix.codec == "btc_base58check":
        payload = _base58check_decode(address)
        expected_version = 0x00 if prefix.address_family == "btc_p2pkh" else 0x05
        if len(payload) != 21 or payload[0] != expected_version:
            raise AddressCodecError("invalid_btc_base58_payload")
        return _base58check_encode(payload), payload
    if prefix.codec in {"btc_bech32", "btc_bech32m"}:
        hrp, version, program, spec = _decode_witness_address(address)
        if hrp != "bc":
            raise AddressCodecError("invalid_btc_hrp")
        expected_spec = "bech32" if prefix.codec == "btc_bech32" else "bech32m"
        if spec != expected_spec:
            raise AddressCodecError("invalid_btc_witness_checksum")
        expected = {
            "btc_bech32_v0_p2wpkh": (0, 20),
            "btc_bech32_v0_p2wsh": (0, 32),
            "btc_bech32m_v1_p2tr": (1, 32),
        }.get(prefix.address_family)
        if expected is None or (version, len(program)) != expected:
            raise AddressCodecError("invalid_btc_witness_program")
        payload = bytes([version]) + program
        return make_btc_witness_address(version, program, hrp=hrp), payload
    if prefix.codec == "tron_base58check_21":
        payload = _base58check_decode(address)
        if len(payload) != 21 or payload[0] != 0x41:
            raise AddressCodecError("invalid_tron_payload")
        return _base58check_encode(payload), payload
    if prefix.codec == "solana_base58_32":
        payload = _base58_decode(address)
        if len(payload) != 32:
            raise AddressCodecError("invalid_solana_payload_length")
        return _base58_encode(payload), payload
    if prefix.codec in {"aptos_hex_32", "sui_hex_32"}:
        if not HEX_32_RE.fullmatch(address):
            raise AddressCodecError(f"invalid_{prefix.chain_code}_address")
        payload = bytes.fromhex(address[2:])
        return "0x" + payload.hex(), payload
    raise AddressCodecError(f"unsupported_codec:{prefix.codec}")


def _decode_payload(prefix: KeyPrefix, payload: bytes) -> str:
    if prefix.codec == "evm_hex_20":
        if len(payload) != 20:
            raise AddressCodecError("invalid_evm_payload_length")
        return "0x" + payload.hex()
    if prefix.codec == "btc_base58check":
        return _base58check_encode(payload)
    if prefix.codec in {"btc_bech32", "btc_bech32m"}:
        if len(payload) not in {21, 33}:
            raise AddressCodecError("invalid_btc_witness_payload_length")
        return make_btc_witness_address(payload[0], payload[1:])
    if prefix.codec == "tron_base58check_21":
        if len(payload) != 21 or payload[0] != 0x41:
            raise AddressCodecError("invalid_tron_payload")
        return _base58check_encode(payload)
    if prefix.codec == "solana_base58_32":
        if len(payload) != 32:
            raise AddressCodecError("invalid_solana_payload_length")
        return _base58_encode(payload)
    if prefix.codec in {"aptos_hex_32", "sui_hex_32"}:
        if len(payload) != 32:
            raise AddressCodecError(f"invalid_{prefix.chain_code}_payload_length")
        return "0x" + payload.hex()
    raise AddressCodecError(f"unsupported_codec:{prefix.codec}")


def _prefix_bytes(prefix_code: int) -> bytes:
    return int(prefix_code).to_bytes(2, "big", signed=False)


def _base58_decode(value: str) -> bytes:
    number = 0
    for char in value:
        if char not in BASE58_INDEX:
            raise AddressCodecError("invalid_base58_character")
        number = number * 58 + BASE58_INDEX[char]
    result = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zeroes + result


def _base58_encode(payload: bytes) -> str:
    number = int.from_bytes(payload, "big")
    chars: list[str] = []
    while number:
        number, rem = divmod(number, 58)
        chars.append(BASE58_ALPHABET[rem])
    leading_zeroes = len(payload) - len(payload.lstrip(b"\x00"))
    return "1" * leading_zeroes + ("".join(reversed(chars)) if chars else "")


def _base58check_decode(value: str) -> bytes:
    decoded = _base58_decode(value)
    if len(decoded) < 5:
        raise AddressCodecError("base58check_too_short")
    payload, checksum = decoded[:-4], decoded[-4:]
    if _double_sha256(payload)[:4] != checksum:
        raise AddressCodecError("base58check_checksum_mismatch")
    return payload


def _base58check_encode(payload: bytes) -> str:
    return _base58_encode(payload + _double_sha256(payload)[:4])


def _double_sha256(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()


def _decode_witness_address(address: str) -> tuple[str, int, bytes, str]:
    if address.lower() != address and address.upper() != address:
        raise AddressCodecError("mixed_case_bech32")
    normalized = address.lower()
    if "1" not in normalized:
        raise AddressCodecError("missing_bech32_separator")
    hrp, data_part = normalized.rsplit("1", 1)
    if not hrp or len(data_part) < 6:
        raise AddressCodecError("invalid_bech32_length")
    try:
        data = [BECH32_INDEX[char] for char in data_part]
    except KeyError as exc:
        raise AddressCodecError("invalid_bech32_character") from exc
    polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if polymod == 1:
        spec = "bech32"
    elif polymod == 0x2BC830A3:
        spec = "bech32m"
    else:
        raise AddressCodecError("bech32_checksum_mismatch")
    values = data[:-6]
    if not values:
        raise AddressCodecError("missing_witness_version")
    version = values[0]
    if version > 16:
        raise AddressCodecError("invalid_witness_version")
    program = bytes(_convertbits(values[1:], 5, 8, False))
    if not 2 <= len(program) <= 40:
        raise AddressCodecError("invalid_witness_program_length")
    if version == 0 and len(program) not in {20, 32}:
        raise AddressCodecError("invalid_v0_witness_program_length")
    return hrp, version, program, spec


def _bech32_encode(hrp: str, data: list[int], spec: str) -> str:
    combined = data + _bech32_create_checksum(hrp, data, spec)
    return hrp + "1" + "".join(BECH32_ALPHABET[item] for item in combined)


def _bech32_create_checksum(hrp: str, data: list[int], spec: str) -> list[int]:
    const = 1 if spec == "bech32" else 0x2BC830A3
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ const
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            if (top >> i) & 1:
                chk ^= generator[i]
    return chk


def _convertbits(data: Iterable[int], from_bits: int, to_bits: int, pad: bool) -> list[int]:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << to_bits) - 1
    max_acc = (1 << (from_bits + to_bits - 1)) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            raise AddressCodecError("invalid_convertbits_value")
        acc = ((acc << from_bits) | value) & max_acc
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        raise AddressCodecError("invalid_bech32_padding")
    return ret
