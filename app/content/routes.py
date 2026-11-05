"""Content routes: project management, prompt generation with history, draft review, and approval."""

import json
from pathlib import Path

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

import app.web
from app.content.prompts import build_content_prompt
from app.core.auth import (
    redirect,
    render_page,
    require_admin,
    require_record_access,
    require_user,
    validate_attachment,
    verify_form_csrf,
)
from app.core.db import database, get_project, get_projects, utc_now

router = APIRouter()


def _content_row(config, record: dict, drafts: dict, project_name: str = "") -> list[str]:
    # Columns: ID, Approved At, Title, LinkedIn, Newsletter, Website, Status, Project, Link
    return [
        f"C-{record['id']}",
        record.get("approved_at") or "",
        record.get("title", ""),
        drafts.get("linkedin", ""),
        drafts.get("newsletter", ""),
        drafts.get("website", ""),
        record.get("status", ""),
        project_name or "General",
        f"{config.public_base_url.rstrip('/')}/content/{record['id']}" if config.public_base_url else "",
    ]


@router.get("/content")
def content_list(request: Request, project_id: int | None = None):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        projects = get_projects(db, active_only=True, owner_id=user["id"] if user["role"] != "admin" else None)
        query = """
            SELECT c.id, c.title, c.created_at, c.status, c.sheet_error, c.project_id,
                   p.name as project_name, COALESCE(NULLIF(c.creator_name,''),u.display_name) AS display_name,
                   COALESCE(NULLIF(c.creator_email,''),u.email) AS email
            FROM content_records c
            LEFT JOIN projects p ON p.id = c.project_id
            JOIN users u ON u.id = c.created_by
        """
        params = []
        if project_id:
            query += " WHERE c.project_id = ?"
            params.append(project_id)
        query += " ORDER BY c.id DESC LIMIT 30"
        rows = [dict(r) for r in db.execute(query, params)] if user["role"] == "admin" else []

        # Fetch compact recent approved history for the selected or default project
        selected_pid = project_id or (projects[0]["id"] if projects else 1)
        history_rows = db.execute("""
            SELECT id, title, approved_at, drafts_json
            FROM content_records
            WHERE project_id = ? AND status IN ('Approved', 'Published')
            ORDER BY approved_at DESC LIMIT 5
        """, (selected_pid,)).fetchall() if user["role"] == "admin" else []
        history = []
        for hr in history_rows:
            history.append({
                "id": hr["id"],
                "title": hr["title"],
                "approved_at": hr["approved_at"],
                "drafts": json.loads(hr["drafts_json"]),
            })

    return render_page(
        "content.html",
        user=user,
        rows=rows,
        projects=projects,
        selected_project_id=selected_pid,
        history=history,
        error="",
    )


@router.post("/content/projects")
async def create_project_route(request: Request):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    name = str(form.get("name", "")).strip()[:100]
    description = str(form.get("description", "")).strip()[:2000]
    if not name:
        raise HTTPException(400, "Project name is required")
    with database(config.database_path) as db:
        try:
            db.execute(
                "INSERT INTO projects (name, description, active, created_at, created_by) VALUES (?, ?, 1, ?, ?)",
                (name, description, utc_now(), user["id"]),
            )
        except Exception as exc:
            raise HTTPException(400, "A project with this name already exists") from exc
    return redirect("/content")


@router.get("/source/content/{record_id}")
def source_content(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        row = require_record_access(db, "content_records", record_id, user)
    path = config.database_path.parent / "uploads" / f"content-{record_id}"
    if not row or not row["source_name"] or not path.is_file():
        raise HTTPException(404, "Source file not found")
    return FileResponse(path, media_type=row["source_mime"], headers={"Content-Disposition": "inline"})


@router.post("/content/generate")
async def content_generate(
    request: Request,
    title: str = Form(...),
    source: str = Form(""),
    project_id: int = Form(1),
    file: UploadFile | None = File(None),
    csrf: str = Form(...),
):
    config = request.app.state.config
    user = require_user(request, config)
    if csrf != user["csrf_token"]:
        raise HTTPException(403, "Refresh and try again")
    try:
        attachment = await validate_attachment(file)
        if not source.strip() and not attachment:
            raise ValueError("Add a brief or attach a document")
        if len(source) > 20_000:
            raise ValueError("Brief is too long")

        with database(config.database_path) as db:
            project = get_project(db, project_id)
            if not project or not project["active"] or (user["role"] != "admin" and project["created_by"] != user["id"]):
                raise HTTPException(404, "Project not found")
            # Strict boundary: ONLY approved posts from the same project
            history_rows = db.execute("""
                SELECT title, approved_at, drafts_json
                FROM content_records
                WHERE project_id = ? AND status IN ('Approved', 'Published')
                  AND (? = 'admin' OR created_by = ?)
                ORDER BY approved_at DESC LIMIT 3
            """, (project["id"], user["role"], user["id"])).fetchall()
            history = []
            for hr in history_rows:
                history.append({
                    "title": hr["title"],
                    "approved_at": hr["approved_at"],
                    "drafts": json.loads(hr["drafts_json"]),
                })

        prompt = build_content_prompt(project=project, history=history)

        result = await app.web.ask_model(
            config,
            config.content_provider,
            config.content_model,
            prompt,
            source_text=source[:20_000],
            attachment=attachment,
        )
        drafts = {
            key: str(result.get(key) or "").strip()[:10_000]
            for key in ("linkedin", "newsletter", "website")
        }
        if not all(drafts.values()):
            raise ValueError("AI returned incomplete drafts. Please try again")

        with database(config.database_path) as db:
            db.execute("""
                INSERT INTO content_records
                (created_at, created_by, project_id, title, source_text, source_name, source_mime, drafts_json, status, creator_name, creator_email)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                utc_now(),
                user["id"],
                project["id"],
                title.strip()[:150],
                source.strip(),
                attachment.name if attachment else None,
                attachment.mime if attachment else None,
                json.dumps(drafts),
                "Needs your attention",
                user["display_name"] or user["email"].split("@", 1)[0],
                user["email"],
            ))
            record_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            if attachment:
                folder = config.database_path.parent / "uploads"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"content-{record_id}").write_bytes(attachment.data)

        return redirect(f"/content/{record_id}")
    except ValueError as exc:
        with database(config.database_path) as db:
            projects = get_projects(db, active_only=True, owner_id=user["id"] if user["role"] != "admin" else None)
            rows = [dict(r) for r in db.execute(
                "SELECT c.id, c.title, c.created_at, c.status, c.sheet_error, p.name as project_name, "
                "u.display_name, u.email FROM content_records c LEFT JOIN projects p ON p.id = c.project_id "
                "JOIN users u ON u.id=c.created_by ORDER BY c.id DESC LIMIT 30"
            )] if user["role"] == "admin" else []
        return render_page("content.html", user=user, rows=rows, projects=projects,
                           selected_project_id=project_id, history=[], error=str(exc))


@router.get("/content/{record_id}")
def content_review(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    with database(config.database_path) as db:
        row = require_record_access(db, "content_records", record_id, user)
        record = dict(row)
        project = get_project(db, record.get("project_id", 1))
        # Past approved items in this project for side context
        history_rows = db.execute("""
            SELECT id, title, approved_at, drafts_json
            FROM content_records
            WHERE project_id = ? AND status IN ('Approved', 'Published') AND id <> ?
            ORDER BY approved_at DESC LIMIT 5
        """, (record.get("project_id", 1), record_id)).fetchall() if user["role"] == "admin" else []
        history = [
            {"id": hr["id"], "title": hr["title"], "approved_at": hr["approved_at"], "drafts": json.loads(hr["drafts_json"])}
            for hr in history_rows
        ]
    return render_page(
        "content_review.html",
        user=user,
        record=record,
        project=project,
        history=history,
        drafts=json.loads(record["drafts_json"]),
        error="",
    )


@router.post("/content/{record_id}/approve")
async def content_approve(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    form = await request.form()
    drafts = {
        key: str(form.get(key, "")).strip()[:10_000]
        for key in ("linkedin", "newsletter", "website")
    }
    if not all(drafts.values()):
        raise HTTPException(400, "Review all three drafts")
    with database(config.database_path) as db:
        row = require_record_access(db, "content_records", record_id, user)
        if row["approved_at"]:
            return redirect(f"/content/{record_id}")
        db.execute("""
            UPDATE content_records
            SET drafts_json = ?, status = 'Approved', approved_at = ?, approved_by = ?, actor_name = ?, actor_email = ?
            WHERE id = ?
        """, (json.dumps(drafts), utc_now(), user["id"],
                user["display_name"] or user["email"].split("@", 1)[0], user["email"], record_id))
    await app.web.sync_record(config, "content", record_id)
    return redirect(f"/content/{record_id}")


@router.post("/content/{record_id}/publish")
async def content_publish(request: Request, record_id: int):
    config = request.app.state.config
    user = require_admin(request, config)
    await verify_form_csrf(request, user)
    if not config.publish_webhook_url.startswith("https://") or not config.publish_webhook_token:
        raise HTTPException(400, "Configure an HTTPS publishing webhook and token first")
    with database(config.database_path) as db:
        row = db.execute("SELECT * FROM content_records WHERE id = ?", (record_id,)).fetchone()
        if not row or not row["approved_at"]:
            raise HTTPException(409, "Approve the draft before publishing")
        if row["published_at"]:
            return redirect(f"/content/{record_id}")
        record = dict(row)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.post(
                config.publish_webhook_url,
                headers={
                    "Authorization": f"Bearer {config.publish_webhook_token}",
                    "Idempotency-Key": f"content-{record_id}",
                },
                json={
                    "id": record_id,
                    "title": record["title"],
                    "drafts": json.loads(record["drafts_json"]),
                },
            )
            response.raise_for_status()
    except Exception as exc:
        with database(config.database_path) as db:
            db.execute("UPDATE content_records SET publication_error = ? WHERE id = ?",
                       (str(exc)[:200], record_id))
    else:
        with database(config.database_path) as db:
            db.execute("""
                UPDATE content_records
                SET status = 'Published', published_at = ?, publication_error = NULL
                WHERE id = ?
            """, (utc_now(), record_id))
    return redirect(f"/content/{record_id}")


@router.post("/content/{record_id}/sync")
async def content_sync_retry(request: Request, record_id: int):
    config = request.app.state.config
    user = require_user(request, config)
    await verify_form_csrf(request, user)
    with database(config.database_path) as db:
        require_record_access(db, "content_records", record_id, user)
    await app.web.sync_record(config, "content", record_id)
    return redirect(f"/content/{record_id}")
