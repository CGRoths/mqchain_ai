import sqlite3

con = sqlite3.connect("data/mqchain_ai.db")
cur = con.cursor()

query = """
select
    e.entity_name,
    a.chain_slug,
    a.address_class,
    r.role,
    count(*)
from mq_approved_addresses a
join mq_entities e
    on e.id = a.entity_id
join mq_approved_address_roles r
    on r.approved_address_id = a.id
where lower(e.entity_name) like '%binance%'
group by
    e.entity_name,
    a.chain_slug,
    a.address_class,
    r.role
order by
    count(*) desc
"""

rows = cur.execute(query).fetchall()

if not rows:
    print("No approved Binance hot/cold/reserve rows found.")
else:
    for row in rows:
        print(row)

con.close()
