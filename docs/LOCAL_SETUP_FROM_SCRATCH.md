# Build and Run Impact Circles Tools on a New Windows Computer

This guide assumes a new Windows 10/11 computer and no Python, database server, or development tools. These steps are for the person setting up the app. Everyday staff use only the browser link.

## 1. Get the project files

Open the project's GitHub repository in a browser. Choose **Code → Download ZIP**, save the file, and extract it to a normal folder such as `Documents\Impact-Tools`. Open the extracted folder and confirm it contains `README.md`, `pyproject.toml`, `.env.example`, and `app`. If Windows hides files starting with a dot, turn on **View → Show → Hidden items** in File Explorer. You can also clone with GitHub Desktop if you already use it.

## 2. Install the tool that gets Python and dependencies

Install [uv for Windows](https://docs.astral.sh/uv/getting-started/installation/) using its official installer or WinGet. If WinGet is available, open **PowerShell** from the Start menu and run:

```powershell
winget install --id=astral-sh.uv -e
```

Close and reopen PowerShell, then run `uv --version` to check installation. You do **not** need to install Python or SQL separately: uv can install Python, and SQLite comes with Python.

## 3. Open PowerShell inside the project folder

In File Explorer, open the extracted `Impact-Tools` folder. Click the address bar, type `powershell`, and press Enter. The new window should open in the same folder. Run:

```powershell
uv python install 3.12
uv sync --python 3.12
```

This downloads Python 3.12 and the packages listed in the project. It creates `.venv` locally. An internet connection is needed the first time.

## 4. Create local settings and the first admin

In that same PowerShell window, run:

```powershell
Copy-Item .env.example .env
notepad .env
```

Edit and save these lines in Notepad. Replace the examples with your own private values:

```text
BOOTSTRAP_ADMIN_EMAIL=your.work.email@example.org
BOOTSTRAP_ADMIN_PASSWORD=choose-a-long-private-password
SESSION_SECRET=paste-a-random-secret-here
DATABASE_PATH=./data/impact_tools.db
COOKIE_SECURE=false
PUBLIC_BASE_URL=http://127.0.0.1:8000
```

The admin password must be at least 12 characters. Generate a random session secret with `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`, then paste its output after `SESSION_SECRET=`. Do not share or commit `.env`. Set `FINANCE_PROVIDER`, `FINANCE_MODEL`, `CONTENT_PROVIDER`, `CONTENT_MODEL`, and the corresponding provider API key as instructed by the provider's [model list](https://ai.google.dev/gemini-api/docs/models) and [API key guide](https://ai.google.dev/gemini-api/docs/api-key). The included `.env.example` shows the available fields. Model names and provider pricing can change. Leave `SHEETS_WEBHOOK_URL` and `SHEETS_WEBHOOK_SECRET` blank until connecting Sheets.

## 5. Create or migrate the database and start the app

Run:

```powershell
uv run uvicorn app.web:app --host 127.0.0.1 --port 8000 --workers 1
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in a browser and sign in with the admin email and password from `.env`. The first startup creates the `data` folder and SQLite database, applies every pending migration, and creates the first admin **only if the users table is empty**. There is no SQL server to install and no manual database import on a fresh device. Later starts apply only new migrations and keep existing users and claims. Changing the bootstrap values in `.env` later does not reset an existing admin account.

To stop the local app, press **Ctrl+C** in its PowerShell window. To restart it later, reopen PowerShell in the project folder and run the same `uv run uvicorn ...` command. Only people on this computer can open its `127.0.0.1` link.

## 6. Finish admin setup

In **Settings**, add team members, choose categories, and set the single **EUR daily allowance**.

### Connect Google Sheets

1. Sign in to an organization-owned Google account, create a spreadsheet, and add two tabs named exactly **Finance** and **Content**. Copy the 26 Finance column headings and 13 Content column headings from the [Admin Guide](ADMIN_GUIDE.md) into row 1 of the respective tabs.
2. In that spreadsheet, open **Extensions → Apps Script**. Replace the editor contents with the complete **Webhook code for Apps Script** in the Admin Guide and save it.
3. Find the spreadsheet ID in its address: in `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`, copy only the part between `/d/` and `/edit`. In Apps Script, open **Project Settings → Script Properties** and add `SPREADSHEET_ID` with that value.
4. Generate a new private secret with `uv run python -c "import secrets; print(secrets.token_urlsafe(32))"`. Add a second Script Property named `SHEETS_WEBHOOK_SECRET` with this secret. Keep it available for the next step; the app and script must use **exactly the same value**.
5. Select **Deploy → New deployment → Web app**. Set **Execute as: Me** using the organization account, and **Who has access: Anyone** so the server can call it. Approve Google's authorization prompt. Copy the deployment URL ending in `/exec`; do not copy the editor or `/dev` URL. The script still checks the private shared secret on every request.
6. Stop the local app with **Ctrl+C**, open `.env` in Notepad, and add these two lines using your real values:

   ```text
   SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/YOUR_DEPLOYMENT_ID/exec
   SHEETS_WEBHOOK_SECRET=the-exact-secret-from-script-properties
   ```

   Save `.env` and restart with `uv run uvicorn app.web:app --host 127.0.0.1 --port 8000 --workers 1`. For hosting, put these same values in the host's private environment settings instead of publishing `.env`.
7. Confirm a **synthetic** finance claim. Check for a new `F-...` row in the Finance tab. Approve a synthetic content draft and check for a `C-...` row in Content. If the app says **Google Sheets needs attention**, the claim is still saved locally; check the URL, secret, deployment access, tab names, and **Executions** in Apps Script, then use **Try Sheets again**. Re-deploy the web app after changing its code. Share the spreadsheet only with staff who should see financial data; app roles do not control Google Drive sharing.

The Admin Guide contains the full schema, script, and troubleshooting steps. Google Sheets is an export of confirmed records; the app's SQLite database remains the source used for sign-in, claim history, and calculations.

## Existing data, migrations, and backups

Before updating code on an existing installation, back up the SQLite file **and** the adjacent `uploads` folder. From the project folder, run `uv run python -m scripts.backup --output .\impact-tools-backup.zip` and store the resulting ZIP somewhere private. Then update the project files, run `uv sync --python 3.12`, and start the app. Startup runs migrations automatically. Do not delete the database to reset a password: that would also delete claim history, and the original uploaded files must stay paired with their database records.

If the app does not start, check that `.env` exists, the secret is at least 24 characters, the admin password is at least 12 characters, and no other app is using port 8000. For provider errors, check the API key and model name. For Sheets errors, confirm the webhook URL, secret, tab names, and Google sharing settings. See the [Architecture Reference](TECHNICAL.md) for details.
