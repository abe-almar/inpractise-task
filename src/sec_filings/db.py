"""Connection and schema management: the numbered SQL files under sql/ are applied once each and recorded in
schema_migrations, so a later change is a new 002_*.sql file, never an edit of an applied one."""

from __future__ import annotations

import logging
from importlib import resources

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

MIGRATION_LOCK = 727401  # arbitrary advisory-lock key so two runs never migrate at once


class DatabaseUnavailable(RuntimeError):
    """The database cannot be reached. The message is one line, fit to print, with the URL's password masked."""


def connect(database_url: str) -> psycopg.Connection:
    try:
        return psycopg.connect(database_url, row_factory=dict_row)
    except psycopg.ProgrammingError:   # the driver's text would quote the string, password included
        raise DatabaseUnavailable("DATABASE_URL is not a valid connection string") from None
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailable(_unreachable_message(database_url, exc)) from None


def describe(database_url: str) -> str:
    """`host:port/database`, for the first line of every command's output: where the job connects, never as whom or
    with what password. A string the driver cannot parse is named as such instead of raised, so the line can be
    printed before the connection attempt and the connection error still follows."""
    try:
        info = conninfo_to_dict(database_url)
    except psycopg.ProgrammingError:
        return "(invalid DATABASE_URL)"
    host, port = _address(info)
    return " ".join(f"{host}:{port}/{info.get('dbname') or 'default database'}".split())


def _address(info: dict) -> tuple[str, str]:
    return (info.get("host") or info.get("hostaddr") or "default host", info.get("port") or "default port")


def _unreachable_message(database_url: str, exc: psycopg.OperationalError) -> str:
    info = conninfo_to_dict(database_url)
    host, port = _address(info)
    reason = str(exc).partition("\n")[0]
    if info.get("password"):
        reason = reason.replace(info["password"], "***")
    message = (f"cannot connect to the database at {host}:{port} ({reason}). Is it running? "
               "If another program holds that port, `DB_PORT=<free port> make run` moves the database make supplies; "
               "with your own DATABASE_URL or .env, change the port there.")
    return " ".join(message.split())


def migrate(conn: psycopg.Connection) -> list[str]:
    """Apply every sql/NNN_*.sql not yet recorded. Idempotent; returns the files applied this time."""
    applied_now: list[str] = []
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK,))
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT version FROM schema_migrations")
        done = {row["version"] for row in cur.fetchall()}
        sql_dir = resources.files("sec_filings").joinpath("sql")
        for entry in sorted(sql_dir.iterdir(), key=lambda e: e.name):
            if not entry.name.endswith(".sql") or entry.name in done:
                continue
            log.info("applying migration %s", entry.name)
            cur.execute(entry.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (entry.name,))
            applied_now.append(entry.name)
        cur.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK,))
    conn.commit()
    return applied_now
