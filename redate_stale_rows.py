# -*- coding: utf-8 -*-
"""Re-derive publish dates stored before the teaser-carousel fix, 2026-08-25.

extract_published_date used to take the earliest <time> on a page. Where every
<time> belonged to a "similar news" carousel — TÜBİTAK, Ufuk Avrupa — that read a
neighbouring article's date and stored it. Rows written then are wrong by up to
two weeks, and the value drifted as the carousel rotated.

Deliberately narrow. A row is a candidate ONLY when its page proves the old rule
misfired: every <time> on it belongs to a teaser, and the stored date is one of
those teaser values. That is the signature of the bug and nothing else.

Re-deriving every dated row was tried first and rejected. It proposed changes on
sites that never had the carousel problem — dates that came from the body-text
guess, which this fix does not touch — and there is no ground truth to judge them
against. Correcting what is provably wrong beats rewriting what is merely old.

Idempotent: a row already holding the derived date is left alone.

Three rules keep this from making things worse:
  * A future date is never written. The today-filter already refuses one, on the
    grounds that a future date in an announcement is a deadline or an event, not
    a publication. Rewriting a stored date to one would be a regression.
  * A row is only touched when a date can actually be derived. A page that no
    longer resolves (404, layout change) keeps what it has rather than being
    blanked on the strength of a failed fetch.
  * An implausibly old date is refused. Some sites render an empty date field as
    the Unix epoch, and "01 OCAK 1970" parses cleanly into a date that is worse
    than the one it would replace.

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


def derive(row):
    """(new_date, was_teaser_derived) for one row, or (None, False) if unreadable.

    was_teaser_derived is the proof this row is a victim of the carousel bug:
    every <time> on the page sits in a teaser, and the stored date is the one the
    old rule would have picked from them — their minimum.
    """
    try:
        soup = BeautifulSoup(scraper.fetch_page(row["url"]), "lxml")
    except Exception:
        return None, False               # unreadable: no verdict, keep what we have

    times = soup.select("time[datetime]")
    teaser = [scraper._normalize_date(e.get("datetime") or "") for e in times
              if scraper._in_teaser(e)]
    own = [e for e in times if not scraper._in_teaser(e)]
    teaser = [d for d in teaser if d]
    was_teaser = bool(teaser) and not own and min(teaser) == row["published_date"]

    found, _conf = scraper.extract_published_date(soup)
    if found:
        return found, was_teaser
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "iframe", "noscript"]):
        tag.decompose()
    text = scraper.extract_text(soup)
    guess = scraper._cue_published_date(text) or database.extract_date(text[:2500])
    return guess, was_teaser


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
            derived = list(ex.map(derive, rows))

        changes, unread, future, untouched, implausible = [], 0, 0, 0, 0
        for r, (got, was_teaser) in zip(rows, derived):
            if got is None:
                unread += 1
                continue
            if not was_teaser:
                untouched += 1           # never had the carousel problem
                continue
            if not got or got == r["published_date"]:
                continue
            if got < scraper._EARLIEST_PLAUSIBLE_DATE:
                implausible += 1
                continue
            if got > today:
                future += 1
                continue
            changes.append((r, got))

        print(f"Unreadable (left alone):      {unread}")
        print(f"Not a carousel victim:        {untouched}")
        print(f"Implausible date refused:     {implausible}")
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
