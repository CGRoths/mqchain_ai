from collections import Counter
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.review.approval_registry import get_unique_candidate_groups
from app.review.source_verification import find_source_verification_for_candidate

engine = create_engine("sqlite:///data/mqchain_ai.db")
job = 7

with Session(engine) as db:
    groups = get_unique_candidate_groups(db, source_job_id=job)

    print("group_count =", len(groups))
    print("readiness_count =", dict(Counter(g.approval_readiness for g in groups)))
    print("address_class_count =", dict(Counter(g.address_class for g in groups)))
    print("source_trust_count =", dict(Counter(g.source_trust_status for g in groups)))

    print("\nSAMPLES:")
    for g in groups[:30]:
        c = g.candidates[0]
        v = find_source_verification_for_candidate(db, c)
        print("=" * 100)
        print("candidate_id:", c.id)
        print("role:", c.suggested_role)
        print("class:", g.address_class)
        print("network:", c.source_network, "chain:", c.chain_slug)
        print("confidence:", c.confidence_initial)
        print("len(c.evidence):", len(c.evidence))
        print("group readiness:", g.approval_readiness)
        print("group source trust:", g.source_trust_status)
        print("verification:", None if v is None else {
            "id": v.id,
            "scope": v.verification_scope,
            "status": v.verification_status,
            "trust": v.source_trust,
            "verified_by": v.verified_by,
            "verified_at": str(v.verified_at),
        })
