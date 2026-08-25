# -*- coding: utf-8 -*-
"""One-time, idempotent release of tombstoned standing-programme pages, 2026-08-25.

A standing programme page (KOSGEB /destekdetay/, an agency's /destekler/<slug>)
carries no publish date, because there is nothing to date. The date filter reads
that as "no evidence" and rejects it — and since the page fetched fine, the
rejection is recorded as a verdict, tombstoning the link for TOMBSTONE_TTL_DAYS.
Every scan since has skipped these without re-fetching.

apply_today_filter now keeps an undated catalogue page instead. This clears the
verdicts already on record so the next scan can actually reach them; without it
add_grant's tombstone check keeps returning None and nothing changes.

Only tombstones on catalogue paths are removed — every other rejection stands.
Also blanks content_hash on the sites involved so the next scan re-parses them
rather than short-circuiting on "unchanged".

Run once on each host after deploying:  python3 clear_catalog_tombstones.py
"""
import config
import database


def main():
    markers = getattr(config, "PROGRAM_CATALOG_PATH_MARKERS", [])
    if not markers:
        print("No PROGRAM_CATALOG_PATH_MARKERS configured — nothing to do.")
        return

    conn = database.get_connection()
    try:
        where = " OR ".join("normalized_url LIKE ?" for _ in markers)
        params = ["%" + m + "%" for m in markers]

        rows = conn.execute(
            "SELECT s.id, s.site_id, s.normalized_url, s.reason, si.name"
            " FROM seen_links s LEFT JOIN sites si ON si.id = s.site_id"
            f" WHERE {where}",
            params,
        ).fetchall()
        if not rows:
            print("No tombstoned catalogue pages found — nothing to release.")
            return

        print(f"Releasing {len(rows)} tombstoned catalogue page(s):")
        for r in rows:
            print(f"  [{r['name'] or '?'}] {r['reason']}  {r['normalized_url'][-66:]}")

        site_ids = sorted({r["site_id"] for r in rows})
        conn.execute(f"DELETE FROM seen_links WHERE {where}", params)
        placeholders = ",".join("?" * len(site_ids))
        conn.execute(
            f"UPDATE sites SET content_hash = '' WHERE id IN ({placeholders})", site_ids
        )
        conn.commit()

        print(f"\nTombstones cleared:      {len(rows)}")
        print(f"Sites queued to re-parse: {len(site_ids)}")
        for r in conn.execute(
            f"SELECT name FROM sites WHERE id IN ({placeholders})", site_ids
        ).fetchall():
            print(f"  - {r['name']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
