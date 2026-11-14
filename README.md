# Newto SQL Practice (pilot)

A small web app for the Newto SQL course. The learner types SQL in the
browser; the server runs it against the RetailHub PostgreSQL database and
shows the rows.

Pilot scope: one shared, read-only database. Per-learner databases,
feedback, hints and checkpoints come later.

## What is in this folder

| File | What it is |
| --- | --- |
| `main.py` | The server program (Python, FastAPI). |
| `static/index.html` | The web page: editor, Run button, results table. |
| `db/create_practice_role.sql` | Creates the read-only database login. |
| `requirements.txt` | The Python packages the app needs. |
| `.env.example` | Template for the settings file `.env`. |

## Run it on a laptop (Windows PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env        # then edit .env and set DB_PASSWORD
.venv\Scripts\python -m uvicorn main:app --port 8000
```

Then open http://127.0.0.1:8000 in a browser. Stop the app with Ctrl+C.

## Safety

1. The database login `practice_ro` can only SELECT.
2. Every session is read-only with a 5-second statement timeout.
3. The app shows at most 500 rows per query.
4. A watchdog cancels any query that outlives the time limit.
5. Passwords live in `.env`, which Git ignores.
