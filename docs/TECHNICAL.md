# Impact Circles Team Tools: Technical Reference

A technical reference describing the architecture, security controls, data model, external integrations, operational constraints, and active production blockers. A local synthetic claim was verified in the connected Finance Sheet on 5 November 2026. Hosted operation and team Sheet sharing remain unverified.

---

## 1. System Architecture

The application is implemented as a single, unified FastAPI service (`app.web:app`) providing two integrated workflows:
1. **Financial Document Workflow:** Document upload, candidate fact extraction via multimodal AI, user review/input, server-side arithmetic calculation, duplicate detection, and Google Sheets export.
2. **Project-Aware Content Creator:** Working title and brief ingest, project narrative scoping, bounded approved history context, 3-channel draft generation (LinkedIn, Newsletter, Website), user in-place editing, approval, and Google Sheets export.

```text
                           ┌────────────────────────┐
                           │   Standard Web Browser │
                           └───────────┬────────────┘
                                       │ HTTPS
                                       ▼
                       ┌────────────────────────────────┐
                       │  Reverse Proxy (Caddy / Render)│
                       │   - TLS termination            │
                       │   - 9 MB body limit            │
                       └───────────────┬────────────────┘
                                       │ HTTP
                                       ▼
                       ┌────────────────────────────────┐
                       │       FastAPI Application      │
                       │         (Single Worker)        │
                       │  - Auth & HMAC session cookie  │
                       │  - CSRF validation             │
                       │  - Modular routers             │
                       └───────┬───────┬───────┬────────┘
                               │       │       │
            ┌──────────────────┘       │       └──────────────────┐
            ▼                          ▼                          ▼
   ┌─────────────────┐       ┌─────────────────┐       ┌─────────────────────┐
   │ SQLite disk or  │       │  Multimodal AI  │       │ Google Apps Script  │
   │ remote Postgres │       │  (Gemini/OAI/   │       │   (/exec Webhook)   │
   │ DB + originals │       │   Anthropic)    │       │          │          │
   └─────────────────┘       └─────────────────┘       │          ▼          │
                                                       │ Google Sheets Tab   │
                                                       │ (Finance / Content) │
                                                       └─────────────────────┘
```

### Key Architectural Invariants
- **Single Process & Single Worker (`--workers 1`):** The Docker service uses one worker. Local and paid-disk installs use SQLite; setting `DATABASE_URL` selects PostgreSQL for a diskless trial. The container listens on the host's `PORT` variable or 8000. The final duplicate check locks finance records before confirming a claim.
- **Multimodal AI as Feature Extractor Only:** Models extract candidate facts only; they never compute monetary amounts, exchange rates, or per diem days. All calculations are executed server-side using `decimal.Decimal`.
- **Zero Local OCR Overhead:** The application transmits original PDF or image binary bytes directly to the external multimodal model (base64). No local OCR engines (Tesseract) or PDF renderers (Poppler, pdf2image) are installed.
- **Permanent Ledger Attribution:** When a record is created and subsequently confirmed or approved, the user's `display_name` and `email` are permanently snapshotted into `creator_name`, `creator_email`, `actor_name`, and `actor_email`. Subsequent edits to user accounts do not alter historical records.

---

## 2. Data Model & Database Migrations

SQLite operates with `PRAGMA foreign_keys = ON` and `PRAGMA busy_timeout = 15000`. The schema is managed through versioned migrations in [`app/core/migrations.py`](../app/core/migrations.py).

For a **fresh** remote PostgreSQL database, set `DATABASE_URL`; [`app/core/postgres_migrations.py`](../app/core/postgres_migrations.py) creates the equivalent current schema. The PostgreSQL path is intended for the free trial and does not import an existing SQLite database automatically. New PDF and image originals are stored in `finance_sources` and `content_sources` alongside their parent records. Existing disk files remain readable through a fallback path.

### Entity Relationship Diagram

```text
┌────────────────────────┐         1:N         ┌────────────────────────┐
│         users          ├────────────────────►│        sessions        │
│ ────────────────────── │                     │ ────────────────────── │
│ id (PK)                │                     │ token_hash (PK)        │
│ email (UNIQUE)         │                     │ user_id (FK -> users)  │
│ password_hash          │                     │ csrf_token             │
│ role (admin/member)    │                     │ expires_at             │
│ display_name           │                     └────────────────────────┘
│ active (0/1)           │
│ created_at             │         1:N         ┌────────────────────────┐
└──────────┬─────────────┼────────────────────►│    login_attempts      │
           │             │                     │ ────────────────────── │
           │             │                     │ email, attempted_at    │
           │             │                     └────────────────────────┘
           │             │
           │ 1:N         │ 1:N
           ▼             ▼
┌────────────────────────┐         1:N         ┌────────────────────────┐
│        projects        ├────────────────────►│    content_records     │
│ ────────────────────── │                     │ ────────────────────── │
│ id (PK)                │                     │ id (PK)                │
│ name (UNIQUE)          │                     │ project_id (FK)        │
│ description            │                     │ created_by (FK)        │
│ created_by (FK)        │                     │ title, source_text     │
│ active (0/1)           │                     │ source_name, drafts_json│
└────────────────────────┘                     │ status (Approved/...)  │
                                               │ creator_name/email     │
                                               │ approved_by (FK)       │
                                               │ actor_name/email       │
                                               │ sheet_synced_at/error  │
                                               └────────────────────────┘

┌────────────────────────┐                     ┌────────────────────────┐
│    finance_records     │                     │     expense_lines      │
│ ────────────────────── │         1:N         │ ────────────────────── │
│ id (PK)                ├────────────────────►│ id (PK)                │
│ created_by (FK)        │                     │ record_id (FK)         │
│ source_name, mime, hash│                     │ line_type, description │
│ details_json           │                     │ original_amount/curr   │
│ calculation_json       │                     │ fx_rate, eur_amount    │
│ total_eur, source_curr │                     └────────────────────────┘
│ confirmed_at           │
│ duplicate_of (FK)      │                     ┌────────────────────────┐
│ confirmed_by (FK)      │                     │   destination_rates    │
│ duplicate_override_rsn │                     │ ────────────────────── │
│ creator_name/email     │                     │ id (PK)                │
│ actor_name/email       │                     │ destination (UNIQUE)   │
│ sheet_synced_at/error  │                     │ currency, allowance    │
└────────────────────────┘                     │ hotel_ceiling          │
                                               └────────────────────────┘
```

### Migration History
1. **Migration 1 (Base Schema):** Created core tables: `users`, `sessions`, `login_attempts`, `settings`, `finance_records`, `content_records`. Seeded default settings (`per_diem_rates`, `categories`, `claim_include_booking_amount`).
2. **Migration 2 (Projects Entity):** Created `projects` table with default `General / Unassigned` (ID 1) and added `project_id` foreign key to `content_records`.
3. **Migration 3 (Destination Rates & EUR Lines):** Created `destination_rates` and `expense_lines` tables; added `total_eur`, `source_currency`, `destination`, `trip_start`, `trip_end` columns to `finance_records`.
4. **Migration 4 (Display Names & Duplicate Review):** Added `display_name` to `users`; added `duplicate_of`, `confirmed_by`, and `duplicate_override_reason` to `finance_records`; added `approved_by` to `content_records`. Initialized display names from email usernames.
5. **Migration 5 (Project Ownership):** Added `created_by` to `projects` and backfilled owners based on earliest recorded draft creator or default admin.
6. **Migration 6 (Attribution Snapshots):** Added `creator_name`, `creator_email`, `actor_name`, `actor_email` to `finance_records` and `content_records`, backfilling existing records.
7. **Migration 7 (EUR Allowance):** Kept the configured EUR daily allowance and removed older currency-specific daily allowance settings. Historical confirmed claims are unchanged.
8. **Migration 8 (Original Documents):** Added database tables for newly uploaded finance and content originals. Older files in `uploads/` remain readable.

---

## 3. Security Architecture & Controls

- **Password Storage:** Uses PBKDF2-HMAC-SHA256 with 260,000 iterations and a 16-byte cryptographically secure salt. Passwords must be at least 12 characters.
- **Session Tokens:** 32-byte cryptographically random session tokens stored in the browser as an `impact_session` cookie. On the server, only the HMAC-SHA256 digest of the token (keyed by `SESSION_SECRET`) is stored in the `sessions` table.
- **Cookie Security:** Cookies are configured with `HttpOnly=True`, `SameSite="lax"`, and `Secure=True` in production (controlled via `COOKIE_SECURE` environment variable).
- **CSRF Protection:** State-changing POST requests require a hidden `csrf` token matching the current session's `csrf_token`. Validation uses constant-time string comparison (`hmac.compare_digest`).
- **Brute-Force Login Throttling:** Failed login attempts are recorded in `login_attempts`. If more than 5 failed attempts occur within 15 minutes for an email address, login requests for that address are throttled.
- **Authorization Boundaries:**
  - Members can view only their assigned projects and their own current records.
  - Members can see their own finance history; organization-wide finance history, admin settings, user management, and duplicate overrides require `role == 'admin'`.
  - New finance and content originals are stored as database bytes with their parent record. The `/source/finance/{id}` route checks the session and record ownership before serving the file: members can open their own originals and admins can open all. An old `uploads/finance-<id>` file remains available as a fallback. The uploader or an admin can delete an unconfirmed draft and its upload; confirmed claims cannot be deleted through that control.
- **HTTP Security Headers:**
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: same-origin`
  - `Cache-Control: private, no-store` (for authenticated responses and the served app JavaScript/CSS)
- **Spreadsheet Security:** App roles do not grant or restrict access to Google Sheets. Google Drive sharing permissions must be managed independently to protect financial ledgers.

---

## 4. Google Sheets Integration & Protocol

The application exports confirmed and approved records to Google Sheets via an outbound HTTPS POST request to an Apps Script Web App.

```text
FastAPI app/integrations/sheets.py
       │
       │ POST https://script.google.com/macros/s/.../exec
       │ Payload: { "secret": "...", "tab": "Finance|Content", "values": [...] }
       ▼
Google Apps Script (SheetsWebhook.gs)
       │
       │ 1. Validate secret (len >= 24)
       │ 2. Validate row length (Finance: 26 cols, Content: 13 cols)
       │ 3. Validate ID format (/^F-\d+$/ or /^C-\d+$/)
       │ 4. Acquire LockService lock (20 sec)
       │ 5. Read Column A; if ID exists, skip append (idempotent)
       │ 6. Escape formula triggers: /^\s*[=+@-]/ -> prepend "'"
       │ 7. Append row, flush spreadsheet, release lock
       ▼
HTTP 302/303 Redirect to https://script.googleusercontent.com/...
       │
       │ (FastAPI client follows redirect with GET without forwarding secret)
       ▼
HTTP 200 JSON: { "status": "ok", "id": "F-1" }
```

### Verification & Test Status (2026-11-05)
On 2026-11-02, the Apps Script deployment was updated to accept 26-column Finance and 13-column Content rows. Both formats were acknowledged using existing synthetic IDs (`F-1`, `C-1`) in automated tests. That check confirmed:
- Successful authentication using `SHEETS_WEBHOOK_SECRET`.
- Rejection of invalid tab names or improper row lengths.
- Script lock acquisition and safe release.
- Column A deduplication and formula character escaping.
- Proper redirect handling via `script.googleusercontent.com`.

On 2026-11-05, the local app confirmed synthetic claim F-119 for 206.50 EUR and the connected Finance Sheet displayed its row with 3 payable days, a 35.50 EUR rate, a 106.50 EUR allowance, and a 206.50 EUR total. This does not establish organization-owned access: the visible Sheet sharing state was **Private to only me**. Hosted verification and a shared organization Sheet remain open.

---

## 5. Duplicate Detection Engine

Located in [`app/finance/duplicates.py`](../app/finance/duplicates.py):
1. **File Hash Check:** Compares the SHA-256 hash of the uploaded document with `source_hash` of all confirmed claims in `finance_records`.
2. **Normalized Invoice Check:** Normalizes both invoice number and vendor:
   ```python
   def _normalized(value: str) -> str:
       return "".join(ch for ch in unicodedata.normalize("NFKC", str(value or "")).casefold() if ch.isalnum())
   ```
   If both normalized fields match a confirmed record, it is flagged as a duplicate.
3. **Transaction Serialization:** During claim confirmation, `db.execute("BEGIN IMMEDIATE")` takes a SQLite write lock or is translated to a PostgreSQL finance-table lock, preventing two concurrent requests from confirming duplicate claims simultaneously.
4. **Admin Override:** If a duplicate is detected, only an admin can confirm it by providing an explicit `duplicate_override_reason` (minimum 5 characters). Non-admins cannot bypass this check.

---

## 6. Known Production Blockers & Finance Limitations

The following items are active blockers that must be resolved before using the system for live financial disbursements:

1. **Finance policy approval:**
   The active route `/finance/{id}/confirm` executes `_claim()`, which applies one flat EUR daily allowance and counts inclusive calendar days. It excludes dates already paid on the same uploader's confirmed travel claims. Finance still needs to approve this policy for actual reimbursements. The more comprehensive `calculate_policy_claim()` function (destination rates and hotel ceilings) is not wired into the active workflow.
2. **Daily FX dependency:**
   [`app/integrations/fx.py`](../app/integrations/fx.py) requests EUR-based rates from ExchangeRate-API Open Access, caches them for up to 12 hours, and rejects quotes older than 36 hours. A foreign-currency confirmation stops if no recent quote is available; there is no hardcoded fallback. The dated rate and provider are saved in `calculation_json` and the booking `expense_lines` entry. The API is free, updates once daily, is rate limited, and requires attribution in the UI. The quote is an indicative reference rate, so finance should approve its use for reimbursement.
3. **Multi-Leg Trips & Itemized Receipts:**
   Current confirmation assumes one primary document and flat per diem per trip. Complex multi-leg trips with multiple itemized invoices cannot yet be combined into a single claim.

---

## 7. Performance & Memory Profile

- **Memory Target:** Configured with a `< 300 MB` resident memory budget in `compose.yaml` (`mem_limit: 300m`).
- **Measured Local Baseline:**
  - Base FastAPI resident memory after import: ~74 MB RSS.
  - Peak resident memory while buffering an 8 MB PDF request payload: ~104 MB RSS.
- **Upload Limit:** Capped at 8 MB at the FastAPI application layer (`app/core/auth.py`), and 9 MB at the Caddy reverse proxy layer.
