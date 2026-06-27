import sqlite3, json

job = 7
db = sqlite3.connect(r"data\mqchain_ai.db")
db.row_factory = sqlite3.Row

rows = db.execute("""
select
  id,
  source_job_id,
  source_document_id,
  source_sheet,
  candidate_id,
  entity_name,
  verification_scope,
  verification_status,
  source_trust,
  verified_by,
  verified_at,
  source_url,
  official_referrer_url
from mq_source_verifications
where source_job_id = ?
order by id desc
""", (job,)).fetchall()

print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))
