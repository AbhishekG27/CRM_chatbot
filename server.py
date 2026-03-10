import os
import json
import uuid
import traceback
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from db import get_db
from gemini_ai import ask_gemini, clear_session, inject_result
from query_executor import execute_query, serialize

load_dotenv()

app = Flask(__name__, static_folder="public", static_url_path="")
CORS(app)

PORT = int(os.getenv("PORT", 3000))
LOG_FILE = os.path.join(os.path.dirname(__file__), "logs", "chat_logs.json")

# Ensure logs directory exists
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)


def read_logs():
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_log(session_id, user_message, ai_response, db_result):
    """Append chat log entry to local JSON file."""
    try:
        logs = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)

        log_entry = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "question": user_message,
            "answer": ai_response.get("answer", ""),
            "data": db_result.get("data"),  # Save results for UI persistence
            "db_query": {
                "collection": ai_response.get("collection"),
                "operation": ai_response.get("operation"),
                "filter": ai_response.get("query"),
                "pipeline": ai_response.get("pipeline"),
                "sort": ai_response.get("sort"),
                "limit": ai_response.get("limit"),
            },
            "result_count": db_result.get("count", 0),
            "success": db_result.get("success", False),
            "error": db_result.get("error"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        logs.append(log_entry)
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[LOG ERROR] Failed to save log: {e}")


@app.route("/api/leads-count")
def leads_count():
    try:
        db = get_db()
        count = db["leads"].count_documents({})
        return jsonify({"count": count})
    except Exception as e:
        return jsonify({"count": None, "error": str(e)}), 500


@app.route("/")
def index():
    return send_from_directory("public", "index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    try:
        body = request.get_json()
        if not body or not body.get("message"):
            return jsonify({"error": "Message is required"}), 400

        user_message = body["message"].strip()
        session_id = body.get("session_id") or str(uuid.uuid4())

        # Step 1: Ask Gemini to generate query + answer (no DB data sent to AI)
        ai_response = ask_gemini(session_id, user_message)

        # Step 2: Execute the MongoDB query
        db_result = execute_query(ai_response)

        # Step 3: If AI gave a vague placeholder answer, replace with actual result
        if db_result.get("success"):
            data = db_result.get("data")
            col = ai_response.get("collection", "records")
            op = ai_response.get("operation", "none")
            limit = ai_response.get("limit", 10)
            total = db_result.get("total_count")  # real total from count_documents

            if isinstance(data, dict) and "count" in data:
                # count_documents operation — give a direct answer
                ai_response["answer"] = f"There are **{data['count']}** {col} in total."

            elif isinstance(data, list) and len(data) > 0:
                shown = len(data)
                if op == "aggregate":
                    ai_response["answer"] = f"Here is the breakdown by group:"
                elif total is not None and total > shown:
                    ai_response["answer"] = (
                        f"Found **{total}** {col} in total. "
                        f"Here are the first {shown}:"
                    )
                elif total is not None:
                    ai_response["answer"] = f"Found **{total}** {col} matching your query:"
                else:
                    ai_response["answer"] = f"Found **{shown}** {col} matching your query:"

            elif (data is None or (isinstance(data, list) and len(data) == 0)) and op != "none":
                ai_response["answer"] = f"No {col} found matching your criteria."



        # Step 3: Inject real DB result back into AI history for follow-up accuracy
        inject_result(session_id, db_result, ai_response)

        # Step 4: Save log to local JSON file
        save_log(session_id, user_message, ai_response, db_result)

        # Step 4: Return response to frontend (no query info exposed)
        return jsonify({
            "session_id": session_id,
            "question": user_message,
            "answer": ai_response.get("answer", "I couldn't find an answer."),
            "data": db_result.get("data"),
            "count": db_result.get("count", 0),
            "total_count": db_result.get("total_count"),
            "success": db_result.get("success", True),
            "error": db_result.get("error"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/history", methods=["GET"])
def get_history():
    try:
        logs = read_logs()
        session_id = request.args.get("session_id")
        limit = int(request.args.get("limit", 50))
        if session_id:
            logs = [l for l in logs if l.get("session_id") == session_id]
        # Return most recent first
        logs = list(reversed(logs[-limit:]))
        return jsonify({"logs": logs, "count": len(logs)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/history", methods=["DELETE"])
def clear_history():
    try:
        session_id = request.args.get("session_id")
        if session_id:
            logs = read_logs()
            logs = [l for l in logs if l.get("session_id") != session_id]
            clear_session(session_id)
        else:
            logs = []
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, indent=2)
        return jsonify({"success": True})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print(f"🚀 CMR Chatbot running at http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
