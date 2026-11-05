"""Local demo setup script for Impact Circles team tools.

Seeds the SQLite database with demonstration accounts, projects, approved history,
destination rates, and creates sample synthetic documents so the entire workflow
can be evaluated locally without live cloud API charges or hosting subscriptions.
"""

import json
from pathlib import Path

from app.core.auth import hash_password
from app.core.config import load_config
from app.core.db import database, initialize, utc_now


def setup_demo():
    config = load_config()
    db_path = config.database_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    initialize(db_path)

    print(f"Initializing demonstration data at: {db_path}")

    with database(db_path) as db:
        # 1. Accounts
        admin_pass = hash_password("DemoAdmin2026!")
        member_pass = hash_password("DemoMember2026!")

        db.execute("""
            INSERT INTO users (id, email, password_hash, role, active, created_at)
            VALUES (1, 'admin@impactcircles.org', ?, 'admin', 1, ?)
            ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash, active=1
        """, (admin_pass, utc_now()))

        db.execute("""
            INSERT INTO users (id, email, password_hash, role, active, created_at)
            VALUES (2, 'member@impactcircles.org', ?, 'member', 1, ?)
            ON CONFLICT(email) DO UPDATE SET password_hash=excluded.password_hash, active=1
        """, (member_pass, utc_now()))

        # 2. Projects
        projects = [
            (1, "General / Unassigned", "General organizational communications and unassigned drafts", 1),
            (2, "Egypt Clean Energy & SDG AI", "Researching decentralized solar microgrids and machine learning monitoring for green communities in Upper Egypt.", 1),
            (3, "Berlin Urban Mobility AI", "Exploring participatory AI frameworks for sustainable municipal public transit in Berlin-Brandenburg.", 1),
        ]
        for pid, name, desc, active in projects:
            db.execute("""
                INSERT INTO projects (id, name, description, active, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description
            """, (pid, name, desc, active, utc_now()))

        # 3. Seed Approved Content Lineage for Project 2
        p2_approved = {
            "linkedin": "Proud to announce our team deployed 50 smart solar sensors across Aswan yesterday! #CleanEnergy #ImpactCircles #SDGs",
            "newsletter": "Dear Partners, our Upper Egypt solar pilot reached 100% telemetry online this week, tracking renewable generation in real time.",
            "website": "### Aswan Solar Pilot Reaches Operational Milestone\nFifty edge sensors now stream continuous energy generation metrics to the Impact Circles open dashboard.",
        }
        db.execute("""
            INSERT OR IGNORE INTO content_records
            (id, created_at, created_by, project_id, title, source_text, drafts_json, status, approved_at)
            VALUES (101, '2026-10-20T10:00:00Z', 1, 2, 'Aswan Pilot Deployment Milestone',
                    'Deployed 50 solar monitoring edge sensors across Aswan community grid.',
                    ?, 'Approved', '2026-10-20T14:30:00Z')
        """, (json.dumps(p2_approved),))

        # 4. Seed Confirmed Travel Claim
        claim_calc = {
            "booking_amount": "280.00",
            "booking_currency": "EUR",
            "booking_included": True,
            "booking_eur": "280.00",
            "booking_fx_rate": "1.0",
            "travel_days": 3,
            "daily_rate": "40.00",
            "daily_allowance": "120.00",
            "total": "400.00",
            "total_eur": "400.00",
            "currency": "EUR",
            "original_currency": "EUR",
            "destination": "Germany",
            "expense_lines": [
                {"description": "Lufthansa Flight LH123", "original_amount": "280.00", "original_currency": "EUR",
                 "fx_rate": "1.0", "fx_display": "1.0000", "fx_provider": "Direct (Identity)", "eur_amount": "280.00"},
                {"description": "Daily Allowance (3 days @ 40.00 EUR/day)", "original_amount": "120.00", "original_currency": "EUR",
                 "fx_rate": "1.0", "fx_display": "1.0000", "fx_provider": "EU Benchmark", "eur_amount": "120.00"},
            ],
            "rule": "EU Policy: 3 calendar days per diem (40.00 EUR/day) + converted receipts in EUR.",
        }
        claim_details = {
            "document_type": "flight",
            "traveller": "Ahmed Magdi",
            "start_date": "2026-10-15",
            "end_date": "2026-10-17",
            "origin": "Cairo",
            "destination": "Berlin",
            "vendor": "Lufthansa",
            "amount": "280.00",
            "currency": "EUR",
            "category": "Travel",
            "description": "Roundtrip travel for project planning conference in Berlin",
        }
        db.execute("""
            INSERT OR IGNORE INTO finance_records
            (id, created_at, created_by, source_name, source_mime, source_hash, status,
             details_json, calculation_json, confirmed_at, total_eur, source_currency, destination)
            VALUES (101, '2026-10-14T09:00:00Z', 1, 'flight_berlin_sample.pdf', 'application/pdf',
                    'demo-hash-101', 'Confirmed', ?, ?, '2026-10-14T11:00:00Z', '400.00', 'EUR', 'Germany')
        """, (json.dumps(claim_details), json.dumps(claim_calc)))

    # 5. Create synthetic sample upload files
    demo_files_dir = db_path.parent / "demo_files"
    demo_files_dir.mkdir(parents=True, exist_ok=True)

    sample_pdf = b"%PDF-1.4\n1 0 obj\n<< /Title (Sample Airline Ticket - Impact Circles) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    (demo_files_dir / "sample_flight_ticket.pdf").write_bytes(sample_pdf)

    # 1x1 transparent PNG
    sample_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    (demo_files_dir / "sample_hotel_receipt.png").write_bytes(sample_png)

    # Also make sure uploads dir has the sample file for viewing
    uploads_dir = db_path.parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    (uploads_dir / "finance-101").write_bytes(sample_pdf)

    print("\n[SUCCESS] Local Demonstration Setup Complete!")
    print("--------------------------------------------------")
    print("Demo Admin User  : admin@impactcircles.org")
    print("Demo Password    : DemoAdmin2026!")
    print("Demo Member User : member@impactcircles.org")
    print("Demo Password    : DemoMember2026!")
    print("Sample Uploads   : data/demo_files/sample_flight_ticket.pdf")
    print("                   data/demo_files/sample_hotel_receipt.png")
    print("--------------------------------------------------")
    print("To start local server:")
    print("  uvicorn app.web:app --host 127.0.0.1 --port 8000 --workers 1")


if __name__ == "__main__":
    setup_demo()
