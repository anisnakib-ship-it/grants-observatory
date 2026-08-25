# -*- coding: utf-8 -*-
"""Re-derive publish dates stored before the teaser-carousel fix, 2026-08-25.

extract_published_date used to take the earliest <time> on a page. Where every
<time> belonged to a "similar news" carousel — TÜBİTAK, Ufuk Avrupa — that read a
neighbouring article's date and stored it. Rows written then are wrong by up to
two weeks, and the value drifted as the carousel rotated.

Re-fetches each scraped row and re-derives its date the way the fixed pipeline
does. Idempotent: a row already holding the derived date is left alone.

Two rules keep this from making things worse:
  * A future date is never written. The today-filter already refuses one, on the
    grounds that a future date in an announcement is a deadline or an event, not
    a publication. Rewriting a stored date to one would be a regression.
  * A row is only touched when a date can actually be derived. A page that no
    longer resolves (404, layout change) keeps what it has rather than being
    blanked on the strength of a failed fetch.

Hand-entered rows are never touched: the date is the user's own.

Dry run (default) prints what it would change:  python3 redate_stale_rows.py
Apply:                                          python3 redate_stale_rows.py --apply
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from bs4 import BeautifulSoup

import database
import scraper


def derive(url):
    """The date the fixed pipeline resolves for this page, or '' if none."""
    try:
        soup = BeautifulSoup(scraper.fetch_page(url), "lxml")
    except Exception:
        return None                      # unreadable: no verdict, keep what we have
    found, _conf = scraper.extract_published_date(soup)
    if found:
        return found
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "iframe", "noscript"]):
        tag.decompose()
    text = scraper.extract_text(soup)
    return scraper._cue_published_date(text) or database.extract_date(text[:2500])


def main():
    apply = "--apply" in sys.argv
    today = date.today().isoformat()

    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, url, published_date FROM grants"
            " WHERE COALESCE(is_manual, 0) = 0 AND COALESCE(published_date, '') != ''"
            " ORDER BY id DESC"
        ).fetchall()
        print(f"Scraped rows carrying a date: {len(rows)}")

        with ThreadPoolExecutor(max_workers=8) as ex:
            derived = list(ex.map(lambda r: derive(r["url"]), rows))

        changes, unread, future = [], 0, 0
        for r, got in zip(rows, derived):
            if got is None:
                unread += 1
                continue
            if not got or got == r["published_date"]:
                continue
            if got > today:
                future += 1
                continue
            changes.append((r, got))

        print(f"Unreadable (left alone):      {unread}")
        print(f"Future date refused:          {future}")
        print(f"Rows to correct:              {len(changes)}")
        print()
        for r, got in changes:
            print(f"  id={r['id']:<7} {r['published_date']} -> {got}   {r['title'][:52]}")

        if not changes:
            return
        if not apply:
            print("\nDry run. Re-run with --apply to write these.")
            return

        for r, got in changes:
            conn.execute(
                "UPDATE grants SET published_date = ?, updated_at = ? WHERE id = ?",
                (got, database._now(), r["id"]),
            )
        conn.commit()
        print(f"\nUpdated {len(changes)} row(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
