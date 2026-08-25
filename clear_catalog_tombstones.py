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

It also retags catalogue rows already stored as item_type='funding'. Those render
with the "~ NEW-TODAY / first seen today" label and a DATE of "today", which reads
as a claim the programme was announced today — it was not; it has no announcement
date at all. item_type='program' is what makes the card show it as standing.
Hand-added rows are left alone: their item_type is the user's own choice.

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

        # Both statements below are independent of the tombstones and must run
        # even when there are none left to clear: on a host where an earlier
        # version of this script already released them, the rows still carry the
        # wrong item_type and would keep rendering as today's announcements.

        # Undo over-tagging by the first version of this rule, which retagged
        # catalogue-path rows regardless of whether they carried a date.
        untag = conn.execute(
            "UPDATE grants SET item_type = 'funding'"
            " WHERE COALESCE(is_manual, 0) = 0 AND item_type = 'program'"
            " AND COALESCE(published_date, '') != ''"
        )
        retag = conn.execute(
            "UPDATE grants SET item_type = 'program'"
            " WHERE COALESCE(is_manual, 0) = 0 AND item_type = 'funding'"
            # Only rows with no date. A catalogue-path page that carries a real
            # publish date is a dated announcement, not a standing programme.
            " AND COALESCE(published_date, '') = ''"
            f" AND ({where})",
            params,
        )
        conn.commit()
        print(f"Rows retagged standing:  {retag.rowcount}")
        print(f"Rows corrected to dated: {untag.rowcount}")

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
