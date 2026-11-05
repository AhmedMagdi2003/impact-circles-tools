"""Invoice extraction, review and confirmation use the normal finance flow."""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.web import create_app


def test_invoice_pdf_can_be_reviewed_confirmed_and_exported(monkeypatch, tmp_path):
    cfg = replace(load_config(), database_path=tmp_path / "test.db",
                  session_secret="test-secret-longer-than-24-characters", cookie_secure=False)
    initialize(cfg.database_path)
    with database(cfg.database_path) as db:
        db.execute("INSERT INTO users(email,password_hash,role,created_at) VALUES (?,?,?,?)",
                   ("admin@example.org", hash_password("test-password-long"), "admin", "now"))
    sent, model_calls = [], []

    async def fake_model(config, provider, model, prompt, source_text="", attachment=None):
        model_calls.append(1)
        assert attachment.data.startswith(b"%PDF")
        return {"document_type": "invoice", "invoice_number": "INV-42", "invoice_date": "2026-11-01",
                "vendor": "Example supplier", "amount": "125.00", "currency": "EUR"}

    async def fake_sheet(config, tab, row):
        sent.append((tab, row))

    monkeypatch.setattr("app.web.ask_model", fake_model)
    monkeypatch.setattr("app.web.append_once", fake_sheet)
    with TestClient(create_app(cfg)) as client:
        client.post("/login", data={"email": "admin@example.org", "password": "test-password-long"})
        csrf = client.get("/").text.split('name="csrf" value="')[1].split('"')[0]
        assert "invoice" in client.get("/finance").text.lower()
        upload = client.post("/finance/upload", data={"csrf": csrf},
                             files={"file": ("invoice.pdf", b"%PDF-1.4\nexample", "application/pdf")},
                             follow_redirects=False)
        assert upload.status_code == 303
        review = client.get(upload.headers["location"])
        assert 'value="invoice" selected' in review.text
        assert 'value="INV-42"' in review.text
        unconfirmed = client.get("/finance/1/confirmed", follow_redirects=False)
        assert unconfirmed.status_code == 303
        assert unconfirmed.headers["location"] == "/finance/1"
        token = review.text.split('name="rule_token" value="')[1].split('"')[0]
        response = client.post("/finance/1/confirm", data={
            "csrf": csrf, "rule_token": token, "document_type": "invoice", "vendor": "Example supplier",
            "invoice_number": "INV-42", "invoice_date": "2026-11-01", "amount": "125.00",
            "currency": "EUR", "category": "Other", "description": "Office supplies",
        }, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/finance/1/confirmed"
        confirmed = client.get(response.headers["location"])
        assert confirmed.status_code == 200
        assert "Claim F-1 has been confirmed." in confirmed.text
        assert "125.00 EUR" in confirmed.text
        assert 'href="/finance">Upload another document</a>' in confirmed.text
        assert 'href="/finance/1">View claim details</a>' in confirmed.text
        again = client.post("/finance/upload", data={"csrf": csrf},
                            files={"file": ("invoice.pdf", b"%PDF-1.4\nexample", "application/pdf")},
                            follow_redirects=False)
        assert again.headers["location"] == "/finance/2"
        assert len(model_calls) == 2
    with database(cfg.database_path) as db:
        row = db.execute("SELECT details_json, calculation_json FROM finance_records WHERE id=1").fetchone()
        details, calculation = json.loads(row["details_json"]), json.loads(row["calculation_json"])
    assert details["invoice_number"] == "INV-42" and details["invoice_date"] == "2026-11-01"
    assert calculation["daily_allowance"] == "0.00" and calculation["total_eur"] == "125.00"
    assert sent[0][0] == "Finance" and sent[0][1][20:22] == ["INV-42", "2026-11-01"]
