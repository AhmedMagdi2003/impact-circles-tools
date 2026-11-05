"""Admin management routes: settings, users, and organization controls."""

from fastapi import APIRouter, HTTPException, Request

from app.core.auth import (
    hash_password,
    redirect,
    render_page,
    require_admin,
    verify_form_csrf,
)
from app.core.db import database, get_projects, setting, utc_now
from app.finance.rules import money

router = APIRouter()


def parse_rates(text: str) -> dict[str, str]:
    amount = text.strip()
    if amount.upper().startswith("EUR,"):
        amount = amount[4:].strip()
    if "\n" in amount or "," in amount:
        raise ValueError("Set one daily allowance amount in EUR")
    return {"EUR": str(money(amount))}


@router.get("/admin")
def admin_page(request: Request):
    config = request.app.state.config
    user = require_admin(request, config)
    with database(config.database_path) as db:
        settings = {r["key"]: r["value"] for r in db.execute("SELECT * FROM settings")}
        settings["daily_allowance_eur"] = next(
            (line.split(",", 1)[1].strip() for line in settings.get("per_diem_rates", "").splitlines()
             if line.upper().startswith("EUR,")), "35.50")
        users = [dict(r) for r in db.execute("SELECT id, email, display_name, role, active FROM users ORDER BY id")]
        projects = get_projects(db, active_only=False)
        destination_rates = [dict(r) for r in db.execute(
            "SELECT * FROM destination_rates ORDER BY destination ASC"
        ).fetchall()]
    return render_page(
        "admin.html",
        user=user,
        settings=settings,
        users=users,
        projects=projects,
        destination_rates=destination_rates,
        error="",
    )


@router.post("/admin/settings")
async def update_settings(request: Request):
    config = request.app.state.config
    user = require_admin(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    try:
        rates = parse_rates(str(form.get("per_diem_rates", "")))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    categories = ",".join(
        dict.fromkeys(
            item.strip()[:50]
            for item in str(form.get("categories", "")).split(",")
            if item.strip()
        )
    )
    if not categories:
        raise HTTPException(400, "Add at least one category")
    values = {
        "per_diem_rates": "\n".join(f"{code},{rate}" for code, rate in rates.items()),
        "categories": categories,
        "claim_include_booking_amount": "true" if form.get("claim_include_booking_amount") else "false",
    }
    with database(config.database_path) as db:
        for key, value in values.items():
            db.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))
    return redirect("/admin")


@router.post("/admin/users")
async def add_user(request: Request):
    config = request.app.state.config
    user = require_admin(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    email = str(form.get("email", "")).lower().strip()
    display_name = str(form.get("display_name", "")).strip()[:100]
    if "@" not in email or len(email) > 254:
        raise HTTPException(400, "Enter a valid email address")
    if not display_name:
        display_name = email.split("@", 1)[0]
    role = str(form.get("role", "member"))
    if role not in {"member", "admin"}:
        raise HTTPException(400, "Choose a role")
    password_hash = hash_password(str(form.get("password", "")))
    with database(config.database_path) as db:
        try:
            db.execute(
                "INSERT INTO users(email, password_hash, role, created_at, display_name) VALUES (?, ?, ?, ?, ?)",
                (email, password_hash, role, utc_now(), display_name),
            )
        except Exception as exc:
            raise HTTPException(400, "This email may already have an account") from exc
    return redirect("/admin")


@router.post("/admin/users/{user_id}/name")
async def rename_user(request: Request, user_id: int):
    config = request.app.state.config
    admin = require_admin(request, config)
    await verify_form_csrf(request, admin)
    form = await request.form()
    name = str(form.get("display_name", "")).strip()[:100]
    if not name:
        raise HTTPException(400, "Enter a name")
    with database(config.database_path) as db:
        db.execute("UPDATE users SET display_name = ? WHERE id = ?", (name, user_id))
    return redirect("/admin")


@router.post("/admin/projects/{project_id}/owner")
async def assign_project_owner(request: Request, project_id: int):
    config = request.app.state.config
    admin = require_admin(request, config)
    await verify_form_csrf(request, admin)
    form = await request.form()
    try:
        owner_id = int(str(form.get("owner_id", "")))
    except ValueError as exc:
        raise HTTPException(400, "Choose a team member") from exc
    with database(config.database_path) as db:
        if not db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (owner_id,)).fetchone():
            raise HTTPException(400, "Choose an active team member")
        if not db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone():
            raise HTTPException(404, "Project not found")
        db.execute("UPDATE projects SET created_by=? WHERE id=?", (owner_id, project_id))
    return redirect("/admin")


@router.post("/admin/users/{user_id}/toggle")
async def toggle_user(request: Request, user_id: int):
    config = request.app.state.config
    admin = require_admin(request, config)
    await verify_form_csrf(request, admin)
    if admin["id"] == user_id:
        raise HTTPException(400, "You cannot disable your own account")
    with database(config.database_path) as db:
        db.execute("UPDATE users SET active = 1 - active WHERE id = ?", (user_id,))
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return redirect("/admin")
