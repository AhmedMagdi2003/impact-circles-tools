"""EUR allowance and dated daily FX are fixed inputs to confirmed claims."""

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.integrations import fx
from app.web import create_app
from app.finance.routes import _finance_row


def test_existing_database_keeps_only_its_eur_allowance(tmp_path):
    path = tmp_path / "existing.db"
    initialize(path)
    with database(path) as db:
        db.execute("UPDATE settings SET value=? WHERE key='per_diem_rates'",
                   ("EUR,42.25\nUSD,50.00\nEGP,1800.00",))
        db.execute("DELETE FROM schema_migrations WHERE version=7")
    initialize(path)
    with database(path) as db:
        assert db.execute("SELECT value FROM settings WHERE key='per_diem_rates'").fetchone()[0] == "EUR,42.25"


def test_exchange_api_uses_dated_rate_and_fails_closed(monkeypatch):
    fx._CACHE.clear()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": "success", "base_code": "EUR",
                    "time_last_update_unix": int(datetime.now(timezone.utc).timestamp()),
                    "rates": {"USD": 1.25, "EGP": 60}}

    monkeypatch.setattr(fx.httpx, "get", lambda *args, **kwargs: Response())
    rate = fx.get_eur_rate("USD")
    assert rate["rate"] == Decimal("0.8")
    assert rate["date"] == datetime.now(timezone.utc).date().isoformat()
    fx._CACHE.clear()
    class StaleResponse(Response):
        def json(self):
            data = super().json()
            data["time_last_update_unix"] -= 3 * 86400
            return data

    monkeypatch.setattr(fx.httpx, "get", lambda *args, **kwargs: StaleResponse())
    with pytest.raises(ValueError, match="Daily exchange rates are unavailable"):
        fx.get_eur_rate("USD")
    monkeypatch.setattr(fx.httpx, "get", lambda *args, **kwargs: (_ for _ in ()).throw(fx.httpx.ConnectError("offline")))
    with pytest.raises(ValueError, match="Daily exchange rates are unavailable"):
        fx.get_eur_rate("USD")


def test_foreign_claim_review_and_saved_fx_audit(monkeypatch, tmp_path):
    config = replace(load_config(), database_path=tmp_path / "claims.db",
                     session_secret="test-secret-longer-than-24-characters", cookie_secure=False)
    initialize(config.database_path)
    details = {"document_type": "flight", "traveller": "A. Example", "start_date": "2026-11-10",
               "end_date": "2026-11-12", "origin": "Cairo", "destination": "Berlin",
               "amount": "100.00", "currency": "USD", "category": "Travel"}
    with database(config.database_path) as db:
        db.execute("INSERT INTO users(email,password_hash,role,created_at) VALUES (?,?,?,?)",
                   ("alice@example.org", hash_password("test-password-long"), "member", "now"))
        db.execute("INSERT INTO finance_records(created_at,created_by,source_name,source_mime,source_hash,status,details_json) "
                   "VALUES (?,?,?,?,?,?,?)", ("now", 1, "ticket.pdf", "application/pdf", "hash", "Needs your attention", json.dumps(details)))

    monkeypatch.setattr("app.finance.routes.get_eur_rate", lambda currency: {
        "source_currency": currency, "rate": Decimal("0.8"), "date": "2026-11-05",
        "provider": "ExchangeRate-API Open Access"})

    async def fake_sync(*args):
        pass

    monkeypatch.setattr("app.web.sync_record", fake_sync)
    with TestClient(create_app(config)) as client:
        client.post("/login", data={"email": "alice@example.org", "password": "test-password-long"})
        csrf = client.get("/").text.split('name="csrf" value="')[1].split('"')[0]
        page = client.get("/finance/1")
        assert "186.50 EUR" in page.text
        assert "2026-11-05" in page.text
        token = page.text.split('name="rule_token" value="')[1].split('"')[0]
        changed = client.post("/finance/1/confirm", data={**details, "amount": "120.00", "csrf": csrf,
                                                         "rule_token": token})
        assert changed.status_code == 200 and "202.50 EUR" in changed.text
        response = client.post("/finance/1/confirm", data={**details, "csrf": csrf, "rule_token": token}, follow_redirects=False)
        assert response.headers["location"] == "/finance/1/confirmed"

    with database(config.database_path) as db:
        row = dict(db.execute("SELECT * FROM finance_records WHERE id=1").fetchone())
        calc = json.loads(row["calculation_json"])
        line = db.execute("SELECT fx_rate,fx_date,fx_provider,eur_amount FROM expense_lines WHERE record_id=1").fetchone()
    assert (calc["booking_claim"], calc["daily_allowance"], calc["total_eur"]) == ("80.00", "106.50", "186.50")
    assert tuple(line) == ("0.8", "2026-11-05", "ExchangeRate-API Open Access", "80.00")
    exported = _finance_row(config, row, details, calc)
    assert (exported[10], exported[12], exported[13], exported[14], exported[19]) == (
        "100.00", "35.50", "106.50", "186.50", "USD")
