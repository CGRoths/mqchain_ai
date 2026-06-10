from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.models.intake import ApprovedAddress, ApprovedAddressEvidence, ApprovedAddressRole, Entity  # noqa: E402


EXPORT_FIELDS = [
    "entity_name",
    "chain_slug",
    "source_network",
    "address",
    "normalized_address",
    "address_class",
    "role",
    "source_trust_status",
    "confidence_score",
    "status",
    "evidence_count",
    "first_approved_at",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export approved registry addresses.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args()
    if not args.output and not args.output_json:
        parser.error("Provide --output for CSV or --output-json for JSON.")

    init_db()
    with SessionLocal() as db:
        rows = _registry_rows(db)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(str(args.output))
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(rows, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        print(str(args.output_json))
    return 0


def _registry_rows(db) -> list[dict]:
    evidence_count = func.count(ApprovedAddressEvidence.id).label("evidence_count")
    stmt = (
        select(Entity, ApprovedAddress, ApprovedAddressRole, evidence_count)
        .join(ApprovedAddress, ApprovedAddress.entity_id == Entity.id)
        .join(ApprovedAddressRole, ApprovedAddressRole.approved_address_id == ApprovedAddress.id)
        .outerjoin(ApprovedAddressEvidence, ApprovedAddressEvidence.approved_address_id == ApprovedAddress.id)
        .group_by(Entity.id, ApprovedAddress.id, ApprovedAddressRole.id)
        .order_by(Entity.entity_name.asc(), ApprovedAddress.chain_slug.asc(), ApprovedAddress.normalized_address.asc(), ApprovedAddressRole.role.asc())
    )
    rows = []
    for entity, approved, role, count in db.execute(stmt):
        rows.append(
            {
                "entity_name": entity.entity_name,
                "chain_slug": approved.chain_slug,
                "source_network": approved.source_network,
                "address": approved.address,
                "normalized_address": approved.normalized_address,
                "address_class": approved.address_class,
                "role": role.role,
                "source_trust_status": approved.source_trust_status,
                "confidence_score": approved.confidence_score,
                "status": approved.status,
                "evidence_count": int(count or 0),
                "first_approved_at": approved.first_approved_at,
            }
        )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
