# -*- coding: utf-8 -*-
"""One-time, idempotent source fix for TÜBİTAK and KOSGEB, 2026-08-24.

TÜBİTAK was configured with feed_url=.../rss.xml. run_scan prefers a feed
whenever one is set, so its site page was never scraped — and that feed is a
10-item mixed news/duyuru firehose that carries almost no funding calls (measured
on 2026-08-24: 0 keyword-matching calls in the feed vs 5 on /tr/duyuru, with 14 of
18 duyuru items absent from the feed entirely). Point the site at the duyuru
listing and drop the feed so scrape_site handles it.

Both sites also need their rejected-link verdicts cleared: announcements dropped
for want of a parseable date were tombstoned for TOMBSTONE_TTL_DAYS and would
stay invisible even after the scraper fix. Only these two sites' verdicts are
cleared — database.clear_tombstones() wipes every site's.

content_hash is blanked so the next scan re-parses instead of short-circuiting on
"unchanged".

Run once on each host after deploying:  python3 apply_tubitak_kosgeb_fix.py
"""
import database

TUBITAK_URL = "https://tubitak.gov.tr/tr/duyuru"
# Matched on url LIKE, so a trailing-slash or www variant in the DB still hits.
TUBITAK_MATCH = "%tubitak.gov.tr%"
KOSGEB_MATCH = "%kosgeb.gov.tr%"


def main():
    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, url, feed_url FROM sites WHERE url LIKE ? OR url LIKE ?",
            (TUBITAK_MATCH, KOSGEB_MATCH),
        ).fetchall()
        if not rows:
            print("No TÜBİTAK or KOSGEB site row found — nothing to do.")
            return

        print("Before:")
        for r in rows:
            print(f"  [{r['id']}] {r['name']}\n        url={r['url']}\n        feed_url={r['feed_url']!r}")

        tubitak = [r for r in rows if "tubitak.gov.tr" in r["url"]]
        tubitak_ids = {r["id"] for r in tubitak}
        # UNIQUE(url) would reject the update if some OTHER row already holds the
        # target URL; check first so the failure is a clear message, not an
        # IntegrityError. A row already sitting on the target URL is this script
        # having run before, which is fine.
        clash = next(
            (r for r in conn.execute(
                "SELECT id, name FROM sites WHERE url = ?", (TUBITAK_URL,)
            ).fetchall() if r["id"] not in tubitak_ids),
            None,
        )
        if clash:
            print(f"\nABORT: {TUBITAK_URL} is already used by site "
                  f"[{clash['id']}] {clash['name']}. Resolve that first.")
            return

        changed = 0
        for r in tubitak:
            cur = conn.execute(
                "UPDATE sites SET url = ?, feed_url = '', content_hash = '' WHERE id = ?",
                (TUBITAK_URL, r["id"]),
            )
            changed += cur.rowcount

        # Force a full re-parse of KOSGEB too, so its announcements are re-read
        # with the repaired date extraction rather than skipped as unchanged.
        conn.execute(
            "UPDATE sites SET content_hash = '' WHERE url LIKE ?", (KOSGEB_MATCH,)
        )

        site_ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(site_ids))
        cur = conn.execute(
            f"DELETE FROM seen_links WHERE site_id IN ({placeholders})", site_ids
        )
        cleared = cur.rowcount
        conn.commit()

        print(f"\nTÜBİTAK site rows repointed: {changed}")
        print(f"Tombstoned links cleared:    {cleared}")
        print("\nAfter:")
        for r in conn.execute(
            f"SELECT id, name, url, feed_url FROM sites WHERE id IN ({placeholders})",
            site_ids,
        ).fetchall():
            print(f"  [{r['id']}] {r['name']}\n        url={r['url']}\n        feed_url={r['feed_url']!r}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
