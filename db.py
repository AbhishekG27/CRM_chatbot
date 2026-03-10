from pymongo import MongoClient
from dotenv import load_dotenv
import os

load_dotenv()

_client = None
_db = None

def get_db():
    global _client, _db
    if _db is None:
        uri = os.getenv("MONGO_URI")
        _client = MongoClient(uri)
        _db = _client[os.getenv("DB_NAME", "crm_prod")]
    return _db

def get_collection(name):
    return get_db()[name]
