import json
import re
import os
import time
from google import genai
from google.genai import types
from dotenv import load_dotenv
from schema_context import SCHEMA_CONTEXT

load_dotenv()

_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Set it as an environment variable before starting the server."
    )

# Initialize the new google-genai client
client = genai.Client(api_key=_api_key)

MODEL = "gemini-2.0-flash"

# Store conversation histories per session_id
# Each session holds a list of google.genai Content objects (dicts)
_sessions = {}

MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]


def get_or_create_session(session_id: str):
    if session_id not in _sessions:
        _sessions[session_id] = []
    return _sessions[session_id]


def clear_session(session_id: str):
    if session_id in _sessions:
        del _sessions[session_id]


def inject_result(session_id: str, db_result: dict, ai_response: dict):
    """
    After running the DB query, append a short summary of the real result
    into the model's last history turn so follow-up questions have proper context.
    """
    history = _sessions.get(session_id)
    if not history:
        return

    col = ai_response.get("collection", "records")
    op = ai_response.get("operation", "none")
    data = db_result.get("data")

    if not db_result.get("success") or op == "none":
        return

    if isinstance(data, dict) and "count" in data:
        summary = f"[DB result: count={data['count']} for collection={col}]"
    elif isinstance(data, list):
        summary = f"[DB result: returned {len(data)} {col} records]"
    elif isinstance(data, dict):
        summary = f"[DB result: 1 {col} record returned]"
    else:
        summary = f"[DB result: no data returned for {col}]"

    # Append result context to the last model turn
    if history and history[-1]["role"] == "model":
        prev = history[-1]["parts"][0]["text"]
        history[-1]["parts"][0]["text"] = prev + "\n" + summary


def parse_gemini_response(text: str) -> dict:
    """Parse JSON from Gemini response, handling markdown code fences."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'\s*```\s*$', '', text, flags=re.MULTILINE)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {
        "collection": None,
        "operation": "none",
        "answer": text
    }


def ask_gemini(session_id: str, user_message: str) -> dict:
    """
    Send user question to Google Gemini 2.0 Flash.
    Returns structured dict with MongoDB query and answer.
    """
    history = get_or_create_session(session_id)

    prompt = (
        user_message + "\n\n"
        "Remember to use the default projection for the collection. "
        "Respond ONLY with the JSON format specified in your system instructions."
    )

    for attempt in range(MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=history + [{"role": "user", "parts": [{"text": prompt}]}],
                config=types.GenerateContentConfig(
                    system_instruction=SCHEMA_CONTEXT,
                    response_mime_type="application/json",
                ),
            )

            ai_text = response.text
            parsed = parse_gemini_response(ai_text)

            # Update history
            history.append({"role": "user", "parts": [{"text": prompt}]})
            history.append({"role": "model", "parts": [{"text": ai_text}]})

            # Trim to last 20 exchanges (40 entries)
            if len(history) > 40:
                _sessions[session_id] = history[-40:]

            return parsed

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower():
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_DELAYS[attempt]
                    time.sleep(wait)
                    continue
            raise

    raise Exception("Gemini API rate limit exceeded after retries.")
