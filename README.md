# CMR Chatbot (Render-ready)

## Run locally

1. Create `.env` (see `.env.example`)
2. Install deps:
   - `pip install -r requirements.txt`
3. Start:
   - `python server.py`
4. Open:
   - `http://localhost:3000`

## Deploy to Render

Use `render.yaml` (recommended) or set the commands manually:

- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn server:app --bind 0.0.0.0:$PORT`

Set environment variables in Render:

- `MONGO_URI`
- `DB_NAME` (e.g. `crm_prod`)
- `GEMINI_API_KEY`

