# Why Production Needs Paid Hosting, and How to Run a Free Trial

**A free browser trial is possible.** The paid subscription is needed for this project's current design when the team needs reliable, persistent storage on Render. A free trial can test the hosted workflow, but the storage choice determines whether accounts, claims, and original files survive a restart.

## What the team needs

Staff should open one HTTPS link in a browser and use **Upload → Check details → Confirm** without installing Python or opening a terminal. To provide that link, the application must run continuously on a server connected to the internet. The server also needs durable storage for its SQLite database and original uploaded PDFs/images, plus private environment settings for sessions, the AI provider, and the Google Sheets webhook.

A local computer can run the app for development, but `127.0.0.1` opens only on that computer. Closing its PowerShell window or turning off the computer stops the service. A public domain alone does not run the application or preserve its files.

## Free trial options

| Option | What it tests | Storage limit |
|---|---|---|
| [Render Free web service](https://render.com/docs/free) | A real cloud deployment, public `onrender.com` link, login, AI reading, review, confirmation, and Sheets export | No persistent disk. It spins down after inactivity and loses its SQLite database and uploaded originals on spin-down, restart, or redeploy. Use only disposable accounts and synthetic documents. |
| Existing company VPS or a [cloud VM trial with persistent disk](https://cloud.google.com/products/compute) | The full server and storage lifecycle, including restart, backups, and a custom domain | Requires a suitable server/account and more setup. Google Cloud advertises trial credits for eligible new accounts; usage must stay within the account's credit and limits. |

To try **Render Free**, create a Web Service from the [public repository](https://github.com/AhmedMagdi2003/impact-circles-tools) using the Dockerfile and choose the **Free** plan. Create it manually rather than applying the repository's paid `render.yaml`, because that blueprint specifies a Starter service and persistent disk. Set `DATABASE_PATH=/tmp/impact_tools.db`, `COOKIE_SECURE=true`, a stable `SESSION_SECRET` of at least 24 characters, first-run admin email/password, provider/model/API key, and the Sheets webhook settings as private environment variables. Set `PUBLIC_BASE_URL` to the assigned `https://...onrender.com` address, then redeploy if needed. Use a **separate test Google Sheet** and synthetic invoices only. Confirm a sample claim, verify its Sheet row, and test the app before it idles. A restart or idle spin-down starts a new empty database, so this does **not** validate persistent history or original-file retrieval after restart.

If using the [free Gemini API tier](https://ai.google.dev/gemini-api/docs/pricing), check its current model limits and data-use terms. Its pricing page says free-tier content may be used to improve Google products. Use synthetic trial documents rather than real employee invoices.

**Google Sheets cannot replace SQLite in the current code.** The application reads users, sessions, drafts, history, settings, and per-diem dates from SQLite. The Sheet receives only confirmed finance and approved content rows; it is not read back into the app, and it does not contain the uploaded PDF/image bytes. On a free Render reset, claim IDs can start again at `F-1`; the Sheets webhook deduplicates by ID, so reusing the same Sheet after a reset could make a new claim appear to sync while its new row is skipped. Keep each trial's data disposable and use a fresh test Sheet when resetting the service. A persistent free trial requires an existing server or a VM trial with durable storage, not a Sheet-only setting.

## GoDaddy DNS access and a branded URL

The app's **admin** role does not grant GoDaddy domain access. The owner of the GoDaddy account should [invite you as a delegate](https://www.godaddy.com/help/invite-a-delegate-to-access-my-godaddy-account-12376) using your own GoDaddy account email. Ask for **Domains Only** with **management** permission for `impactcircles.org`, without transfer or purchase permission. The owner can limit the domain through [delegate folder permissions](https://www.godaddy.com/help/delegate-access-levels-of-permission-12374). Do not share the owner's password.

No DNS change is required for the first Render trial: share its `onrender.com` link. Later, after the service is running, add `tools.impactcircles.org` in Render's **Custom Domains** settings. If the domain uses GoDaddy nameservers, create a GoDaddy **CNAME** record with host `tools` pointing to the service's exact `...onrender.com` hostname, then verify the domain in Render. Keep the existing root website and email records unchanged. If the domain's nameservers point elsewhere, the DNS record must be changed at that actual DNS provider; a GoDaddy login alone will not edit it. See [Render custom domains](https://render.com/docs/custom-domains), [Render subdomain DNS](https://render.com/docs/configure-other-dns), and [GoDaddy DNS records](https://www.godaddy.com/help/manage-dns-records-680).

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
