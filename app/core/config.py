"""Environment-only secrets and per-tool model choices."""

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    database_path: Path
    session_secret: str
    cookie_secure: bool
    public_base_url: str
    finance_provider: str
    finance_model: str
    content_provider: str
    content_model: str
    openai_api_key: str
    gemini_api_key: str
    anthropic_api_key: str
    sheets_webhook_url: str
    sheets_webhook_secret: str
    publish_webhook_url: str
    publish_webhook_token: str


def load_config() -> Config:
    # A local .env is useful for development. Cloud and VPS deployments set these
    # values in the host's secret manager instead.
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    get = os.environ.get
    return Config(
        database_path=Path(get("DATABASE_PATH", "./data/impact_tools.db")),
        session_secret=get("SESSION_SECRET", ""),
        cookie_secure=get("COOKIE_SECURE", "true").lower() == "true",
        public_base_url=get("PUBLIC_BASE_URL", ""),
        finance_provider=get("FINANCE_PROVIDER", "gemini").lower(),
        finance_model=get("FINANCE_MODEL", ""),
        content_provider=get("CONTENT_PROVIDER", "gemini").lower(),
        content_model=get("CONTENT_MODEL", ""),
        openai_api_key=get("OPENAI_API_KEY", ""),
        gemini_api_key=get("GEMINI_API_KEY", ""),
        anthropic_api_key=get("ANTHROPIC_API_KEY", ""),
        sheets_webhook_url=get("SHEETS_WEBHOOK_URL", ""),
        sheets_webhook_secret=get("SHEETS_WEBHOOK_SECRET", ""),
        publish_webhook_url=get("PUBLISH_WEBHOOK_URL", ""),
        publish_webhook_token=get("PUBLISH_WEBHOOK_TOKEN", ""),
    )
