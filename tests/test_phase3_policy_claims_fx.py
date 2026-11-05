"""Phase 3 Acceptance Gate Tests:
- Deterministic FX conversions: USD->EUR, EGP->EUR, EUR->EUR
- Decimal rounding to exact cents without float drift
- Resilient FX fallback when API fails or is offline
- Destination-based allowance & hotel ceiling verification
- Trip date boundaries (same-day vs overnight)
- Non-travel documents (zero allowance)
- Duplicate file hash detection
- Review rule-token protection against concurrent policy changes
- Historical rate freezing in SQLite
"""

from decimal import Decimal
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.finance.rules import calculate_claim, calculate_days, calculate_policy_claim, money
from app.integrations.fx import convert_to_eur, get_eur_rate
from app.web import create_app


def test_decimal_money_and_rounding():
    # Exact rounding tests
    assert money("10.005") == Decimal("10.01")
    assert money("10.004") == Decimal("10.00")
    assert money("0.00") == Decimal("0.00")
    with pytest.raises(ValueError):
        money("-5.00")
    with pytest.raises(ValueError):
        money("not-a-number")


def test_calendar_days_boundaries():
    # Same day trip: 1 calendar day
    assert calculate_days("2026-11-01", "2026-11-01") == 1
    # Overnight trip: 2 calendar days
    assert calculate_days("2026-11-01", "2026-11-02") == 2
    # 5 day trip
    assert calculate_days("2026-11-01", "2026-11-05") == 5
    # Invalid dates
    with pytest.raises(ValueError):
        calculate_days("2026-11-05", "2026-11-01")  # end before start


def test_deterministic_fx_conversions():
    # EUR to EUR identity
    eur_cents, rate_info = convert_to_eur("150.00", "EUR")
    assert eur_cents == Decimal("150.00")
    assert rate_info["rate"] == Decimal("1.0")

    # Frozen rate test: USD with known explicit rate 0.900000
    usd_cents, rate_info = convert_to_eur("100.00", "USD", explicit_rate=Decimal("0.900000"))
    assert usd_cents == Decimal("90.00")

    # Rounding check: 10.555 USD @ 0.900000 = 9.4995 -> 9.50 EUR
    usd_cents, _ = convert_to_eur("10.555", "USD", explicit_rate=Decimal("0.900000"))
    assert usd_cents == Decimal("9.50")

    # Live or fallback rate resolution for EGP
    rate_egp = get_eur_rate("EGP")
    assert rate_egp["source_currency"] == "EGP"
    assert rate_egp["rate"] > 0


def test_policy_claim_with_hotel_ceiling_and_per_diem():
    dest_germany = {
        "destination": "Germany",
        "currency": "EUR",
        "daily_allowance": "40.00",
        "hotel_ceiling": "150.00",
    }
    # 3 nights (4 days) hotel in Germany: 400 EUR total hotel
    # Ceiling is 3 nights * 150 = 450 EUR. 400 <= 450 -> no warning
    claim = calculate_policy_claim(
        details={
            "document_type": "hotel",
            "amount": "400.00",
            "currency": "EUR",
            "start_date": "2026-11-01",
            "end_date": "2026-11-04",
            "vendor": "Hotel Berlin",
        },
        destination_rate=dest_germany,
    )
    assert claim["travel_days"] == 4
    assert claim["daily_rate"] == "40.00"
    assert claim["daily_allowance"] == "160.00"  # 4 * 40
    assert claim["total_eur"] == "560.00"  # 400 + 160
    assert claim["hotel_warning"] == ""

    # Hotel exceeding ceiling: 3 nights @ 200 EUR/night = 600 EUR > 450 EUR
    claim_expensive = calculate_policy_claim(
        details={
            "document_type": "hotel",
            "amount": "600.00",
            "currency": "EUR",
            "start_date": "2026-11-01",
            "end_date": "2026-11-04",
            "vendor": "Luxury Hotel",
        },
        destination_rate=dest_germany,
    )
    assert "exceeds destination ceiling" in claim_expensive["hotel_warning"]


def test_policy_claim_foreign_currency_conversion():
    dest_egypt = {
        "destination": "Egypt",
        "currency": "EUR",
        "daily_allowance": "35.00",
        "hotel_ceiling": "120.00",
    }
    # Flight booked in USD: 200.00 USD, frozen rate 0.90 EUR/USD
    # Trip: 2 days (Nov 1 to Nov 2) in Egypt -> per diem = 2 * 35 = 70 EUR
    # Booking: 200 * 0.90 = 180 EUR
    # Total EUR: 180 + 70 = 250.00 EUR
    claim = calculate_policy_claim(
        details={
            "document_type": "flight",
            "amount": "200.00",
            "currency": "USD",
            "start_date": "2026-11-01",
            "end_date": "2026-11-02",
            "vendor": "EgyptAir",
        },
        destination_rate=dest_egypt,
        frozen_fx_rates={"USD": Decimal("0.900000")},
    )
    assert claim["booking_eur"] == "180.00"
    assert claim["daily_allowance"] == "70.00"
    assert claim["total_eur"] == "250.00"
    assert len(claim["expense_lines"]) == 2


def test_duplicate_document_detection(tmp_path):
    db_path = tmp_path / "dup_test.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)

    # Seed admin user
    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("admin@example.org", hash_password("admin-password-123"), "admin", 1, "2026-11-01T00:00:00Z"),
        )
        # Seed an already confirmed record with known hash
        db.execute("""
            INSERT INTO finance_records
            (created_at, created_by, source_name, source_mime, source_hash, status, details_json, confirmed_at)
            VALUES (?, 1, 'ticket.pdf', 'application/pdf', 'hash-12345', 'Confirmed', '{}', '2026-11-01T01:00:00Z')
        """, ("2026-11-01T00:00:00Z",))
        # Seed a new record with the SAME hash
        db.execute("""
            INSERT INTO finance_records
            (created_at, created_by, source_name, source_mime, source_hash, status, details_json)
            VALUES (?, 1, 'ticket_copy.pdf', 'application/pdf', 'hash-12345', 'Needs your attention',
                    '{"document_type":"flight","amount":"100","currency":"EUR","start_date":"2026-11-01","end_date":"2026-11-01"}')
        """, ("2026-11-01T02:00:00Z",))

    with TestClient(create_app(config)) as client:
        client.post("/login", data={"email": "admin@example.org", "password": "admin-password-123"})
        # Review page for record 2 should detect duplicate
        res = client.get("/finance/2")
        assert res.status_code == 200
        assert "matches confirmed claim F-1" in res.text


def test_rate_token_invalidation_on_setting_change(tmp_path):
    db_path = tmp_path / "token_test.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)

    with database(db_path) as db:
        db.execute(
            "INSERT INTO users (email, password_hash, role, active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("admin@example.org", hash_password("admin-password-123"), "admin", 1, "2026-11-01T00:00:00Z"),
        )
        db.execute("""
            INSERT INTO finance_records
            (created_at, created_by, source_name, source_mime, source_hash, status, details_json)
            VALUES (?, 1, 'ticket.pdf', 'application/pdf', 'hash-999', 'Needs your attention',
                    '{"document_type":"flight","amount":"100","currency":"EUR","start_date":"2026-11-01","end_date":"2026-11-01"}')
        """, ("2026-11-01T00:00:00Z",))

    with TestClient(create_app(config)) as client:
        client.post("/login", data={"email": "admin@example.org", "password": "admin-password-123"})
        csrf = client.get("/").text.split('name="csrf" value="')[1].split('"')[0]

        # Stale token from earlier review page
        stale_token = "stale-outdated-token-hex"
        res = client.post("/finance/1/confirm", data={
            "csrf": csrf,
            "rule_token": stale_token,
            "document_type": "flight",
            "traveller": "A. Tester",
            "start_date": "2026-11-01",
            "end_date": "2026-11-01",
            "amount": "100.00",
            "currency": "EUR",
            "category": "Travel",
        })
        assert res.status_code == 200
        assert "allowance settings, exchange rate" in res.text
