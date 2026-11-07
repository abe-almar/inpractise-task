"""Connection and schema management: the numbered SQL files under sql/ are applied once each and recorded in
schema_migrations, so a later change is a new 002_*.sql file, never an edit of an applied one."""

from __future__ import annotations

import logging
from importlib import resources

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

MIGRATION_LOCK = 727401  # arbitrary advisory-lock key so two runs never migrate at once


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


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
