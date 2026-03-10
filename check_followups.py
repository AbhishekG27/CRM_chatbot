from db import get_db
import re

db = get_db()
r = re.compile('follow', re.I)
stages = list(db['funnel_stages'].find({'name': r}))

print(f"Found {len(stages)} stages with 'follow':")
for s in stages:
    sid = str(s['_id'])
    name = s.get('name')
    count = db['leads'].count_documents({'funnel_stage_id': sid})
    print(f"- {name} ({sid}) - Leads: {count}")
