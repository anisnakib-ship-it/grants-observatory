"""One-time pass: detail-scrape all pending programs that aren't enriched yet,
populating deadline / funding / eligibility / contact (and deadline_date)."""
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import database
from scraper import scrape_grant_details

WORKERS = 8


def main():
    conn = sqlite3.connect(database.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, url FROM grants WHERE status='pending' AND details_scraped=0"
    ).fetchall()
    conn.close()
    total = len(rows)
    print(f"Enriching {total} pending programs with {WORKERS} workers...", flush=True)

    done = 0
    ok = 0
    deadlines = 0
    errors = 0
    start = time.time()

    def work(row):
        res = scrape_grant_details(row["url"])
        if res["status"] == "ok":
            database.update_grant_details(row["id"], res["details"])
            return res["details"].get("deadline", "")
        return None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(work, r): r for r in rows}
        for fut in as_completed(futures):
            done += 1
            try:
                dl = fut.result()
                if dl is None:
                    errors += 1
                else:
                    ok += 1
                    if dl.strip():
                        deadlines += 1
            except Exception:
                errors += 1
            if done % 50 == 0 or done == total:
                el = int(time.time() - start)
                print(f"  {done}/{total}  ok={ok} deadlines={deadlines} errors={errors}  ({el}s)", flush=True)

    # Final stats
    conn = sqlite3.connect(database.DATABASE_PATH)
    with_dl = conn.execute("SELECT COUNT(*) FROM grants WHERE deadline_date != ''").fetchone()[0]
    conn.close()
    print(f"DONE. enriched_ok={ok} new_deadlines_found={deadlines} errors={errors}", flush=True)
    print(f"Total grants now with a parsed deadline_date: {with_dl}", flush=True)


if __name__ == "__main__":
    main()
