"""One-time importer: load the monitored sites from sites_seed.json into a fresh
database. Use this on a host where you can't copy grants_monitor.db over.

    .venv/bin/python import_sites.py

Then start the app — it will scan and rebuild the programs itself. (Only the
sites list is seeded here; program history is not, and gets re-scanned.)

Safe to run before the app is started. If the app is already running, stop it
first (pm2 stop grants-monitor) so the database isn't locked.
"""
import json
import sys

import database

sys.stdout.reconfigure(encoding="utf-8")


def main():
    database.init_db()
    with open("sites_seed.json", encoding="utf-8") as f:
        sites = json.load(f)

    # Single connection, single transaction — avoids "database is locked".
    conn = database.get_connection()
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        for s in sites:
            conn.execute(
                "INSERT OR IGNORE INTO sites (name, url, category, css_selector, feed_url, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (s["name"], s["url"], s.get("category", ""), s.get("css_selector", ""),
                 s.get("feed_url", ""), s.get("is_active", 1)),
            )
            conn.execute(
                "UPDATE sites SET feed_url = ?, is_active = ?, css_selector = ? WHERE url = ?",
                (s.get("feed_url", ""), s.get("is_active", 1), s.get("css_selector", ""), s["url"]),
            )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
    finally:
        conn.close()
    print(f"Imported {len(sites)} sites; database now has {total} sites.")


if __name__ == "__main__":
    main()
