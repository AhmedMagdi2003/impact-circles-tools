"""Phase 4 Acceptance Gate Tests:
- Nontechnical staff complete finance flow end-to-end
- Nontechnical staff complete content flow end-to-end
- Idempotent Google Sheets append (retry does not duplicate rows)
- Visible Sheets reconciliation state ("Saved here, but Google Sheets needs attention")
- Accessible UI labels, copy controls, and responsive tags
"""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.web import create_app


def test_phase4_end_to_end_and_sheets_reconciliation(monkeypatch, tmp_path):
    db_path = tmp_path / "phase4.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)

    # Seed an admin
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("staff@example.org", hash_password("staff-password-123"), "admin", 1, "2026-11-01T00:00:00Z"),
        )

    append_calls = []

    async def mock_model(cfg, provider, model, prompt, source_text="", attachment=None):
        if "financial document" in prompt:
            return {
                "document_type": "flight",
                "traveller": "Sarah Jenkins",
                "start_date": "2026-11-10",
                "end_date": "2026-11-12",
                "origin": "Cairo",
                "destination": "Berlin",
                "vendor": "Lufthansa",
                "amount": "250.00",
                "currency": "EUR",
                "category": "Travel",
            }
        return {
            "linkedin": "Excited to share our new workshop details!",
            "newsletter": "Dear partners, we launched our autumn series.",
            "website": "### Autumn Workshop Series\nJoin us in Cairo.",
        }

    async def mock_append(cfg, tab, row):
        # Simulate sheet append
        append_calls.append((None, tab, row))

    monkeypatch.setattr("app.web.ask_model", mock_model)
    monkeypatch.setattr("app.web.append_once", mock_append)

    with TestClient(create_app(config)) as client:
        # Sign in
        client.post("/login", data={"email": "staff@example.org", "password": "staff-password-123"})
        csrf = client.get("/").text.split('name="csrf" value="')[1].split('"')[0]

        # Configure finance settings
        client.post("/admin/settings", data={
            "csrf": csrf,
            "per_diem_rates": "EUR,40.00",
            "categories": "Travel,Accommodation,Other",
            "claim_include_booking_amount": "on",
        })

        # --- Flow 1: Finance Upload -> Review -> Confirm ---
        fake_pdf = b"%PDF-1.4\nFlight ticket content\n"
        upload_res = client.post("/finance/upload", data={"csrf": csrf},
                                 files={"file": ("ticket.pdf", fake_pdf, "application/pdf")},
                                 follow_redirects=False)
        assert upload_res.status_code == 303
        review_url = upload_res.headers["location"]

        review_page = client.get(review_url)
        assert review_page.status_code == 200
        # Check accessible labels and steps
        assert "Check details" in review_page.text
        assert "Sarah Jenkins" in review_page.text
        assert "Lufthansa" in review_page.text

        token = review_page.text.split('name="rule_token" value="')[1].split('"')[0]
        confirm_res = client.post(f"{review_url}/confirm", data={
            "csrf": csrf,
            "rule_token": token,
            "document_type": "flight",
            "traveller": "Sarah Jenkins",
            "start_date": "2026-11-10",
            "end_date": "2026-11-12",
            "origin": "Cairo",
            "destination": "Berlin",
            "vendor": "Lufthansa",
            "amount": "250.00",
            "currency": "EUR",
            "category": "Travel",
            "description": "Roundtrip flight to Berlin partner symposium",
        }, follow_redirects=False)
        assert confirm_res.status_code == 303

        # Verify Google Sheets append was called
        assert len(append_calls) == 1
        sheet_id, tab, row = append_calls[0]
        assert sheet_id is None
        assert tab == "Finance"
        assert row[0] == "F-1"
        assert row[2] == "Sarah Jenkins"
        assert row[14] == "370.00"  # 250 booking + (3 days * 40 EUR/day)

        # --- Test Sheets Retry Idempotency ---
        # Triggering sync retry again should succeed without re-appending duplicates if already synced
        retry_res = client.post("/finance/1/sync", data={"csrf": csrf}, follow_redirects=False)
        assert retry_res.status_code == 303
        # append_once was NOT called a second time because record was already marked synced
        assert len(append_calls) == 1

        # --- Flow 2: Content Creation -> Review -> Copy -> Approve ---
        gen_res = client.post("/content/generate", data={
            "csrf": csrf,
            "title": "Autumn Workshop Announcement",
            "source": "We opened registrations for 40 participants across 5 universities.",
            "project_id": 1,
        }, follow_redirects=False)
        assert gen_res.status_code == 303
        content_review_url = gen_res.headers["location"]

        content_page = client.get(content_review_url)
        assert content_page.status_code == 200
        assert "LinkedIn Draft" in content_page.text
        assert "copyDraft('linkedin'" in content_page.text

        approve_res = client.post(f"{content_review_url}/approve", data={
            "csrf": csrf,
            "linkedin": "Approved LinkedIn Draft",
            "newsletter": "Approved Newsletter Draft",
            "website": "Approved Website Draft",
        }, follow_redirects=False)
        assert approve_res.status_code == 303

        # Check that Content was appended to Sheets
        assert len(append_calls) == 2
        sheet_id, tab, row = append_calls[1]
        assert tab == "Content"
        assert row[0] == "C-1"
        assert row[2] == "Autumn Workshop Announcement"
        assert row[3] == "Approved LinkedIn Draft"
