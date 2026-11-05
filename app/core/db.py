"""Small SQLite store. Google Sheets is a readable export of approved records."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.migrations import run_migrations, utc_now


def connect(path: Path) -> sqlite3.Connection:
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
        run_migrations(db)


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
