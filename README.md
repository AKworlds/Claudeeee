# Personal Dashboard

One place for money and career progress: net worth, income, 50/30/20 budget, milestones, forecast, job applications, certifications and projects. Single user, desktop and phone.

- Backend: FastAPI + SQLite (`backend/`). Frontend: static SPA served by FastAPI (`frontend/`, no build step).
- Deploy: Docker Compose, `app` + `caddy` (automatic HTTPS and basic auth). See `PLAN.md` for the architecture and API.

## Deploy on a Hostinger VPS (Ubuntu)

1. **DNS**: create an `A` record for e.g. `dash.yourdomain.com` pointing to the VPS IP.
2. **Firewall**:
   ```bash
   sudo ufw allow 22,80,443/tcp && sudo ufw enable
   ```
   If you use Hostinger's hPanel firewall for the VPS, allow TCP 80 and 443 there too.
3. **Install Docker** (includes the compose plugin):
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER   # log out and back in
   ```
4. **Clone**:
   ```bash
   git clone https://github.com/AKworlds/Claudeeee.git ~/dashboard && cd ~/dashboard
   mkdir -p data config
   cp .env.example .env
   cp config/dashboard.example.yaml config/dashboard.yaml
   ```
   The container runs as uid 1000; if your VPS user has a different uid run `sudo chown -R 1000:1000 data`.
5. **Password hash**:
   ```bash
   docker run --rm caddy:2.8 caddy hash-password
   ```
   Put the output in `.env` as `DASH_PASSWORD_HASH`, **doubling every `$` to `$$`**. Also set `DASH_DOMAIN` and `DASH_USER`.
6. **Start**:
   ```bash
   docker compose -f deploy/docker-compose.yml up -d --build
   docker compose -f deploy/docker-compose.yml logs -f
   ```
   Open `https://dash.yourdomain.com` and sign in. Caddy fetches the certificate on first request (needs ports 80/443 open and DNS propagated).

Data layout: host `./data` is mounted at `/srv/dashboard/data` inside the container (SQLite DB, workbook, backups), so the default `workbook.path: data/budget.xlsx` works unchanged. Host `./config` is mounted read-only at `/config`.

## Your budget workbook

- **Upload**: Settings tab, "Upload workbook" (.xlsx, max 20 MB), or
- **scp**: `scp budget.xlsx user@vps:~/dashboard/data/budget.xlsx`. The scheduler notices the changed file and re-imports within 5 minutes (`scheduler.workbook_poll_seconds`). Unchanged files (same SHA-256) are skipped.

Each import replaces all finance rows with the workbook contents; the workbook is the source of truth.

### Default workbook layout

Out of the box the importer expects these sheets (header row may sit anywhere in the first 10 rows; extra columns are ignored):

| Sheet | Columns | Notes |
|---|---|---|
| `Income` | Date, Source, Type, Amount, Note | Type/Source text like "Tesla" or "Paycheck" -> paycheck, "Dividend" -> dividend |
| `Transactions` | Date, Description, Category, Amount | Spending; Category decides needs/wants/savings |
| `Net Worth` | Date, Account, Type, Balance | One row per account per snapshot; Type "Asset" or "Liability"/"Loan"/"Credit" |
| `Side Business` | Date, Description, Revenue, Expense | Optional |

Dates can be real Excel dates or `YYYY-MM-DD` / `MM/DD/YYYY`; amounts can be numbers or text like `$1,234.56`. `backend/scripts/make_sample_workbook.py` builds a complete example. Monthly charts stop at the last complete month, so a half-finished current month doesn't look like an income drop.

### Mapping your sheets, columns and categories

Edit `config/dashboard.yaml` (then `docker compose -f deploy/docker-compose.yml restart app`):

- `workbook.sheets.<income|transactions|balances|business>`: `name` is the sheet name; `columns` maps our fields to your header text (case-insensitive). The business sheet is optional.
- `workbook.income_types`: text found in your Type column -> `paycheck | dividend | interest | business | other`.
- `workbook.category_buckets`: each spending category -> `needs | wants | savings | business_expense | ignore`. Unmapped categories fall into `default_bucket`.
- `finance.liquid_accounts` / `invested_accounts`: account-name patterns used for emergency-fund months and the FI/invested figure.

Savings targets, return rates, safe withdrawal rate and monthly contribution are edited in the Settings tab.

## Workbook template

Start from a blank workbook that already has the right sheets, headers, dropdowns and a live Summary sheet:

- Settings tab > "Download blank template", or
- `cd backend && python scripts/make_template_workbook.py ../data/budget_template.xlsx` (add `--applications file.json` to prefill the Job Applications sheet).

Sheets: Start Here (instructions), Income, Transactions, Net Worth, Side Business, Job Applications, a hidden Lists sheet (dropdown values and the category -> bucket table) and Summary (formulas only; the importer ignores it). Keep the header names, add rows below the existing ones, and extra columns (like the "Bucket (auto)" helper) are ignored by the importer.

The optional `Job Applications` sheet (`Company, Role, Status, Applied On, Last Update, Source, URL, Notes`, configurable under `workbook.sheets.applications`) is upserted into the Career tab on every import: rows are matched by company + role, never deleted, and merge into an existing email-detected application instead of duplicating it. Invalid statuses become `applied`.

## Auto-sync from Google Drive

The dashboard can pull the workbook from Drive with a read-only service account (no personal Google login stored).

1. Open the Google Cloud console and create a project.
2. Enable the **Google Drive API** for it (APIs & Services > Library).
3. IAM & Admin > Service Accounts > create a service account.
4. Open it > Keys > Add key > Create new key > JSON. Download the file.
5. On the VPS save it as `~/dashboard/secrets/google-service-account.json` (`mkdir -p secrets` first), then `chmod 600` it and make it owned by uid 1000 (`sudo chown 1000:1000 secrets/google-service-account.json`).
6. Open the workbook in Google Drive and Share it with the service account's email (shown in the JSON as `client_email` and in Settings > Google Drive workbook), role Viewer.
7. Copy the file id from the URL (the part between `/d/` and `/edit`) into `config/dashboard.yaml`:
   ```yaml
   workbook:
     drive_file_id: "<file id>"
   ```
8. Make sure `.env` has `GOOGLE_SERVICE_ACCOUNT_FILE=/secrets/google-service-account.json` (it is in `.env.example`), then `docker compose -f deploy/docker-compose.yml up -d`.

Works for an uploaded `.xlsx` or a native Google Sheet (exported as .xlsx). Editing an `.xlsx` in desktop Excel works if the file lives in a Google Drive for desktop synced folder. Changes appear within `scheduler.workbook_poll_seconds` (5 minutes) or immediately with "Sync now" in Settings. A local copy is kept at `workbook.path`.

## Job tracking

**GitHub** (optional): projects sync from public repos of `career.github_user`. Set `GITHUB_TOKEN` in `.env` only to avoid rate limits.

**Gmail / IMAP**:
1. Enable 2-Step Verification, then create an app password at Google Account > Security > App passwords.
2. In `.env`: `IMAP_HOST=imap.gmail.com`, `IMAP_USER=you@gmail.com`, `IMAP_PASSWORD=<app password>`.
3. Recommended: create a Gmail filter (for example `subject:(application OR interview OR "thank you for applying") OR from:(greenhouse.io OR lever.co OR workday.com)`) that applies a label such as `Jobs`, then set `career.email.folder: Jobs` in `config/dashboard.yaml`. This keeps the scan small and avoids false matches.
4. Recreate the app to load env changes: `docker compose -f deploy/docker-compose.yml up -d`.

The first email sync scans about 10 years of history (`career.email.initial_lookback_days: 3650`); later syncs only look back `lookback_days` (30). The Career tab's "Full email rescan" button repeats the full scan.

Syncs run every `career_sync_hours` (default 6) and on demand from the Career tab ("Sync GitHub", "Sync email").

## Updating

```bash
cd ~/dashboard && git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

## Backups

`deploy/backup.sh` writes a consistent SQLite copy to `data/backups/dashboard-<timestamp>.db` and keeps the newest 14. Cron (daily 03:15):

```bash
crontab -e
15 3 * * * /home/youruser/dashboard/deploy/backup.sh >> /home/youruser/dashboard-backup.log 2>&1
```

Also copy `data/backups` off the server occasionally.

Restore (the DB runs in WAL mode, so remove the side files or stale changes get replayed onto the backup):

```bash
docker compose -f deploy/docker-compose.yml stop app
rm -f data/dashboard.db-wal data/dashboard.db-shm
cp data/backups/dashboard-<timestamp>.db data/dashboard.db
docker compose -f deploy/docker-compose.yml start app
```

## Local development

```bash
cd Claudeeee   # repo root
python3 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
mkdir -p data
python backend/scripts/make_sample_workbook.py     # creates data/sample_budget.xlsx
cp data/sample_budget.xlsx data/budget.xlsx
DASH_SCHEDULER=0 uvicorn app.main:app --reload --app-dir backend
# http://127.0.0.1:8000   (API docs: /api/docs)
cd backend && pytest
```

Without Caddy there is no authentication: only run it locally.
