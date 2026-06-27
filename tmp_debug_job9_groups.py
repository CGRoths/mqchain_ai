from collections import Counter
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.review.approval_registry import get_unique_candidate_groups

engine = create_engine("sqlite:///data/mqchain_ai.db")
job = 9

with Session(engine) as db:
    groups = get_unique_candidate_groups(db, source_job_id=job)

    print("readiness_count =", dict(Counter(g.approval_readiness for g in groups)))
    print("address_class_count =", dict(Counter(g.address_class for g in groups)))
    print("source_trust_count =", dict(Counter(g.source_trust_status for g in groups)))

    print("\nLOW CONFIDENCE GROUPS:")
    for g in groups:
        if g.approval_readiness == "needs_review_official_low_confidence":
            c = g.candidates[0]
            print("=" * 100)
            print("candidate_id:", c.id)
            print("entity:", c.entity_name)
            print("chain:", c.chain_slug)
            print("address:", c.normalized_address)
            print("role:", c.suggested_role)
            print("class:", g.address_class)
            print("trust:", g.source_trust_status)
            print("confidence:", c.confidence_initial)
            print("evidence:", len(c.evidence))

    print("\nUNMAPPED ROLE SAMPLES:")
    shown = 0
    for g in groups:
        if g.approval_readiness == "needs_review_unmapped_official_role":
            c = g.candidates[0]
            print("=" * 100)
            print("candidate_id:", c.id)
            print("chain:", c.chain_slug)
            print("address:", c.normalized_address)
            print("role:", c.suggested_role)
            print("class:", g.address_class)
            print("trust:", g.source_trust_status)
            print("confidence:", c.confidence_initial)
            shown += 1
            if shown >= 30:
                break
