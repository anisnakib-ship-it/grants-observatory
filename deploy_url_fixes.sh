#!/usr/bin/env bash
# One-shot deploy helper for live-DB URL corrections.
#
# git pull only updates sites_seed.json; it does NOT touch the running
# grants_monitor.db. import_sites.py can't help either — it matches rows on the
# UNIQUE url column, so a changed URL would insert a duplicate instead of
# updating. This script applies the URL changes directly to the live DB.
#
# Usage (on the server):
#     cd /home/moez/grants-monitor
#     git pull && bash deploy_url_fixes.sh
#
# Idempotent: re-running it is harmless (rows already fixed simply match nothing).
set -euo pipefail

cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

"$PY" - <<'PY'
import sqlite3

# (old_url, new_url) pairs — matched on the old URL so only the intended row moves.
CHANGES = [
    ("https://www.doka.org.tr/destekler_Acik-TR.html", "https://www.doka.org.tr/"),
    ("http://turkey.embassy.gov.au/ankaturkish/home.html",
     "https://turkey.embassy.gov.au/ankaturkish/home.html"),
]

c = sqlite3.connect("grants_monitor.db")
c.execute("PRAGMA busy_timeout=5000")
for old, new in CHANGES:
    cur = c.execute("UPDATE sites SET url=? WHERE url=?", (new, old))
    print(f"{'updated' if cur.rowcount else 'no-op '}: {old} -> {new}")
c.commit()

print("\nCurrent rows:")
for r in c.execute(
    "SELECT id, name, url FROM sites "
    "WHERE url LIKE '%doka.org%' OR url LIKE '%embassy.gov.au%'"
):
    print(" ", r)
c.close()
PY

echo
echo "Restarting app..."
pm2 restart grants-monitor
echo "Done."
