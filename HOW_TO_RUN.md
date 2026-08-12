# How to Run — Grants Observatory

A Flask app that scans Turkish institutional websites for new grant/funding
announcements, stores them in SQLite, and shows them in a dashboard where you
triage each one — **Send** it out by email, or **Delete** (hide) it. You can also
**add a program by hand** when the scan didn't find it (section 6).

This document covers running it **locally on Windows**. For the Ubuntu server
that actually runs it, see **[deploy/DEPLOY.md](deploy/DEPLOY.md)**.

---

## 1. Prerequisites

- **Python 3.10+** — check with:
  ```powershell
  python --version
  ```
  Developed on 3.12; the production host runs 3.10.12.
- Optionally an Excel site list (default:
  `%USERPROFILE%\OneDrive\Desktop\Daily Check.xlsx`). This is only needed to seed
  a **brand-new, empty** database. If you copied `grants_monitor.db` across, the
  sites are already in it and seeding is skipped with a log line. A missing file
  is harmless.

---

## 2. One-time setup

Open **PowerShell** in the project folder:

```powershell
cd "C:\Users\Anis\Desktop\Projects\Grants Observatory"
```

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

---

## 3. Set the dashboard password (required first)

Every route requires a login — there is no anonymous access, not even to the
API. Set the password once per host:

```powershell
python set_password.py
```

Only a PBKDF2 hash is stored, in `settings.json` (git-ignored). Restart the app
afterwards. Until a password is set the app **refuses all logins** rather than
running open, so if the login page says *"No password is set"*, run the above.

`settings.json` also holds the Flask session signing key, generated on first
run. Back it up: losing it logs everyone out and needs the password set again.
It is git-ignored and must stay that way — `config.py` is in a public repo.

---

## 4. Run the app

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
  Server: waitress
============================================================
```

Open **http://127.0.0.1:5000** and log in. Stop with **Ctrl + C**.

### What happens on start

- The database is initialized and any pending migrations run automatically.
- Sites are seeded from Excel **only if the database has no sites at all**.
- A background scheduler scans every `scan_interval_hours` (default 3). It does
  **not** scan immediately on startup.

### Triggering a scan manually

Use the **"Scan Now"** button in the dashboard. Calling `/api/scan` with
`Invoke-RestMethod` will return **401 Unauthorized** — the API requires a logged-in
session, so a bare POST won't work.

---

## 5. What a scan actually keeps

This surprises people, so it is worth stating plainly: a scan finds many links
but **keeps only announcements published within the scan date range**, which
defaults to **today only**. Everything else is deleted.

- `SCAN_TODAY_ONLY = True` — keep only in-range items; drop the rest, including
  anything whose publish date can't be established.
- `SCAN_RANGE_START` / `SCAN_RANGE_END` — empty means today. Set both to widen
  the window (e.g. backfilling a week).
- Rejected links are **tombstoned** for `TOMBSTONE_TTL_DAYS` (30) so later scans
  skip them instead of re-fetching every hour.

So "the scan found 200 links but only kept 3" is normal, not a bug. A scan that
keeps **nothing for days**, on the other hand, is worth investigating — check
`grants_monitor.log` for the `Date-filter` summary line.

If you widen the date range, use the dashboard's **full** rescan rather than a
normal one: old tombstones were recorded against the old range and would
otherwise suppress links the new range should keep. Note it also clears the
current pending list, so don't use it just to change the interval.

---

## 6. Adding a program by hand

Some programs never turn up in a scan — a colleague forwards one, or the source
publishes somewhere the monitor doesn't watch. **Add program** (top right of the
Announcements list) puts one in the list directly.

Title, link, source and category are required; everything else is optional. The
source is free text (with suggestions from sources already in use) and the
category comes from the fixed list in `config.CATEGORIES`, so a manual entry
filters, sorts and charts exactly like a scraped one — and can be sent by email
and mirrored through the export API the same way.

What makes a manual entry different:

- It carries a **MANUAL** badge instead of VERIFIED / NEW-TODAY, and a pencil
  button that reopens the form. Scraped rows have no pencil: the next re-scrape
  would overwrite the edit.
- **A full re-scan will not delete it.** Normal announcements that aren't sent
  are cleared when you re-fetch a new date range; nothing a person typed is,
  because no scan could bring it back.
- **Fetch Details is hidden on it.** A scrape replaces the detail fields
  wholesale, blanks included, so it would erase what you typed. Use the pencil.
- The link is deduplicated against the whole list. Adding one that already
  exists is refused; if it is in the Deleted list, restore it instead.

Behind the scenes these rows hang off a hidden placeholder site (they need one —
`grants.site_id` is `NOT NULL`) and carry their own source and category, which
override the site's everywhere the list is read. The placeholder is inactive and
hidden from the Sources tab, so no scan ever visits it.

---

## 7. Configuration

Overrides come from **environment variables** (that run only) and
**`settings.json`** (persistent, next to `app.py`).

### Environment variables (PowerShell, current session)

```powershell
$env:GRANTS_DEBUG = "true"                                 # Flask debug + dev server
$env:GRANTS_PORT = "8080"                                  # change port
$env:GRANTS_EXCEL_PATH = "D:\path\to\Daily Check.xlsx"     # custom seed file
python app.py
```

`GRANTS_HOST` defaults to `127.0.0.1`. Don't set it to `0.0.0.0` on the server —
see DEPLOY.md for why that breaks the login throttle's security.

### settings.json (persistent)

```json
{
  "scan_interval_hours": 3,
  "scan_range_start": "",
  "scan_range_end": "",
  "tombstone_ttl_days": 30,
  "auto_scrape_details": true,
  "auto_scrape_details_limit": 50,
  "probe_limit_per_site": 40,
  "probe_limit_total": 400,
  "email_enabled": false,
  "email_provider": "sendgrid",
  "email_sender": "you@example.com",
  "email_recipients": ["someone@example.com"],
  "scan_alert_enabled": true,
  "desktop_notifications": true,
  "auth_session_hours": 12,
  "session_cookie_secure": false
}
```

Email, notification and interval settings are also editable from the **gear
icon** in the dashboard. Secrets belong here, never in `config.py`.

| key | notes |
|---|---|
| `session_cookie_secure` | `true` when the app is only ever reached over HTTPS (as in production, through the tunnel). Keep `false` for local plain-HTTP runs, or the browser silently refuses to send the cookie and login appears to fail for no reason. |
| `auth_session_hours` | How long a login lasts (default 12). |
| `export_api_key` | Enables the read-only export feed. See [deploy/EXPORT_API.md](deploy/EXPORT_API.md). |

> **Email note:** for Gmail SMTP use an **App Password**. The `gmail_api`
> provider uses OAuth instead — run `gmail_auth.py` once to create `token.json`.

---

## 8. Useful files

| File | Purpose |
|------|---------|
| `app.py` | Main app — run this |
| `config.py` | Defaults; loads overrides from `settings.json`. **Public repo — no secrets** |
| `scraper.py` | Fetching, keyword matching, the date filter |
| `database.py` | Schema, migrations, dedup and tombstones |
| `grants_monitor.db` | SQLite database (your data) — git-ignored |
| `grants_monitor.log` | Run log — check here first when something fails |
| `settings.json` | Persistent config **and secrets** — git-ignored |
| `deploy/DEPLOY.md` | Deploying and operating the Ubuntu host |
| `deploy/EXPORT_API.md` | Read-only feed for a sibling platform |

---

## 9. Troubleshooting

- **`ModuleNotFoundError`** → dependencies not installed; run
  `pip install -r requirements.txt` with the venv activated.
- **Login page says "No password is set"** → run `python set_password.py`.
- **Login silently fails** → if `session_cookie_secure` is `true` but you're on
  plain HTTP, the browser won't send the cookie. Set it to `false` locally.
- **API call returns 401** → expected; every route needs a logged-in session.
- **Port 5000 already in use** → `$env:GRANTS_PORT = "8080"` and rerun.
- **"Excel seed file not found"** → harmless if the database already has sites.
- **A scan keeps almost nothing** → usually correct behaviour, see section 5.
- **A site shows an error** → check `grants_monitor.log`. Some sites are
  deactivated on purpose because they block automated requests (currently the
  Japan embassy and Yeşilay); others fail transiently or have moved (a 404 means
  the URL needs updating in the dashboard).
- **"That link is already in the announcements list"** → the program is already
  there, possibly under a different title. Search for it; if it isn't in the
  active list, look in **Deleted** and restore it.
- **No pencil button on a row** → only hand-added rows are editable; see
  section 6.
- **An announcement you expected never appeared** → check in order: is the site
  active and is `last_error` empty; is the item's publish date inside the scan
  range; and is there a tombstone for it in `seen_links` from an earlier
  rejection.
- **Reset everything** → stop the app and delete `grants_monitor.db` (plus any
  `-wal` / `-shm` files); it is recreated and re-seeded on next start. This
  destroys all triage history.
