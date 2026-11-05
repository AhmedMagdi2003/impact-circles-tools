"""Phase 1 Acceptance Gate Tests:
- Login and session verification
- Role restrictions (member vs admin)
- CSRF protection
- Login throttling after repeated failures
- Versioned database migrations and upgrade preservation
- Backup creation and restoration verification
"""

import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, get_projects, initialize, run_migrations
from app.web import create_app


def test_auth_and_role_restrictions(tmp_path):
    db_path = tmp_path / "test.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)
    # Create an admin and a regular member
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("admin@example.org", hash_password("admin-password-123"), "admin", 1, "2026-11-01T00:00:00Z"),
        )
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("member@example.org", hash_password("member-password-123"), "member", 1, "2026-11-01T00:00:00Z"),
        )

    with TestClient(create_app(config)) as client:
        # Unauthenticated user redirected to login
        res = client.get("/", follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/login"

        # Member login
        res = client.post("/login", data={"email": "member@example.org", "password": "member-password-123"}, follow_redirects=False)
        assert res.status_code == 303
        assert res.headers["location"] == "/"

        # Member can view finance and content
        res = client.get("/finance")
        assert res.status_code == 200
        res = client.get("/content")
        assert res.status_code == 200

        # Member is FORBIDDEN from admin
        res = client.get("/admin")
        assert res.status_code == 403

        # Logout
        csrf = client.get("/").text.split('name="csrf" value="')[1].split('"')[0]
        res = client.post("/logout", data={"csrf": csrf}, follow_redirects=False)
        assert res.status_code == 303

        # Admin login
        res = client.post("/login", data={"email": "admin@example.org", "password": "admin-password-123"}, follow_redirects=False)
        assert res.status_code == 303

        # Admin can view admin page
        res = client.get("/admin")
        assert res.status_code == 200
        assert "Settings" in res.text or "per_diem_rates" in res.text


def test_csrf_enforcement(tmp_path):
    db_path = tmp_path / "csrf_test.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("admin@example.org", hash_password("admin-password-123"), "admin", 1, "2026-11-01T00:00:00Z"),
        )

    with TestClient(create_app(config)) as client:
        client.post("/login", data={"email": "admin@example.org", "password": "admin-password-123"})
        # Bad CSRF on settings update
        res = client.post("/admin/settings", data={"csrf": "invalid-token", "per_diem_rates": "EUR,30.00"})
        assert res.status_code == 403


def test_login_throttling(tmp_path):
    db_path = tmp_path / "throttle_test.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("victim@example.org", hash_password("victim-password-123"), "member", 1, "2026-11-01T00:00:00Z"),
        )

    with TestClient(create_app(config)) as client:
        # 5 failed login attempts
        for _ in range(5):
            res = client.post("/login", data={"email": "victim@example.org", "password": "wrong-password"})
            assert "Email or password is incorrect" in res.text

        # 6th attempt should trigger 15-minute lockout message
        res = client.post("/login", data={"email": "victim@example.org", "password": "victim-password-123"})
        assert "Too many attempts" in res.text


def test_migration_from_legacy_v1_database(tmp_path):
    """Verify that an existing unversioned database upgrades cleanly without losing data."""
    legacy_db = tmp_path / "legacy.db"
    # Create raw unversioned v1 database without schema_migrations table
    conn = sqlite3.connect(legacy_db)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            csrf_token TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE login_attempts (email TEXT NOT NULL, attempted_at TEXT NOT NULL);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE finance_records (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id),
            source_name TEXT NOT NULL, source_mime TEXT NOT NULL, source_hash TEXT NOT NULL, status TEXT NOT NULL,
            details_json TEXT NOT NULL, calculation_json TEXT, confirmed_at TEXT,
            sheet_synced_at TEXT, sheet_error TEXT
        );
        CREATE TABLE content_records (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id),
            title TEXT NOT NULL, source_text TEXT NOT NULL, source_name TEXT,
            source_mime TEXT, drafts_json TEXT NOT NULL,
            status TEXT NOT NULL, approved_at TEXT, sheet_synced_at TEXT, sheet_error TEXT,
            published_at TEXT, publication_error TEXT
        );
        INSERT INTO users VALUES (1, 'legacy_user@example.org', 'fakehash', 'admin', 1, '2026-10-01T00:00:00Z');
        INSERT INTO content_records VALUES (1, '2026-10-01T00:00:00Z', 1, 'Old Draft', 'Source', NULL, NULL, '{}', 'Needs your attention', NULL, NULL, NULL, NULL, NULL);
        INSERT INTO finance_records VALUES (1, '2026-10-01T00:00:00Z', 1, 'invoice.pdf', 'application/pdf', 'hash1', 'Confirmed', '{}', '{}', '2026-10-02T00:00:00Z', NULL, NULL);
    """)
    conn.commit()
    conn.close()

    # Now run migrations through initialize
    initialize(legacy_db)

    # Verify migration results
    with database(legacy_db) as db:
        # Check migrations table exists and versions recorded
        versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
        assert 1 in versions
        assert 2 in versions
        assert 3 in versions
        assert 4 in versions
        assert 5 in versions
        assert 6 in versions

        # Check existing user preserved
        user = db.execute("SELECT email, role FROM users WHERE id = 1").fetchone()
        assert user["email"] == "legacy_user@example.org"
        assert user["role"] == "admin"

        # Check legacy content record migrated to default project (project_id = 1)
        content = db.execute("SELECT id, title, project_id FROM content_records WHERE id = 1").fetchone()
        assert content["title"] == "Old Draft"
        assert content["project_id"] == 1

        # Check default project exists
        projects = get_projects(db)
        assert len(projects) >= 1
        assert projects[0]["name"] == "General / Unassigned"

        # Check destination_rates exists and has seeded rates
        rates = db.execute("SELECT COUNT(*) FROM destination_rates").fetchone()[0]
        assert rates >= 5

        # Check finance_records has new columns
        cols = [c[1] for c in db.execute("PRAGMA table_info(finance_records)").fetchall()]
        assert "total_eur" in cols
        assert "source_currency" in cols


def test_backup_and_restore(tmp_path):
    """Verify scripts/backup.py produces a restorable archive."""
    db_path = tmp_path / "backup_src.db"
    initialize(db_path)
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("test_backup@example.org", hash_password("backup-password-123"), "member", 1, "2026-11-01T00:00:00Z"),
        )
    # Add dummy upload
    uploads = db_path.parent / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    sample_file = uploads / "finance-1"
    sample_file.write_bytes(b"Dummy upload payload")

    backup_zip = tmp_path / "backup.zip"
    cmd = [sys.executable, "-m", "scripts.backup", "--output", str(backup_zip)]
    res = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1]), env={"DATABASE_PATH": str(db_path)}, capture_output=True, text=True)
    assert res.returncode == 0
    assert backup_zip.is_file()

    # Test restoration into new path
    restore_dir = tmp_path / "restored"
    restore_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup_zip, "r") as zf:
        zf.extractall(restore_dir)

    restored_db = restore_dir / "impact_tools.db"
    assert restored_db.is_file()
    with database(restored_db) as db:
        row = db.execute("SELECT email FROM users WHERE email = 'test_backup@example.org'").fetchone()
        assert row is not None
        assert row["email"] == "test_backup@example.org"

    assert (restore_dir / "uploads" / "finance-1").read_bytes() == b"Dummy upload payload"
