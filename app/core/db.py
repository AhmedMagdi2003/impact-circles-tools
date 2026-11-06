"""SQLite locally, or PostgreSQL for a diskless Render trial."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.migrations import run_migrations, utc_now


class HybridRow(dict):
    """Keep SQLite's column-name and positional row access on PostgreSQL."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def fetchone(self):
        row = self.cursor.fetchone()
        return HybridRow(row) if row is not None else None

    def fetchall(self):
        return [HybridRow(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        return (HybridRow(row) for row in self.cursor)


class PostgresConnection:
    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row

        self.connection = psycopg.connect(url, row_factory=dict_row, connect_timeout=10)

    def execute(self, query: str, params=()):
        if query.strip().upper() == "BEGIN IMMEDIATE":
            query = "LOCK TABLE finance_records IN SHARE ROW EXCLUSIVE MODE"
        else:
            query = query.replace("?", "%s")
        return PostgresCursor(self.connection.execute(query, params))

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def connect(path: Path):
    if database_url := os.environ.get("DATABASE_URL", ""):
        return PostgresConnection(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 15000")
    return db


@contextmanager
def database(path: Path):
    db = connect(path)
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(path: Path) -> None:
    with database(path) as db:
        if os.environ.get("DATABASE_URL"):
            from app.core.postgres_migrations import run_postgres_migrations
            run_postgres_migrations(db)
        else:
            run_migrations(db)


def store_source(db, kind: str, record_id: int, data: bytes) -> None:
    table = {"finance": "finance_sources", "content": "content_sources"}[kind]
    db.execute(f"INSERT INTO {table}(record_id, data) VALUES (?, ?)", (record_id, data))


def get_source(db, kind: str, record_id: int) -> bytes | None:
    table = {"finance": "finance_sources", "content": "content_sources"}[kind]
    row = db.execute(f"SELECT data FROM {table} WHERE record_id = ?", (record_id,)).fetchone()
    return bytes(row["data"]) if row else None


def setting(db: sqlite3.Connection, key: str) -> str:
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else ""


def set_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))


def get_projects(db: sqlite3.Connection, active_only: bool = True, owner_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM projects"
    conditions, params = [], []
    if active_only:
        conditions.append("active = 1")
    if owner_id is not None:
        conditions.append("created_by = ?")
        params.append(owner_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id ASC"
    return [dict(r) for r in db.execute(query, params).fetchall()]


def get_project(db: sqlite3.Connection, project_id: int) -> dict | None:
    row = db.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return dict(row) if row else None


def get_destination_rates(db: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in db.execute("SELECT * FROM destination_rates ORDER BY destination ASC").fetchall()]
