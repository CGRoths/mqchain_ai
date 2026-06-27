import sqlite3, json

job = 7
db = sqlite3.connect(r"data\mqchain_ai.db")
db.row_factory = sqlite3.Row

rows = db.execute("""
select
  c.id,
  c.entity_name,
  c.source_network,
  c.chain_slug,
  c.normalized_address,
  c.suggested_role,
  c.confidence_initial,
  c.raw_reference,
  count(e.id) as evidence_count
from mq_address_candidates c
left join mq_address_evidence e on e.candidate_id = c.id
where c.source_job_id = ?
group by c.id
order by c.id
limit 30
""", (job,)).fetchall()

summary = {}

for r in rows:
    raw = r["raw_reference"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}

    explicit = None
    discovery = {}

    if isinstance(raw, dict):
        explicit = raw.get("approval_readiness")
        discovery = raw.get("discovery_permission") or {}
        if not explicit and isinstance(discovery, dict):
            explicit = discovery.get("approval_readiness")

    key = (r["evidence_count"], explicit)
    summary[key] = summary.get(key, 0) + 1

    print("=" * 100)
    print("candidate_id:", r["id"])
    print("role:", r["suggested_role"])
    print("network:", r["source_network"])
    print("chain_slug:", r["chain_slug"])
    print("confidence:", r["confidence_initial"])
    print("live_evidence_count:", r["evidence_count"])
    print("raw.approval_readiness:", raw.get("approval_readiness") if isinstance(raw, dict) else None)
    print("raw.discovery_permission.approval_readiness:", discovery.get("approval_readiness") if isinstance(discovery, dict) else None)
    print("raw.source_trust_level:", raw.get("source_trust_level") if isinstance(raw, dict) else None)

print("\nSUMMARY:")
for key, n in summary.items():
    print(key, "=>", n)
