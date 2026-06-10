from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.review.candidate_audit import audit_candidates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Report candidate audit counts and review buckets.")
    parser.add_argument("--source-job-id", type=int, default=None)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output-json", type=Path, default=None)
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        report = audit_candidates(db, source_job_id=args.source_job_id, limit_samples=args.samples)

    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
        print(str(args.output_json))
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
