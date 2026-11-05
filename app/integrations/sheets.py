"""Send confirmed records to the organization's Apps Script webhook."""

from urllib.parse import urlparse

import httpx

from app.core.config import Config


async def append_once(config: Config, tab: str, row: list[str]) -> None:
    url = urlparse(config.sheets_webhook_url)
    if (url.scheme != "https" or url.netloc != "script.google.com"
            or not url.path.startswith("/macros/s/") or not url.path.endswith("/exec")
            or url.query or url.fragment):
        raise ValueError("An admin must configure the Apps Script deployment URL")
    if len(config.sheets_webhook_secret) < 24:
        raise ValueError("An admin must configure the Sheets webhook secret")
    if tab not in {"Finance", "Content"} or not row or not row[0]:
        raise ValueError("Invalid Sheets record")

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        response = await client.post(config.sheets_webhook_url, json={
            "secret": config.sheets_webhook_secret, "tab": tab, "values": row,
        })
        # ContentService sends its response through a one-time Google redirect.
        # Fetch only the documented host, using GET without sending the secret.
        if response.status_code in {302, 303}:
            target = urlparse(response.headers.get("location", ""))
            if target.scheme != "https" or target.netloc != "script.googleusercontent.com":
                raise ValueError("The Sheets script returned an unexpected redirect")
            response = await client.get(target.geturl())
        if response.status_code in {401, 403}:
            raise ValueError("Apps Script denied access. Ask an admin to check deployment access and redeploy.")
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError as exc:
            raise ValueError("The Sheets script returned an unreadable response") from exc
        if not isinstance(result, dict) or result.get("status") != "ok" or result.get("id") != row[0]:
            raise ValueError("Google Sheets needs attention. Check the script and retry")
