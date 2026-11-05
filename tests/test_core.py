"""The core workflow must never save an AI-calculated total."""

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.config import load_config
from app.core.db import database
from app.finance.rules import calculate_claim
from app.web import create_app


def test_claim_uses_fixed_inclusive_calendar_rule():
    result = calculate_claim({"document_type": "flight", "amount": "120.00", "currency": "EUR",
                              "start_date": "2026-10-30", "end_date": "2026-11-01"},
                             "35.50", "EUR", True)
    assert result["travel_days"] == 3
    assert result["daily_allowance"] == "106.50"
    assert result["total"] == "226.50"


def test_upload_review_confirm_and_sheets(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@example.org")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "a-long-test-password")
    config = replace(load_config(), database_path=tmp_path / "data" / "test.db",
                     session_secret="test-secret-longer-than-24-chars", cookie_secure=False,
                     finance_provider="gemini", finance_model="test-model",
                     content_provider="openai", content_model="test-model")
    calls, exports = [], []

    async def fake_model(cfg, provider, model, prompt, source_text="", attachment=None):
        calls.append((provider, model, attachment.mime if attachment else None,
                      attachment.data if attachment else None))
        if "financial document" in prompt:
            return {"document_type": "flight", "traveller": "A. Example",
                    "start_date": "2026-10-30", "end_date": "2026-11-01",
                    "origin": "Cairo", "destination": "Berlin", "amount": "120.00",
                    "currency": "EUR"}
        return {"linkedin": "Post", "newsletter": "Newsletter", "website": "Website"}

    async def fake_append(cfg, tab, row):
        exports.append((None, tab, row))

    monkeypatch.setattr("app.web.ask_model", fake_model)
    monkeypatch.setattr("app.web.append_once", fake_append)
    with TestClient(create_app(config)) as client:
        assert client.get("/static/images/impactcircles_logo.png").status_code == 200
        response = client.post("/login", data={"email": "admin@example.org",
                                               "password": "a-long-test-password"},
                               follow_redirects=False)
        assert response.status_code == 303
        csrf = client.get("/").text.split('name="csrf" value="')[1].split('"')[0]
        client.post("/admin/settings", data={"csrf": csrf, "per_diem_rates": "35.50",
            "claim_include_booking_amount": "on",
            "categories": "Travel,Other"})
        pdf = b"%PDF-1.4\nOriginal bytes\n"
        response = client.post("/finance/upload", data={"csrf": csrf},
                               files={"file": ("ticket.pdf", pdf, "application/pdf")},
                               follow_redirects=False)
        assert response.status_code == 303
        assert calls[-1] == ("gemini", "test-model", "application/pdf", pdf)
        review = client.get(response.headers["location"])
        assert "226.50 EUR" in review.text
        token = review.text.split('name="rule_token" value="')[1].split('"')[0]
        response = client.post("/finance/1/confirm", data={"csrf": csrf, "rule_token": token,
            "document_type": "flight", "traveller": "A. Example", "start_date": "2026-10-30",
            "end_date": "2026-11-01", "origin": "Cairo", "destination": "Berlin",
            "vendor": "Airline", "amount": "120.00", "currency": "EUR",
            "category": "Travel", "description": "Flight"}, follow_redirects=False)
        assert response.status_code == 303
        assert exports[-1][1] == "Finance"
        assert exports[-1][2][14] == "226.50"
        with database(config.database_path) as db:
            saved = db.execute("SELECT details_json,calculation_json FROM finance_records").fetchone()
            assert json.loads(saved["calculation_json"])["total"] == "226.50"
        content = client.post("/content/generate", data={"csrf": csrf, "title": "Update",
            "source": "We held a workshop."}, follow_redirects=False)
        assert content.status_code == 303
        assert calls[-1][:2] == ("openai", "test-model")
        approved = client.post("/content/1/approve", data={"csrf": csrf,
            "linkedin": "Edited post", "newsletter": "Newsletter", "website": "Website"},
            follow_redirects=False)
        assert approved.status_code == 303
        assert exports[-1][1] == "Content"
        assert exports[-1][2][3] == "Edited post"


def test_foreign_booking_uses_dated_rate_and_eur_allowance():
    result = calculate_claim(
        {"document_type": "train", "amount": "100", "currency": "USD",
         "start_date": "2026-10-30", "end_date": "2026-10-31"},
        "35", "EUR", True, fx_info={"source_currency": "USD", "rate": "0.8",
                                      "date": "2026-11-05", "provider": "Test API"},
    )
    assert (result["booking_claim"], result["daily_allowance"], result["total"], result["currency"]) == (
        "80.00", "70.00", "150.00", "EUR")
    assert result["booking_fx_date"] == "2026-11-05"


def test_ordinary_receipt_has_no_per_diem():
    result = calculate_claim({"document_type": "other", "amount": "42.00",
                              "currency": "USD"}, "0", "EUR", False,
                             fx_info={"source_currency": "USD", "rate": "0.8",
                                      "date": "2026-11-05", "provider": "Test API"})
    assert result["travel_days"] == 0
    assert result["daily_allowance"] == "0.00"
    assert result["total"] == "33.60" and result["currency"] == "EUR"


def test_egp_invoice_and_ticket_both_convert_to_eur():
    quote = {"source_currency": "EGP", "rate": "0.016", "date": "2026-11-05",
             "provider": "Test daily API"}
    invoice = calculate_claim({"document_type": "invoice", "amount": "1200.00", "currency": "EGP"},
                              "35.50", "EUR", True, fx_info=quote)
    ticket = calculate_claim({"document_type": "flight", "amount": "1200.00", "currency": "EGP",
                              "start_date": "2026-11-20", "end_date": "2026-11-21"},
                             "35.50", "EUR", True, fx_info=quote)
    assert (invoice["booking_claim"], invoice["daily_allowance"], invoice["total"]) == (
        "19.20", "0.00", "19.20")
    assert (ticket["booking_claim"], ticket["daily_allowance"], ticket["total"]) == (
        "19.20", "71.00", "90.20")
