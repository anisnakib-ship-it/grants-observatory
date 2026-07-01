# Deploying Grants Observatory on Ubuntu (192.168.1.135, user: moez)

Strategy: **code via git**, **secrets/data via scp** (never commit secrets).
Runs `python app.py` (web UI + background scan scheduler) under **systemd**,
bound to `0.0.0.0` so it's reachable on the LAN.

---

## A. On Windows (push the code)
`.gitignore` already excludes settings.json / client_secret*.json / token.json /
the DB / venv / logs. Confirm, then push to your GitHub/GitLab repo:
```powershell
cd "C:\Users\anisn\OneDrive\Desktop\Grants monitoring"
git init
git add .
git status            # <-- MUST NOT list settings.json, token.json, client_secret*.json, *.db
git commit -m "Grants Observatory"
git branch -M main
git remote add origin <YOUR_REPO_URL>
git push -u origin main
```

## B. On the server (moez@192.168.1.135)
```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
cd ~
git clone <YOUR_REPO_URL> grants-monitor
cd grants-monitor
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## C. Copy the secrets + data over (from Windows, scp)
These are git-ignored on purpose, so send them directly:
```powershell
cd "C:\Users\anisn\OneDrive\Desktop\Grants monitoring"
scp settings.json client_secret.json token.json grants_monitor.db moez@192.168.1.135:~/grants-monitor/
```
Then lock them down on the server:
```bash
cd ~/grants-monitor && chmod 600 settings.json client_secret.json token.json grants_monitor.db
```

## D. Test
```bash
GRANTS_HOST=0.0.0.0 .venv/bin/python app.py
```
From another machine: `http://192.168.1.135:5000`. Ctrl+C to stop.

## E. Run as a service (auto-start on boot, auto-restart)
```bash
sudo cp deploy/grants-monitor.service /etc/systemd/system/grants-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable --now grants-monitor
systemctl status grants-monitor --no-pager
journalctl -u grants-monitor -f        # live logs
```

## F. Firewall (if ufw is enabled)
```bash
sudo ufw allow 5000/tcp
```

---

## Updating later
```bash
cd ~/grants-monitor && git pull && sudo systemctl restart grants-monitor
```
(Secrets/DB stay put — they're not tracked by git, so `git pull` won't touch them.)

## Notes
- Dashboard: `http://192.168.1.135:5000`
- `plyer` desktop notifications don't work headless; the code ignores that. Turn
  them off in the gear panel to keep logs clean.
- Gmail `token.json` is portable — copying it means email works with no re-consent.
  If it ever fails with `invalid_grant`, re-run `gmail_auth.py` where a browser is
  available (or via SSH X-forwarding / a local run) and re-copy token.json.
