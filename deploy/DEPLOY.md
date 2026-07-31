# Deploying Grants Observatory

**Code via git, secrets and data via scp** — `settings.json`, `client_secret.json`,
`token.json` and `grants_monitor.db` are git-ignored and must never be committed.

---

## What is actually running

| | |
|---|---|
| Host | `moez@192.168.1.135` — Ubuntu 22.04.5 |
| Directory | `/home/moez/grants-monitor` |
| Interpreter | `/home/moez/grants-monitor/.venv/bin/python` (Python 3.10.12) |
| Process manager | **PM2** (`ecosystem.config.js`), app name `grants-monitor` |
| Bind address | **`127.0.0.1:5000` — loopback only** |
| Public URL | `https://grants.sunandsun.com.tr` via Cloudflare Tunnel |
| Boot persistence | `pm2-moez.service` (enabled) + `pm2 save` |
| WSGI server | waitress |

The app is **not** run under systemd. `grants-monitor.service` in this directory
is an unused alternative kept for reference; installing it would run a second
copy against the same SQLite file. Don't, unless you first remove the PM2 app.

### Why loopback only

`cloudflared` connects over loopback, so nothing needs the app on the LAN.
Binding to `0.0.0.0` would add a second, unencrypted way in — and it would break
the login throttle's security: `_throttle_key` in `app.py` trusts
`CF-Connecting-IP`, which is only unforgeable when the tunnel is the sole path.
Anyone on the LAN could otherwise spoof that header and evade the lockout.

The tunnel's routing lives in `/etc/cloudflared/config.yml` (root-owned), which
maps `grants.sunandsun.com.tr` → `http://localhost:5000`. Other hostnames on the
same tunnel point at unrelated apps; edit with care.

---

## Routine update (the common case)

```bash
ssh moez@192.168.1.135
cd ~/grants-monitor
git pull --ff-only
pm2 restart grants-monitor --update-env
```

Database migrations in `init_db()` run automatically at startup — no separate step.

Secrets and the database are untracked, so `git pull` never touches them.

## Changing environment variables

`--update-env` re-reads the **shell's** environment, *not* `ecosystem.config.js`.
Editing that file and running a plain restart silently keeps the old values —
this is how the app spent time listening on `0.0.0.0` after the loopback change
was already committed. To actually apply it:

```bash
cd ~/grants-monitor
pm2 delete grants-monitor
pm2 start ecosystem.config.js
pm2 save                     # persist across reboots
```

`pm2 save` is required, or the old definition returns on the next boot.

---

## Verifying a deploy

```bash
cd ~/grants-monitor && git log --oneline -1
PID=$(pm2 pid grants-monitor); tr '\0' '\n' < /proc/$PID/environ | grep GRANTS_
ss -ltnp | grep 5000                      # expect 127.0.0.1:5000, NOT 0.0.0.0:5000
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/          # 302 -> login
curl -s -o /dev/null -w '%{http_code}\n' https://grants.sunandsun.com.tr/login   # 200
curl -s http://192.168.1.135:5000/ ; echo "  (should refuse)"
```

If the public URL fails but loopback works, the problem is the tunnel:
`systemctl status cloudflared`.

---

## Secrets and data

Git-ignored on purpose — copy them directly, never through the repo:

```powershell
scp settings.json client_secret.json token.json grants_monitor.db moez@192.168.1.135:~/grants-monitor/
```

```bash
cd ~/grants-monitor && chmod 600 settings.json client_secret.json token.json grants_monitor.db
```

`settings.json` holds every host-specific override — scan interval and date
range, email recipients, the dashboard password hash, the Flask secret key and
the export API key. Merge into it with `config.save_settings({...})` rather than
rewriting the file, so a partial write can't drop unrelated keys.

Set or change the dashboard password:

```bash
.venv/bin/python set_password.py
```

Back up before anything that touches the schema or bulk-edits rows:

```bash
cp grants_monitor.db grants_monitor.db.bak-$(date +%Y%m%d)
```

---

## First-time setup from scratch

```bash
sudo apt update && sudo apt install -y git python3 python3-venv python3-pip
git clone <REPO_URL> ~/grants-monitor && cd ~/grants-monitor
python3 -m venv .venv
.venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt
# copy secrets + DB across (see above), then:
npm install -g pm2                      # if PM2 isn't present
pm2 start ecosystem.config.js && pm2 save
pm2 startup                             # prints a sudo command; run it once
```

Site seeding from Excel is only needed on a genuinely empty database; with
`grants_monitor.db` copied over, the sites are already there and seeding is
skipped with a log line.

---

## Logs

```bash
pm2 logs grants-monitor                       # live
tail -f ~/.pm2/logs/grants-monitor-error.log  # app log (stderr): scans, access, errors
tail -f ~/.pm2/logs/grants-monitor-out.log    # startup banner only
```

Application logging goes to **stderr**, so scan results and access lines land in
`grants-monitor-error.log` despite the name. The same content is also written to
`~/grants-monitor/grants_monitor.log`.

Neither file is rotated and both grow together (~10 MB each as of July 2026).
Truncate them when convenient:

```bash
pm2 flush grants-monitor && : > ~/grants-monitor/grants_monitor.log
```

Lines worth knowing when diagnosing a missed announcement:

- `Date-filter [start..end]: kept N in-range (M via feed), dropped N, deferred N (K unreadable)`
- `Tombstoned N rejected link(s) for 30d` — those links are skipped until the verdict expires
- `Probe ceiling hit` — a site is emitting far too many candidates; its layout probably changed

---

## Export API

A read-only feed of sent programs for a sibling platform. Setup, contract and
field semantics: **[EXPORT_API.md](EXPORT_API.md)**.

---

## Gotchas

- **Never mount or copy `grants_monitor.db` for another process to read live.**
  Single-writer SQLite in WAL mode, written by the scanner every hour; a second
  reader over a network mount risks corruption. Use the export API.
- **`plyer` desktop notifications don't work headless.** The code tolerates it;
  turn them off in the gear panel to keep the logs clean.
- **Gmail `token.json` is portable** — copying it avoids re-consent. On
  `invalid_grant`, re-run `gmail_auth.py` where a browser is available and copy
  the new token across.
- **`config.py` is in a public repo.** No secret belongs in it — `settings.json`
  only.
