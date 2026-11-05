"""Verify raw PDF bytes reach each provider's native document input."""

import asyncio
import base64
from dataclasses import replace

import httpx
import pytest

from app.integrations.ai import Attachment, ask_model
from app.core.config import load_config


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
def test_direct_pdf_payload(monkeypatch, provider):
    config = replace(load_config(), openai_api_key="test", gemini_api_key="test",
                     anthropic_api_key="test")
    raw = b"%PDF-1.4\nSample"
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        payload = json.loads(request.content)
        captured.append(payload)
        answer = '{"traveller":"Example"}'
        if provider == "openai":
            body = {"output": [{"content": [{"type": "output_text", "text": answer}]}]}
        elif provider == "gemini":
            body = {"candidates": [{"content": {"parts": [{"text": answer}]}}]}
        else:
            body = {"content": [{"type": "text", "text": answer}]}
        return httpx.Response(200, json=body)

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        return real_client(transport=httpx.MockTransport(handler))

    monkeypatch.setattr("app.integrations.ai.httpx.AsyncClient", fake_client)
    result = asyncio.run(ask_model(config, provider, "sample-model", "Read this",
                                   attachment=Attachment("ticket.pdf", "application/pdf", raw)))
    assert result["traveller"] == "Example"
    payload = captured[0]
    encoded = base64.b64encode(raw).decode()
    if provider == "openai":
        assert payload["input"][0]["content"][1]["file_data"].endswith(encoded)
    elif provider == "gemini":
        assert payload["contents"][0]["parts"][1]["inline_data"]["data"] == encoded
    else:
        assert payload["messages"][0]["content"][1]["source"]["data"] == encoded
