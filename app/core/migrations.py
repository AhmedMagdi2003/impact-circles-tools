"""Versioned SQLite migrations.

Migrates an existing local database without losing users, uploads,
finance records or content drafts. Fresh installs and upgraded databases
arrive at the identical schema version.
"""

import sqlite3
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_applied_versions(db: sqlite3.Connection) -> set[int]:
    db.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)
    rows = db.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def record_migration(db: sqlite3.Connection, version: int, description: str) -> None:
    db.execute(
        "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
        (version, utc_now(), description),
    )


def apply_migration_1(db: sqlite3.Connection) -> None:
    """Migration 1: Base schema (users, sessions, attempts, settings, finance, content)."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','member')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        csrf_token TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS login_attempts (
        email TEXT NOT NULL,
        attempted_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS login_attempt_email ON login_attempts(email, attempted_at);
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS finance_records (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        created_by INTEGER NOT NULL REFERENCES users(id),
        source_name TEXT NOT NULL,
        source_mime TEXT NOT NULL,
        source_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        details_json TEXT NOT NULL,
        calculation_json TEXT,
        confirmed_at TEXT,
        sheet_synced_at TEXT,
        sheet_error TEXT
    );
    CREATE INDEX IF NOT EXISTS finance_hash ON finance_records(source_hash);
    CREATE TABLE IF NOT EXISTS content_records (
        id INTEGER PRIMARY KEY,
        created_at TEXT NOT NULL,
        created_by INTEGER NOT NULL REFERENCES users(id),
        title TEXT NOT NULL,
        source_text TEXT NOT NULL,
        source_name TEXT,
        source_mime TEXT,
        drafts_json TEXT NOT NULL,
        status TEXT NOT NULL,
        approved_at TEXT,
        sheet_synced_at TEXT,
        sheet_error TEXT,
        published_at TEXT,
        publication_error TEXT
    );
    """)
    for key, value in {
        "per_diem_rates": "EUR,35.50",
        "categories": "Travel,Accommodation,Meals,Operations,Other",
        "sheets_spreadsheet_id": "",
        "claim_include_booking_amount": "true",
    }.items():
        db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))


def apply_migration_2(db: sqlite3.Connection) -> None:
    """Migration 2: Projects entity and content project lineage."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        description TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    """)
    # Seed default project if absent
    db.execute("""
        INSERT OR IGNORE INTO projects (id, name, description, active, created_at)
        VALUES (1, 'General / Unassigned', 'General team activities and unassigned drafts', 1, ?)
    """, (utc_now(),))

    # Add project_id to content_records if not present
    cols = [col[1] for col in db.execute("PRAGMA table_info(content_records)").fetchall()]
    if "project_id" not in cols:
        db.execute("ALTER TABLE content_records ADD COLUMN project_id INTEGER REFERENCES projects(id)")
        db.execute("UPDATE content_records SET project_id = 1 WHERE project_id IS NULL")


def apply_migration_3(db: sqlite3.Connection) -> None:
    """Migration 3: Destination rate tables, EUR conversion fields, and itemized expense lines."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS destination_rates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        destination TEXT NOT NULL UNIQUE,
        currency TEXT NOT NULL,
        daily_allowance TEXT NOT NULL,
        hotel_ceiling TEXT NOT NULL,
        effective_date TEXT NOT NULL,
        source_notes TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS expense_lines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id INTEGER NOT NULL REFERENCES finance_records(id) ON DELETE CASCADE,
        line_type TEXT NOT NULL,
        description TEXT NOT NULL,
        original_amount TEXT NOT NULL,
        original_currency TEXT NOT NULL,
        fx_rate TEXT NOT NULL,
        fx_date TEXT NOT NULL,
        fx_provider TEXT NOT NULL,
        eur_amount TEXT NOT NULL,
        evidence_doc TEXT
    );
    """)

    # Seed destination rates table if empty
    existing_dest = db.execute("SELECT COUNT(*) FROM destination_rates").fetchone()[0]
    if existing_dest == 0:
        default_rates = [
            ("Germany", "EUR", "40.00", "150.00", "2026-01-01", "EU Staff Regs 2025/693 benchmark"),
            ("Egypt", "EUR", "35.00", "120.00", "2026-01-01", "MENA project internal rate"),
            ("Belgium", "EUR", "42.00", "160.00", "2026-01-01", "EU Headquarter / Brussels standard"),
            ("France", "EUR", "42.00", "160.00", "2026-01-01", "Paris & French regional partner meetings"),
            ("Other / General", "EUR", "35.00", "130.00", "2026-01-01", "Default international allowance rate"),
        ]
        db.executemany("""
            INSERT OR IGNORE INTO destination_rates
            (destination, currency, daily_allowance, hotel_ceiling, effective_date, source_notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, default_rates)

    # Add columns to finance_records if not present
    cols = [col[1] for col in db.execute("PRAGMA table_info(finance_records)").fetchall()]
    if "total_eur" not in cols:
        db.execute("ALTER TABLE finance_records ADD COLUMN total_eur TEXT")
    if "source_currency" not in cols:
        db.execute("ALTER TABLE finance_records ADD COLUMN source_currency TEXT")
    if "destination" not in cols:
        db.execute("ALTER TABLE finance_records ADD COLUMN destination TEXT")
    if "trip_start" not in cols:
        db.execute("ALTER TABLE finance_records ADD COLUMN trip_start TEXT")
    if "trip_end" not in cols:
        db.execute("ALTER TABLE finance_records ADD COLUMN trip_end TEXT")


def apply_migration_4(db: sqlite3.Connection) -> None:
    """Keep ownership, duplicate decisions and human-readable account names."""
    for table, column, definition in (
        ("users", "display_name", "TEXT NOT NULL DEFAULT ''"),
        ("finance_records", "duplicate_of", "INTEGER REFERENCES finance_records(id)"),
        ("finance_records", "confirmed_by", "INTEGER REFERENCES users(id)"),
        ("finance_records", "duplicate_override_reason", "TEXT"),
        ("content_records", "approved_by", "INTEGER REFERENCES users(id)"),
    ):
        cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    db.execute("UPDATE users SET display_name = substr(email, 1, instr(email, '@') - 1) "
               "WHERE display_name = '' AND instr(email, '@') > 1")


def apply_migration_5(db: sqlite3.Connection) -> None:
    """Assign existing projects to their earliest creator, or the first admin."""
    cols = {row[1] for row in db.execute("PRAGMA table_info(projects)")}
    if "created_by" not in cols:
        db.execute("ALTER TABLE projects ADD COLUMN created_by INTEGER REFERENCES users(id)")
    db.execute("""UPDATE projects SET created_by = COALESCE(
        (SELECT created_by FROM content_records WHERE project_id = projects.id ORDER BY id LIMIT 1),
        (SELECT id FROM users WHERE role = 'admin' ORDER BY id LIMIT 1))
        WHERE created_by IS NULL""")


def apply_migration_6(db: sqlite3.Connection) -> None:
    """Snapshot names and emails so later account edits do not rewrite history."""
    for table in ("finance_records", "content_records"):
        cols = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for column in ("creator_name", "creator_email", "actor_name", "actor_email"):
            if column not in cols:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        db.execute(f"""UPDATE {table} SET
            creator_name = COALESCE((SELECT COALESCE(NULLIF(display_name, ''), substr(email, 1, instr(email, '@') - 1))
                                     FROM users WHERE id={table}.created_by), ''),
            creator_email = COALESCE((SELECT email FROM users WHERE id={table}.created_by), '')
            WHERE creator_email = ''""")


def apply_migration_7(db: sqlite3.Connection) -> None:
    """Keep the existing EUR allowance and remove inactive currency rates."""
    row = db.execute("SELECT value FROM settings WHERE key='per_diem_rates'").fetchone()
    eur_rate = next((line.split(",", 1)[1].strip() for line in (row[0] if row else "").splitlines()
                     if line.strip().upper().startswith("EUR,")), "35.50")
    db.execute("UPDATE settings SET value=? WHERE key='per_diem_rates'", (f"EUR,{eur_rate}",))


def apply_migration_8(db: sqlite3.Connection) -> None:
    """Keep newly uploaded originals in the same durable store as their records."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS finance_sources (
            record_id INTEGER PRIMARY KEY REFERENCES finance_records(id) ON DELETE CASCADE,
            data BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS content_sources (
            record_id INTEGER PRIMARY KEY REFERENCES content_records(id) ON DELETE CASCADE,
            data BLOB NOT NULL
        );
    """)


MIGRATIONS = [
    (1, "Base schema with auth, finance, content and settings", apply_migration_1),
    (2, "Projects entity and content project lineage", apply_migration_2),
    (3, "Destination rate tables, EUR conversion and expense lines", apply_migration_3),
    (4, "Account names and duplicate review", apply_migration_4),
    (5, "Project ownership", apply_migration_5),
    (6, "Record attribution snapshots", apply_migration_6),
    (7, "EUR-only daily allowance", apply_migration_7),
    (8, "Original uploads stored with records", apply_migration_8),
]


def run_migrations(db: sqlite3.Connection) -> None:
    applied = get_applied_versions(db)

    # Detect legacy unversioned database (e.g. users table exists but schema_migrations is empty)
    if not applied:
        existing_tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "users" in existing_tables and "finance_records" in existing_tables:
            # Legacy v1 database detected: record v1 as already applied
            record_migration(db, 1, "Legacy v1 base schema recorded")
            applied.add(1)

    for version, desc, func in MIGRATIONS:
        if version not in applied:
            func(db)
            record_migration(db, version, desc)
            applied.add(version)
