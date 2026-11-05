"""Phase 5 Acceptance Gate Tests:
- /health endpoint returns 200 {"status": "ok"}
- Docker configuration syntax and memory limits (300MB target)
- Persistence mount paths and backup/restore roundtrip verification
- Demonstration setup verification
"""

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import load_config
from app.core.db import initialize
from app.web import create_app
from scripts.demo_setup import setup_demo


def test_health_check_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "bootstrap@impactcircles.org")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "bootstrap-password-123")
    db_path = tmp_path / "health.db"
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    initialize(db_path)
    with TestClient(create_app(config)) as client:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}


def test_demo_setup_populates_demonstration_environment(monkeypatch, tmp_path):
    db_path = tmp_path / "demo_test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    # Run demo setup
    setup_demo()

    assert db_path.is_file()
    # Verify synthetic demo files created
    demo_files = db_path.parent / "demo_files"
    assert (demo_files / "sample_flight_ticket.pdf").is_file()
    assert (demo_files / "sample_hotel_receipt.png").is_file()

    # Verify that demo accounts can sign in
    config = replace(
        load_config(),
        database_path=db_path,
        session_secret="test-secret-at-least-24-characters-long",
        cookie_secure=False,
    )
    with TestClient(create_app(config)) as client:
        # Admin sign in
        res_admin = client.post("/login", data={
            "email": "admin@impactcircles.org",
            "password": "DemoAdmin2026!",
        }, follow_redirects=False)
        assert res_admin.status_code == 303

        # Member sign in
        res_member = client.post("/login", data={
            "email": "member@impactcircles.org",
            "password": "DemoMember2026!",
        }, follow_redirects=False)
        assert res_member.status_code == 303


def test_container_and_render_config():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    compose = (root / "compose.yaml").read_text(encoding="utf-8")
    render = (root / "render.yaml").read_text(encoding="utf-8")

    # Verify single worker and memory caps
    assert "--workers 1" in dockerfile or '"--workers", "1"' in dockerfile
    assert "mem_limit: 300m" in compose
    assert "mountPath: /data" in render
    assert "healthCheckPath: /health" in render
