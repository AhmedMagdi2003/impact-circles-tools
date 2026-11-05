"""One direct multimodal request per job. No local PDF rendering or OCR."""

import asyncio
import base64
import json
import re
from dataclasses import dataclass

import httpx

from app.core.config import Config

AI_TIMEOUT_SECONDS = 90


@dataclass(frozen=True)
class Attachment:
    name: str
    mime: str
    data: bytes


def _json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I)
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("AI did not return a usable result. Please try again.")
    return value


def _key(config: Config, provider: str) -> str:
    key = {
        "openai": config.openai_api_key,
        "gemini": config.gemini_api_key,
        "anthropic": config.anthropic_api_key,
    }.get(provider, "")
    if not key:
        raise ValueError(f"An admin must configure the {provider} API key first")
    return key


async def ask_model(
    config: Config, provider: str, model: str, prompt: str,
    source_text: str = "", attachment: Attachment | None = None,
) -> dict:
    """Bound the entire provider call and show safe, actionable errors."""
    try:
        return await asyncio.wait_for(
            _ask_model(config, provider, model, prompt, source_text, attachment),
            timeout=AI_TIMEOUT_SECONDS,
        )
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ValueError("The AI service took too long to respond. Please try again in a moment.") from exc
    except httpx.RequestError as exc:
        raise ValueError("Could not connect to the AI service. Check your internet connection and try again.") from exc
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("The AI service returned an incomplete response. Please try again.") from exc


async def _ask_model(
    config: Config, provider: str, model: str, prompt: str,
    source_text: str = "", attachment: Attachment | None = None,
) -> dict:
    """Pass raw PDF/image bytes to the selected provider in this request."""
    if provider not in {"openai", "gemini", "anthropic"}:
        raise ValueError("Choose OpenAI, Gemini, or Anthropic in the server settings")
    if not model:
        raise ValueError("An admin must configure a model name first")
    key = _key(config, provider)
    instruction = prompt + ("\n\nSource brief:\n" + source_text if source_text else "")
    encoded = base64.b64encode(attachment.data).decode("ascii") if attachment else ""
    timeout = httpx.Timeout(90.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        if provider == "openai":
            parts = [{"type": "input_text", "text": instruction}]
            if attachment:
                if attachment.mime == "application/pdf":
                    parts.append({"type": "input_file", "filename": attachment.name,
                                  "file_data": f"data:application/pdf;base64,{encoded}"})
                else:
                    parts.append({"type": "input_image",
                                  "image_url": f"data:{attachment.mime};base64,{encoded}"})
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": [{"role": "user", "content": parts}],
                      "max_output_tokens": 1800},
            )
            _check(response)
            data = response.json()
            text = "".join(block.get("text", "") for item in data.get("output", [])
                           for block in item.get("content", []) if block.get("type") == "output_text")
        elif provider == "gemini":
            parts = [{"text": instruction}]
            if attachment:
                parts.append({"inline_data": {"mime_type": attachment.mime, "data": encoded}})
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={"contents": [{"parts": parts}],
                      "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 1800}},
            )
            _check(response)
            data = response.json()
            text = "".join(part.get("text", "") for part in
                           data["candidates"][0]["content"]["parts"])
        else:
            blocks = [{"type": "text", "text": instruction}]
            if attachment:
                kind = "document" if attachment.mime == "application/pdf" else "image"
                blocks.append({"type": kind, "source": {"type": "base64",
                               "media_type": attachment.mime, "data": encoded}})
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                json={"model": model, "max_tokens": 1800,
                      "messages": [{"role": "user", "content": blocks}]},
            )
            _check(response)
            text = "".join(block.get("text", "") for block in response.json().get("content", [])
                           if block.get("type") == "text")
    try:
        return _json_object(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("AI could not return usable details. Please try again.") from exc


def _check(response: httpx.Response) -> None:
    if response.is_error:
        # Provider bodies can contain request data. Keep them out of staff pages and logs.
        if response.status_code == 429:
            raise ValueError("The AI service is busy or its usage limit has been reached. Try again later or ask an admin to check the quota.")
        if response.status_code in {401, 403}:
            raise ValueError("The AI service denied access. Ask an admin to check the API key and permissions.")
        if response.status_code == 404:
            raise ValueError("The selected AI model is unavailable. Ask an admin to check the model name.")
        raise ValueError("The AI service could not complete this request. Please try again later.")
