"""Unified FastAPI entry point for Impact Circles team tools.

Combines finance document workflows and project-aware content generation
into a single maintainable application with role-based auth, SQLite store,
and Google Sheets exports.
"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from app.core.admin_routes import router as admin_router
from app.core.auth import hash_password, require_record_access, require_user, verify_form_csrf
from app.core.config import Config, load_config
from app.core.db import database, initialize, utc_now
from app.core.routes import router as core_router
from app.content.routes import _content_row, router as content_router
from app.finance.routes import _finance_row, router as finance_router
from app.finance.rules import calculate_claim
from app.integrations.ai import ask_model
from app.integrations.sheets import append_once

ROOT = Path(__file__).resolve().parent


async def sync_record(config: Config, kind: str, record_id: int) -> str | None:
    """Sync a confirmed finance claim or approved content draft to Google Sheets."""
    table = "finance_records" if kind == "finance" else "content_records"
    with database(config.database_path) as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
        if not row or not row["confirmed_at" if kind == "finance" else "approved_at"]:
            return "This item has not been confirmed"
        if row["sheet_synced_at"]:
            return None
        record = dict(row)

    try:
        if kind == "finance":
            details = json.loads(record["details_json"])
            calc = json.loads(record["calculation_json"])
            values = _finance_row(config, record, details, calc)
            tab = "Finance"
        else:
            with database(config.database_path) as db:
                p_row = db.execute(
                    "SELECT name FROM projects WHERE id = ?", (record.get("project_id", 1),)
                ).fetchone()
                project_name = p_row["name"] if p_row else "General"
            values = _content_row(config, record, json.loads(record["drafts_json"]), project_name)
            tab = "Content"

        values.extend([record.get("creator_name") or "", record.get("creator_email") or "",
                       record.get("actor_name") or "", record.get("actor_email") or ""])
        await append_once(config, tab, values)
    except Exception as exc:
        error = (
            str(exc)[:160]
            if isinstance(exc, ValueError)
            else "Google Sheets could not be reached. Ask an admin to check the connection."
        )
        with database(config.database_path) as db:
            db.execute(f"UPDATE {table} SET sheet_error = ? WHERE id = ?", (error, record_id))
        return error

    with database(config.database_path) as db:
        db.execute(
            f"UPDATE {table} SET sheet_synced_at = ?, sheet_error = NULL WHERE id = ?",
            (utc_now(), record_id),
        )
    return None


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if len(config.session_secret) < 24:
            raise RuntimeError("Set a unique SESSION_SECRET of at least 24 characters")
        initialize(config.database_path)
        with database(config.database_path) as db:
            count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if count == 0:
                email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "").lower().strip()
                password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
                if not email or not password:
                    raise RuntimeError("Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD")
                db.execute(
                    "INSERT INTO users(email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (email, hash_password(password), "admin", utc_now()),
                )
            db.execute("UPDATE projects SET created_by=(SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1) "
                       "WHERE created_by IS NULL")
        yield

    app = FastAPI(
        title="Impact Circles Tools",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.config = config
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if (not request.url.path.startswith("/static/")
                or request.url.path in {"/static/app.js", "/static/style.css"}):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    # Mount modular routers
    app.include_router(core_router)
    app.include_router(admin_router)
    app.include_router(finance_router)
    app.include_router(content_router)

    # Universal Sheets sync retry route
    @app.post("/{kind}/{record_id}/sync")
    async def universal_sync_retry(request: Request, kind: str, record_id: int):
        user = require_user(request, config)
        await verify_form_csrf(request, user)
        if kind not in {"finance", "content"}:
            raise HTTPException(404, "Unknown export kind")
        with database(config.database_path) as db:
            require_record_access(db, "finance_records" if kind == "finance" else "content_records", record_id, user)
        await sync_record(config, kind, record_id)
        from app.core.auth import redirect
        return redirect(f"/{kind}/{record_id}")

    return app


app = create_app()
