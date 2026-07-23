# How to Run — Grants Monitoring System

A Flask app that scans Turkish institutional websites for new grant/funding
announcements, stores them in SQLite, and shows them in a dashboard with an
accept/reject triage workflow.

---

## 1. Prerequisites

- **Python 3.11+** (developed on 3.13) — check with:
  ```powershell
  python --version
  ```
- The site list comes from an Excel file (default:
  `C:\Users\anisn\OneDrive\Desktop\Daily Check.xlsx`). This is only needed the
  **first time** to seed the database; after that the sites live in
  `grants_monitor.db`. If the file is missing, seeding is skipped with a warning
  and the app still runs.

---

## 2. One-time setup

Open **PowerShell** in the project folder:

```powershell
cd "C:\Users\anisn\OneDrive\Desktop\Grants monitoring"
```

(Recommended) create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

---

## 3. Run the app

```powershell
python app.py
```

You should see:

```
============================================================
  GRANTS MONITORING SYSTEM
  Dashboard: http://127.0.0.1:5000
  Scan interval: every 3 hours
  Debug mode: False
============================================================
```



Open the dashboard in your browser:

> **http://127.0.0.1:5000**

Stop the app with **Ctrl + C**.

### What happens on start
- The database is initialized / migrated automatically.
- Sites are (re)seeded from the Excel file if it's present.
- A background scheduler runs a scan **every 3 hours** (it does **not** scan
  immediately on startup).

### Triggering a scan manually
Click **"Scan Now"** in the dashboard, or call the API:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:5000/api/scan
```

A scan visits every active site, finds grant links by keyword, and then
auto-scrapes detail pages (deadline / amount / eligibility / contact) for the
newly found grants.

---

## 3a. Set the dashboard password (required)

The dashboard requires a login. Set the password once per host:

```powershell
python set_password.py
```

Only a hash is stored, in `settings.json` (git-ignored). Restart the app
afterwards so it picks the change up.

Until a password is set the app **refuses all logins** rather than running open —
so if the login page says *"No password is set"*, run the command above.

Two settings matter once the app is reachable from the internet:

| settings.json key | Set it when |
|---|---|
| `session_cookie_secure` | `true` once everyone uses the `https://` address. Leave `false` while anyone still uses the plain-HTTP LAN address, or their browser will refuse to send the session cookie and login will appear to fail. |
| `auth_session_hours` | How long a login lasts (default 12). |

`settings.json` now holds the session signing key and the password hash. Back it
up; losing it logs everyone out and needs the password set again.

---

## 4. Configuration (optional)

Settings can be overridden in two ways. **Environment variables** take effect
for that run; **`settings.json`** (in the project folder) persists.

### Environment variables (PowerShell, current session)

```powershell
$env:GRANTS_DEBUG = "true"                                 # enable Flask debug
$env:GRANTS_PORT = "8080"                                  # change port
$env:GRANTS_HOST = "0.0.0.0"                               # listen on all interfaces
$env:GRANTS_EXCEL_PATH = "D:\path\to\Daily Check.xlsx"     # custom seed file
python app.py
```

### settings.json (persistent)

Create `settings.json` next to `app.py`:

```json
{
  "scan_interval_hours": 3,
  "auto_scrape_details": true,
  "auto_scrape_details_limit": 50,
  "flask_debug": false,
  "excel_path": "C:\\Users\\anisn\\OneDrive\\Desktop\\Daily Check.xlsx",
  "email_enabled": false,
  "email_sender": "you@gmail.com",
  "email_password": "app-password-here",
  "email_recipients": ["someone@example.com"],
  "desktop_notifications": true
}
```

You can also edit email/notification/interval settings from the **gear icon**
in the dashboard.

> **Email note:** for Gmail use an **App Password**, not your normal password.

---

## 5. Useful files

| File | Purpose |
|------|---------|
| `app.py` | Main app — run this |
| `config.py` | Defaults and settings overrides |
| `grants_monitor.db` | SQLite database (your data) |
| `grants_monitor.log` | Run log — check here if something fails |
| `settings.json` | Optional persistent config (you create it) |

---

## 6. Troubleshooting

- **`ModuleNotFoundError`** → dependencies not installed; run
  `pip install -r requirements.txt` (with the venv activated).
- **Port 5000 already in use** → set `$env:GRANTS_PORT = "8080"` and rerun.
- **"Excel seed file not found" warning** → harmless; the app uses the sites
  already in the database. Set `GRANTS_EXCEL_PATH` if you want to seed from a
  different location.
- **A site shows an error** → check `grants_monitor.log`. Sites that block
  automated requests (e.g. the Japan embassy, HTTP 403) are deactivated on
  purpose and won't be scanned.
- **Reset everything** → stop the app and delete `grants_monitor.db` (and the
  `-wal`/`-shm` files if present); it will be recreated and re-seeded on next
  start.
