# Centralised Vision One dashboard

A self-hosted dashboard for Eventus Security showing all managed Trend Vision One clients in one place.

- **Live data every 5 minutes** from each client's Vision One tenant through the official public API (v3.0), including endpoint inventory, Standard Endpoint Protection, Server & Workload Protection, and Workbench alerts from the last 7 days.
- **Overview page** shows every client with its health, plus a comparison table. **Client page** has the full endpoint inventory with search, filters by protection type, and an "only issues" switch.
- **Report button** builds a PDF health report and creates a **draft** in your Outlook mailbox with the PDF attached. Nothing is ever sent automatically. If Outlook isn't configured yet, the button offers the PDF as a download.
- Each client's API key is **encrypted at rest** with a master key that is kept outside the database.

Case management stays in the separate Trend portal and is out of scope here.

## 1. Create a Vision One API key per client

Repeat this in each client's Vision One console (for example `portal.in.xdr.trendmicro.com` for Bandhan Life and IFL, and `portal.sg.xdr.trendmicro.com` for Clix Capital):

1. **Administration → User Roles**: create a role named `Dashboard Read-Only` with *view* permission for **Endpoint Inventory / Endpoint Security** and **Workbench**. If you skip Workbench, the alert counters stay empty but everything else still works.
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
docker compose exec app python -m app.cli add-client --name "Bandhan Life" --region in --report-to "client-contact@example.com"
docker compose exec app python -m app.cli add-client --name "IFL - India First Life" --region in
docker compose exec app python -m app.cli add-client --name "Clix Capital" --region sg
docker compose exec app python -m app.cli test --name "Bandhan Life"     # checks the key
docker compose exec app python -m app.cli poll                           # first pull right away
```

Open `http://127.0.0.1:8000` on the server. For access from other machines on the LAN, either change the port mapping in `docker-compose.yml` to `"8000:8000"` or, preferably, put Nginx or Caddy with HTTPS in front of it.

Other commands: `list-clients`, `edit-client --name X --region sg --report-to a@b.com --enabled no`, `set-key --name X` (rotate key), and `dump --name X` (prints one raw endpoint record, which is useful for checking field names).

## 3. Outlook drafts (Microsoft 365)

1. In **Microsoft Entra admin centre → App registrations → New registration**, create an app named `Vision One Dashboard` with single tenant.
2. Under **API permissions**, add **Microsoft Graph → Application → Mail.ReadWrite**, then **Grant admin consent**.
3. Under **Certificates & secrets**, create a new client secret.
4. Put `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, and `GRAPH_MAILBOX` (your mailbox) in `.env`, then run `docker compose up -d`.
5. **Recommended:** limit the app to your mailbox only by running this in Exchange Online PowerShell:
   ```powershell
   New-ApplicationAccessPolicy -AppId <GRAPH_CLIENT_ID> -PolicyScopeGroupId you@eventussecurity.com -AccessRight RestrictAccess -Description "V1 dashboard drafts"
   ```

The dashboard only calls `POST /users/{mailbox}/messages`, which creates a draft. It never sends mail.

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
