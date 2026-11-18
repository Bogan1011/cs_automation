# Centralised Vision One dashboard

A self-hosted dashboard for Eventus Security showing all managed Trend Vision One clients in one place.

- **Live data every 5 minutes** from each client's Vision One tenant through the official public API (v3.0). It covers operational endpoint health only: endpoint counts, Standard Endpoint Protection versus Server & Workload Protection, online and offline status, component update status, and agent and sensor versions. It does **not** pull telemetry, detections or alerts.
- **Overview page** shows every client with its health, plus a comparison table. **Client page** has the full endpoint inventory with search, filters by protection type, and an "only issues" switch.
- **Report button** builds a PDF health report and creates a **draft** in your Outlook mailbox with the PDF attached. Nothing is ever sent automatically. If Outlook isn't configured yet, the button offers the PDF as a download.
- Each client's API key is **encrypted at rest** with a master key that is kept outside the database.

Case management stays in the separate Trend portal and is out of scope here.

## 1. Create a Vision One API key per client

Repeat this in each client's Vision One console (for example `portal.in.xdr.trendmicro.com` for clients in India, and `portal.sg.xdr.trendmicro.com` for Singapore):

1. **Administration → User Roles**: create a role named `Dashboard Read-Only` that has only *view* permission for **Endpoint Inventory / Endpoint Security**. Nothing else is needed.
2. **Administration → API Keys → Add API key**: assign the role, set an expiry date, and copy the key.

Region codes: `in` India, `sg` Singapore, `us` United States, `eu` Europe, `uk` United Kingdom, `jp` Japan, `au` Australia, `mea` Middle East & Africa. The region is the part of the portal URL after `portal.`.

## 2. Install on Ubuntu 24.04

```bash
# Docker
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER && newgrp docker

# Code
git clone https://github.com/Bogan1011/cs_automation.git && cd cs_automation
cp .env.example .env
docker compose build
docker compose run --rm app python -m app.cli gen-key   # paste output into MASTER_KEY in .env
nano .env                                              # set passwords, SESSION_SECRET (long random string)
docker compose up -d
```

Back up `MASTER_KEY`. Without it, the stored API keys can't be decrypted, and you'd have to enter them again.

Add the clients. Each command asks for the API key with hidden input:

```bash
docker compose exec app python -m app.cli add-client --name "Acme Corp" --region in --report-to "client-contact@example.com"
docker compose exec app python -m app.cli add-client --name "ABC - Alpha Beta Corp" --region in
docker compose exec app python -m app.cli add-client --name "Globex Ltd" --region sg
docker compose exec app python -m app.cli test --name "Acme Corp"     # checks the key
docker compose exec app python -m app.cli poll                           # first pull right away
```

### Sync now instead of waiting 5 minutes

```bash
./scripts/force_refresh.sh                  # all clients
./scripts/force_refresh.sh "Acme Corp"   # one client
./scripts/force_refresh.sh --all-details    # also fetch every exact pattern version now
```

The dashboard has the same thing as buttons: **Sync all now** on Home and **Sync now** on each client tab. A manual sync and the 5-minute job never run at the same time; one waits for the other.

Open `http://127.0.0.1:8000` on the server. To reach it from other machines on the LAN, set `BIND_ADDR` in `.env` to the server's LAN IP, for example `192.168.1.67`, then run `docker compose up -d`. For anything beyond a trusted LAN, put Nginx or Caddy with HTTPS in front.

Other commands: `list-clients`, `edit-client --name X --region sg --report-to a@b.com --enabled no`, `set-key --name X` (rotate key), and `dump --name X` (prints one raw endpoint record, which is useful for checking field names).

## Exact pattern versions (optional)

The Vision One API gives Trend's per-endpoint verdict (latest or outdated) but not the version numbers themselves. For exact versions, also connect each product console's own API:

```bash
# Server & Workload Protection: create a read-only API key in the SWP console (Administration > User Management > API Keys)
docker compose exec app python -m app.cli set-swp --name "Acme Corp" --url https://workload.<region>.cloudone.trendmicro.com
docker compose exec app python -m app.cli swp-inspect --name "Acme Corp"     # check the fields it returns

# Standard Endpoint Protection (Apex One as a Service): add an application under Automation API Access Settings
docker compose exec app python -m app.cli set-apex --name "Acme Corp" --url https://<tenant>.manage.trendmicro.com --app-id <Application ID>
docker compose exec app python -m app.cli apex-probe --name "Acme Corp"
```

Once these are connected, the next poll matches computers to endpoints by host name and fills in the Patterns table with exact versions. The latest version of each pattern is the newest one found across all clients.

## 3. Outlook drafts and support case emails (Microsoft 365)

**Without admin rights (recommended):**
1. Open https://portal.azure.com, go to **App registrations → New registration**, name it `Vision One Dashboard`, choose single tenant, and click Register.
2. On the **Authentication** page, set **Allow public client flows** to **Yes**.
3. Under **API permissions → Microsoft Graph → Delegated**, add `Mail.ReadWrite`. Don't add Application permissions.
4. Put `GRAPH_TENANT_ID` and `GRAPH_CLIENT_ID` in `.env`, leave `GRAPH_CLIENT_SECRET` empty, and run `docker compose up -d --build`.
5. Sign in once: `docker compose exec -it app python -m app.cli graph-login`. Open the link, enter the code, and approve.
   The refresh token is stored encrypted in the database. The dashboard acts as you, on your own mailbox only. Run the command again if Microsoft ever asks you to sign in again.

**App-only (needs an admin):** grant application permission Mail.ReadWrite, then set `GRAPH_CLIENT_SECRET` and `GRAPH_MAILBOX` as well.

Report drafts are only created (`POST …/messages`). The dashboard never sends mail.

### Support cases from email

The same app reads Trend support case emails (subjects with `TM-12345678`) every 5 minutes with `GET /users/{mailbox}/messages`.
It only reads them. With the no-admin sign-in it reads your own mailbox. With app-only, it reads `CASES_MAILBOX`, or `GRAPH_MAILBOX` if that isn't set.
Cases are matched to a client by the tag in the subject, like `[ABC]`. You can change the tag at the bottom of the client's Support cases page.
To read new emails right away, run `docker compose exec app python -m app.cli sync-cases`.

## Customer Success console

The home page is the team's internal console. It uses no client API data.

- **Work board** (`/work`): who is working on which client issue, its status (Open, In progress, Waiting on client, Waiting on Trend, Resolved), priority, Trend case number and a history of every update.
- **Case library** (`/library`): finished cases with the issue, root cause and solution highlighted. Paste the closure email and press *Read the email* to fill the fields, then check and save. When a work item is resolved, *Save to case library* copies it across.
- **Scripts** (`/scripts`): read-only health checks in `troubleshooting/<deep-security|xdr|apex-one>/`. Add a `.ps1` (with a `.SYNOPSIS` block) or `.sh` (with comment lines at the top) there and rebuild to show it.
- **Trend articles** (`/articles`): the latest Vision One articles from Trend Business Success, read every `ARTICLE_INTERVAL_MINUTES` (default 180) from `TREND_ARTICLE_URLS`. Each new article page is read once for its summary and solution.
- **Tools** (`/tools`): links to Trend's official tools, for example OneDrive share links.

Give each teammate their own sign-in so updates show who made them: `DASHBOARD_USERS=akshay:password1,colleague:password2` in `.env`, then `docker compose up -d`.

The parked client dashboard is at `/vision-one` and its tab appears only when a client is added.

## Removing a client and its API keys

Client API keys (Vision One, Server & Workload, Apex) are stored only in the Postgres database, encrypted with `MASTER_KEY`. They are never in the repository or in `.env`.

```bash
docker compose exec app python -m app.cli list-clients
docker compose exec -it app python -m app.cli remove-client --name "Client name"   # one client
docker compose exec -it app python -m app.cli remove-client --all                  # every client
docker compose exec app python -m app.cli forget-graph   # Microsoft 365 sign-in and case sync state
```

`remove-client` deletes the client row with its encrypted keys, endpoints, endpoint details, snapshots and support cases. To wipe everything instead, run `docker compose down -v`, which deletes the database volume. Then revoke the keys in each Trend console too, because deleting a local copy does not disable a key.

## Milestones

`milestone/v1-client-dashboard` (branch) holds the multi-client dashboard as it was on 2026-11-18, before client data was removed. Restore it with `git checkout milestone/v1-client-dashboard`.

## Branding

The Docker build downloads the Eventus logo from eventussecurity.com into `app/static/brand/`. To use a different file, put a white-on-transparent logo at `app/static/brand/logo.webp` and rebuild. Brand colours are the CSS variables at the top of `app/static/style.css`.

## Moving to cloud hosting later

The same `docker compose up -d` works on any Linux VM. Copy `.env`, then either restore a Postgres dump (`docker compose exec db pg_dump -U v1dash v1dash > backup.sql`) or start fresh and re-add the clients.

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
DASHBOARD_PASSWORD=pw MASTER_KEY=$(python -m app.cli gen-key) uvicorn app.main:app --reload   # uses SQLite in ./data
```
