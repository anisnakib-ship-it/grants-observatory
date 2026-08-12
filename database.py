import sqlite3
import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import config
from config import DATABASE_PATH


# Query params that are tracking/session noise and should be ignored when
# deciding whether two URLs point at the same program.
_TRACKING_PARAMS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid",
    "mc_cid", "mc_eid", "_ga", "ref", "ref_src", "ref_url", "source", "spm",
    "sessionid", "session_id", "sid", "phpsessid", "jsessionid", "aspsessionid",
    "cid", "ck", "cache", "_", "t", "ts", "timestamp", "rand", "random",
}
_WS_RE = re.compile(r"\s+")


def normalize_url(url):
    """Return a canonical key for a URL so trivial variations (scheme, www,
    trailing slash, tracking params) don't look like a different program."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return url.strip().lower()
    scheme = (parts.scheme or "http").lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")):
        netloc = netloc[:-3]
    elif (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc[:-4]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=False):
        kl = k.lower()
        if kl in _TRACKING_PARAMS or kl.startswith("utm_"):
            continue
        kept.append((k, v))
    kept.sort()
    query = urlencode(kept)
    # Fold http/https into one key so a scheme flip isn't seen as "new".
    return urlunsplit(("http", netloc, path, query, ""))


def normalize_title(title):
    """Lowercase + whitespace-collapsed title, used as a secondary dedup key."""
    if not title:
        return ""
    return _WS_RE.sub(" ", title.lower().strip())[:300]


# Month names (Turkish + English, with and without diacritics) -> month number.
_MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
    "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DATE_WORD_RE = re.compile(r"(\d{1,2})\s+([A-Za-zÇŞĞÜÖİçşğüöı]+)\s+(\d{4})")
_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[\./\-](\d{1,2})[\./\-](\d{4})\b")
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")


def extract_date(text):
    """Find the first plausible date in free text and return it as YYYY-MM-DD,
    or '' if none found. Handles '19 Haziran 2026', '19.06.2026', '2026-06-19'."""
    if not text:
        return ""
    m = _DATE_WORD_RE.search(text)
    if m:
        # Turkish-aware lowering: "HAZİRAN".lower() yields "hazi̇ran" (dotted-i
        # plus combining dot), which misses the dictionary. Map İ->i, I->ı first.
        month_word = m.group(2).replace("İ", "i").replace("I", "ı").lower()
        mm = _MONTHS.get(month_word)
        if mm:
            d, y = int(m.group(1)), int(m.group(3))
            if 1 <= d <= 31:
                return f"{y:04d}-{mm:02d}-{d:02d}"
    m = _DATE_NUM_RE.search(text)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _DATE_ISO_RE.search(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


def get_connection():
    # timeout + busy_timeout: under the scan's concurrent workers (MAX_WORKERS),
    # WAL still serializes writers. Without a busy timeout a writer that can't get
    # the lock immediately raises "database is locked"; give it 30s to wait instead.
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT '',
            css_selector TEXT DEFAULT '',
            last_checked TEXT,
            last_status TEXT DEFAULT 'pending',
            last_error TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS grants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            description TEXT DEFAULT '',
            keywords_matched TEXT DEFAULT '[]',
            is_new INTEGER DEFAULT 1,
            is_read INTEGER DEFAULT 0,
            is_notified INTEGER DEFAULT 0,
            found_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (site_id) REFERENCES sites(id),
            UNIQUE(site_id, url)
        );

        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            sites_scanned INTEGER DEFAULT 0,
            new_grants_found INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            status TEXT DEFAULT 'running'
        );

        -- Links we have already evaluated and rejected (out of the scan's date
        -- range, or undatable). The grants row itself is deleted, so without
        -- this the link looks brand new on the next scan: add_grant's dedup
        -- works by finding an existing row. See tombstone_and_delete.
        CREATE TABLE IF NOT EXISTS seen_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            normalized_url TEXT NOT NULL,
            normalized_title TEXT DEFAULT '',
            published_date TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            tombstoned_at TEXT DEFAULT (datetime('now')),
            UNIQUE(site_id, normalized_url)
        );

        CREATE INDEX IF NOT EXISTS idx_grants_found_at ON grants(found_at DESC);
        CREATE INDEX IF NOT EXISTS idx_grants_is_new ON grants(is_new);
        CREATE INDEX IF NOT EXISTS idx_grants_site_id ON grants(site_id);
        CREATE INDEX IF NOT EXISTS idx_seen_links_title ON seen_links(site_id, normalized_title);
    """)

    # Add detail columns if they don't exist (migration for existing DBs)
    existing_cols = {row[1] for row in cursor.execute("PRAGMA table_info(grants)").fetchall()}
    new_cols = {
        "detailed_description": "TEXT DEFAULT ''",
        "deadline": "TEXT DEFAULT ''",
        "funding_amount": "TEXT DEFAULT ''",
        "eligibility": "TEXT DEFAULT ''",
        "application_url": "TEXT DEFAULT ''",
        "contact_info": "TEXT DEFAULT ''",
        "details_scraped": "INTEGER DEFAULT 0",
        "details_scraped_at": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT 'pending'",
        "normalized_url": "TEXT DEFAULT ''",
        "normalized_title": "TEXT DEFAULT ''",
        "last_seen_at": "TEXT DEFAULT ''",
        "published_date": "TEXT DEFAULT ''",
        "deadline_date": "TEXT DEFAULT ''",
        "item_type": "TEXT DEFAULT 'funding'",
        # New flat-list model: sent_at stamps when an announcement was emailed
        # (drives the "Sent" badge); is_deleted soft-hides it (kept so a future
        # scan won't re-add it via URL dedup). Replaces the accept/reject split.
        "sent_at": "TEXT DEFAULT ''",
        "is_deleted": "INTEGER DEFAULT 0",
        # Watermark for the export API's incremental sync. found_at only records
        # discovery and last_seen_at only moves on re-scrape, so neither reflects
        # a triage action — a consumer polling "what changed since X" would miss
        # every send, hide and restore. Touched by every mutation that alters an
        # exported field; see _touch().
        "updated_at": "TEXT DEFAULT ''",
        # Hand-added announcements (see add_manual_grant). A manual row has no
        # real source site, so it carries its own source name and category and
        # overrides the joined sites columns everywhere the list is read
        # (_SOURCE_EXPR / _CATEGORY_EXPR). is_manual also protects it from the
        # scan's bulk cleanups — nothing a person typed should be swept away by
        # a re-scan (see clear_pending_grants).
        "is_manual": "INTEGER DEFAULT 0",
        "manual_source": "TEXT DEFAULT ''",
        "manual_category": "TEXT DEFAULT ''",
    }
    newly_added = [col for col in new_cols if col not in existing_cols]
    for col in newly_added:
        cursor.execute(f"ALTER TABLE grants ADD COLUMN {col} {new_cols[col]}")

    # One-time migration from the old accept/reject model to the flat list:
    # everything previously 'rejected' becomes soft-deleted (hidden but
    # recoverable); 'accepted'/'pending' stay in the active list.
    if "is_deleted" in newly_added:
        cursor.execute("UPDATE grants SET is_deleted = 1 WHERE status = 'rejected'")

    # Seed the export watermark for rows that predate it. Without a backfill they
    # would all carry '' and sort before any real timestamp, so a consumer's first
    # sync would still see them — but with no ordering among themselves. Use the
    # newest timestamp the row already has.
    if "updated_at" in newly_added:
        cursor.execute(
            "UPDATE grants SET updated_at = MAX("
            "  IFNULL(sent_at, ''), IFNULL(last_seen_at, ''), IFNULL(found_at, '')"
            ")"
        )

    # Sites migration: feed_url holds a validated RSS/Atom feed for sites that
    # publish one (ingested via feeds for reliable publish dates).
    site_cols = {row[1] for row in cursor.execute("PRAGMA table_info(sites)").fetchall()}
    if "feed_url" not in site_cols:
        cursor.execute("ALTER TABLE sites ADD COLUMN feed_url TEXT DEFAULT ''")

    # One-time backfill of publish dates parsed from existing grant titles.
    if "published_date" in newly_added:
        for row in cursor.execute("SELECT id, title FROM grants").fetchall():
            d = extract_date(row[1] or "")
            if d:
                cursor.execute("UPDATE grants SET published_date = ? WHERE id = ?", (d, row[0]))

    # One-time backfill of deadline dates parsed from existing deadline text.
    if "deadline_date" in newly_added:
        for row in cursor.execute("SELECT id, deadline FROM grants WHERE deadline != ''").fetchall():
            d = extract_date(row[1] or "")
            if d:
                cursor.execute("UPDATE grants SET deadline_date = ? WHERE id = ?", (d, row[0]))

    # Backfill dedup keys for grants inserted before normalization existed.
    todo = cursor.execute(
        "SELECT id, url, title, found_at FROM grants WHERE normalized_url IS NULL OR normalized_url = ''"
    ).fetchall()
    for row in todo:
        cursor.execute(
            "UPDATE grants SET normalized_url = ?, normalized_title = ?, "
            "last_seen_at = CASE WHEN last_seen_at IS NULL OR last_seen_at = '' THEN ? ELSE last_seen_at END "
            "WHERE id = ?",
            (normalize_url(row[1]), normalize_title(row[2]), row[3] or "", row[0]),
        )

    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_grants_norm_url ON grants(site_id, normalized_url)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_grants_norm_title ON grants(site_id, normalized_title)"
    )

    conn.commit()
    conn.close()


def add_site(name, url, category="", css_selector=""):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sites (name, url, category, css_selector) VALUES (?, ?, ?, ?)",
            (name, url, category, css_selector),
        )
        conn.commit()
    finally:
        conn.close()


# Manual announcements still need a sites row: grants.site_id is NOT NULL and
# every list query joins sites. This placeholder satisfies that without being a
# real source — it is inactive so no scan ever visits it, hidden from
# get_all_sites so it can't be toggled or shown as a monitored source, and its
# name/category are never displayed (each manual row carries its own; see
# _SOURCE_EXPR). The URL is the identity key, so it must not change.
MANUAL_SITE_URL = "manual://entries"
MANUAL_SITE_NAME = "Manual entry"


def _manual_site_id(conn):
    """Row id of the placeholder site, creating it on first use."""
    row = conn.execute("SELECT id FROM sites WHERE url = ?", (MANUAL_SITE_URL,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO sites (name, url, category, is_active) VALUES (?, ?, '', 0)",
        (MANUAL_SITE_NAME, MANUAL_SITE_URL),
    )
    return cur.lastrowid


def get_all_sites(active_only=True):
    """Real monitored sources. Excludes the manual placeholder — it is not a site
    anyone scans, filters by, or should see in the Sources tab."""
    conn = get_connection()
    try:
        query = "SELECT * FROM sites WHERE url != ?"
        params = [MANUAL_SITE_URL]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY name"
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def update_site_status(site_id, status, error="", content_hash=""):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sites SET last_checked = ?, last_status = ?, last_error = ?, content_hash = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), status, error, content_hash, site_id),
        )
        conn.commit()
    finally:
        conn.close()


def _is_tombstoned(conn, site_id, norm_url, norm_title, published_date=""):
    """True if a recent scan already evaluated this link and rejected it.

    Mirrors add_grant's dedup keys so a link that changed URL but kept its title
    (or vice versa) is still recognised.

    The title key additionally requires the publish dates to agree. A title alone
    is too weak to carry a REJECTION forward: agencies reuse one wording for a
    recurring notice (TKDK posts an identically-titled "çağrı ilanı tarihinde
    değişiklik" whenever a call slips), so a title-only match let an April item's
    verdict silently bury a genuinely new July announcement at a new URL — and
    because the suppression happens before the insert, it left no row, no
    tombstone and no log line to notice. Same title AND same date is still the
    same item, which keeps the "site changed its URL scheme" saving intact.
    When either date is unknown we do NOT suppress: re-examining costs one detail
    fetch, whereas a wrong verdict hides a real program for TOMBSTONE_TTL_DAYS —
    the same asymmetry the probe ceiling is sized around (see config.py).

    Verdicts expire after config.TOMBSTONE_TTL_DAYS so a republished page gets
    re-examined eventually — see the config comment for why that matters. A stale
    verdict simply stops matching here; the link is then re-evaluated and, if
    rejected again, its tombstone is refreshed by tombstone_and_delete.
    """
    clauses = ["(normalized_url != '' AND normalized_url = ?)"]
    params = [site_id, norm_url]
    if config.DEDUP_BY_TITLE:
        clauses.append(
            "(? != '' AND normalized_title = ?"
            " AND ? != '' AND IFNULL(published_date, '') = ?)"
        )
        params += [norm_title, norm_title, published_date, published_date]
    sql = f"SELECT 1 FROM seen_links WHERE site_id = ? AND ({' OR '.join(clauses)})"
    ttl = int(getattr(config, "TOMBSTONE_TTL_DAYS", 30) or 0)
    if ttl > 0:
        sql += " AND tombstoned_at > datetime('now', ?)"
        params.append(f"-{ttl} days")
    return conn.execute(sql + " LIMIT 1", params).fetchone() is not None


def add_grant(site_id, title, url, description="", keywords_matched=None, published_date="", item_type="funding"):
    conn = get_connection()
    try:
        norm_url = normalize_url(url)
        norm_title = normalize_title(title)
        # Fall back to a date parsed from the title if none was supplied.
        published_date = published_date or extract_date(title)
        now = datetime.utcnow().isoformat()

        # Already in the archive? Match on normalized URL, and (optionally) on an
        # identical normalized title from the same site. Refresh last_seen_at and
        # report it as not-new so it isn't re-extracted/re-notified.
        clauses = ["(normalized_url != '' AND normalized_url = ?)"]
        params = [site_id, norm_url]
        if config.DEDUP_BY_TITLE:
            clauses.append("(? != '' AND normalized_title = ?)")
            params += [norm_title, norm_title]
        existing = conn.execute(
            f"SELECT id FROM grants WHERE site_id = ? AND ({' OR '.join(clauses)}) LIMIT 1",
            params,
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE grants SET last_seen_at = ? WHERE id = ?", (now, existing["id"])
            )
            conn.commit()
            return None

        # Already added by hand? Manual rows live under the placeholder site, so
        # the site-scoped check above never sees them — without this, a scan that
        # later reaches the same page on its real site would insert a second copy
        # of a program the user already entered. Matched on URL only: the title
        # they typed is their own wording and won't match the site's.
        manual = conn.execute(
            "SELECT id FROM grants WHERE is_manual = 1 AND normalized_url != ''"
            " AND normalized_url = ? LIMIT 1",
            (norm_url,),
        ).fetchone()
        if manual:
            conn.execute(
                "UPDATE grants SET last_seen_at = ? WHERE id = ?", (now, manual["id"])
            )
            conn.commit()
            return None

        # Already judged and rejected by an earlier scan. Returning None here is
        # what saves the work: the caller never adds it to new_grants, so the
        # today-filter never fetches its detail page again.
        if _is_tombstoned(conn, site_id, norm_url, norm_title, published_date):
            return None

        cursor = conn.execute(
            """INSERT OR IGNORE INTO grants
                 (site_id, title, url, description, keywords_matched,
                  normalized_url, normalized_title, last_seen_at, published_date, item_type,
                  updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                site_id, title, url, description,
                json.dumps(keywords_matched or [], ensure_ascii=False),
                norm_url, norm_title, now, published_date, item_type,
                now,
            ),
        )
        conn.commit()
        # Return the new grant's id if inserted, else None (duplicate ignored).
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def _manual_values(data):
    """Normalise one manual submission into the columns a grants row needs.

    Whitelist, not a passthrough: only these keys are ever written, so a posted
    body cannot set is_manual, sent_at or the dedup keys. Shape validation
    (required fields, URL scheme, date format) happens in the API layer.
    """
    deadline = (data.get("deadline") or "").strip()
    return {
        "title": (data.get("title") or "").strip(),
        "url": (data.get("url") or "").strip(),
        "manual_source": (data.get("source") or "").strip(),
        "manual_category": (data.get("category") or "").strip(),
        "item_type": "news" if (data.get("item_type") or "") == "news" else "funding",
        "description": (data.get("description") or "").strip(),
        "published_date": (data.get("published_date") or "").strip(),
        "deadline": deadline,
        # The form posts an ISO date, so the parsed column is set from it
        # directly instead of being re-extracted from free text.
        "deadline_date": deadline,
        "funding_amount": (data.get("funding_amount") or "").strip(),
        "eligibility": (data.get("eligibility") or "").strip(),
        "application_url": (data.get("application_url") or "").strip(),
        "contact_info": (data.get("contact_info") or "").strip(),
    }


def _has_details(vals):
    """Whether enough detail fields were filled in for the modal's details panel
    to be worth showing. Drives details_scraped, which is what the UI checks."""
    return any(vals[k] for k in (
        "deadline", "funding_amount", "eligibility", "application_url", "contact_info"
    ))


def _url_taken(conn, norm_url, exclude_id=None):
    """The existing row using this URL, if any — including soft-deleted ones, so
    a re-add is reported as 'restore that instead' rather than silently making a
    second copy that the Deleted view still hides."""
    sql = "SELECT id, is_deleted FROM grants WHERE normalized_url != '' AND normalized_url = ?"
    params = [norm_url]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return conn.execute(sql + " LIMIT 1", params).fetchone()


def add_manual_grant(data):
    """Insert a hand-entered announcement into the active list.

    Returns (grant_id, error) where error is '' on success, 'duplicate' if the
    URL is already listed, or 'deleted' if it is listed but hidden.

    found_at is left to the column DEFAULT so a manual row carries the same
    timestamp shape as a scraped one — the dashboard slices it by position.
    """
    vals = _manual_values(data)
    conn = get_connection()
    try:
        norm_url = normalize_url(vals["url"])
        dup = _url_taken(conn, norm_url)
        if dup:
            return None, ("deleted" if dup["is_deleted"] else "duplicate")
        now = datetime.utcnow().isoformat()
        cur = conn.execute(
            """INSERT INTO grants
                 (site_id, title, url, description, keywords_matched,
                  normalized_url, normalized_title, last_seen_at, updated_at,
                  published_date, deadline, deadline_date, item_type,
                  funding_amount, eligibility, application_url, contact_info,
                  details_scraped, details_scraped_at,
                  is_manual, manual_source, manual_category)
               VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (
                _manual_site_id(conn), vals["title"], vals["url"], vals["description"],
                norm_url, normalize_title(vals["title"]), now, now,
                vals["published_date"], vals["deadline"], vals["deadline_date"],
                vals["item_type"], vals["funding_amount"], vals["eligibility"],
                vals["application_url"], vals["contact_info"],
                1 if _has_details(vals) else 0, now if _has_details(vals) else "",
                vals["manual_source"], vals["manual_category"],
            ),
        )
        conn.commit()
        return cur.lastrowid, ""
    finally:
        conn.close()


def update_manual_grant(grant_id, data):
    """Edit a hand-entered announcement. Returns (ok, error), where error is
    'not_manual' for a scraped row — those are owned by the scraper and would be
    overwritten on the next re-scrape, so they are not editable here."""
    vals = _manual_values(data)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, is_manual FROM grants WHERE id = ?", (grant_id,)
        ).fetchone()
        if not row:
            return False, "not_found"
        if not row["is_manual"]:
            return False, "not_manual"
        norm_url = normalize_url(vals["url"])
        dup = _url_taken(conn, norm_url, exclude_id=grant_id)
        if dup:
            return False, ("deleted" if dup["is_deleted"] else "duplicate")
        now = datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE grants SET
                 title = ?, url = ?, description = ?,
                 normalized_url = ?, normalized_title = ?,
                 published_date = ?, deadline = ?, deadline_date = ?, item_type = ?,
                 funding_amount = ?, eligibility = ?, application_url = ?, contact_info = ?,
                 details_scraped = ?, manual_source = ?, manual_category = ?,
                 updated_at = ?
               WHERE id = ?""",
            (
                vals["title"], vals["url"], vals["description"],
                norm_url, normalize_title(vals["title"]),
                vals["published_date"], vals["deadline"], vals["deadline_date"],
                vals["item_type"], vals["funding_amount"], vals["eligibility"],
                vals["application_url"], vals["contact_info"],
                1 if _has_details(vals) else 0,
                vals["manual_source"], vals["manual_category"],
                now, grant_id,
            ),
        )
        conn.commit()
        return True, ""
    finally:
        conn.close()


def distinct_sources():
    """Names for the dashboard's Source filter: every monitored site plus the
    sources typed on manual rows, which belong to no site."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sites WHERE url != ?"
            " UNION SELECT manual_source FROM grants WHERE IFNULL(manual_source, '') != ''",
            (MANUAL_SITE_URL,),
        ).fetchall()
        return sorted({(r["name"] or "").strip() for r in rows} - {""})
    finally:
        conn.close()


def get_recent_grants(limit=100, unread_only=False, status_filter=None, include_deleted=False):
    conn = get_connection()
    try:
        query = f"""
            SELECT g.*, {_SOURCE_EXPR} as site_name, {_CATEGORY_EXPR} as site_category
            FROM grants g
            JOIN sites s ON g.site_id = s.id
        """
        conditions = []
        params = []
        if not include_deleted:
            conditions.append("g.is_deleted = 0")
        if unread_only:
            conditions.append("g.is_read = 0")
        if status_filter:
            conditions.append("g.status = ?")
            params.append(status_filter)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY g.found_at DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


# A grant's source and category normally come from the site it was scraped from,
# but a manual announcement has no real site (see _manual_site_id) and carries
# its own. Every read path selects, filters and sorts through these so the two
# kinds of row behave identically in the list, the filters and the donut.
_SOURCE_EXPR = "COALESCE(NULLIF(g.manual_source, ''), s.name)"
_CATEGORY_EXPR = "COALESCE(NULLIF(g.manual_category, ''), s.category)"


_SORT_MAP = {
    "date-desc": "g.found_at DESC",
    "date-asc": "g.found_at ASC",
    "source": f"{_SOURCE_EXPR} ASC, g.found_at DESC",
    "category": f"{_CATEGORY_EXPR} ASC, g.found_at DESC",
    "published-desc": "g.published_date DESC, g.found_at DESC",
    # Soonest upcoming deadline first; unknown deadlines sorted last.
    "deadline": "CASE WHEN g.deadline_date = '' THEN 1 ELSE 0 END, g.deadline_date ASC",
}


# A program's effective "release date": its parsed publish date, or the date we
# first found it when no publish date could be detected.
_RELEASE_EXPR = "COALESCE(NULLIF(g.published_date, ''), substr(g.found_at, 1, 10))"


def query_grants(status=None, category=None, source=None, q=None, days=None,
                 has_deadline=False, released_after=None, released_before=None,
                 hide_expired=False, sort="date-desc", limit=30, offset=0,
                 deleted=False, sent=None):
    """Filter the whole grants archive in SQL. Returns (rows, total_matching).

    deleted: False -> active list only; True -> soft-deleted only; None -> both.
    sent:    True  -> only already-sent; False -> only not-yet-sent; None -> both.
    """
    conn = get_connection()
    try:
        conditions = []
        params = []
        if deleted is not None:
            conditions.append("g.is_deleted = ?")
            params.append(1 if deleted else 0)
        if sent is not None:
            conditions.append("g.sent_at != ''" if sent else "g.sent_at = ''")
        if status:
            conditions.append("g.status = ?")
            params.append(status)
        if category:
            conditions.append(f"{_CATEGORY_EXPR} = ?")
            params.append(category)
        if source:
            conditions.append(f"{_SOURCE_EXPR} = ?")
            params.append(source)
        if q:
            like = f"%{q.lower()}%"
            conditions.append(
                "(LOWER(g.title) LIKE ? OR LOWER(g.description) LIKE ? "
                f"OR LOWER({_SOURCE_EXPR}) LIKE ?)"
            )
            params += [like, like, like]
        if has_deadline:
            conditions.append("g.deadline != ''")
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=int(days))).isoformat()
            conditions.append("g.found_at >= ?")
            params.append(cutoff)
        # Release-date range (on the effective release date).
        if released_after:
            conditions.append(f"{_RELEASE_EXPR} >= ?")
            params.append(released_after)
        if released_before:
            conditions.append(f"{_RELEASE_EXPR} <= ?")
            params.append(released_before)
        # Hide programs whose (known) deadline is in the past.
        if hide_expired:
            today = datetime.now().strftime("%Y-%m-%d")
            conditions.append("NOT (g.deadline_date != '' AND g.deadline_date < ?)")
            params.append(today)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = conn.execute(
            f"SELECT COUNT(*) FROM grants g JOIN sites s ON g.site_id = s.id{where}",
            params,
        ).fetchone()[0]

        order = _SORT_MAP.get(sort, _SORT_MAP["date-desc"])
        rows = conn.execute(
            f"""SELECT g.*, {_SOURCE_EXPR} AS site_name, {_CATEGORY_EXPR} AS site_category
                FROM grants g JOIN sites s ON g.site_id = s.id{where}
                ORDER BY {order} LIMIT ? OFFSET ?""",
            params + [int(limit), int(offset)],
        ).fetchall()
        return [dict(r) for r in rows], total
    finally:
        conn.close()


def category_counts(status=None):
    """Count grants per site category (optionally filtered by status)."""
    conn = get_connection()
    try:
        sql = (f"SELECT {_CATEGORY_EXPR} AS cat, COUNT(*) AS n "
               "FROM grants g JOIN sites s ON g.site_id = s.id")
        params = []
        if status:
            sql += " WHERE g.status = ?"
            params.append(status)
        sql += " GROUP BY cat"
        return {(row["cat"] or "Diger"): row["n"] for row in conn.execute(sql, params).fetchall()}
    finally:
        conn.close()


def mark_grant_read(grant_id):
    conn = get_connection()
    try:
        conn.execute("UPDATE grants SET is_read = 1, is_new = 0 WHERE id = ?", (grant_id,))
        conn.commit()
    finally:
        conn.close()


def _now():
    """Timestamp for the export watermark. UTC ISO-8601, matching sent_at and
    last_seen_at so the MAX() backfill in init_db compares like with like."""
    return datetime.utcnow().isoformat()


def set_grant_status(grant_id, status):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE grants SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), grant_id),
        )
        conn.commit()
    finally:
        conn.close()


def reset_content_hashes():
    """Clear every site's stored content_hash so the next scan re-parses all sites
    (bypasses the 'unchanged' skip). Used when the scan date range changes."""
    conn = get_connection()
    try:
        conn.execute("UPDATE sites SET content_hash = ''")
        conn.commit()
    finally:
        conn.close()


def clear_pending_grants():
    """Delete un-acted-on announcements to rebuild the list for a new date range.
    Spares Sent ones (a record of what went out) and soft-deleted ones (so they
    stay hidden and aren't re-added by the next scan). Returns the number removed.

    Manual entries are spared too: no scan can rediscover something a person
    typed in, so deleting one here would destroy it permanently — and a re-scan
    for a different date range is not a request to throw away hand-entered work.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM grants WHERE (sent_at IS NULL OR sent_at = '')"
            " AND is_deleted = 0 AND IFNULL(is_manual, 0) = 0"
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def mark_grants_sent(grant_ids):
    """Stamp sent_at on the given ids (only if not already sent). Returns the
    number newly stamped."""
    if not grant_ids:
        return 0
    conn = get_connection()
    try:
        now = datetime.utcnow().isoformat()
        placeholders = ",".join("?" * len(grant_ids))
        cur = conn.execute(
            f"UPDATE grants SET sent_at = ?, updated_at = ? WHERE id IN ({placeholders}) "
            f"AND (sent_at IS NULL OR sent_at = '')",
            [now, now] + list(grant_ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def soft_delete_grants(grant_ids):
    """Hide the given ids (is_deleted=1). The rows stay so URL dedup keeps a
    future scan from re-adding them. Returns the number hidden."""
    if not grant_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(grant_ids))
        cur = conn.execute(
            f"UPDATE grants SET is_deleted = 1, updated_at = ? WHERE id IN ({placeholders})",
            [_now()] + list(grant_ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def restore_deleted(grant_ids):
    """Un-hide soft-deleted ids (is_deleted=0). Returns the number restored."""
    if not grant_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(grant_ids))
        cur = conn.execute(
            f"UPDATE grants SET is_deleted = 0, updated_at = ? WHERE id IN ({placeholders})",
            [_now()] + list(grant_ids),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_grants(grant_ids):
    """Hard-delete the given grant ids. Returns the number removed."""
    if not grant_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(grant_ids))
        cur = conn.execute(f"DELETE FROM grants WHERE id IN ({placeholders})", list(grant_ids))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def tombstone_and_delete(grant_ids, reason=""):
    """Record the given grants as evaluated-and-rejected, then delete their rows.

    Deleting alone is self-defeating: add_grant's dedup works by finding an
    existing row, so a deleted link looks brand new on the next scan and gets
    re-inserted and its detail page re-fetched. On an hourly interval that
    re-work is the bulk of every scan. The tombstone preserves the verdict after
    the row is gone.

    Verdicts expire after config.TOMBSTONE_TTL_DAYS, so a link already carrying a
    tombstone has its timestamp REFRESHED here rather than ignored — otherwise a
    link that expired once would be re-evaluated on every subsequent scan, which
    is the churn this whole mechanism exists to stop.

    A deliberate back-dated re-scan clears verdicts outright (see
    clear_tombstones, wired to the dashboard's "full" scan).

    Returns (recorded, deleted), where recorded counts new + refreshed verdicts.
    """
    if not grant_ids:
        return 0, 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(grant_ids))
        rows = conn.execute(
            f"SELECT site_id, normalized_url, normalized_title, published_date "
            f"FROM grants WHERE id IN ({placeholders})",
            list(grant_ids),
        ).fetchall()
        # A row with no normalized_url has no usable key, and UNIQUE(site_id,
        # normalized_url) would collapse every such row into a single tombstone
        # that then suppresses unrelated links. Leave those untombstoned.
        keep = [r for r in rows if (r["normalized_url"] or "")]
        recorded = 0
        if keep:
            cur = conn.executemany(
                "INSERT INTO seen_links "
                "(site_id, normalized_url, normalized_title, published_date, reason) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(site_id, normalized_url) DO UPDATE SET "
                "  tombstoned_at = datetime('now'), "
                "  normalized_title = excluded.normalized_title, "
                "  published_date = excluded.published_date, "
                "  reason = excluded.reason",
                [(r["site_id"], r["normalized_url"], r["normalized_title"] or "",
                  r["published_date"] or "", reason) for r in keep],
            )
            recorded = max(cur.rowcount, 0)
        deleted = conn.execute(
            f"DELETE FROM grants WHERE id IN ({placeholders})", list(grant_ids)
        ).rowcount
        conn.commit()
        return recorded, deleted
    finally:
        conn.close()


def export_sent_grants(since="", limit=200):
    """Programs that have been sent out, for the read-only export API.

    'Sent' is the curated set, not status='accepted': the accept/reject split was
    replaced by the flat sent_at/is_deleted model (see the init_db comment), and
    status='accepted' now only holds a handful of legacy rows. sent_at is what the
    dashboard's Send action stamps, so it is what a consumer should mirror.

    Ordered by the (updated_at, id) watermark and filtered with a STRICT '>' so a
    consumer can pass the last row's cursor back and resume exactly where it left
    off. id breaks ties because a bulk send stamps one identical timestamp across
    every row in the batch — ordering by updated_at alone would let rows sharing
    that timestamp be split across pages non-deterministically and silently skip
    some. Callers get is_deleted rather than a filtered list so a program you
    later withdraw propagates as a state change instead of vanishing.
    """
    conn = get_connection()
    try:
        limit = max(1, min(int(limit or 200), 1000))
        params = []
        where = ["IFNULL(g.sent_at, '') != ''"]
        if since:
            # Compare on the same (updated_at, id) tuple the ordering uses.
            where.append("(IFNULL(g.updated_at, '') > ? OR "
                         "(IFNULL(g.updated_at, '') = ? AND g.id > ?))")
            cursor_ts, _, cursor_id = str(since).partition("|")
            params += [cursor_ts, cursor_ts, int(cursor_id or 0)]
        rows = conn.execute(
            f"""SELECT g.id, g.title, g.url, g.normalized_url, g.description,
                       g.detailed_description, g.published_date, g.deadline,
                       g.deadline_date, g.funding_amount, g.eligibility,
                       g.application_url, g.contact_info, g.item_type,
                       g.keywords_matched, g.sent_at, g.is_deleted, g.status,
                       g.found_at, g.updated_at, g.is_manual,
                       {_SOURCE_EXPR} AS site_name, {_CATEGORY_EXPR} AS site_category
                  FROM grants g JOIN sites s ON g.site_id = s.id
                 WHERE {' AND '.join(where)}
              ORDER BY IFNULL(g.updated_at, '') ASC, g.id ASC
                 LIMIT ?""",
            params + [limit + 1],          # +1 probes for a further page
        ).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["keywords_matched"] = json.loads(d.get("keywords_matched") or "[]")
            except (ValueError, TypeError):
                d["keywords_matched"] = []
            d["is_deleted"] = bool(d["is_deleted"])
            # Lets a consumer tell a hand-entered program from a scraped one.
            d["is_manual"] = bool(d["is_manual"])
            # Stable identity: grant ids are NOT stable — the deferred-probe path
            # hard-deletes rows and a later scan re-inserts them with a fresh id.
            # normalized_url survives that, so consumers should key on it.
            d["key"] = d.pop("normalized_url")
            items.append(d)
        next_cursor = (
            f"{rows[-1]['updated_at'] or ''}|{rows[-1]['id']}" if rows else (since or "")
        )
        return {"items": items, "next_cursor": next_cursor, "has_more": has_more}
    finally:
        conn.close()


def clear_tombstones():
    """Forget every rejected-link verdict so the next scan re-evaluates from
    scratch. Wired to the "full" re-scan, which exists to rebuild the list after
    the date range changes — exactly the case where old verdicts are wrong."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM seen_links")
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def set_published_date(grant_id, published_date):
    """Force-set published_date to an exact value (may be '' to clear a bad date).
    Unlike update_grant_details, this overwrites even when the new value is empty."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE grants SET published_date = ?, updated_at = ? WHERE id = ?",
            (published_date or "", _now(), grant_id),
        )
        conn.commit()
    finally:
        conn.close()


def bulk_set_status(grant_ids, status):
    if not grant_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(grant_ids))
        conn.execute(
            f"UPDATE grants SET status = ?, updated_at = ? WHERE id IN ({placeholders})",
            [status, _now()] + list(grant_ids),
        )
        conn.commit()
    finally:
        conn.close()


def mark_all_read():
    conn = get_connection()
    try:
        conn.execute("UPDATE grants SET is_read = 1, is_new = 0 WHERE is_read = 0")
        conn.commit()
    finally:
        conn.close()


def get_stats():
    conn = get_connection()
    try:
        stats = {}
        stats["total_sites"] = conn.execute("SELECT COUNT(*) FROM sites WHERE is_active = 1").fetchone()[0]
        stats["total_grants"] = conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
        stats["new_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE is_new = 1").fetchone()[0]
        stats["unread_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE is_read = 0").fetchone()[0]
        # Flat-list model: active = visible announcements, sent = already emailed,
        # deleted = soft-hidden. (pending_grants kept as an alias for active so any
        # older callers / the scan-status payload keep working.)
        stats["active_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE is_deleted = 0").fetchone()[0]
        stats["sent_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE sent_at != '' AND is_deleted = 0").fetchone()[0]
        stats["deleted_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE is_deleted = 1").fetchone()[0]
        stats["pending_grants"] = stats["active_grants"]
        stats["sites_with_errors"] = conn.execute("SELECT COUNT(*) FROM sites WHERE last_status = 'error' AND is_active = 1").fetchone()[0]
        # Links already judged out-of-range; each one is a detail fetch a scan
        # no longer has to repeat. Growth here is the fix working.
        stats["tombstoned_links"] = conn.execute("SELECT COUNT(*) FROM seen_links").fetchone()[0]

        last_scan = conn.execute("SELECT * FROM scan_logs ORDER BY started_at DESC LIMIT 1").fetchone()
        stats["last_scan"] = dict(last_scan) if last_scan else None
        return stats
    finally:
        conn.close()


def create_scan_log():
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO scan_logs (started_at) VALUES (?)",
            (datetime.utcnow().isoformat(),),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def update_scan_log(scan_id, sites_scanned, new_grants_found, errors, status="completed"):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE scan_logs SET finished_at = ?, sites_scanned = ?, new_grants_found = ?, errors = ?, status = ? WHERE id = ?",
            (datetime.utcnow().isoformat(), sites_scanned, new_grants_found, errors, status, scan_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_grant_by_id(grant_id):
    conn = get_connection()
    try:
        row = conn.execute(
            f"""SELECT g.*, {_SOURCE_EXPR} as site_name, {_CATEGORY_EXPR} as site_category
               FROM grants g JOIN sites s ON g.site_id = s.id
               WHERE g.id = ?""",
            (grant_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_grant_details(grant_id, details):
    conn = get_connection()
    try:
        published_date = details.get("published_date", "")
        deadline = details.get("deadline", "")
        deadline_date = extract_date(deadline)
        conn.execute(
            """UPDATE grants SET
                detailed_description = ?,
                deadline = ?,
                deadline_date = CASE WHEN ? != '' THEN ? ELSE deadline_date END,
                funding_amount = ?,
                eligibility = ?,
                application_url = ?,
                contact_info = ?,
                published_date = CASE WHEN ? != '' THEN ? ELSE published_date END,
                details_scraped = 1,
                details_scraped_at = ?,
                updated_at = ?
               WHERE id = ?""",
            (
                details.get("detailed_description", ""),
                deadline,
                deadline_date, deadline_date,
                details.get("funding_amount", ""),
                details.get("eligibility", ""),
                details.get("application_url", ""),
                details.get("contact_info", ""),
                published_date, published_date,
                datetime.utcnow().isoformat(),
                _now(),
                grant_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_scan_history(limit=20):
    conn = get_connection()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM scan_logs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()]
    finally:
        conn.close()
