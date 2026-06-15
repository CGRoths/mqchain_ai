from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.labels.chain_registry_seed import KEY_PREFIX_SEEDS, seed_compact_label_dictionaries  # noqa: E402
from app.labels.key_codec import AddressCodecError, KeyPrefix, decode_full_key, encode_address_key  # noqa: E402
from app.labels.value_codec import CurrentLabelValue, TimelineLabelValue, pack_current_value, pack_timeline_value  # noqa: E402
from app.models.compact_label import KeyPrefixDict  # noqa: E402


CURRENT_VALUE_FIXTURE = CurrentLabelValue(
    schema_version=1,
    confidence_score=95,
    label_status=1,
    quality_tier=1,
    entity_id=0x01020304,
    protocol_id=0x05060708,
    role_id=0x090A,
    flags=0x0B0C,
    batch_id=0x0102030405060708,
    first_seen_block_or_slot=0x0D0E0F10,
    last_seen_block_or_slot=0x11121314,
)
TIMELINE_VALUE_FIXTURE = TimelineLabelValue(
    schema_version=1,
    confidence_score=95,
    label_status=2,
    quality_tier=1,
    entity_id=0x01020304,
    protocol_id=0x05060708,
    role_id=0x090A,
    flags=0x0B0C,
    batch_id=0x0102030405060708,
    valid_to_block_or_slot=0x15161718191A1B1C,
    first_seen_block_or_slot=0x0D0E0F10,
    last_seen_block_or_slot=0x11121314,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit Phase 1 compact-label key hex gate fixtures.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--pytest-result", default=None)
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        seed_compact_label_dictionaries(db)
        db.flush()
        report = build_phase1_report(db, pytest_result=args.pytest_result)
        db.rollback()

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text_report(report))
    return 0 if report["gate_passed"] else 1


def build_phase1_report(db, *, pytest_result: str | None = None) -> dict[str, Any]:
    seed_by_prefix = {int(seed["prefix_code"]): seed for seed in KEY_PREFIX_SEEDS}
    active_rows = db.scalars(
        select(KeyPrefixDict)
        .where(KeyPrefixDict.is_active.is_(True), KeyPrefixDict.codec_status == "active")
        .order_by(KeyPrefixDict.prefix_code.asc())
    ).all()

    fixtures = []
    invalid_test_count = 0
    round_trip_count = 0
    for row in active_rows:
        seed = seed_by_prefix.get(row.prefix_code)
        fixture = _fixture_for_row(row, seed, active_rows)
        if fixture["invalid_address_test"] == "pass":
            invalid_test_count += 1
        if fixture["round_trip_test"] == "pass":
            round_trip_count += 1
        fixtures.append(fixture)

    total = int(db.scalar(select(func.count(KeyPrefixDict.prefix_code))) or 0)
    planned = _status_count(db, "planned")
    experimental = _status_count(db, "experimental")
    disabled = _status_count(db, "disabled")
    active_count = len(active_rows)
    key_hex_fixture_count = sum(1 for fixture in fixtures if fixture["test_status"] == "pass")
    gate_passed = active_count == key_hex_fixture_count == invalid_test_count == round_trip_count
    return {
        "total_prefix_rows": total,
        "active_prefix_count": active_count,
        "planned_prefix_count": planned,
        "experimental_prefix_count": experimental,
        "disabled_prefix_count": disabled,
        "active_prefixes_with_key_hex_fixture": key_hex_fixture_count,
        "active_prefixes_with_invalid_address_test": invalid_test_count,
        "active_prefixes_with_round_trip_test": round_trip_count,
        "mqv_v1_32_byte_value_fixture_hex": pack_current_value(CURRENT_VALUE_FIXTURE).hex(),
        "mqt_v1_40_byte_timeline_fixture_hex": pack_timeline_value(TIMELINE_VALUE_FIXTURE).hex(),
        "pytest_result": pytest_result or "not_provided",
        "gate_passed": gate_passed,
        "fixtures": fixtures,
    }


def render_text_report(report: dict[str, Any]) -> str:
    lines = [
        "Phase 1 CTO Key Hex Gate Report",
        f"total_prefix_rows: {report['total_prefix_rows']}",
        f"active_prefix_count: {report['active_prefix_count']}",
        f"planned_prefix_count: {report['planned_prefix_count']}",
        f"experimental_prefix_count: {report['experimental_prefix_count']}",
        f"disabled_prefix_count: {report['disabled_prefix_count']}",
        f"active_prefixes_with_key_hex_fixture: {report['active_prefixes_with_key_hex_fixture']}",
        f"active_prefixes_with_invalid_address_test: {report['active_prefixes_with_invalid_address_test']}",
        f"active_prefixes_with_round_trip_test: {report['active_prefixes_with_round_trip_test']}",
        f"mqv_v1_32_byte_value_fixture_hex: {report['mqv_v1_32_byte_value_fixture_hex']}",
        f"mqt_v1_40_byte_timeline_fixture_hex: {report['mqt_v1_40_byte_timeline_fixture_hex']}",
        f"pytest_result: {report['pytest_result']}",
        f"gate_passed: {str(report['gate_passed']).lower()}",
        "",
    ]
    for fixture in report["fixtures"]:
        lines.extend(
            [
                f"chain_code: {fixture['chain_code']}",
                f"address_family: {fixture['address_family']}",
                f"codec: {fixture['codec']}",
                f"prefix_code: {fixture['prefix_code']}",
                f"prefix_hex: {fixture['prefix_hex']}",
                f"sample_address: {fixture['sample_address']}",
                f"normalized_display: {fixture['normalized_display']}",
                f"payload_hex: {fixture['payload_hex']}",
                f"full_key_hex: {fixture['full_key_hex']}",
                f"payload_len: {fixture['payload_len']}",
                f"test_status: {fixture['test_status']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip()


def _fixture_for_row(row: KeyPrefixDict, seed: dict[str, Any] | None, active_rows: list[KeyPrefixDict]) -> dict[str, Any]:
    base = {
        "chain_code": row.chain_code,
        "address_family": row.address_family,
        "codec": row.codec,
        "prefix_code": row.prefix_code,
        "prefix_hex": int(row.prefix_code).to_bytes(2, "big").hex(),
        "sample_address": seed.get("sample_address") if seed else None,
        "normalized_display": None,
        "payload_hex": None,
        "full_key_hex": None,
        "payload_len": row.payload_len,
        "round_trip_test": "fail",
        "invalid_address_test": "fail",
        "test_status": "fail:missing_seed_fixture",
    }
    if not seed or not seed.get("sample_address") or not seed.get("invalid_address"):
        return base
    prefix = _prefix_from_row(row)
    try:
        encoded = encode_address_key(prefix, seed["sample_address"])
        decoded = decode_full_key([_prefix_from_row(item) for item in active_rows], encoded.full_key)
        base.update(
            {
                "normalized_display": encoded.normalized_display,
                "payload_hex": encoded.payload_hex,
                "full_key_hex": encoded.full_key_hex,
                "payload_len": encoded.payload_len,
                "round_trip_test": "pass" if decoded.normalized_display == encoded.normalized_display else "fail",
            }
        )
    except AddressCodecError as exc:
        base["test_status"] = f"fail:{exc}"
        return base
    try:
        encode_address_key(prefix, seed["invalid_address"])
    except AddressCodecError:
        base["invalid_address_test"] = "pass"
    else:
        base["invalid_address_test"] = "fail"
    base["test_status"] = "pass" if base["round_trip_test"] == "pass" and base["invalid_address_test"] == "pass" else "fail"
    return base


def _prefix_from_row(row: KeyPrefixDict) -> KeyPrefix:
    return KeyPrefix(
        prefix_code=row.prefix_code,
        chain_code=row.chain_code,
        chain_name=row.chain_name,
        chain_family=row.chain_family,
        address_family=row.address_family,
        codec=row.codec,
        codec_status=row.codec_status,
        payload_len=row.payload_len,
        is_active=row.is_active,
        evm_chain_id=row.evm_chain_id,
        slip44_id=row.slip44_id,
        native_symbol=row.native_symbol,
        description=row.description,
    )


def _status_count(db, status: str) -> int:
    return int(db.scalar(select(func.count(KeyPrefixDict.prefix_code)).where(KeyPrefixDict.codec_status == status)) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
