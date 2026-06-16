from __future__ import annotations

import struct
from dataclasses import dataclass


CURRENT_VALUE_LEN = 32
TIMELINE_VALUE_LEN = 40
_CURRENT_STRUCT = struct.Struct("<BBBBIIHHQII")
_TIMELINE_STRUCT = struct.Struct("<BBBBIIHHQQII")
V1_MAX_SIGNED_SAFE_ID = 32767


class ValueCodecError(ValueError):
    pass


@dataclass(frozen=True)
class CurrentLabelValue:
    schema_version: int
    confidence_score: int
    label_status: int
    quality_tier: int
    entity_id: int
    protocol_id: int
    role_id: int
    flags: int
    batch_id: int
    first_seen_block_or_slot: int
    last_seen_block_or_slot: int


@dataclass(frozen=True)
class TimelineLabelValue:
    schema_version: int
    confidence_score: int
    label_status: int
    quality_tier: int
    entity_id: int
    protocol_id: int
    role_id: int
    flags: int
    batch_id: int
    valid_to_block_or_slot: int
    first_seen_block_or_slot: int
    last_seen_block_or_slot: int


LabelValueV1 = CurrentLabelValue
TimelineValueV1 = TimelineLabelValue


def pack_current_value(value: CurrentLabelValue) -> bytes:
    _validate_common(value)
    payload = _CURRENT_STRUCT.pack(
        value.schema_version,
        value.confidence_score,
        value.label_status,
        value.quality_tier,
        value.entity_id,
        value.protocol_id,
        value.role_id,
        value.flags,
        value.batch_id,
        value.first_seen_block_or_slot,
        value.last_seen_block_or_slot,
    )
    if len(payload) != CURRENT_VALUE_LEN:
        raise ValueCodecError("current_value_length_mismatch")
    return payload


def unpack_current_value(payload: bytes) -> CurrentLabelValue:
    if len(payload) != CURRENT_VALUE_LEN:
        raise ValueCodecError(f"invalid_current_value_length:{len(payload)}")
    return CurrentLabelValue(*_CURRENT_STRUCT.unpack(payload))


def pack_timeline_value(value: TimelineLabelValue) -> bytes:
    _validate_common(value)
    _validate_uint64("valid_to_block_or_slot", value.valid_to_block_or_slot)
    payload = _TIMELINE_STRUCT.pack(
        value.schema_version,
        value.confidence_score,
        value.label_status,
        value.quality_tier,
        value.entity_id,
        value.protocol_id,
        value.role_id,
        value.flags,
        value.batch_id,
        value.valid_to_block_or_slot,
        value.first_seen_block_or_slot,
        value.last_seen_block_or_slot,
    )
    if len(payload) != TIMELINE_VALUE_LEN:
        raise ValueCodecError("timeline_value_length_mismatch")
    return payload


def unpack_timeline_value(payload: bytes) -> TimelineLabelValue:
    if len(payload) != TIMELINE_VALUE_LEN:
        raise ValueCodecError(f"invalid_timeline_value_length:{len(payload)}")
    return TimelineLabelValue(*_TIMELINE_STRUCT.unpack(payload))


def _validate_common(value: CurrentLabelValue | TimelineLabelValue) -> None:
    for field_name in ("schema_version", "confidence_score", "label_status", "quality_tier"):
        _validate_uint8(field_name, getattr(value, field_name))
    _validate_uint32("entity_id", value.entity_id)
    _validate_uint32("protocol_id", value.protocol_id)
    _validate_v1_role_id(value.role_id)
    _validate_uint16("flags", value.flags)
    _validate_uint64("batch_id", value.batch_id)
    if int(value.batch_id) < 1:
        raise ValueCodecError("batch_id_must_be_positive")
    _validate_uint32("first_seen_block_or_slot", value.first_seen_block_or_slot)
    _validate_uint32("last_seen_block_or_slot", value.last_seen_block_or_slot)


def _validate_uint8(field_name: str, value: int) -> None:
    if not 0 <= int(value) <= 0xFF:
        raise ValueCodecError(f"{field_name}_out_of_uint8_range")


def _validate_uint16(field_name: str, value: int) -> None:
    if not 0 <= int(value) <= 0xFFFF:
        raise ValueCodecError(f"{field_name}_out_of_uint16_range")


def _validate_v1_role_id(value: int) -> None:
    if not 0 <= int(value) <= V1_MAX_SIGNED_SAFE_ID:
        raise ValueCodecError("role_id_out_of_v1_signed_safe_range")


def _validate_uint32(field_name: str, value: int) -> None:
    if not 0 <= int(value) <= 0xFFFFFFFF:
        raise ValueCodecError(f"{field_name}_out_of_uint32_range")


def _validate_uint64(field_name: str, value: int) -> None:
    if not 0 <= int(value) <= 0xFFFFFFFFFFFFFFFF:
        raise ValueCodecError(f"{field_name}_out_of_uint64_range")
