"""One-time importer: load the monitored sites from sites_seed.json into a fresh
database. Use this on a host where you can't copy grants_monitor.db over.

    .venv/bin/python import_sites.py

Then start the app — it will scan and rebuild the programs itself. (Only the
sites list is seeded here; program history is not, and gets re-scanned.)
"""
import json
import sys

import database

sys.stdout.reconfigure(encoding="utf-8")


def main():
    database.init_db()
    with open("sites_seed.json", encoding="utf-8") as f:
        sites = json.load(f)
    conn = database.get_connection()
    try:
        for s in sites:
            database.add_site(s["name"], s["url"], s.get("category", ""), s.get("css_selector", ""))
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
