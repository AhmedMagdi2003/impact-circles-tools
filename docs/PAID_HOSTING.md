# Why Impact Circles Needs Paid Hosting for a Public Link

## What the team needs

Staff should open one HTTPS link in a browser and use **Upload → Check details → Confirm** without installing Python or opening a terminal. To provide that link, the application must run continuously on a server connected to the internet. The server also needs durable storage for its SQLite database and original uploaded PDFs/images, plus private environment settings for sessions, the AI provider, and the Google Sheets webhook.

A local computer can run the app for development, but `127.0.0.1` opens only on that computer. Closing its PowerShell window or turning off the computer stops the service. A public domain alone does not run the application or preserve its files.

## Recommended first deployment

The repository includes a `render.yaml` blueprint for a **paid Render web service with a persistent disk** mounted at `/data`. `DATABASE_PATH` points to `/data/impact_tools.db`; uploaded originals live in `/data/uploads`. Configure the admin bootstrap values, model API key, Sheets webhook, `PUBLIC_BASE_URL`, and other secrets in the host environment. Use HTTPS and `COOKIE_SECURE=true`. The app should run as one process/worker with SQLite. Set up backups of both the database and uploads, then test sign-in, a claim, its original file, and Sheet export after a redeploy.

[Render's free web service](https://render.com/docs/free) can spin down while idle and does not provide persistent local storage suitable for this SQLite and upload design. A [paid service with a persistent disk](https://render.com/docs/disks) is therefore the practical option for a dependable team link. Render can provide a public `onrender.com` address; a [custom domain with HTTPS](https://render.com/docs/custom-domains) can later provide an Impact Circles branded URL.

## What the subscription pays for

| Need | Benefit to staff and admins |
|---|---|
| Always available web service | One browser link; no daily user setup or terminal |
| Persistent disk | Claims, accounts, and uploaded originals survive restarts and deployments |
| HTTPS and domain support | Protected sign-in and a stable, memorable address |
| Server environment settings | API keys and session secrets stay outside the public repository |
| Hosting logs, health checks, and backup access | Easier troubleshooting and recovery |

Hosting is one cost. AI document reading and content generation may have separate provider charges; domain renewal and backup storage may also cost money. Google Sheets access and Apps Script are subject to your Google account and quotas. Check current [Render pricing](https://render.com/pricing), the chosen AI provider's pricing, and expected document volume before budgeting. Do not rely on an old fixed monthly estimate.

## Reusing infrastructure for future projects

If Impact Circles later buys a suitable **VPS**, it can run this app and additional projects on the same server as separate services or containers, subject to available CPU, memory, disk, security isolation, backups, and maintenance capacity. That can spread the base server cost across future projects. With Render, the same account can manage future projects, but each service and its disk are billed separately; a Render persistent disk is attached to one service rather than shared between unrelated apps. A VPS gives more consolidation control and more system administration work.

## Before team rollout

Confirm finance policy and the EUR allowance rate, deploy with persistent storage and backups, move the Sheet to an organization-owned account with appropriate sharing, and run an end-to-end hosted test. The [Admin Guide](ADMIN_GUIDE.md) covers Sheets setup, and the [Architecture Reference](TECHNICAL.md) explains the service and storage boundaries.
