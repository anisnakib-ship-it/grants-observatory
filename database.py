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

        CREATE INDEX IF NOT EXISTS idx_grants_found_at ON grants(found_at DESC);
        CREATE INDEX IF NOT EXISTS idx_grants_is_new ON grants(is_new);
        CREATE INDEX IF NOT EXISTS idx_grants_site_id ON grants(site_id);
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
    }
    newly_added = [col for col in new_cols if col not in existing_cols]
    for col in newly_added:
        cursor.execute(f"ALTER TABLE grants ADD COLUMN {col} {new_cols[col]}")

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


def get_all_sites(active_only=True):
    conn = get_connection()
    try:
        query = "SELECT * FROM sites"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY name"
        return [dict(row) for row in conn.execute(query).fetchall()]
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

        cursor = conn.execute(
            """INSERT OR IGNORE INTO grants
                 (site_id, title, url, description, keywords_matched,
                  normalized_url, normalized_title, last_seen_at, published_date, item_type)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                site_id, title, url, description,
                json.dumps(keywords_matched or [], ensure_ascii=False),
                norm_url, norm_title, now, published_date, item_type,
            ),
        )
        conn.commit()
        # Return the new grant's id if inserted, else None (duplicate ignored).
        return cursor.lastrowid if cursor.rowcount > 0 else None
    finally:
        conn.close()


def get_recent_grants(limit=100, unread_only=False, status_filter=None):
    conn = get_connection()
    try:
        query = """
            SELECT g.*, s.name as site_name, s.category as site_category
            FROM grants g
            JOIN sites s ON g.site_id = s.id
        """
        conditions = []
        params = []
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


_SORT_MAP = {
    "date-desc": "g.found_at DESC",
    "date-asc": "g.found_at ASC",
    "source": "s.name ASC, g.found_at DESC",
    "category": "s.category ASC, g.found_at DESC",
    "published-desc": "g.published_date DESC, g.found_at DESC",
    # Soonest upcoming deadline first; unknown deadlines sorted last.
    "deadline": "CASE WHEN g.deadline_date = '' THEN 1 ELSE 0 END, g.deadline_date ASC",
}


# A program's effective "release date": its parsed publish date, or the date we
# first found it when no publish date could be detected.
_RELEASE_EXPR = "COALESCE(NULLIF(g.published_date, ''), substr(g.found_at, 1, 10))"


def query_grants(status=None, category=None, source=None, q=None, days=None,
                 has_deadline=False, released_after=None, released_before=None,
                 hide_expired=False, sort="date-desc", limit=30, offset=0):
    """Filter the whole grants archive in SQL. Returns (rows, total_matching)."""
    conn = get_connection()
    try:
        conditions = []
        params = []
        if status:
            conditions.append("g.status = ?")
            params.append(status)
        if category:
            conditions.append("s.category = ?")
            params.append(category)
        if source:
            conditions.append("s.name = ?")
            params.append(source)
        if q:
            like = f"%{q.lower()}%"
            conditions.append(
                "(LOWER(g.title) LIKE ? OR LOWER(g.description) LIKE ? OR LOWER(s.name) LIKE ?)"
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
            f"""SELECT g.*, s.name AS site_name, s.category AS site_category
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
        sql = "SELECT s.category AS cat, COUNT(*) AS n FROM grants g JOIN sites s ON g.site_id = s.id"
        params = []
        if status:
            sql += " WHERE g.status = ?"
            params.append(status)
        sql += " GROUP BY s.category"
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


def set_grant_status(grant_id, status):
    conn = get_connection()
    try:
        conn.execute("UPDATE grants SET status = ? WHERE id = ?", (status, grant_id))
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
    """Delete pending programs (keeps accepted/rejected triage). Used to rebuild
    the pending set for a new date range. Returns the number removed."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM grants WHERE status = 'pending'")
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


def set_published_date(grant_id, published_date):
    """Force-set published_date to an exact value (may be '' to clear a bad date).
    Unlike update_grant_details, this overwrites even when the new value is empty."""
    conn = get_connection()
    try:
        conn.execute("UPDATE grants SET published_date = ? WHERE id = ?", (published_date or "", grant_id))
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
            f"UPDATE grants SET status = ? WHERE id IN ({placeholders})",
            [status] + list(grant_ids),
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
        stats["pending_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE status = 'pending'").fetchone()[0]
        stats["accepted_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE status = 'accepted'").fetchone()[0]
        stats["rejected_grants"] = conn.execute("SELECT COUNT(*) FROM grants WHERE status = 'rejected'").fetchone()[0]
        stats["sites_with_errors"] = conn.execute("SELECT COUNT(*) FROM sites WHERE last_status = 'error' AND is_active = 1").fetchone()[0]

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
            """SELECT g.*, s.name as site_name, s.category as site_category
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
                details_scraped_at = ?
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
