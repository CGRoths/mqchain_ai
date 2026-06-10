from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.review.approval_registry import approve_candidate_groups  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Approve unique candidate groups into the approved address registry.")
    parser.add_argument("--source-job-id", type=int, default=None)
    parser.add_argument("--readiness", dest="approval_readiness", default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", default=True)
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", default="system")
    args = parser.parse_args()

    dry_run = not args.apply
    init_db()
    with SessionLocal() as db:
        result = approve_candidate_groups(
            db,
            source_job_id=args.source_job_id,
            approval_readiness=args.approval_readiness,
            dry_run=dry_run,
            actor=args.actor,
        )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
