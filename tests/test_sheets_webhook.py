"""Exercise the Apps Script HTTP boundary without sending real financial data."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.core.config import load_config
from app.integrations.sheets import append_once
from app.core.db import database, initialize
from app.finance.routes import _finance_row
from app.web import sync_record


def config():
    return replace(load_config(), sheets_webhook_url="https://script.google.com/macros/s/test/exec",
                   sheets_webhook_secret="a-secret-at-least-24-characters")


def test_webhook_posts_and_follows_google_redirect_without_secret(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["tab"] == "Finance" and body["values"][0] == "F-42"
            assert body["secret"] == config().sheets_webhook_secret
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/macros/echo?id=1"})
        assert request.method == "GET" and not request.content
        return httpx.Response(200, json={"status": "ok", "id": "F-42"})

    client = httpx.AsyncClient
    monkeypatch.setattr("app.integrations.sheets.httpx.AsyncClient",
                        lambda **kwargs: client(transport=httpx.MockTransport(handler)))
    asyncio.run(append_once(config(), "Finance", ["F-42"]))
    assert len(requests) == 2


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"status": "error"}),
    httpx.Response(200, json={"status": "ok", "id": "wrong"}),
    httpx.Response(200, text="not json"),
    httpx.Response(401),
    httpx.Response(403),
    httpx.Response(302, headers={"Location": "https://evil.example/collect"}),
])
def test_webhook_rejects_unacknowledged_results_and_unsafe_redirects(monkeypatch, response):
    client = httpx.AsyncClient
    monkeypatch.setattr("app.integrations.sheets.httpx.AsyncClient",
                        lambda **kwargs: client(transport=httpx.MockTransport(lambda request: response)))
    with pytest.raises(ValueError):
        asyncio.run(append_once(config(), "Content", ["C-7"]))


def test_webhook_rejects_untrusted_endpoint_before_sending():
    invalid = replace(config(), sheets_webhook_url="https://example.com/exec")
    with pytest.raises(ValueError):
        asyncio.run(append_once(invalid, "Finance", ["F-1"]))


def test_finance_export_uses_saved_eur_total_and_keeps_original_currency():
    row = _finance_row(config(), {"id": 5}, {"currency": "USD", "amount": "100"},
                       {"total": "120.00", "total_eur": "110.00", "currency": "USD"})
    assert len(row) == 22
    assert row[14:16] == ["110.00", "EUR"]
    assert row[19] == "USD"


def test_finance_export_days_are_payable_days_after_overlap():
    row = _finance_row(config(), {"id": 8}, {"currency": "EUR", "amount": "90"},
                       {"travel_days": 2, "already_claimed_days": 2,
                        "eligible_days": 0, "daily_rate": "35.50",
                        "daily_allowance": "0.00", "total_eur": "90.00"})
    assert row[11:14] == ["0", "35.50", "0.00"]


def test_failed_sync_can_be_retried_without_losing_record(monkeypatch, tmp_path):
    cfg = replace(config(), database_path=tmp_path / "data.db")
    initialize(cfg.database_path)
    with database(cfg.database_path) as db:
        db.execute("INSERT INTO users(email,password_hash,role,created_at) VALUES ('a@b.test','hash','admin','now')")
        db.execute("""INSERT INTO content_records(created_at,created_by,title,source_text,drafts_json,status,approved_at)
                      VALUES ('now',1,'Title','Brief',?,'Approved','now')""",
                   (json.dumps({"linkedin": "A", "newsletter": "B", "website": "C"}),))
    attempts = []

    async def deliver(_cfg, tab, row):
        attempts.append((tab, row[0]))
        if len(attempts) == 1:
            raise ValueError("Sheets temporarily unavailable")

    monkeypatch.setattr("app.web.append_once", deliver)
    assert asyncio.run(sync_record(cfg, "content", 1)) == "Sheets temporarily unavailable"
    with database(cfg.database_path) as db:
        saved = db.execute("SELECT approved_at,sheet_error,sheet_synced_at FROM content_records WHERE id=1").fetchone()
        assert saved["approved_at"] and saved["sheet_error"] and not saved["sheet_synced_at"]
    assert asyncio.run(sync_record(cfg, "content", 1)) is None
    assert asyncio.run(sync_record(cfg, "content", 1)) is None
    assert attempts == [("Content", "C-1"), ("Content", "C-1")]
