"""Slow and unavailable providers should produce usable messages."""

import asyncio

import httpx
import pytest

import app.integrations.ai as ai
from app.core.config import load_config


def test_provider_call_has_a_total_timeout(monkeypatch):
    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    monkeypatch.setattr(ai, "_ask_model", slow)
    monkeypatch.setattr(ai, "AI_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ValueError, match="took too long"):
        asyncio.run(ai.ask_model(load_config(), "gemini", "model", "Read"))


def test_provider_connection_failure_is_readable(monkeypatch):
    async def disconnected(*args, **kwargs):
        raise httpx.ConnectError("sensitive connection details")

    monkeypatch.setattr(ai, "_ask_model", disconnected)
    with pytest.raises(ValueError, match="Could not connect") as error:
        asyncio.run(ai.ask_model(load_config(), "gemini", "model", "Read"))
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("status,expected", [
    (429, "usage limit"), (401, "denied access"), (403, "denied access"),
    (404, "model is unavailable"), (503, "try again later"),
])
def test_provider_errors_do_not_expose_response_body(status, expected):
    with pytest.raises(ValueError) as error:
        ai._check(httpx.Response(status, text="private provider response"))
    assert expected in str(error.value)
    assert "private" not in str(error.value)
