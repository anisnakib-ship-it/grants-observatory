import logging
import threading
import time
from datetime import datetime, date

from flask import Flask, render_template, jsonify, request, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler

import os
import json as json_module
import config
import database
from scraper import run_scan, scrape_grant_details
from notifier import notify_new_grants
from seed_sites import seed_from_excel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("grants_monitor.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.jinja_env.filters["from_json"] = lambda s: json_module.loads(s) if isinstance(s, str) else s

# Category -> stable ASCII slug for the color-coding CSS class, so Turkish
# category names (e.g. "Kalkınma Ajansı") still resolve to ".cat-kalkinma-ajansi".
_CAT_TR = str.maketrans({
    "ç": "c", "Ç": "c", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ı": "i", "İ": "i", "â": "a",
})


def _cat_slug(cat):
    folded = (cat or "").translate(_CAT_TR).lower()
    parts = "".join(ch if ch.isalnum() else "-" for ch in folded).split("-")
    return "-".join(p for p in parts if p)


app.jinja_env.filters["catslug"] = _cat_slug

access_logger = logging.getLogger("access")


def _client_ip(environ):
    """Best-effort real client IP for logging.

    Behind Cloudflare Tunnel every request reaches the app from 127.0.0.1, so
    REMOTE_ADDR alone would make every log line identical. CF-Connecting-IP is
    set by cloudflared and carries the real client; REMOTE_ADDR covers the
    direct-LAN case we have today.

    X-Forwarded-For is checked only as a fallback for other servers: waitress
    defaults to clear_untrusted_proxy_headers=True with no trusted_proxy, so it
    STRIPS every X-Forwarded-* header before the app sees it. That is a good
    default — it stops a LAN client forging one — but it means this branch never
    fires under waitress. If nginx is ever put in front, pass trusted_proxy and
    trusted_proxy_headers to serve() rather than relying on this line.

    Logging only. These values are informational; never use them to authorize.
    """
    for key in ("HTTP_CF_CONNECTING_IP", "HTTP_X_FORWARDED_FOR"):
        value = environ.get(key, "")
        if value:
            # X-Forwarded-For is a chain; the original client is leftmost.
            return value.split(",")[0].strip()
    return environ.get("REMOTE_ADDR", "-")


class AccessLog:
    """Log one line per request: client, method, path, status, duration.

    Waitress — unlike Flask's dev server — does not log requests, so this
    restores the per-request visibility we had from werkzeug. In debug mode
    werkzeug still logs as well, so lines appear twice there; that only affects
    local development.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        started = time.monotonic()
        captured = {}

        def _start_response(status, headers, exc_info=None):
            captured["status"] = status
            return start_response(status, headers, exc_info)

        try:
            return self.wsgi_app(environ, _start_response)
        finally:
            path = environ.get("PATH_INFO", "")
            if environ.get("QUERY_STRING"):
                path = f"{path}?{environ['QUERY_STRING']}"
            access_logger.info(
                "%s %s %s %s %.0fms",
                _client_ip(environ),
                environ.get("REQUEST_METHOD", "-"),
                path,
                captured.get("status", "-").split(" ")[0],
                (time.monotonic() - started) * 1000,
            )


app.wsgi_app = AccessLog(app.wsgi_app)

scan_lock = threading.Lock()
scan_in_progress = False
scan_started_at = 0.0  # monotonic timestamp of the current scan's claim
scheduler = None

# A scan that has held the slot longer than this is presumed dead (hung socket,
# SQLite lock, crashed thread that never hit `finally: _end_scan()`). Set well
# above a legitimate full scan (~6 min) so it never pre-empts a real one. This
# self-heals the in-memory flag so a hung scan can't wedge scanning until a
# process restart.
STALE_SCAN_SECONDS = 20 * 60


def _begin_scan():
    """Atomically claim the scan slot. Returns True if this caller may scan.
    Reclaims the slot from a previous scan that has run past STALE_SCAN_SECONDS
    (presumed dead) so a hung scan can't block scanning indefinitely."""
    global scan_in_progress, scan_started_at
    with scan_lock:
        if scan_in_progress:
            held = time.monotonic() - scan_started_at
            if held < STALE_SCAN_SECONDS:
                return False
            logger.warning(
                f"Reclaiming scan slot from a scan held {held / 60:.1f} min "
                f"(> {STALE_SCAN_SECONDS // 60} min) — presumed dead."
            )
        scan_in_progress = True
        scan_started_at = time.monotonic()
        return True


def _end_scan():
    global scan_in_progress
    with scan_lock:
        scan_in_progress = False


def _do_scan():
    try:
        result = run_scan()
        notify_new_grants(result)
    except Exception as e:
        logger.error(f"Scan failed: {e}")
    finally:
        _end_scan()


def scheduled_scan():
    if not _begin_scan():
        logger.info("Scan already in progress, skipping.")
        return
    _do_scan()


# --- Routes ---

@app.route("/")
def dashboard():
    stats = database.get_stats()
    # First page of the active list and the deleted (recovery) list; expired hidden
    # by default to match the UI toggle. The rest is loaded via /api/grants/cards.
    active_grants, active_total = database.query_grants(deleted=False, hide_expired=True, limit=GRANTS_PAGE_SIZE)
    deleted_grants, deleted_total = database.query_grants(deleted=True, hide_expired=True, limit=GRANTS_PAGE_SIZE)
    sites = database.get_all_sites(active_only=False)
    scan_history = database.get_scan_history(limit=10)
    # Category distribution for the donut chart (active announcements).
    cat_counts = database.category_counts()
    site_cat_counts = {}
    for s in sites:
        cat = s.get("category", "Diger")
        site_cat_counts[cat] = site_cat_counts.get(cat, 0) + 1
    return render_template(
        "dashboard.html",
        stats=stats,
        active_grants=active_grants,
        deleted_grants=deleted_grants,
        sites=sites,
        scan_history=scan_history,
        scan_in_progress=scan_in_progress,
        config=config,
        cat_counts=cat_counts,
        site_cat_counts=site_cat_counts,
        active_total=active_total,
        deleted_total=deleted_total,
        today=date.today().isoformat(),
    )


@app.route("/api/scan", methods=["POST"])
def trigger_scan():
    if not _begin_scan():
        return jsonify({"status": "already_running"}), 409
    # A "full" scan (used when the date range changes) re-parses every site and
    # rebuilds the pending set for the new range: reset content hashes so the
    # unchanged-skip doesn't short-circuit, and clear existing pending programs.
    full = bool((request.json or {}).get("full")) if request.is_json else False
    if full:
        database.reset_content_hashes()
        database.clear_pending_grants()
    threading.Thread(target=_do_scan, daemon=True).start()
    return jsonify({"status": "started", "full": full})


@app.route("/api/scan-status")
def scan_status():
    stats = database.get_stats()
    return jsonify({
        "in_progress": scan_in_progress,
        "stats": stats,
    })


@app.route("/api/grants")
def api_grants():
    limit = request.args.get("limit", 100, type=int)
    unread_only = request.args.get("unread", "false") == "true"
    grants = database.get_recent_grants(limit=limit, unread_only=unread_only)
    return jsonify(grants)


GRANTS_PAGE_SIZE = 30


@app.route("/api/grants/cards")
def api_grant_cards():
    """Server-side filtered + paginated grant cards (rendered HTML).
    Lets the dashboard search/filter the ENTIRE archive, not just the first page.
    view ∈ {active, deleted}: active = the announcements list; deleted = recovery."""
    view = request.args.get("view", "active")
    offset = request.args.get("offset", 0, type=int)
    hide_expired = request.args.get("hide_expired", "true") == "true"

    rows, total = database.query_grants(
        deleted=(view == "deleted"),
        category=request.args.get("category") or None,
        source=request.args.get("source") or None,
        q=request.args.get("q") or None,
        released_after=request.args.get("released_after") or None,
        released_before=request.args.get("released_before") or None,
        hide_expired=hide_expired,
        sort=request.args.get("sort", "date-desc"),
        limit=GRANTS_PAGE_SIZE,
        offset=offset,
    )
    html = render_template(
        "_grant_cards.html",
        grants=rows,
        card_mode=("deleted" if view == "deleted" else "active"),
        start_idx=offset,
        today=date.today().isoformat(),
    )
    return jsonify({
        "html": html,
        "total": total,
        "returned": len(rows),
        "offset": offset,
        "has_more": offset + len(rows) < total,
    })


@app.route("/api/grants/<int:grant_id>/read", methods=["POST"])
def api_mark_read(grant_id):
    database.mark_grant_read(grant_id)
    return jsonify({"status": "ok"})


@app.route("/api/grants/<int:grant_id>/delete", methods=["POST"])
def api_delete_grant(grant_id):
    """Soft-delete: hide the announcement but keep the row (so a future scan
    won't re-add it). Recoverable from the Deleted view."""
    database.soft_delete_grants([grant_id])
    return jsonify({"status": "ok"})


@app.route("/api/grants/<int:grant_id>/restore", methods=["POST"])
def api_restore_grant(grant_id):
    """Un-hide a soft-deleted announcement, returning it to the active list."""
    database.restore_deleted([grant_id])
    return jsonify({"status": "ok"})


@app.route("/api/grants/read-all", methods=["POST"])
def api_mark_all_read():
    database.mark_all_read()
    return jsonify({"status": "ok"})


@app.route("/api/grants/bulk", methods=["POST"])
def api_bulk_action():
    """Bulk delete or restore selected announcements. action ∈ {delete, restore}."""
    data = request.json or {}
    action = data.get("action", "")
    ids = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify({"status": "error", "error": "No grants specified"}), 400
    if action == "delete":
        n = database.soft_delete_grants(ids)
    elif action == "restore":
        n = database.restore_deleted(ids)
    else:
        return jsonify({"status": "error", "error": "Invalid action"}), 400
    return jsonify({"status": "ok", "updated": n})


@app.route("/api/grants/send", methods=["POST"])
def api_send_grants():
    """Email the selected announcements to the configured recipients, then stamp
    them Sent. Already-sent ids are skipped so nothing goes out twice."""
    from notifier import build_programs_email, send_email_html
    if not config.EMAIL_ENABLED:
        return jsonify({"status": "error", "error": "Email is disabled in settings"}), 400
    if not config.EMAIL_RECIPIENTS:
        return jsonify({"status": "error", "error": "No recipients configured"}), 400
    data = request.json or {}
    ids = [int(i) for i in data.get("ids", []) if str(i).isdigit()]
    if not ids:
        return jsonify({"status": "error", "error": "No announcements selected"}), 400

    # Only send ones not already sent; fetch their full rows for the email body.
    rows = [database.get_grant_by_id(i) for i in ids]
    to_send = [r for r in rows if r and not (r.get("sent_at") or "")]
    if not to_send:
        return jsonify({"status": "error", "error": "All selected are already sent"}), 400

    html = build_programs_email(to_send, heading="Yeni Duyurular / New Announcements")
    ok, error = send_email_html(config.EMAIL_SUBJECT, html)
    if not ok:
        return jsonify({"status": "error", "error": error}), 500
    n = database.mark_grants_sent([r["id"] for r in to_send])
    return jsonify({"status": "ok", "sent": n, "recipients": config.EMAIL_RECIPIENTS})


@app.route("/api/grants/<int:grant_id>/details", methods=["POST"])
def api_fetch_grant_details(grant_id):
    grant = database.get_grant_by_id(grant_id)
    if not grant:
        return jsonify({"status": "error", "error": "Grant not found"}), 404

    result = scrape_grant_details(grant["url"])
    if result["status"] == "ok":
        database.update_grant_details(grant_id, result["details"])
        # Return the updated grant
        updated = database.get_grant_by_id(grant_id)
        return jsonify({"status": "ok", "grant": updated})
    else:
        return jsonify({"status": "error", "error": result.get("error", "Unknown error")}), 500


@app.route("/api/grants/<int:grant_id>")
def api_get_grant(grant_id):
    grant = database.get_grant_by_id(grant_id)
    if not grant:
        return jsonify({"status": "error", "error": "Grant not found"}), 404
    return jsonify(grant)


@app.route("/api/sites")
def api_sites():
    sites = database.get_all_sites(active_only=False)
    return jsonify(sites)


@app.route("/api/sites/<int:site_id>/toggle", methods=["POST"])
def api_toggle_site(site_id):
    conn = database.get_connection()
    try:
        current = conn.execute("SELECT is_active FROM sites WHERE id = ?", (site_id,)).fetchone()
        if current:
            new_val = 0 if current["is_active"] else 1
            conn.execute("UPDATE sites SET is_active = ? WHERE id = ?", (new_val, site_id))
            conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/sites/<int:site_id>/selector", methods=["POST"])
def api_set_selector(site_id):
    data = request.json or {}
    selector = (data.get("css_selector") or "").strip()
    conn = database.get_connection()
    try:
        conn.execute("UPDATE sites SET css_selector = ? WHERE id = ?", (selector, site_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify({
        "scan_interval_hours": config.SCAN_INTERVAL_HOURS,
        "email_enabled": config.EMAIL_ENABLED,
        "email_provider": config.EMAIL_PROVIDER,
        "email_sender": config.EMAIL_SENDER,
        "email_from_name": config.EMAIL_FROM_NAME,
        "email_subject": config.EMAIL_SUBJECT,
        "email_smtp_server": config.EMAIL_SMTP_SERVER,
        "email_smtp_port": config.EMAIL_SMTP_PORT,
        "scan_range_start": config.SCAN_RANGE_START,
        "scan_range_end": config.SCAN_RANGE_END,
        "email_recipients": config.EMAIL_RECIPIENTS,
        "scan_alert_enabled": config.SCAN_ALERT_ENABLED,
        "scan_alert_recipients": config.SCAN_ALERT_RECIPIENTS,
        # Never expose the key itself; just report whether one is configured.
        "sendgrid_key_set": bool(config.SENDGRID_API_KEY),
        "desktop_notifications": config.DESKTOP_NOTIFICATIONS_ENABLED,
    })


@app.route("/api/test-email", methods=["POST"])
def api_test_email():
    from notifier import send_email_html
    data = request.json or {}
    # Allow overriding the recipient for a one-off test, else use configured list.
    to = data.get("to")
    recipients = [to] if to else config.EMAIL_RECIPIENTS
    if not recipients:
        return jsonify({"status": "error", "error": "No recipient provided"}), 400
    html = (
        "<html><body style='font-family:Arial,sans-serif;'>"
        "<h2>Grants Monitor — test email</h2>"
        "<p>If you can read this, SendGrid delivery is working.</p>"
        f"<p style='color:#666;'>Provider: {config.EMAIL_PROVIDER} · From: {config.EMAIL_SENDER}</p>"
        "</body></html>"
    )
    ok, error = send_email_html("[Grants Monitor] Test email", html, recipients)
    if ok:
        return jsonify({"status": "ok", "sent_to": recipients})
    return jsonify({"status": "error", "error": error}), 500


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.json
    settings_path = os.path.join(config.BASE_DIR, "settings.json")
    settings = {}
    if os.path.exists(settings_path):
        with open(settings_path, encoding="utf-8") as f:
            settings = json_module.load(f)
    settings.update(data)
    with open(settings_path, "w", encoding="utf-8") as f:
        json_module.dump(settings, f, indent=2)
    # Update in-memory config
    for key, attr in [
        ("email_enabled", "EMAIL_ENABLED"),
        ("email_provider", "EMAIL_PROVIDER"),
        ("desktop_notifications", "DESKTOP_NOTIFICATIONS_ENABLED"),
        ("email_sender", "EMAIL_SENDER"),
        ("email_from_name", "EMAIL_FROM_NAME"),
        ("email_subject", "EMAIL_SUBJECT"),
        ("email_smtp_server", "EMAIL_SMTP_SERVER"),
        ("email_smtp_port", "EMAIL_SMTP_PORT"),
        ("scan_range_start", "SCAN_RANGE_START"),
        ("scan_range_end", "SCAN_RANGE_END"),
        ("email_password", "EMAIL_PASSWORD"),
        ("email_recipients", "EMAIL_RECIPIENTS"),
        ("scan_alert_enabled", "SCAN_ALERT_ENABLED"),
        ("scan_alert_recipients", "SCAN_ALERT_RECIPIENTS"),
        ("sendgrid_api_key", "SENDGRID_API_KEY"),
        ("scan_interval_hours", "SCAN_INTERVAL_HOURS"),
    ]:
        if key in data:
            setattr(config, attr, data[key])

    # Recipients may arrive as a comma/semicolon/newline-separated string.
    if isinstance(config.EMAIL_RECIPIENTS, str):
        raw = config.EMAIL_RECIPIENTS.replace(";", ",").replace("\n", ",")
        config.EMAIL_RECIPIENTS = [e.strip() for e in raw.split(",") if e.strip()]

    # Apply a changed scan interval to the running scheduler immediately.
    if "scan_interval_hours" in data and scheduler is not None:
        try:
            hours = float(config.SCAN_INTERVAL_HOURS)
            if hours > 0:
                scheduler.reschedule_job("grant_scan", trigger="interval", hours=hours)
                logger.info(f"Rescheduled scan to every {hours} hours.")
        except Exception as e:
            logger.warning(f"Could not reschedule scan job: {e}")

    logger.info("Settings saved.")
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Initialize database and seed sites. Only seed on first run (empty DB);
    # re-seeding on every boot would resurrect deleted sites and duplicate any
    # rows whose URL was manually repointed. Use seed_sites.py to (re)seed.
    database.init_db()
    if not database.get_all_sites(active_only=False):
        if os.path.exists(config.EXCEL_PATH):
            seed_from_excel(config.EXCEL_PATH)
        else:
            logger.warning(f"Excel seed file not found, skipping seed: {config.EXCEL_PATH}")
    else:
        logger.info("Sites already present; skipping Excel re-seed.")

    # Start scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scheduled_scan,
        "interval",
        hours=config.SCAN_INTERVAL_HOURS,
        id="grant_scan",
        # NOTE: do NOT pass next_run_time=None here — in APScheduler that leaves the
        # job with no scheduled run (paused forever), which silently disables auto-scan.
        # The interval trigger's default already schedules the FIRST run one interval
        # out (not immediately), which is the intended "don't scan on startup" behavior.
    )
    scheduler.start()
    logger.info(f"Scheduler started. Scanning every {config.SCAN_INTERVAL_HOURS} hours.")

    # Serve. Flask's built-in server is development tooling — single-threaded by
    # default and explicitly not meant to take real traffic — so it's used only in
    # debug mode, where its auto-reloader is the whole point. Everything else goes
    # through waitress, a production WSGI server that runs the same on Windows and
    # on the Ubuntu host (no C extensions, unlike gunicorn which is POSIX-only).
    server = "Flask dev server" if config.FLASK_DEBUG else "waitress"
    print("\n" + "=" * 60)
    print("  GRANTS MONITORING SYSTEM")
    print(f"  Dashboard: http://{config.FLASK_HOST}:{config.FLASK_PORT}")
    print(f"  Scan interval: every {config.SCAN_INTERVAL_HOURS} hours")
    print(f"  Debug mode: {config.FLASK_DEBUG}")
    print(f"  Server: {server}")
    print("=" * 60 + "\n")
    if config.FLASK_DEBUG:
        app.run(debug=True, host=config.FLASK_HOST, port=config.FLASK_PORT)
    else:
        from waitress import serve
        # The dashboard fans out several /api/grants/cards requests at once, so a
        # few threads keep it responsive. The scan runs in its own thread either
        # way and is unaffected by this pool.
        serve(app, host=config.FLASK_HOST, port=config.FLASK_PORT, threads=8)
