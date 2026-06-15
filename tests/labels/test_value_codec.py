from __future__ import annotations

import pytest

from app.labels.value_codec import (
    CURRENT_VALUE_LEN,
    TIMELINE_VALUE_LEN,
    CurrentLabelValue,
    TimelineLabelValue,
    ValueCodecError,
    pack_current_value,
    pack_timeline_value,
    unpack_current_value,
    unpack_timeline_value,
)
from scripts.compact_label_phase1_report import CURRENT_VALUE_FIXTURE, TIMELINE_VALUE_FIXTURE


def test_mqv_v1_current_value_exact_bytes_and_roundtrip() -> None:
    expected = "015f010104030201080706050a090c0b0807060504030201100f0e0d14131211"

    packed = pack_current_value(CURRENT_VALUE_FIXTURE)

    assert len(packed) == CURRENT_VALUE_LEN
    assert packed.hex() == expected
    assert unpack_current_value(packed) == CURRENT_VALUE_FIXTURE


def test_mqt_v1_timeline_value_exact_bytes_and_roundtrip() -> None:
    expected = "015f020104030201080706050a090c0b08070605040302011c1b1a1918171615100f0e0d14131211"

    packed = pack_timeline_value(TIMELINE_VALUE_FIXTURE)

    assert len(packed) == TIMELINE_VALUE_LEN
    assert packed.hex() == expected
    assert unpack_timeline_value(packed) == TIMELINE_VALUE_FIXTURE


def test_value_codec_rejects_wrong_lengths_and_out_of_range_fields() -> None:
    with pytest.raises(ValueCodecError):
        unpack_current_value(b"\x00" * 31)
    with pytest.raises(ValueCodecError):
        unpack_timeline_value(b"\x00" * 39)
    with pytest.raises(ValueCodecError):
        pack_current_value(
            CurrentLabelValue(
                schema_version=256,
                confidence_score=1,
                label_status=1,
                quality_tier=1,
                entity_id=1,
                protocol_id=1,
                role_id=1,
                flags=1,
                batch_id=1,
                first_seen_block_or_slot=1,
                last_seen_block_or_slot=1,
            )
        )
    with pytest.raises(ValueCodecError):
        pack_timeline_value(
            TimelineLabelValue(
                schema_version=1,
                confidence_score=1,
                label_status=1,
                quality_tier=1,
                entity_id=1,
                protocol_id=1,
                role_id=65536,
                flags=1,
                batch_id=1,
                valid_to_block_or_slot=1,
                first_seen_block_or_slot=1,
                last_seen_block_or_slot=1,
            )
        )
