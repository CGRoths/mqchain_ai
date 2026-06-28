from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.review.source_verification import verify_source_sheets_from_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Create source-sheet verification rows from sheet manifest metadata.")
    parser.add_argument("--source-job-id", type=int, required=True)
    parser.add_argument("--verified-by", required=True)
    parser.add_argument("--apply", action="store_true", help="Create or update verification rows. Defaults to dry-run.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing rows.")
    parser.add_argument("--update-existing", action="store_true", help="Update safe fields on existing source-sheet verifications.")
    args = parser.parse_args()

    dry_run = True
    if args.apply:
        dry_run = False
    if args.dry_run:
        dry_run = True

    init_db()
    with SessionLocal() as db:
        result = verify_source_sheets_from_candidates(
            db,
            source_job_id=args.source_job_id,
            verified_by=args.verified_by,
            dry_run=dry_run,
            update_existing=args.update_existing,
        )

    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
