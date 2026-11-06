# Why Production Needs Paid Hosting, and How to Run a Free Trial

**A free browser trial is possible.** Railway's no-card trial can host the web app while a separate Neon Free PostgreSQL database preserves accounts, claims, settings, and original documents. Paid hosting is still the simpler production path when the team needs predictable uptime, more capacity, and operational backups. This Render account required a card even for its Free web service, so Railway is the current trial path.

## What the team needs

Staff should open one HTTPS link in a browser and use **Upload → Check details → Confirm** without installing Python or opening a terminal. To provide that link, the application must run on a public web service. Its records and original documents need durable storage, plus private environment settings for sessions, the AI provider, and the Google Sheets webhook.

A local computer can run the app for development, but `127.0.0.1` opens only on that computer. Closing its PowerShell window or turning off the computer stops the service. A public domain alone does not run the application or preserve its files.

## Free trial options

| Option | What it tests | Storage limit |
|---|---|---|
| [Railway Free Trial](https://docs.railway.com/pricing/free-trial) + [Neon Free PostgreSQL](https://neon.com/pricing) | A real cloud deployment, public `*.up.railway.app` link, login, AI reading, review, confirmation, source retrieval, and Sheets export after configuration | Neon stores records and original documents outside Railway, so they survive web-service redeploy. Railway gives new accounts a one-time $5 credit for up to 30 days, then $1/month of free credit. The service may stop when credit is exhausted. Use synthetic documents for initial acceptance testing. |
| Railway with local SQLite only | Short same-day walkthrough | Without an attached volume, the app's local database disappears on redeploy or replacement. Do not rely on its history. |
| Existing company VPS or a [cloud VM trial with persistent disk](https://cloud.google.com/products/compute) | The full server and storage lifecycle, including restart, backups, and a custom domain | Requires a suitable server/account and more setup. Google Cloud advertises trial credits for eligible new accounts; usage must stay within the account's credit and limits. |

### Railway trial setup

1. [Sign up for Railway](https://railway.com/login) with the GitHub account that owns the [public repository](https://github.com/AhmedMagdi2003/impact-circles-tools). Review the GitHub authorization screen and grant only the repository access needed for this deployment. Railway says signup does not require a card.
2. Check the Railway account's **Trial** status. A **Full Trial** has the outbound network access needed for Neon and the AI provider. Railway says a **Limited Trial** restricts outbound access and ports; connecting an established GitHub account may verify the account, but verification is not guaranteed. Test a claim before sharing the link.
3. Create **New Project → Deploy from GitHub repo** and select `AhmedMagdi2003/impact-circles-tools`. Railway detects the root `Dockerfile`. Choose **Add Variables** before the first deployment.
4. In the service's **Variables** page, set `DATABASE_URL` to the **direct** Neon PostgreSQL URL, `COOKIE_SECURE=true`, a stable `SESSION_SECRET` of at least 24 characters, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, `FINANCE_PROVIDER=gemini`, `FINANCE_MODEL`, and `GEMINI_API_KEY`. Set the content provider/model if testing content generation. Do not use the placeholder values from `.env.example`, and never commit passwords, database URLs, or API keys. The PostgreSQL URL overrides `DATABASE_PATH`. Rotate any credential previously shared in chat before using real documents.
5. Deploy the staged changes. In the service's **Settings → Networking → Public Networking**, choose **Generate Domain** and set its target port to **8000**, the container's listening port. Set `PUBLIC_BASE_URL` to the full generated `https://...up.railway.app` URL and redeploy.
6. Create a **separate test Google Sheet** using the [Admin Guide](ADMIN_GUIDE.md), deploy the Apps Script webhook, and set `SHEETS_WEBHOOK_URL` and `SHEETS_WEBHOOK_SECRET` in Railway Variables. Do not connect the current operational Sheet to a fresh database: claim IDs start again at `F-1`, and the webhook deduplicates by ID.
7. Use synthetic tickets and receipts. Confirm a claim, open its original, and verify its Sheet row. Redeploy the app, then check login, history, settings, the original, and the Sheet row again. Keep the Neon database active for the trial.

The first Railway trial lasts up to 30 days or until its $5 credit is spent. After that, its Free plan includes $1/month of credit; the app may not run continuously within that amount. A new user may receive a Limited Trial, whose network restrictions can prevent Neon, Gemini, exchange-rate, or Sheets calls. Do not promise the team a full trial until these calls pass in the hosted app. Railway's [trial terms](https://docs.railway.com/pricing/free-trial), [Dockerfile deployment](https://docs.railway.com/builds/dockerfiles), [variables](https://docs.railway.com/variables), and [domain setup](https://docs.railway.com/networking/domains/working-with-domains) describe the current limits and steps.

If using the [free Gemini API tier](https://ai.google.dev/gemini-api/docs/pricing), check its current model limits and data-use terms. Its pricing page says free-tier content may be used to improve Google products. Use synthetic trial documents rather than real employee invoices.

**Google Sheets is an export, not the operational database.** The application reads users, sessions, drafts, history, settings, and per-diem dates from SQLite or PostgreSQL. The Sheet receives only confirmed finance and approved content rows; it is not read back into the app and does not contain the uploaded PDF/image bytes. Use a fresh test Sheet for a fresh trial database: claim IDs begin at `F-1`, and the webhook deduplicates by ID.

## GoDaddy DNS access and a branded URL

The app's **admin** role does not grant GoDaddy domain access. The owner of the GoDaddy account should [invite you as a delegate](https://www.godaddy.com/help/invite-a-delegate-to-access-my-godaddy-account-12376) using your own GoDaddy account email. Ask for **Domains Only** with **management** permission for `impactcircles.org`, without transfer or purchase permission. The owner can limit the domain through [delegate folder permissions](https://www.godaddy.com/help/delegate-access-levels-of-permission-12374). Do not share the owner's password.

No DNS change is required for the first Railway trial: share its generated `*.up.railway.app` link. Later, add `tools.impactcircles.org` in Railway's **Networking → Custom Domain** settings and follow the exact DNS records Railway displays. If the domain uses GoDaddy nameservers, enter those records in GoDaddy DNS. Keep the existing root website and email records unchanged. If the domain's nameservers point elsewhere, change DNS at that provider. See [Railway domains](https://docs.railway.com/networking/domains/working-with-domains) and [GoDaddy DNS records](https://www.godaddy.com/help/manage-dns-records-680).

## Recommended first deployment

The repository includes a `render.yaml` blueprint for a **paid Render web service with a persistent disk** mounted at `/data`. `DATABASE_PATH` points to `/data/impact_tools.db`; newly uploaded originals are stored inside that SQLite database. Configure the admin bootstrap values, model API key, Sheets webhook, `PUBLIC_BASE_URL`, and other secrets in the host environment. Use HTTPS and `COOKIE_SECURE=true`. The app should run as one process/worker with SQLite. Back up the database and any legacy uploads, then test sign-in, a claim, its original file, and Sheet export after a redeploy.

The Railway trial needs the separate Neon database because its app filesystem is not the chosen durable store. A [paid service with a persistent disk](https://render.com/docs/disks), or suitable paid database hosting, is a simpler production option. The current Render account asked for a card before creating a Free service; no Render service was created. Railway's trial availability and outbound network access still need verification in the signed-in account.

## What the subscription pays for

| Need | Benefit to staff and admins |
|---|---|
| Always available web service | One browser link; no daily user setup or terminal |
| Persistent disk | Claims, accounts, and uploaded originals survive restarts and deployments |
| HTTPS and domain support | Protected sign-in and a stable, memorable address |
| Server environment settings | API keys and session secrets stay outside the public repository |
| Hosting logs, health checks, and backup access | Easier troubleshooting and recovery |

Hosting is one cost. AI document reading and content generation may have separate provider charges; domain renewal and backup storage may also cost money. Google Sheets access and Apps Script are subject to your Google account and quotas. Check current [Railway pricing](https://railway.com/pricing) or [Render pricing](https://render.com/pricing), the chosen AI provider's pricing, and expected document volume before budgeting. Do not rely on an old fixed monthly estimate.

## Reusing infrastructure for future projects

If Impact Circles later buys a suitable **VPS**, it can run this app and additional projects on the same server as separate services or containers, subject to available CPU, memory, disk, security isolation, backups, and maintenance capacity. That can spread the base server cost across future projects. With Render, the same account can manage future projects, but each service and its disk are billed separately; a Render persistent disk is attached to one service rather than shared between unrelated apps. A VPS gives more consolidation control and more system administration work.

## Before team rollout

Confirm finance policy and the EUR allowance rate, deploy with persistent storage and backups, move the Sheet to an organization-owned account with appropriate sharing, and run an end-to-end hosted test. The [Admin Guide](ADMIN_GUIDE.md) covers Sheets setup, and the [Architecture Reference](TECHNICAL.md) explains the service and storage boundaries.
