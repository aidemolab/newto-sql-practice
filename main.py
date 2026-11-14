"""
Newto SQL Practice - pilot app
------------------------------
A small web app: the learner types SQL in the browser, the server runs it
against the RetailHub PostgreSQL database and sends the rows back.

Pilot scope: ONE shared, READ-ONLY database for everyone.
Safety comes from four layers:
  1. The database login (practice_ro) is only allowed to SELECT.
  2. Every session is read-only and has a statement timeout.
  3. The app stops reading after MAX_ROWS rows.
  4. A watchdog cancels any query that outlives the time limit.

Settings are read from a file called .env (see .env.example).
"""

import os
import secrets
import threading
import time
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from psycopg.types.string import TextLoader
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------- settings
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "retailhub")
DB_USER = os.getenv("DB_USER", "practice_ro")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

MAX_ROWS = int(os.getenv("MAX_ROWS", "500"))            # rows shown per query
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "5"))  # time limit per query
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "8"))  # queries at one time
ACCESS_CODE = os.getenv("ACCESS_CODE", "")              # empty = no code needed

MAX_CELL_CHARS = 2000   # very long values are shortened on screen
NUMERIC_OIDS = {20, 21, 23, 26, 700, 701, 790, 1700}  # right-aligned in the table
BOOL_OID = 16

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)

app = FastAPI(title="Newto SQL Practice", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- database
def connect() -> psycopg.Connection:
    """Open a fresh, read-only connection. One per request, then closed,
    so nothing a learner SETs can leak into somebody else's query."""
    conn = psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=5,
        autocommit=True,
        application_name="newto-sql-practice",
        client_encoding="utf8",
        options=(
            f"-c statement_timeout={TIMEOUT_SECONDS * 1000} "
            "-c default_transaction_read_only=on "
            "-c idle_in_transaction_session_timeout=10000"
        ),
    )
    # Return every value exactly as PostgreSQL prints it (as psql would show it),
    # so dates, money and decimals look the same here as in pgAdmin.
    for t in psycopg.postgres.types:
        conn.adapters.register_loader(t.oid, TextLoader)
        if t.array_oid:
            conn.adapters.register_loader(t.array_oid, TextLoader)
    return conn


def describe_columns(conn: psycopg.Connection, sql: str) -> list[dict]:
    """Column headings of a query, without executing it."""
    try:
        pg = conn.pgconn
        prepared = pg.prepare(b"", sql.encode("utf-8"))
        if prepared.status != psycopg.pq.ExecStatus.COMMAND_OK:
            return []
        info = pg.describe_prepared(b"")
        return [
            {"name": (info.fname(i) or b"").decode("utf-8", "replace"),
             "numeric": info.ftype(i) in NUMERIC_OIDS,
             "bool": info.ftype(i) == BOOL_OID}
            for i in range(info.nfields)
        ]
    except Exception:
        return []


def check_access(code: str | None) -> None:
    if ACCESS_CODE and not secrets.compare_digest(code or "", ACCESS_CODE):
        raise HTTPException(status_code=401, detail="Access code required.")


def friendly_error(exc: psycopg.Error, sql: str) -> dict:
    """Turn a PostgreSQL error into something a beginner can act on."""
    diag = exc.diag
    state = diag.sqlstate or ""
    message = diag.message_primary or str(exc).strip() or "The query could not be run."
    line = None
    if diag.statement_position:
        try:
            pos = int(diag.statement_position)
            line = sql.count("\n", 0, max(pos - 1, 0)) + 1
        except ValueError:
            line = None

    note = None
    if "multiple commands" in message:
        message = "Run one statement at a time."
        note = ("This box runs a single statement. Keep one query, ending with "
                "one semicolon, and remove or run the others separately.")
        line = None
    elif state == "57014":  # query_canceled
        message = f"Query stopped: it ran for longer than {TIMEOUT_SECONDS} seconds."
        note = "Check your joins. A missing join condition can multiply the rows."
    elif state in ("25006", "42501"):  # read-only transaction / no privilege
        note = ("This practice database is read-only for now, so you can look at "
                "data with SELECT but not change it.")
    elif state == "42P01":
        note = "Check the table name against the list of tables on the left."
    elif state == "42703":
        note = "Check the column name against the table's columns on the left."
    elif state == "42601" and note is None:
        note = "Check for a missing comma, quote or keyword near the place shown."

    return {
        "ok": False,
        "error": message,
        "detail": diag.message_detail,
        "hint": diag.message_hint,
        "note": note,
        "line": line,
        "sqlstate": state or None,
    }


# ---------------------------------------------------------------- API
class QueryIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)


@app.get("/api/config")
def config():
    return {
        "access_required": bool(ACCESS_CODE),
        "max_rows": MAX_ROWS,
        "timeout_seconds": TIMEOUT_SECONDS,
        "database": DB_NAME,
    }


@app.get("/api/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1")
        return {"ok": True}
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database not reachable.")


SCHEMA_SQL = """
SELECT c.relname,
       a.attname,
       format_type(a.atttypid, a.atttypmod),
       EXISTS (SELECT 1 FROM pg_index i
               WHERE i.indrelid = c.oid AND i.indisprimary
                 AND a.attnum = ANY (i.indkey)) AS is_pk
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE n.nspname = 'public' AND c.relkind = 'r'
  AND a.attnum > 0 AND NOT a.attisdropped
  AND has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname, a.attnum
"""


@app.get("/api/schema")
def schema(x_access_code: str | None = Header(default=None)):
    """Tables and columns, for the panel on the left of the page."""
    check_access(x_access_code)
    try:
        with connect() as conn:
            rows = conn.execute(SCHEMA_SQL).fetchall()
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database not reachable.")
    tables: dict[str, list] = {}
    for table, column, dtype, is_pk in rows:
        dtype = (dtype.replace("character varying", "varchar")
                      .replace("character", "char")
                      .replace("timestamp without time zone", "timestamp"))
        tables.setdefault(table, []).append(
            {"name": column, "type": dtype, "pk": is_pk == "t"})
    return {"tables": [{"name": k, "columns": v} for k, v in tables.items()]}


@app.post("/api/query")
def run_query(body: QueryIn, x_access_code: str | None = Header(default=None)):
    check_access(x_access_code)
    sql = body.sql.strip()
    if not sql:
        return {"ok": False, "error": "Type a query first.", "line": None}

    if not _slots.acquire(timeout=3):
        return {"ok": False, "error": "The practice server is busy. Try again in a moment.",
                "line": None}

    started = time.perf_counter()
    conn = None
    watchdog = None
    try:
        conn = connect()
        # Layer 4: cancel from outside if the query outlives the limit,
        # even if the learner changed statement_timeout themselves.
        watchdog = threading.Timer(TIMEOUT_SECONDS + 1.5, conn.cancel_safe)
        watchdog.daemon = True
        watchdog.start()

        columns, rows, truncated = [], [], False
        cur = conn.cursor()
        # stream() reads one row at a time, so a huge result never fills memory.
        try:
            for record in cur.stream(sql):
                if len(rows) >= MAX_ROWS:
                    truncated = True
                    break
                rows.append(record)
        except psycopg.ProgrammingError as exc:
            # Statements such as SET run fine but have no rows to show.
            if exc.diag.sqlstate or "didn't produce a result" not in str(exc):
                raise

        if cur.description:
            columns = [
                {"name": col.name, "numeric": col.type_code in NUMERIC_OIDS,
                 "bool": col.type_code == BOOL_OID}
                for col in cur.description
            ]
        elif not rows:
            # A SELECT that matched no rows still has column headings to show.
            # Ask PostgreSQL to describe the query (this does not run it again).
            columns = describe_columns(conn, sql)
        status = cur.statusmessage
        if truncated:
            conn.cancel_safe()

        out_rows = []
        for record in rows:
            out = []
            for value, col in zip(record, columns):
                if value is None:
                    out.append(None)
                    continue
                if isinstance(value, (bytes, bytearray, memoryview)):
                    value = bytes(value).decode("utf-8", "replace")
                value = str(value)
                if col["bool"]:
                    value = {"t": "true", "f": "false"}.get(value, value)
                if len(value) > MAX_CELL_CHARS:
                    value = value[:MAX_CELL_CHARS] + "…"
                out.append(value)
            out_rows.append(out)

        return {
            "ok": True,
            "columns": columns,
            "rows": out_rows,
            "row_count": len(out_rows),
            "truncated": truncated,
            "max_rows": MAX_ROWS,
            "status": status,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }
    except psycopg.OperationalError as exc:
        if exc.diag.sqlstate:
            return friendly_error(exc, sql)
        return {"ok": False, "line": None,
                "error": "The practice database could not be reached. Please try again shortly."}
    except psycopg.Error as exc:
        return friendly_error(exc, sql)
    finally:
        if watchdog:
            watchdog.cancel()
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        _slots.release()


# ---------------------------------------------------------------- web page
@app.get("/")
def index():
    return FileResponse(BASE_DIR / "static" / "index.html",
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
