"""Members, admins, project ownership and duplicate approvals are enforced server-side."""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.web import create_app


def test_cross_user_duplicate_requires_admin_and_exports_ownership(monkeypatch, tmp_path):
    cfg = replace(load_config(), database_path=tmp_path / "db.sqlite",
                  session_secret="local-test-secret-longer-than-24-characters", cookie_secure=False)
    initialize(cfg.database_path)
    with database(cfg.database_path) as db:
        for email, name, role in [("admin@example.org", "Admin", "admin"),
                                  ("alice@example.org", "Alice", "member"),
                                  ("bob@example.org", "Bob", "member")]:
            db.execute("INSERT INTO users(email,display_name,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                       (email, name, hash_password("test-password-long"), role, "now"))
    exports = []

    async def model(config, provider, name, prompt, source_text="", attachment=None):
        number = "INV-999" if attachment and b"new-invoice" in attachment.data else "INV-123"
        return {"document_type": "invoice", "invoice_number": number, "vendor": "Supplier Ltd",
                "invoice_date": "2026-11-01", "amount": "40.00", "currency": "EUR"}

    async def export(config, tab, values):
        exports.append((tab, values))

    monkeypatch.setattr("app.web.ask_model", model)
    monkeypatch.setattr("app.web.append_once", export)
    app = create_app(cfg)

    def login(client, email):
        client.post("/login", data={"email": email, "password": "test-password-long"})
        return client.get("/").text.split('name="csrf" value="')[1].split('"')[0]

    def upload(client, csrf, body):
        return client.post("/finance/upload", data={"csrf": csrf},
                           files={"file": ("invoice.pdf", body, "application/pdf")}, follow_redirects=False)

    def confirm(client, csrf, rid, **extra):
        page = client.get(f"/finance/{rid}")
        token = page.text.split('name="rule_token" value="')[1].split('"')[0]
        form = {"csrf": csrf, "rule_token": token, "document_type": "invoice", "invoice_number": "INV-123",
                "vendor": "Supplier Ltd", "amount": "40.00", "currency": "EUR", "category": "Other",
                "traveller": "Test traveller", "description": "Test note"}
        form.update(extra)
        return client.post(f"/finance/{rid}/confirm", data=form, follow_redirects=False)

    with TestClient(app) as alice, TestClient(app) as bob, TestClient(app) as admin:
        alice_csrf = login(alice, "alice@example.org")
        bob_csrf = login(bob, "bob@example.org")
        admin_csrf = login(admin, "admin@example.org")
        assert "Recent documents" not in alice.get("/finance").text
        assert upload(alice, alice_csrf, b"%PDF-1.4\nshared").status_code == 303
        assert confirm(alice, alice_csrf, 1).status_code == 303
        assert exports[0][1][22:26] == ["Alice", "alice@example.org", "Alice", "alice@example.org"]

        assert upload(bob, bob_csrf, b"%PDF-1.4\nshared").status_code == 303
        assert "Needs admin review (1)" in admin.get("/finance").text
        page = bob.get("/finance/2")
        assert "administrator needs to review" in page.text
        assert page.text.index("Your details") < page.text.index("Details read from the document")
        assert bob.get("/finance/1").status_code == 404
        assert bob.get("/source/finance/1").status_code == 404
        confirm(bob, bob_csrf, 2, override_duplicate="yes", duplicate_override_reason="forged")
        with database(cfg.database_path) as db:
            row = db.execute("SELECT status, confirmed_at, duplicate_of FROM finance_records WHERE id=2").fetchone()
            assert row["status"] == "Needs admin review" and not row["confirmed_at"] and row["duplicate_of"] == 1
        assert len(exports) == 1
        assert "bob@example.org" in admin.get("/finance").text
        assert "confirmed claim F-1" in admin.get("/finance/2").text
        assert confirm(admin, admin_csrf, 2, override_duplicate="yes", duplicate_override_reason="Approved exception").status_code == 303
        assert exports[-1][1][22:26] == ["Bob", "bob@example.org", "Admin", "admin@example.org"]
        with database(cfg.database_path) as db:
            audit = db.execute("SELECT duplicate_of,duplicate_override_reason,confirmed_by FROM finance_records WHERE id=2").fetchone()
            assert audit["duplicate_of"] == 1 and audit["duplicate_override_reason"] == "Approved exception" and audit["confirmed_by"] == 1

        # A re-rendered invoice with the same number and vendor also needs review.
        assert upload(bob, bob_csrf, b"%PDF-1.4\ndifferent bytes").status_code == 303
        assert "administrator needs to review" in bob.get("/finance/3").text
        assert len(exports) == 2

        # Two people upload before either one confirms: the second confirmation is still blocked.
        assert upload(alice, alice_csrf, b"%PDF-1.4\nnew-invoice-a").status_code == 303
        assert upload(bob, bob_csrf, b"%PDF-1.4\nnew-invoice-b").status_code == 303
        assert confirm(alice, alice_csrf, 4, invoice_number="INV-999").status_code == 303
        assert confirm(bob, bob_csrf, 5, invoice_number="INV-999").status_code == 303
        with database(cfg.database_path) as db:
            second = db.execute("SELECT status,confirmed_at FROM finance_records WHERE id=5").fetchone()
            assert second["status"] == "Needs admin review" and not second["confirmed_at"]


def test_projects_and_content_are_private_to_their_owner(monkeypatch, tmp_path):
    cfg = replace(load_config(), database_path=tmp_path / "db.sqlite",
                  session_secret="local-test-secret-longer-than-24-characters", cookie_secure=False)
    initialize(cfg.database_path)
    with database(cfg.database_path) as db:
        for email, name, role in [("admin@example.org", "Admin", "admin"),
                                  ("alice@example.org", "Alice", "member"),
                                  ("bob@example.org", "Bob", "member")]:
            db.execute("INSERT INTO users(email,display_name,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                       (email, name, hash_password("test-password-long"), role, "now"))
        db.execute("INSERT INTO projects(name,description,created_at,created_by) VALUES ('Alice project','A','now',2)")
        db.execute("INSERT INTO projects(name,description,created_at,created_by) VALUES ('Bob project','B','now',3)")
    exports, prompts = [], []

    async def model(config, provider, name, prompt, source_text="", attachment=None):
        prompts.append(prompt)
        return {"linkedin": "Post", "newsletter": "Newsletter", "website": "Website"}

    async def export(config, tab, values):
        exports.append((tab, values))

    monkeypatch.setattr("app.web.ask_model", model)
    monkeypatch.setattr("app.web.append_once", export)
    app = create_app(cfg)
    with TestClient(app) as alice, TestClient(app) as bob, TestClient(app) as admin:
        def login(client, email):
            client.post("/login", data={"email": email, "password": "test-password-long"})
            return client.get("/").text.split('name="csrf" value="')[1].split('"')[0]
        alice_csrf = login(alice, "alice@example.org")
        bob_csrf = login(bob, "bob@example.org")
        login(admin, "admin@example.org")
        page = alice.get("/content").text
        assert "Alice project" in page and "Bob project" not in page and "Recent drafts" not in page
        assert alice.post("/content/generate", data={"csrf": alice_csrf, "title": "Blocked", "source": "Info", "project_id": 3}).status_code == 404
        result = alice.post("/content/generate", data={"csrf": alice_csrf, "title": "Alice update", "source": "Info", "project_id": 2}, follow_redirects=False)
        assert result.status_code == 303
        assert bob.get("/content/1").status_code == 404
        assert bob.get("/source/content/1").status_code == 404
        assert bob.post("/content/1/approve", data={"csrf": bob_csrf, "linkedin": "X", "newsletter": "Y", "website": "Z"}).status_code == 404
        assert "Alice project" in admin.get("/content").text and "Bob project" in admin.get("/content").text
        approved = alice.post("/content/1/approve", data={"csrf": alice_csrf, "linkedin": "X", "newsletter": "Y", "website": "Z"}, follow_redirects=False)
        assert approved.status_code == 303
        assert exports[-1][1][9:13] == ["Alice", "alice@example.org", "Alice", "alice@example.org"]
        assert "Alice update" not in bob.get("/content").text
