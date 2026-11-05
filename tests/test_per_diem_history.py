"""One allowance per account per travel day, with private claim history."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize
from app.finance.rules import calculate_claim, calculate_days
from app.web import create_app


def test_overlapping_days_are_paid_once_and_history_is_private(monkeypatch, tmp_path):
    cfg = replace(load_config(), database_path=tmp_path / "claims.db",
                  session_secret="test-secret-longer-than-24-characters", cookie_secure=False)
    initialize(cfg.database_path)
    with database(cfg.database_path) as db:
        for email in ("alice@example.org", "bob@example.org"):
            db.execute(
                "INSERT INTO users(email,password_hash,role,created_at) VALUES (?,?,?,?)",
                (email, hash_password("test-password-long"), "member", "now"),
            )

    documents = {
        "flight": {"document_type": "flight", "traveller": "Alice Example", "start_date": "2026-11-10",
                   "end_date": "2026-11-12", "origin": "Cairo", "destination": "Berlin",
                   "vendor": "Sample Air", "amount": "120.00", "currency": "EUR"},
        "hotel": {"document_type": "hotel", "traveller": "Alice Example", "start_date": "2026-11-11",
                  "end_date": "2026-11-12", "origin": "Berlin", "destination": "Berlin",
                  "vendor": "Sample Hotel", "amount": "90.00", "currency": "EUR"},
        "train": {"document_type": "train", "traveller": "Alice Example", "start_date": "2026-11-12",
                  "end_date": "2026-11-13", "origin": "Berlin", "destination": "Leipzig",
                  "vendor": "Sample Rail", "amount": "40.00", "currency": "EUR"},
        "usd-hotel": {"document_type": "hotel", "traveller": "Alice Example", "start_date": "2026-11-11",
                      "end_date": "2026-11-12", "origin": "Berlin", "destination": "Berlin",
                      "vendor": "Different Sample Hotel", "amount": "90.00", "currency": "USD"},
    }

    async def fake_model(config, provider, model, prompt, source_text="", attachment=None):
        kind = attachment.data.splitlines()[-1].decode().removeprefix("bob-")
        return documents[kind]

    async def fake_sheet(config, tab, row):
        pass

    monkeypatch.setattr("app.web.ask_model", fake_model)
    monkeypatch.setattr("app.web.append_once", fake_sheet)

    def login(client, email):
        response = client.post("/login", data={"email": email, "password": "test-password-long"})
        assert response.status_code == 200
        return client.get("/").text.split('name="csrf" value="')[1].split('"')[0]

    def upload(client, csrf, kind):
        response = client.post("/finance/upload", data={"csrf": csrf}, files={
            "file": (f"{kind}.pdf", f"%PDF-1.4\n{kind}".encode(), "application/pdf")
        }, follow_redirects=False)
        assert response.status_code == 303
        return response.headers["location"]

    def confirm(client, csrf, url, kind, token=None):
        if token is None:
            page = client.get(url)
            token = page.text.split('name="rule_token" value="')[1].split('"')[0]
        details = documents[kind.removeprefix("bob-")]
        return client.post(url + "/confirm", data={
            **details, "csrf": csrf, "rule_token": token, "category": "Travel"
        }, follow_redirects=False)

    with TestClient(create_app(cfg)) as alice, TestClient(create_app(cfg)) as bob:
        alice_csrf = login(alice, "alice@example.org")
        bob_csrf = login(bob, "bob@example.org")
        flight = upload(alice, alice_csrf, "flight")
        hotel = upload(alice, alice_csrf, "hotel")
        hotel_review = alice.get(hotel).text
        assert "2 payable days" in hotel_review
        stale_token = hotel_review.split('name="rule_token" value="')[1].split('"')[0]

        assert confirm(alice, alice_csrf, flight, "flight").headers["location"] == flight + "/confirmed"
        stale = confirm(alice, alice_csrf, hotel, "hotel", stale_token)
        assert stale.status_code == 200
        assert "travel claim changed" in stale.text
        with database(cfg.database_path) as db:
            assert db.execute("SELECT confirmed_at FROM finance_records WHERE id=2").fetchone()[0] is None

        hotel_review = alice.get(hotel).text
        assert "0 payable days" in hotel_review
        assert "2 of 2 travel days already claimed" in hotel_review
        assert confirm(alice, alice_csrf, hotel, "hotel").headers["location"] == hotel + "/confirmed"

        train = upload(alice, alice_csrf, "train")
        train_review = alice.get(train).text
        assert "1 payable day" in train_review
        assert confirm(alice, alice_csrf, train, "train").headers["location"] == train + "/confirmed"

        own_page = alice.get("/finance").text
        assert "Current rate: 35.50 EUR per day" in own_page
        assert "Days paid: 4" in own_page
        assert "Allowance claimed: 142.00 EUR" in own_page
        assert "flight.pdf" in own_page and "hotel.pdf" in own_page and "train.pdf" in own_page
        assert "My claim history" in own_page

        with database(cfg.database_path) as db:
            db.execute("UPDATE settings SET value='false' WHERE key='claim_include_booking_amount'")
        no_allowance = upload(alice, alice_csrf, "usd-hotel")
        assert "0 payable days" in alice.get(no_allowance).text
        assert confirm(alice, alice_csrf, no_allowance, "usd-hotel").status_code == 303
        with database(cfg.database_path) as db:
            zero = json.loads(db.execute(
                "SELECT calculation_json FROM finance_records WHERE id=4"
            ).fetchone()[0])
            assert zero["total"] == "0.00" and zero["total_eur"] == "0.00"

        bob_page = bob.get("/finance").text
        assert "Days paid: 0" in bob_page
        assert "flight.pdf" not in bob_page
        bob_flight = upload(bob, bob_csrf, "bob-flight")
        assert "3 payable days" in bob.get(bob_flight).text
        assert bob.get(flight).status_code == 404

    with database(cfg.database_path) as db:
        results = [json.loads(row[0]) for row in db.execute(
            "SELECT calculation_json FROM finance_records WHERE id IN (1,2,3) ORDER BY id"
        )]
    assert [(r["eligible_days"], r["already_claimed_days"], r["daily_allowance"], r["total"])
            for r in results] == [
                (3, 0, "106.50", "226.50"),
                (0, 2, "0.00", "90.00"),
                (1, 1, "35.50", "75.50"),
            ]


def test_travel_span_over_365_days_is_rejected():
    assert calculate_days("2026-01-01", "2026-12-31") == 365
    with pytest.raises(ValueError, match="between 1 and 365 days"):
        calculate_claim({"document_type": "flight", "amount": "100.00", "currency": "EUR",
                         "start_date": "2026-01-01", "end_date": "2027-01-01"}, "35.50", "EUR", True)


def test_member_deletes_own_draft_and_admin_can_delete_any_draft(tmp_path):
    cfg = replace(load_config(), database_path=tmp_path / "claims.db",
                  session_secret="test-secret-longer-than-24-characters", cookie_secure=False)
    initialize(cfg.database_path)
    with database(cfg.database_path) as db:
        for role in ("admin", "member", "other"):
            db.execute(
                "INSERT INTO users(email,password_hash,role,created_at) VALUES (?,?,?,?)",
                (f"{role}@example.org", hash_password("test-password-long"),
                 "admin" if role == "admin" else "member", "now"),
            )
        for record_id, owner_id, confirmed_at in (
            (1, 2, None), (2, 2, "2026-11-05T10:00:00Z"), (3, 3, None), (4, 2, None)
        ):
            db.execute(
                "INSERT INTO finance_records(id,created_at,created_by,source_name,source_mime,"
                "source_hash,status,details_json,confirmed_at,calculation_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (record_id, "now", owner_id, f"sample-{record_id}.pdf", "application/pdf",
                 f"hash-{record_id}", "Confirmed" if confirmed_at else "Needs your attention",
                 '{}', confirmed_at,
                 json.dumps({"booking_claim": "10.00", "travel_days": 0, "daily_rate": "0.00",
                             "daily_allowance": "0.00", "total": "10.00", "currency": "EUR"})
                 if confirmed_at else None),
            )
    upload_dir = cfg.database_path.parent / "uploads"
    upload_dir.mkdir()
    (upload_dir / "finance-1").write_bytes(b"draft")
    (upload_dir / "finance-2").write_bytes(b"confirmed")
    (upload_dir / "finance-3").write_bytes(b"other draft")
    (upload_dir / "finance-4").write_bytes(b"admin removes")

    with TestClient(create_app(cfg)) as admin, TestClient(create_app(cfg)) as member:
        for client, role in ((admin, "admin"), (member, "member")):
            assert client.post("/login", data={"email": f"{role}@example.org",
                                               "password": "test-password-long"}).status_code == 200
        admin_csrf = admin.get("/finance").text.split('name="csrf" value="')[1].split('"')[0]
        member_csrf = member.get("/finance").text.split('name="csrf" value="')[1].split('"')[0]
        assert "Delete draft" in admin.get("/finance/1").text
        assert "Delete draft" in member.get("/finance/1").text
        assert "Delete draft" not in member.get("/finance/2").text
        assert member.get("/finance/3").status_code == 404
        assert member.post("/finance/3/delete", data={"csrf": member_csrf,
                     "confirm_delete": "yes"}).status_code == 404
        assert member.post("/finance/2/delete", data={"csrf": member_csrf,
                     "confirm_delete": "yes"}).status_code == 409
        assert admin.post("/finance/2/delete", data={"csrf": admin_csrf,
                     "confirm_delete": "yes"}).status_code == 409
        assert member.post("/finance/1/delete", data={"csrf": member_csrf,
                     "confirm_delete": "yes"}, follow_redirects=False).headers["location"] == "/finance"
        assert admin.post("/finance/4/delete", data={"csrf": admin_csrf,
                     "confirm_delete": "yes"}, follow_redirects=False).headers["location"] == "/finance"

    with database(cfg.database_path) as db:
        assert [row[0] for row in db.execute("SELECT id FROM finance_records ORDER BY id")] == [2, 3]
    assert not (upload_dir / "finance-1").exists()
    assert (upload_dir / "finance-2").exists()
    assert (upload_dir / "finance-3").exists()
    assert not (upload_dir / "finance-4").exists()
