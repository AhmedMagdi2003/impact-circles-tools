# Impact Circles Team Tools

One small, unified web application for financial document workflows and project-aware content generation. Staff sign in and use two clear flows:

- **Finance:** upload document → enter your personal details and check extracted document details → confirm claim. Possible duplicates are flagged and sent to an admin for review.
- **Content:** choose your assigned project, add a brief or source file → review and edit three tailored drafts (LinkedIn, newsletter, website) → approve. An admin can optionally forward approved drafts to a publishing webhook.

Admins see shared history, review suspected duplicates, and assign project owners. Members see their own claim history, daily allowance rates and usage, and assigned projects. A travel day already paid on a member's confirmed claim does not earn a second daily allowance on another booking. Records are stored in SQLite for local or disk-backed hosting, or PostgreSQL when `DATABASE_URL` is set for a diskless hosted trial. Confirmed finance claims and approved content drafts are exported to Google Sheets through an Apps Script webhook, including snapshot attribution of who created and who confirmed/approved each record.

The AI model only reads documents and drafts text; it never performs financial calculations. PDFs and images are sent directly as raw bytes to the selected multimodal model (Gemini, OpenAI, or Anthropic); there is no heavy local OCR or PDF rendering engine. Newly uploaded originals are stored with their records in the selected database. Older local installations can still read originals retained in their `uploads/` folder. A member can open their own uploaded original from the claim review page, and an admin can open any claim's original. Other members cannot open it through the app. Access to the separate Google Sheet depends on Google Drive sharing settings.

**Current Stage & Production Blockers:**
- **Google Sheets Webhook:** The Google Apps Script webhook was verified on 2026-11-02 for 26-column Finance and 13-column Content formats. On 2026-11-05, a synthetic flight claim was confirmed in the local app and verified in the connected Finance Sheet as F-119 with a 206.50 EUR total. The visible Sheet sharing state was **Private to only me**. An organization-owned, appropriately shared Sheet and hosted acceptance test remain necessary before team rollout.
- **Finance Policy & FX:** The active flow uses one admin-set EUR allowance. Foreign receipts use a dated daily quote from ExchangeRate-API Open Access; the quote is shown before confirmation and stored with the claim. If a recent rate is unavailable, the claim cannot be confirmed. Finance still needs to approve the reimbursement policy, and the hosted flow needs acceptance testing. See the [Technical Reference](docs/TECHNICAL.md).
- **Daily allowance:** Each account receives the configured rate once per eligible calendar travel day. Confirmed overlapping flight, hotel, and train claims do not pay that same account for a day twice. There is no monthly or lifetime allowance balance. A single travel document may span at most 365 days. See [Smart Claims Calculation](docs/SMART_CLAIMS_CALCULATION.md).

---

## Five Guides Needed to Use and Operate the Project

- **[User Guide](docs/USER_GUIDE.md):** Upload, review, confirm, history, and content drafts, with screenshots.
- **[Admin Guide](docs/ADMIN_GUIDE.md):** Users, settings, duplicate review, Sheets connection, and backups.
- **[Smart Claims Calculation](docs/SMART_CLAIMS_CALCULATION.md):** Exact allowance and currency rules, examples, and code locations.
- **[Build Locally from Scratch](docs/LOCAL_SETUP_FROM_SCRATCH.md):** New Windows device setup, first admin, and database migrations.
- **[Free Trial and Paid Hosting](docs/PAID_HOSTING.md):** Railway trial setup, public browser link, persistent storage, costs, and future project hosting.

The sixth published guide is the **[Architecture and Technical Reference](docs/TECHNICAL.md)** for maintainers.

---

## Repository Layout

```text
app/
  web.py                 # FastAPI application factory, middleware, and sync orchestration
  core/
    config.py            # Environment configuration and validation
    auth.py              # PBKDF2 password hashing, HMAC sessions, CSRF, and file uploads
    db.py                # SQLite/PostgreSQL connection adapter, source files, and settings
    migrations.py        # Versioned SQLite migrations (V1 through V8)
    postgres_migrations.py # Fresh PostgreSQL trial schema
    routes.py            # Core paths: /, /login, /logout, /health, /account
    admin_routes.py      # Admin paths: /admin, /admin/settings, /admin/users, /admin/projects
  finance/
    routes.py            # Financial document ingest, review, confirmation, and file delivery
    rules.py             # Decimal financial math, per diem rules, and date calculations
    prompts.py           # Multimodal candidate fact extraction prompt
    duplicates.py        # Exact file hash and normalized invoice duplicate detection
  content/
    routes.py            # Content generation, draft review, approval, and publishing
    prompts.py           # Dynamic prompt builder with project context and bounded history
  integrations/
    ai.py                # Multimodal API client for Gemini, OpenAI, and Anthropic
    fx.py                # Dated daily ExchangeRate-API quotes; no offline fallback
    sheets.py            # Outbound HTTPS webhook client for Google Apps Script
  templates/             # Jinja2 HTML templates
  static/                # CSS stylesheet, browser JavaScript, and organization logo
docs/                    # Six public guides; other local Markdown notes are excluded from Git
scripts/
  backup.py              # Hot SQLite backup and legacy uploads zip archiver
  demo_setup.py          # Synthetic dataset seeder for local testing
  memory_probe.py        # Local memory usage probe
  google_apps_script/    # Local webhook source; public copy is in the Admin Guide
tests/                   # Automated pytest suites and Apps Script simulation harness
Dockerfile               # Container using official uv binary; accepts host PORT or 8000
render.yaml              # Render web service blueprint declaration
compose.yaml             # Docker Compose configuration with Caddy reverse proxy
pyproject.toml           # Project dependencies, build system, and pytest configuration
```

---

## Local Development Setup

### 1. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
# On Linux/macOS:
cp .env.example .env

# On Windows (PowerShell):
Copy-Item .env.example .env
```

Open `.env` and configure:
- `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`: Used to create the initial admin account on first boot.
- `SESSION_SECRET`: A secure random string of at least 24 characters.
- `COOKIE_SECURE=false`: Required for local testing over unencrypted HTTP (`http://127.0.0.1:8000`).
- `PUBLIC_BASE_URL=http://127.0.0.1:8000`: Base URL for local links.
- Model API keys (`GEMINI_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`).
- `SHEETS_WEBHOOK_URL` and `SHEETS_WEBHOOK_SECRET`: Optional locally; leave blank until testing Google Sheets export.

> [!NOTE]
> **Bootstrap Account Behavior:** `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` are checked *only* when the `users` table is completely empty (`COUNT = 0`). Subsequent server restarts leave all existing database accounts, display names, and passwords completely untouched. Changing these environment variables later will *not* reset or update existing accounts.

### 2. Run with `uv` (Recommended)
`uv` provides fast, deterministic dependency resolution and low disk footprint:
```bash
# Synchronize environment
uv sync

# Start development server
uv run uvicorn app.web:app --host 127.0.0.1 --port 8000 --workers 1
```

### 3. Run with Standard `pip` (Alternative)
```bash
# Create virtual environment
python -m venv .venv

# Activate environment:
# On Linux/macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Install dependencies and start server
pip install -r requirements.txt
uvicorn app.web:app --host 127.0.0.1 --port 8000 --workers 1
```

Open `http://127.0.0.1:8000` in your browser and sign in using your bootstrap admin credentials.

---

## Automated Tests

Run the full automated test suite:
```bash
# Using uv:
uv run pytest -v

# Or using pytest directly:
python -m pytest -v
```

Run the Apps Script simulation harness (verifies row widths, deduplication, formula escaping, and security):
```bash
node tests/sheets_webhook_harness.js
```

Run the memory probe:
```bash
uv run python -m scripts.memory_probe
```

> [!IMPORTANT]
> **Single Worker Constraint:** The Docker service uses **one process and one worker (`--workers 1`)**. This is necessary for the SQLite deployment path and keeps the trial within free compute limits.
