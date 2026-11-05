"""Authentication, sessions, CSRF, templates, and file validation."""

import hashlib
import hmac
import os
from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import Config
from app.core.db import database, utc_now
from app.integrations.ai import Attachment

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())
MAX_UPLOAD = 8 * 1024 * 1024
ALLOWED = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def hash_password(password: str, salt: bytes | None = None) -> str:
    if len(password) < 12:
        raise ValueError("Password must have at least 12 characters")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split(":", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 260_000)
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


def token_hash(config: Config, token: str) -> str:
    return hmac.new(config.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def render_page(name: str, **data) -> HTMLResponse:
    return HTMLResponse(TEMPLATES.get_template(name).render(**data))


def get_session(request: Request, config: Config) -> dict | None:
    token = request.cookies.get("impact_session", "")
    if not token:
        return None
    digest = token_hash(config, token)
    with database(config.database_path) as db:
        row = db.execute("""
            SELECT users.id, users.email, users.role, users.display_name, sessions.csrf_token
            FROM sessions JOIN users ON users.id=sessions.user_id
            WHERE sessions.token_hash=? AND sessions.expires_at>? AND users.active=1
        """, (digest, utc_now())).fetchone()
        return dict(row) if row else None


def require_user(request: Request, config: Config) -> dict:
    user = get_session(request, config)
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in")
    return user


def require_admin(request: Request, config: Config) -> dict:
    user = require_user(request, config)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_record_access(db, table: str, record_id: int, user: dict):
    """An admin can inspect all records; members can access only their own."""
    if table not in {"finance_records", "content_records"}:
        raise ValueError("Unknown record type")
    row = db.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if not row or (user["role"] != "admin" and row["created_by"] != user["id"]):
        raise HTTPException(status_code=404, detail="Record not found")
    if table == "content_records" and user["role"] != "admin":
        project = db.execute("SELECT created_by FROM projects WHERE id = ?", (row["project_id"],)).fetchone()
        if not project or project["created_by"] != user["id"]:
            raise HTTPException(status_code=404, detail="Record not found")
    return row


async def verify_form_csrf(request: Request, user: dict) -> None:
    form = await request.form()
    token = str(form.get("csrf", ""))
    if not hmac.compare_digest(token, user["csrf_token"]):
        raise HTTPException(status_code=403, detail="Refresh and try again")


async def validate_attachment(file: UploadFile | None) -> Attachment | None:
    if not file or not file.filename:
        return None
    mime = file.content_type or ""
    if mime not in ALLOWED:
        raise ValueError("Upload a PDF, JPG, PNG, or WebP file")
    raw = await file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise ValueError("The file must be smaller than 8 MB")
    if not raw:
        raise ValueError("The file is empty")
    signatures = {
        "application/pdf": raw.startswith(b"%PDF"),
        "image/jpeg": raw.startswith(b"\xff\xd8"),
        "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": raw.startswith(b"RIFF") and raw[8:12] == b"WEBP",
    }
    if not signatures.get(mime, False):
        raise ValueError("The file does not match its PDF or image type")
    return Attachment(Path(file.filename).name[:120], mime, raw)
