from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.review.official_auto_approval import auto_approve_official_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto-approve high-confidence official-source candidates.")
    parser.add_argument("--source-job-id", type=int, default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview approvals without writing to the database.")
    mode.add_argument("--apply", action="store_true", help="Apply approvals.")
    args = parser.parse_args()

    dry_run = not args.apply
    init_db()
    with SessionLocal() as db:
        result = auto_approve_official_candidates(
            db,
            source_job_id=args.source_job_id,
            dry_run=dry_run,
            approved_by="cli",
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
