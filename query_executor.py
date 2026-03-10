import json
import traceback
from datetime import datetime, timezone
from bson import ObjectId
from db import get_db, get_collection


class MongoJSONEncoder(json.JSONEncoder):
    """Handle MongoDB-specific types for JSON serialization."""
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        return super().default(obj)


def serialize(data):
    """Convert MongoDB result to JSON-safe format."""
    return json.loads(json.dumps(data, cls=MongoJSONEncoder))


DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d",
]

def _parse_date(s: str):
    """Try to parse a string as a datetime. Returns datetime or original string."""
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return s

def convert_dates(obj):
    """Recursively convert ISO date strings in a query dict/list to datetime objects."""
    if isinstance(obj, dict):
        return {k: convert_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [convert_dates(i) for i in obj]
    if isinstance(obj, str) and len(obj) >= 10 and obj[:4].isdigit() and obj[4] == '-':
        return _parse_date(obj)
    return obj


# Blocked fields - never return these
BLOCKED_FIELDS = {"password": 0, "password_hash": 0}


def execute_query(gemini_response: dict) -> dict:
    """
    Execute a MongoDB query based on Gemini's structured response.
    Returns: { success, data, count, error }
    """
    collection_name = gemini_response.get("collection")
    operation = gemini_response.get("operation", "none")

    if not collection_name or operation == "none":
        return {"success": True, "data": None, "count": 0}

    # Safety check - only allow crm_prod collections
    allowed_collections = {
        "leads", "activities", "lead_forms",
        "users", "funnel_stages", "funnels", "businesses", "chat_logs"
    }
    if collection_name not in allowed_collections:
        return {"success": False, "error": f"Collection '{collection_name}' is not allowed."}

    try:
        col = get_collection(collection_name)
        query_filter = convert_dates(gemini_response.get("query", {}))
        pipeline = convert_dates(gemini_response.get("pipeline", []))
        projection = gemini_response.get("projection", {})
        sort = gemini_response.get("sort", {})
        limit = int(gemini_response.get("limit", 20))
        funnel_stage_name_by_id = None  # lazy-loaded for nicer aggregate output

        # Blocked fields for users — never expose passwords
        BLOCKED = {"password", "password_hash"}

        # Fix projection for users: MongoDB can't mix inclusion and exclusion
        if collection_name == "users" and projection:
            values = set(v for k, v in projection.items() if k != "_id")
            if 1 in values:
                # Inclusion projection: remove blocked keys if someone tried to include them
                for f in BLOCKED:
                    projection.pop(f, None)
                # We'll strip the blocked fields from results in Python instead
                strip_blocked = True
            else:
                # Exclusion projection: safe to add blocked field exclusions
                for f in BLOCKED:
                    projection[f] = 0
                strip_blocked = False
        elif collection_name == "users" and not projection:
            # No projection given: exclude password at DB level
            projection = {f: 0 for f in BLOCKED}
            strip_blocked = False
        else:
            strip_blocked = False

        if operation == "count_documents":
            count = col.count_documents(query_filter or {})
            return {"success": True, "data": {"count": count}, "count": 1}

        elif operation == "find_one":
            cursor = col.find_one(query_filter or {}, projection or None)
            if cursor and strip_blocked:
                for f in BLOCKED:
                    cursor.pop(f, None)
            result = serialize(cursor) if cursor else None
            return {"success": True, "data": result, "count": 1 if result else 0}

        elif operation == "find":
            total_count = col.count_documents(query_filter or {})
            cursor = col.find(query_filter or {}, projection or None)
            if sort:
                cursor = cursor.sort(list(sort.items()))
            cursor = cursor.limit(limit)
            raw = list(cursor)
            if strip_blocked:
                for doc in raw:
                    for f in BLOCKED:
                        doc.pop(f, None)

            # Resolve assigned_to ObjectId → user name
            try:
                user_map = {
                    str(u["_id"]): u.get("name", "")
                    for u in get_db()["users"].find({}, {"name": 1})
                }
                for doc in raw:
                    at = doc.get("assigned_to")
                    if at and str(at) in user_map:
                        doc["assigned_to"] = user_map[str(at)]
            except Exception:
                pass

            results = serialize(raw)
            return {"success": True, "data": results, "count": len(results), "total_count": total_count}


        elif operation == "aggregate":
            if not pipeline:
                return {"success": False, "error": "Aggregate operation requires a pipeline."}
            raw_results = list(col.aggregate(pipeline))
            # Rename '_id' key in aggregate results and map to human names
            HUMAN_MAP = {
                "leadpool": "Pipeline / Follow-up",
                "assigned": "Assigned to Agents",
                "not_interested": "Not Interested",
                "shreyas": "Shreyas Infra",
                "tekspot": "TekspotEdu"
            }
            
            def _looks_like_objectid(s: str) -> bool:
                if not isinstance(s, str) or len(s) != 24:
                    return False
                try:
                    int(s, 16)
                    return True
                except Exception:
                    return False

            def _stage_name_for_id(stage_id: str) -> str | None:
                nonlocal funnel_stage_name_by_id
                if not _looks_like_objectid(stage_id):
                    return None
                if funnel_stage_name_by_id is None:
                    try:
                        funnel_stage_name_by_id = {
                            str(s["_id"]): s.get("name", "")
                            for s in get_db()["funnel_stages"].find({}, {"name": 1})
                        }
                    except Exception:
                        funnel_stage_name_by_id = {}
                name = funnel_stage_name_by_id.get(stage_id)
                return name or None

            for doc in raw_results:
                if "_id" in doc:
                    id_val = doc["_id"]
                    # Pick a readable key name and value
                    target_key = "Group" # Generic but better than 'name'
                    
                    if id_val is None:
                        # For funnel-stage breakdowns, treat null as unassigned/new
                        doc["Stage"] = "New Leads / Unassigned"
                        doc.pop("Group", None)
                    elif isinstance(id_val, str):
                        # If grouping by funnel_stage_id, replace ObjectId-ish values with stage names
                        stage_name = _stage_name_for_id(id_val)
                        if stage_name:
                            doc["Stage"] = stage_name
                            doc.pop("Group", None)
                        else:
                            doc[target_key] = HUMAN_MAP.get(id_val.lower(), id_val)
                    elif isinstance(id_val, dict):
                        # Flatten nested _id and map values if possible
                        stage_set = False
                        for k, v in id_val.items():
                            if isinstance(v, str):
                                stage_name = _stage_name_for_id(v)
                                if stage_name and k in {"funnel_stage_id", "stage_id"}:
                                    doc["Stage"] = stage_name
                                    stage_set = True
                                else:
                                    doc[k] = HUMAN_MAP.get(v.lower(), v)
                            else:
                                doc[k] = v
                        if stage_set:
                            doc.pop("Group", None)
                    else:
                        doc[target_key] = str(id_val)
                    
                    if "_id" in doc:
                        del doc["_id"]

            results = serialize(raw_results)
            return {"success": True, "data": results, "count": len(results)}


        else:
            return {"success": False, "error": f"Unsupported operation: {operation}"}

    except Exception as e:
        traceback.print_exc()
        return {"success": False, "error": str(e)}
