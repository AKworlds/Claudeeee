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
- `GET  /api/finance/income-entries?limit=100` → `{entries:[{id, date, source, type, amount, note, created_at}]}` newest first (app-entered income, table `income_entries`, counted with workbook income, survives imports)
- `POST /api/finance/income-entries` body `{date?(default today), source(<=80), type(paycheck|dividend|interest|business|other), amount(>0,<=10M), note?}` → 201 entry; `PUT /api/finance/income-entries/{id}` partial; `DELETE` (404 if missing)
- `GET  /api/finance/income-sources` → `{sources:[..]}` distinct sources from both income tables, most recent first, max 20
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

## Side-business tax tracker (v2)

Purpose: log each side-business payout (usually from the phone) and get "set aside $X" for taxes,
plus quarterly estimated-payment amounts and due dates. Estimates only; not tax advice.

Data: `business_payouts` (app-entered, survives workbook imports) and `tax_payments` (see db.py).
Business profit for tax = payouts (amount - expenses) + workbook `business` rows (revenue - expense)
for the tax year. The Side Business chart (`/api/finance/business`) also includes payouts.

Settings (settings key `tax_settings`, merged over defaults; unknown/legacy keys such as w2_income and
federal_bracket are dropped on read and save):
`{filing_status: "single" (single|married_joint|head_of_household), state_rate: 0.0 (fraction;
Texas = 0), buffer_pct: 0 (extra % added to each set-aside), use_qbi: true}`. Other income has tax
already withheld and is never tracked; only business income is modelled. SE/QBI figures live in
config `tax:` (yearly update), including `tax.brackets` (2026 progressive brackets per filing status,
`[[upper_bound|null, rate], ...]`).

Math for year Y and cumulative business profit P (P >= 0; losses => 0 tax):
- SE tax IS the self-employed FICA (Social Security + Medicare, both halves): never add FICA on top.
  social_security = se_ss_rate * P * se_net_factor; medicare = se_medicare_rate * P * se_net_factor; SE = their sum.
- Per payout `breakdown {social_security, medicare, federal, state, federal_rate}` (federal_rate = bracket of the payout's top dollar) = incremental component tax x buffer, rounded to cents
  (may sum to a few cents less than set_aside, which is the whole-dollar ceil of the total).
- SE = (se_ss_rate + se_medicare_rate) * P * se_net_factor. No Social Security wage-base cap and no additional
  Medicare tax (noted in the UI). If net SE earnings < 400 at year end the IRS charges no SE tax; keep accruing, add a note.
- QBI = qbi_rate * max(0, P - SE/2) if use_qbi.
- Federal = bracket_tax(max(0, P - SE/2 - QBI), brackets[filing_status]), progressive, on business income alone; standard deduction not applied. State = state_rate * P. Total(P) = SE + Federal + State.
- Per payout (chronological within the year): set_aside_i = ceil((Total(P_i) - Total(P_{i-1})) * (1 + buffer_pct/100)).
- marginal_rate = exact (unrounded) buffered tax on the next $100 / 100. effective_rate = Total(P)/P.
- Quarters from config `tax.quarters`: amount = sum of set_asides for payouts dated in the period;
  paid = sum of tax_payments whose `period` equals the quarter label (e.g. "2026-Q3"); status past_due|due_soon (<=30 days)|upcoming|paid.
- Notes: estimate only / not tax advice; < $1,000 total year tax => estimated payments generally not required;
  federal uses 2026 brackets on side-business profit only (standard deduction not applied); state not included when state_rate = 0.

API (prefix /api/tax), all JSON:
- `GET/PUT /settings` → settings object (PUT validates: filing_status in the enum, state 0..0.15, buffer 0..50; else 422).
- `GET /payouts?year=` → `{payouts:[{id,date,amount,expenses,profit,set_aside,breakdown,saved,note}]}` newest first.
- `POST /payouts` `{date?=today, amount>0, expenses>=0=0, note?, saved?}` → the payout incl. computed `set_aside`, `breakdown`
  and `{rate}` (= set_aside/profit). `PUT /payouts/{id}` partial (e.g. `{saved: 412}`), `DELETE /payouts/{id}`.
- `GET /summary?year=` → `{year, profit_ytd, tax:{se, social_security, medicare, federal, state, total},
  rates:{social_security, medicare, se, federal_marginal, federal_effective, state_rate} (effective rates on profit), effective_rate,
  marginal_rate, set_aside_total, saved_total, shortfall, quarters:[{label, start, end, due, amount, paid, status}],
  projection:{annual_profit, annual_tax} (YTD profit annualized by months elapsed), notes:[str]}`.
- `GET/POST /payments`, `DELETE /payments/{id}`; POST `{date?, amount>0, period, note?}`.

## Phone app + login (v2)

- PWA: `frontend/manifest.webmanifest`, `frontend/sw.js` (app-shell cache, network-first, never caches /api),
  icons in `frontend/icons/` (192, 512, maskable 512, apple-touch 180). Shortcut "Log payout" → `/#tax`.
- Login replaces Caddy basic auth (basic auth is unreliable in iOS home-screen apps): `app/auth.py`.
  Env: `DASH_PASSWORD_HASH` (scrypt, made by `python -m app.auth hash`), `DASH_SECRET_KEY` (cookie signing),
  `DASH_AUTH` (default 1; tests/dev set 0), `DASH_COOKIE_SECURE` (default 1). HMAC-signed session cookie,
  HttpOnly, SameSite=Lax, 30-day expiry. Non-GET /api requests must send header `X-Dash: 1` (CSRF guard).
  Public paths: /login.html, /api/auth/*, /api/health, /manifest.webmanifest, /icons/*, /sw.js, styles.css, login.js.
  Failed logins rate-limited per client IP (5 per 15 min → 429).
