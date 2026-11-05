# Personal Dashboard — Architecture & Contract

Single-user dashboard (finance + career) self-hosted on a Hostinger VPS.

## Stack
- **Backend**: Python 3.12, FastAPI, SQLite (stdlib `sqlite3`, no ORM), openpyxl for Excel ingest,
  httpx for GitHub API, stdlib `imaplib`/`email` for job-email sync. Package root `backend/app`.
- **Frontend**: static SPA in `frontend/` — `index.html`, `app.js`, `styles.css`.
  Vanilla JS (ES modules, no build step), Chart.js 4.4.1 vendored in frontend/vendor (no third-party script hosts). Served by FastAPI at `/`.
- **Deploy**: Docker Compose (`app` + `caddy`). Caddy terminates HTTPS (auto Let's Encrypt) and
  enforces basic auth. A tiny in-container scheduler (background asyncio task) re-imports the
  workbook when its mtime changes and runs career syncs every N hours.

## Layout
```
<repo root>/
  backend/app/main.py          # app factory, router includes, static mount, scheduler start  (OWNER: Fable)
  backend/app/db.py            # get_conn(), init_db() schema                                 (OWNER: Fable)
  backend/app/config.py        # Settings from env + config/dashboard.yaml                    (OWNER: Fable)
  backend/app/services/excel_import.py   # workbook -> DB                 (Agent A)
  backend/app/services/finance.py        # metrics, 50/30/20, milestones, forecast (Agent A)
  backend/app/routers/finance.py         # /api/finance/*                 (Agent A)
  backend/app/services/github_sync.py    # repos -> projects              (Agent B)
  backend/app/services/email_sync.py     # IMAP -> applications           (Agent B)
  backend/app/routers/career.py          # /api/career/*                  (Agent B)
  backend/app/scheduler.py               # periodic jobs                  (Agent B)
  backend/tests/                         # pytest, one file per module    (A & B)
  backend/scripts/make_sample_workbook.py  # generates data/sample_budget.xlsx (Agent A)
  frontend/                              # (Agent C)
  deploy/ Dockerfile, docker-compose.yml, Caddyfile, .env.example, backup.sh  (Agent C)
  config/dashboard.example.yaml          # (Fable)
  README.md                              # setup guide (Agent C, reviewed by Fable)
```

## Data model (SQLite, see db.py — the source of truth)
- `income(id, date, source, type, amount, note)`; type ∈ paycheck|dividend|interest|business|other
- `transactions(id, date, description, category, bucket, amount)`; amount positive = spend;
  bucket ∈ needs|wants|savings|business_expense|ignore (derived from category via config map)
- `balances(id, date, account, kind, balance)`; kind ∈ asset|liability (net-worth snapshots)
- `business(id, date, description, revenue, expense)` (side business, optional sheet)
- `imports(id, imported_at, file, sha256, rows_income, rows_transactions, rows_balances, rows_business, status, message)`
- `milestones(id, name, kind, target, metric, created_at, achieved_at)`; metric ∈ net_worth|emergency_months|invested|custom;
  kind ∈ emergency_fund|net_worth|investing|fi|custom
- `applications(id, company, role, status, applied_on, source, url, notes, last_update, external_id UNIQUE)`;
  status ∈ wishlist|applied|interview|offer|rejected|withdrawn
- `certifications(id, name, issuer, status, earned_on, expires_on, target_date, url)`; status ∈ planned|in_progress|earned|expired
- `projects(id, name, description, url, stars, language, last_push, status, featured, source, external_id UNIQUE)`
  source ∈ manual|github; status ∈ idea|building|done
- `settings(key PRIMARY KEY, value)` JSON-encoded user assumptions overrides

Import is **replace-by-source**: each workbook import deletes all rows in income/transactions/balances/business
and reinserts inside one transaction (workbook is the source of truth for finance). Skips if sha256 unchanged.

## Workbook mapping (config/dashboard.yaml -> `workbook:`)
Sheet names, header names, and category->bucket map are configurable so the real workbook fits without code
changes. Headers are matched case-insensitively, trimmed. Missing optional sheets are skipped with a warning.

## Finance math (services/finance.py)
- Monthly series: income by type, spend by bucket, savings = income - (needs+wants), savings rate.
- 50/30/20 for a month (default latest complete month, `?month=YYYY-MM`): actual % of net income per bucket vs target.
- Net worth series from `balances` (latest balance per account per month, carried forward).
- Emergency fund months = liquid assets (accounts matching config `liquid_accounts` patterns) / avg monthly needs+wants (last 6 mo).
- FI number = annual expenses (avg last 12 mo × 12) / safe_withdrawal_rate (default 0.04). Progress = invested / FI number.
- Forecast: monthly compounding. NW_{t+1} = NW_t*(1+r/12) + monthly_contribution. r from settings
  (scenarios conservative 4% / base 7% / optimistic 10%), contribution = avg monthly savings last 6 months
  (overridable). Return points at 12/60/120 months plus full monthly series for charting.
- Milestones: auto-seeded defaults if table empty (emergency fund 6 mo, NW 50k/100k/250k, FI number).
  Progress computed live; `achieved_at` stamped first time progress ≥ 1.

## API contract (all JSON, prefix /api)
Finance (Agent A):
- `GET  /api/finance/summary` → `{net_worth, net_worth_change_30d, monthly_income, monthly_spend, savings_rate,
   emergency_months, fi_number, fi_progress, invested, business_profit_ytd, last_import}`
- `GET  /api/finance/income?months=12` → `{months:[YYYY-MM], series:{paycheck:[..], dividend:[..], ...}, total:[..]}`
- `GET  /api/finance/net-worth?months=36` → `{points:[{month, assets, liabilities, net_worth}]}`
- `GET  /api/finance/cashflow?months=12` → `{points:[{month, income, needs, wants, savings, savings_rate}]}`
- `GET  /api/finance/budget-rule?month=YYYY-MM` → `{month, net_income, buckets:[{bucket, actual, actual_pct, target_pct, delta_pct}]}`
- `GET  /api/finance/business?months=12` → `{points:[{month, revenue, expense, profit}], ytd_profit}`
- `GET  /api/finance/milestones` → `{milestones:[{id, name, kind, metric, target, current, progress, achieved_at}]}`
- `POST /api/finance/milestones` body `{name, kind, metric, target}`; `PUT/DELETE /api/finance/milestones/{id}`
- `GET  /api/finance/forecast` → `{start_net_worth, monthly_contribution, scenarios:[{name, rate,
   at:{"1y":x,"5y":x,"10y":x}, series:[{month_index, value}]}]}`
- `GET/PUT /api/finance/assumptions` → `{safe_withdrawal_rate, return_rates:{conservative,base,optimistic},
   monthly_contribution_override|null, emergency_target_months, budget_targets:{needs:50,wants:30,savings:20}}`
- `POST /api/finance/import` multipart `file` (.xlsx) → import result; `GET /api/finance/imports` → history
Career (Agent B):
- `GET /api/career/summary` → `{applications_total, active, interviews, offers, response_rate,
   certs_earned, certs_in_progress, projects_total, last_sync:{github, email}}`
- CRUD: `/api/career/applications`, `/api/career/certifications`, `/api/career/projects` (GET list, POST, PUT /{id}, DELETE /{id})
- `GET /api/career/applications/funnel` → `{stages:[{status, count}], weekly:[{week, applied}]}`
- `POST /api/career/sync/github`, `POST /api/career/sync/email[?full=true]` (full = `initial_lookback_days`, default 10y; also used for the first sync) → `{added, updated, message}`
Common: `GET /api/health` → `{ok:true}` (Fable, in main.py)

Errors: FastAPI HTTPException with `{detail}`; 422 for validation.

## Auth & security
- Caddy basic auth (bcrypt hash in .env) in front of everything; app binds only to the compose network.
- Secrets (IMAP app password, GitHub token) via env only, never in yaml or git. `data/` is gitignored.
- Upload limited to .xlsx, 20 MB; openpyxl `read_only=True, data_only=True`.

## Job-data automation
1. **Email (IMAP)**: Gmail app password; scans a label/folder (default INBOX, last N days) for
   confirmation/interview/rejection/offer emails using keyword rules in config; upserts applications keyed
   by a hash of (normalized company, role) — status only moves forward unless rejection/offer.
2. **GitHub**: public repos of configured user (default `AKworlds`) → projects (stars, language, last push).
Both run on the scheduler (default every 6h) and on demand from the UI.

## Template, applications sheet, Drive sync (addendum)
- `scripts/make_template_workbook.py` builds a blank workbook (tables, dropdowns, live Summary); served by `GET /api/finance/template`.
- Optional `Job Applications` sheet (`workbook.sheets.applications`): upserted into `applications` (`external_id` `sheet:<sha1>`, merges into the matching `email:` row); never deletes. Count appears in the import message only.
- `services/drive_sync.py`: service account (`GOOGLE_SERVICE_ACCOUNT_FILE`) + `workbook.drive_file_id`; downloads/exports the file when `modifiedTime` changes, imports, keeps a local copy. Scheduler runs it every `workbook_poll_seconds`. Endpoints: `GET /api/finance/drive`, `POST /api/finance/drive/sync` (400 unconfigured, 502 on SyncError). Settings keys `drive_last_modified`, `last_sync_drive`.
