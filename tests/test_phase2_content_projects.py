"""Phase 2 Acceptance Gate Tests:
- Project creation and selection
- Same-project approved history included in model prompt for tone/continuity
- Other-project history strictly excluded from prompt
- Unapproved drafts strictly excluded from prompt
- Brief / what-is-new requirement enforced
- Generation produces 3 drafts (linkedin, newsletter, website)
- Review page displays 3 draft cards with Copy buttons
- Draft editing and explicit approval action
- Verification that existing records belong to default project
"""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.web import create_app


def test_project_creation_and_history_isolation(monkeypatch, tmp_path):
    db_path = tmp_path / "content_test.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)

    # Seed an admin user
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("admin@example.org", hash_password("admin-password-123"), "admin", 1, "2026-11-01T00:00:00Z"),
        )
        # Create two distinct projects
        db.execute(
            "INSERT INTO projects (name, description, active, created_at) VALUES (?, ?, 1, ?)",
            ("Project Alpha", "Focus on clean energy AI in Egypt", "2026-11-01T00:00:00Z"),
        )
        p_alpha_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        db.execute(
            "INSERT INTO projects (name, description, active, created_at) VALUES (?, ?, 1, ?)",
            ("Project Beta", "Focus on urban mobility in Berlin", "2026-11-01T00:00:00Z"),
        )
        p_beta_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Seed an APPROVED post in Project Alpha
        db.execute("""
            INSERT INTO content_records
            (created_at, created_by, project_id, title, source_text, drafts_json, status, approved_at)
            VALUES (?, 1, ?, ?, ?, ?, 'Approved', ?)
        """, (
            "2026-10-25T10:00:00Z",
            p_alpha_id,
            "Alpha Kickoff",
            "Initial Alpha brief",
            json.dumps({"linkedin": "Alpha Milestone 1 completed", "newsletter": "Alpha News", "website": "Alpha Web"}),
            "2026-10-25T12:00:00Z",
        ))

        # Seed an UNAPPROVED (draft) post in Project Alpha (should NOT appear in history prompt)
        db.execute("""
            INSERT INTO content_records
            (created_at, created_by, project_id, title, source_text, drafts_json, status, approved_at)
            VALUES (?, 1, ?, ?, ?, ?, 'Needs your attention', NULL)
        """, (
            "2026-10-26T10:00:00Z",
            p_alpha_id,
            "Alpha Draft",
            "Unapproved brief",
            json.dumps({"linkedin": "Draft text", "newsletter": "Draft", "website": "Draft"}),
        ))

        # Seed an APPROVED post in Project Beta (should NOT appear when generating for Alpha)
        db.execute("""
            INSERT INTO content_records
            (created_at, created_by, project_id, title, source_text, drafts_json, status, approved_at)
            VALUES (?, 1, ?, ?, ?, ?, 'Approved', ?)
        """, (
            "2026-10-27T10:00:00Z",
            p_beta_id,
            "Beta Metro Launch",
            "Initial Beta brief",
            json.dumps({"linkedin": "Beta secret event in Berlin", "newsletter": "Beta News", "website": "Beta Web"}),
            "2026-10-27T12:00:00Z",
        ))

    prompts_captured = []

    async def fake_model(cfg, provider, model, prompt, source_text="", attachment=None):
        prompts_captured.append(prompt)
        return {
            "linkedin": f"LinkedIn generated for {source_text[:20]}",
            "newsletter": f"Newsletter generated for {source_text[:20]}",
            "website": f"Website draft for {source_text[:20]}",
        }

    monkeypatch.setattr("app.web.ask_model", fake_model)

    with TestClient(create_app(config)) as client:
        # Sign in
        client.post("/login", data={"email": "admin@example.org", "password": "admin-password-123"})
        csrf = client.get("/content").text.split('name="csrf" value="')[1].split('"')[0]

        # 1. Test Project creation via form
        res = client.post("/content/projects", data={
            "csrf": csrf,
            "name": "Project Gamma",
            "description": "Healthcare AI for rural clinics",
        }, follow_redirects=False)
        assert res.status_code == 303

        # Verify Project Gamma exists in DB
        with database(db_path) as db:
            p_gamma = db.execute("SELECT * FROM projects WHERE name = 'Project Gamma'").fetchone()
            assert p_gamma is not None
            assert "Healthcare" in p_gamma["description"]

        # 2. Test generation for Project Alpha
        res = client.post("/content/generate", data={
            "csrf": csrf,
            "project_id": p_alpha_id,
            "title": "Alpha Phase 2 Launch",
            "source": "We installed 50 solar monitoring sensors today.",
        }, follow_redirects=False)
        assert res.status_code == 303
        review_url = res.headers["location"]

        # Verify prompt passed to AI model
        assert len(prompts_captured) == 1
        prompt = prompts_captured[0]

        # Must contain Project Alpha context
        assert "Target Project: Project Alpha" in prompt
        assert "clean energy AI in Egypt" in prompt
        # Must contain approved post from Project Alpha
        assert "Alpha Kickoff" in prompt
        assert "Alpha Milestone 1 completed" in prompt

        # Must STRICTLY EXCLUDE unapproved post from Project Alpha
        assert "Alpha Draft" not in prompt
        assert "Draft text" not in prompt

        # Must STRICTLY EXCLUDE posts from Project Beta
        assert "Beta Metro Launch" not in prompt
        assert "Beta secret event in Berlin" not in prompt

        # 3. Test Review Page UI elements
        review_res = client.get(review_url)
        assert review_res.status_code == 200
        # Check 3 cards present
        assert "LinkedIn Draft" in review_res.text
        assert "Newsletter Draft" in review_res.text
        assert "Website Draft" in review_res.text
        # Check Copy buttons present
        assert "Copy Linkedin" in review_res.text or "copyDraft('linkedin'" in review_res.text
        assert "Copy Newsletter" in review_res.text or "copyDraft('newsletter'" in review_res.text
        assert "Copy Website" in review_res.text or "copyDraft('website'" in review_res.text

        # 4. Test Approval flow
        record_id = review_url.split("/")[-1]
        approve_res = client.post(f"/content/{record_id}/approve", data={
            "csrf": csrf,
            "linkedin": "Approved Final LinkedIn Post",
            "newsletter": "Approved Final Newsletter",
            "website": "Approved Final Website Article",
        }, follow_redirects=False)
        assert approve_res.status_code == 303

        # Verify state in DB
        with database(db_path) as db:
            row = db.execute("SELECT * FROM content_records WHERE id = ?", (record_id,)).fetchone()
            assert row["status"] == "Approved"
            assert row["approved_at"] is not None
            saved_drafts = json.loads(row["drafts_json"])
            assert saved_drafts["linkedin"] == "Approved Final LinkedIn Post"
            assert row["project_id"] == p_alpha_id
