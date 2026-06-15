from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.labels.chain_registry_seed import seed_compact_label_dictionaries  # noqa: E402
from app.labels.dictionary_loader import freeze_dictionary_version  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed compact label key-prefix and role dictionaries.")
    parser.add_argument("--version-name", default="phase1_seed")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        result = seed_compact_label_dictionaries(db)
        version = freeze_dictionary_version(db, args.version_name)
        result["dictionary_version"] = version.version_name
        result["key_prefix_hash"] = version.key_prefix_hash
        result["role_dict_hash"] = version.role_dict_hash
        result["entity_hash"] = version.entity_hash
        result["protocol_hash"] = version.protocol_hash
        if args.dry_run:
            db.rollback()
            result["dry_run"] = True
        else:
            db.commit()
            result["dry_run"] = False
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
