"""Base routes: health, landing, authentication, and user profile."""

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Form, Request

from app.core.auth import (
    get_session,
    hash_password,
    redirect,
    render_page,
    require_user,
    token_hash,
    verify_form_csrf,
    verify_password,
)
from app.core.db import database, utc_now

router = APIRouter()


@router.get("/health")
def health(request: Request):
    config = request.app.state.config
    with database(config.database_path) as db:
        db.execute("SELECT 1").fetchone()
    return {"status": "ok"}


@router.get("/")
def home(request: Request):
    config = request.app.state.config
    user = get_session(request, config)
    if not user:
        return redirect("/login")
    return render_page("home.html", user=user)


@router.get("/login")
def login_page(request: Request):
    config = request.app.state.config
    if get_session(request, config):
        return redirect("/")
    return render_page("login.html", error="")


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    config = request.app.state.config
    email = email.lower().strip()
    with database(config.database_path) as db:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        db.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (cutoff,))
        failures = db.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE email = ?", (email,)
        ).fetchone()[0]
        if failures >= 5:
            return render_page("login.html", error="Too many attempts. Try again in 15 minutes.")
        row = db.execute(
            "SELECT * FROM users WHERE email = ? AND active = 1", (email,)
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            db.execute("INSERT INTO login_attempts VALUES (?, ?)", (email, utc_now()))
            return render_page("login.html", error="Email or password is incorrect")
        db.execute("DELETE FROM login_attempts WHERE email = ?", (email,))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            (token_hash(config, token), row["id"], csrf, expires),
        )
    response = redirect("/")
    response.set_cookie(
        "impact_session",
        token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        max_age=7 * 86400,
    )
    return response


@router.post("/logout")
async def logout(request: Request):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    token = request.cookies.get("impact_session", "")
    with database(config.database_path) as db:
        db.execute(
            "DELETE FROM sessions WHERE token_hash = ?",
            (token_hash(config, token),),
        )
    response = redirect("/login")
    response.delete_cookie("impact_session")
    return response


@router.get("/account")
def account_page(request: Request):
    config = request.app.state.config
    user = require_user(request, config)
    return render_page("account.html", user=user, error="", success="")


@router.post("/account/password")
async def change_password(request: Request):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    current = str(form.get("current_password", ""))
    new_pwd = str(form.get("new_password", ""))
    with database(config.database_path) as db:
        row = db.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not verify_password(current, row["password_hash"]):
            return render_page("account.html", user=user, error="Current password is incorrect", success="")
        try:
            encoded = hash_password(new_pwd)
        except ValueError as exc:
            return render_page("account.html", user=user, error=str(exc), success="")
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (encoded, user["id"]))
        current_token = request.cookies.get("impact_session", "")
        db.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?",
            (user["id"], token_hash(config, current_token)),
        )
    return render_page("account.html", user=user, error="", success="Password updated")
