from db import get_db
from bson import ObjectId

db = get_db()
ids = db['leads'].distinct('funnel_stage_id')
print(f"Found {len(ids)} unique funnel_stage_id values in leads:")
for i in ids:
    if i:
        try:
            stage = db['funnel_stages'].find_one({'_id': ObjectId(i) if isinstance(i, str) and len(i)==24 else i})
            name = stage.get('name') if stage else "Unknown"
        except:
            name = "Error Fetching"
        print(f"- {i} ({name})")
    else:
        print("- None")
