import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Database
DATABASE_PATH = os.path.join(BASE_DIR, "grants_monitor.db")

# Scraping
SCAN_INTERVAL_HOURS = 3
REQUEST_TIMEOUT = 30
MAX_WORKERS = 8
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Strong keywords — a single match is enough.
# Use stems (no trailing suffix) so Turkish inflections match, e.g.
# "destek program" catches programı / programları / programlarımız, and
# "destekleme program" catches "Destekleme Programı" (SOGEP etc.).
GRANT_KEYWORDS_STRONG = [
    "hibe", "destek program", "destekleme program", "mali destek", "mali yardım",
    # "teknik destek" / "mali destek" without the trailing "program" catch titles
    # where a token splits the phrase, e.g. "Teknik Destek-01 Programı".
    "teknik destek program", "teknik destek", "teklif çağrı", "açık çağrı", "son başvuru",
    "ar-ge", "arge", "teşvik", "finansman", "çağrı", "grant", "funding",
    "call for proposals", "r&d", "support programme", "innovation fund",
    # Subsidised credit / loan programmes are a funding instrument. Keep the
    # phrase specific ("kredi program", not bare "kredi") so ordinary bank-loan
    # news doesn't leak in. Catches e.g. KOSGEB "Yapay Zekâ Kredi Programı".
    "kredi program",
    # "girişimci" (entrepreneur) as a title subject is an almost-certain funding
    # signal in this domain (girişimci desteği/hibesi, startup challenges). Catches
    # e.g. "Youth Start-Up Challenge ... Genç Girişimcilere" whose title carries no
    # other strong keyword. Measured against 60 ab-ilan feed items: +1 (only the
    # intended item), 0 collateral.
    "girişimci",
]

# Weak keywords — need at least MIN_WEAK_KEYWORD_MATCHES to match.
# "duyuru"/"ilan"/"program" were removed: too generic, they matched almost any
# institutional navigation. Keep terms that imply an actual funding action.
GRANT_KEYWORDS_WEAK = [
    "destek", "başvuru", "proje", "fon",
    "araştırma", "geliştirme", "inovasyon", "müracaat",
    "research", "innovation", "burs", "ödül",
]

# Combined list for backward compat
GRANT_KEYWORDS = GRANT_KEYWORDS_STRONG + GRANT_KEYWORDS_WEAK

# Negative keywords to filter out irrelevant results (hiring, tenders, procurement).
# A single match discards the candidate, so keep these specific to avoid false drops.
NEGATIVE_KEYWORDS = [
    # Recruitment / personnel
    "personel alımı", "personel alim", "personel ilanı", "işçi alımı", "isci alimi",
    "memur alımı", "memur alim", "sözleşmeli personel", "sozlesmeli personel",
    "eleman alımı", "uzman alımı", "avukat alımı", "mühendis alımı", "muhendis alimi",
    "geçici işçi", "daimi işçi", "iş ilanı", "is ilani", "iş başvurusu",
    "görevde yükselme", "unvan değişikliği", "naklen atama", "atama sonuç",
    "sınav sonuç", "sinav sonuc", "sözlü sınav", "yazılı sınav", "mülakat",
    "stajyer alımı", "bekçi alımı", "şoför alımı",
    # Tenders / procurement
    "ihale", "ihalesi", "açık ihale", "doğrudan temin",
    "dogrudan temin", "satın alma", "satin alma", "mal alımı", "hizmet alımı",
    "hizmet alimi", "yapım işi", "yapim isi", "kiralama", "araç kiralama",
    # Closed / past-state announcements (results, payments, completed reviews)
    "ödendi", "sonuçları açıkland", "sonuclari acikland", "sonuçlandı",
    "sonuclandi", "tamamland", "tamamlanmış", "tamamlanmistir", "tamamlanmıştır",
    "süreci tamamlan", "sona erdi", "sona ermiştir", "sona ermistir",
    "askıya alın", "iptal edil",
    # Institutional / structural / service pages (org charts, report sections,
    # consultancy services) that read like grants but never are. Kept specific
    # so real funding calls (e.g. "danışmanlık desteği") are not dropped.
    "belgesi danışmanlığı", "belgesi danismanligi", "danışmanlık hizmeti",
    "danismanlik hizmeti", "organizasyon yapısı", "organizasyon yapisi",
    "organizasyon şeması", "organizasyon semasi", "strateji belgeleri",
    "faaliyet raporu", "faaliyet raporları", "faaliyet raporlari",
]

# Bare, single-word negatives that only mean "not a grant" when they are the
# SUBJECT of the item (i.e. in the title). Applied to the title ONLY, never to the
# surrounding context/description: words like "kadro"/"atama"/"memur" turn up
# incidentally in legitimate grant bodies (e.g. an eligibility clause "yönetim
# kadrosunun çoğunluğu kadın olmalı") and used to wrongly veto real calls such as
# the Akdeniz Kadın Fonu hibe programı. The specific multi-word variants
# ("memur alımı", "naklen atama", ...) stay in NEGATIVE_KEYWORDS and are still
# checked against title + context.
NEGATIVE_KEYWORDS_TITLE_ONLY = ["atama", "memur", "kadro"]

# Minimum weak-keyword matches required (a single strong keyword is enough).
# Raised to 3 so generic pages don't qualify on two common words.
MIN_WEAK_KEYWORD_MATCHES = 3

# Agency-news mode: in addition to funding opportunities, also track general
# agency activity (openings, project news, ceremonies) when the link sits in a
# news/announcement section. Items are tagged item_type='news' (vs 'funding') so
# the dashboard keeps them visually distinct and grants don't get buried.
TRACK_AGENCY_NEWS = True
NEWS_PATH_SEGMENTS = {
    "haber", "haberler", "duyuru", "duyurular", "news", "etkinlik", "etkinlikler",
    "basin", "basin-aciklamalari", "aktuel", "guncel", "guncel-haberler",
}
# News items don't need grant keywords, but still exclude job ads / procurement
# (hard negatives). Closed-state words (tamamlandı / sona erdi) are deliberately
# NOT excluded for news — openings and completed projects are what news mode wants.
NEWS_HARD_NEGATIVES = [
    "personel alımı", "personel alim", "memur alımı", "memur alim", "işçi alımı",
    "isci alimi", "iş ilanı", "is ilani", "alım ilanı", "alim ilani",
    "sınav sonuç", "sinav sonuc", "mülakat", "görevde yükselme",
    "ihale", "ihalesi", "satın alma", "satin alma", "doğrudan temin",
    "dogrudan temin", "yapım işi", "yapim isi", "hizmet alımı", "mal alımı",
    "kiralama",
]

# Auto detail-scraping: after a scan, automatically visit each newly found
# grant page and extract deadline / amount / eligibility / contact.
AUTO_SCRAPE_DETAILS = True
AUTO_SCRAPE_DETAILS_LIMIT = 50  # cap detail fetches per scan to bound load

# Ceilings on the today-filter's date probe. Every scraped candidate costs one
# detail-page fetch to read its publish date, and a single site whose layout
# changed can emit hundreds of candidates in one scan — enough to hammer that one
# host and stretch a scan past the next scheduled run.
#
# Deliberately NOT AUTO_SCRAPE_DETAILS_LIMIT. That caps enrichment of items
# already kept, where truncating costs only a missing deadline field. The probe
# decides keep-vs-drop, so a tight cap here would silently discard real programs
# (and, since they'd be recorded as rejected, bury them for TOMBSTONE_TTL_DAYS).
# Hence a far higher ceiling, sized to catch pathology rather than busy days.
#
# Links over a ceiling are DEFERRED — removed without a verdict and re-examined
# on the next scan, never tombstoned. Set either to 0 to disable that ceiling.
PROBE_LIMIT_PER_SITE = 40   # primary defence: one broken site can't flood a scan
PROBE_LIMIT_TOTAL = 400     # backstop across all sites
DETAIL_SCRAPE_WORKERS = 8  # detail/today-filter probes run in parallel; higher = faster full scans

# Deduplication: in addition to matching the normalized URL, also treat a
# program with an identical (normalized) title from the same site as already
# seen. Catches cases where a site changes a program's link between scans.
DEDUP_BY_TITLE = True

# How long a rejected link stays rejected. A scan records every out-of-range link
# in seen_links so later scans skip it instead of re-fetching its detail page —
# at an hourly interval that re-work is the bulk of a scan. But agencies do
# sometimes republish at the SAME url (a June call updated in August with a new
# round and a new date), and a permanent verdict would mean never looking again.
# Expiring after this many days keeps ~99% of the saving while restoring that
# rediscovery: a link is re-examined once a month rather than once an hour.
# Set to 0 to make verdicts permanent (maximum saving, no rediscovery).
TOMBSTONE_TTL_DAYS = 30

# How a scan decides a newly-found program is a fresh "announced today" item
# (this drives the alert email, desktop notification and the "new" count):
#   "released" -> only if its detected release date is within the window below
#   "hybrid"   -> released recently OR has no detectable release date
#   "new"      -> any program we hadn't recorded before
ANNOUNCEMENT_MODE = "hybrid"
ANNOUNCEMENT_WINDOW_DAYS = 0

# When True, a scan KEEPS only programs whose PROVABLE publish date falls inside
# the date range below, and deletes everything else (including date-less items).
# The range is inclusive. Empty start/end each default to today, so the default
# (both empty) means "today only". Dates are ISO 'YYYY-MM-DD'.
SCAN_TODAY_ONLY = True
SCAN_RANGE_START = ""  # inclusive; "" = today
SCAN_RANGE_END = ""    # inclusive; "" = today

# A single WordPress/RSS feed page holds only ~10 items, so a high-volume source
# (e.g. ab-ilan.com posts ~10/day) pushes a whole day off page 1 within hours.
# When today-only mode is on, scrape_feed walks ?paged=2,3,... until items predate
# the scan range start, capped here so a busy feed can't fetch unboundedly.
MAX_FEED_PAGES = 6

# Email settings (configure before use)
EMAIL_ENABLED = False
EMAIL_PROVIDER = "sendgrid"  # "sendgrid" | "smtp" | "gmail_api"
EMAIL_SMTP_SERVER = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587
EMAIL_SENDER = ""
EMAIL_FROM_NAME = "Grants Monitor"
# Shared subject line for outgoing program emails (scan alerts + accepted list).
EMAIL_SUBJECT = "Sun Danışmanlık | Yeni Duyuru"
EMAIL_PASSWORD = ""  # SMTP only: use app password for Gmail
EMAIL_RECIPIENTS = []

# Master switch for the automatic post-scan email alert (notify_new_grants).
# When False, a scan still runs (and desktop notifications still fire), but no
# "newly announced today" email is sent. The manual "accepted programs" email is
# unaffected. Override via settings.json ("scan_alert_enabled").
SCAN_ALERT_ENABLED = True

# Extra recipients that receive ONLY the automatic scan-result alert
# (notify_new_grants), not the manual "accepted programs" email. Kept out of the
# public repo — set via settings.json ("scan_alert_recipients") on each host.
SCAN_ALERT_RECIPIENTS = []

# Gmail API (provider "gmail_api"): OAuth2, no password. client_secret.json is the
# downloaded OAuth client; token.json is created by the one-time gmail_auth.py consent.
GMAIL_CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "client_secret.json")
GMAIL_TOKEN_FILE = os.path.join(BASE_DIR, "token.json")
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# SendGrid: read the API key from the environment first; settings.json can
# override. NEVER commit the key to source.
SENDGRID_API_KEY = os.environ.get("GRANTS_SENDGRID_API_KEY", "")

# Desktop notifications
DESKTOP_NOTIFICATIONS_ENABLED = True

# Site seed source (Excel file). Override with the GRANTS_EXCEL_PATH env var
# or "excel_path" in settings.json.
EXCEL_PATH = os.environ.get(
    "GRANTS_EXCEL_PATH",
    os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop", "Daily Check.xlsx"),
)

# --- Dashboard authentication -----------------------------------------------
# Reachable from the internet through Cloudflare Tunnel, so it must not be open.
#
# Only a PBKDF2 hash is ever stored, and it lives in settings.json (git-ignored),
# never in this file — config.py is in a public repo. Set it with:
#     python set_password.py
AUTH_ENABLED = True
AUTH_PASSWORD_HASH = ""
AUTH_SESSION_HOURS = 12

# Failed-login throttle, counted per client IP (CF-Connecting-IP behind the
# tunnel, so one abusive source can't lock out everyone else).
AUTH_MAX_ATTEMPTS = 8
AUTH_LOCKOUT_MINUTES = 15

# Send the session cookie only over HTTPS. Correct once everyone reaches the app
# at https://grants.sunandsun.com.tr — but while people still use the plain-HTTP
# LAN address, a True here means the browser silently refuses to send the cookie
# and login appears to fail for no reason. Flip it when LAN access is retired.
SESSION_COOKIE_SECURE = False

# Flask
DEFAULT_SECRET_KEY = "grants-monitor-secret-key-change-in-production"
# A signed session cookie is only as trustworthy as its key, and the default
# below is a literal published in a public repo — anyone could forge a session.
# app.py replaces it with a generated key on first run and persists that to
# settings.json. Keep the default only as a marker meaning "not yet set".
SECRET_KEY = os.environ.get("GRANTS_SECRET_KEY", DEFAULT_SECRET_KEY)
FLASK_DEBUG = os.environ.get("GRANTS_DEBUG", "false").lower() in ("1", "true", "yes")
FLASK_HOST = os.environ.get("GRANTS_HOST", "127.0.0.1")
FLASK_PORT = int(os.environ.get("GRANTS_PORT", "5000"))

# Load overrides from settings.json if it exists
import json as _json
_settings_path = os.path.join(BASE_DIR, "settings.json")
if os.path.exists(_settings_path):
    with open(_settings_path, encoding="utf-8") as _f:
        _overrides = _json.load(_f)
    SCAN_INTERVAL_HOURS = _overrides.get("scan_interval_hours", SCAN_INTERVAL_HOURS)
    EMAIL_ENABLED = _overrides.get("email_enabled", EMAIL_ENABLED)
    EMAIL_PROVIDER = _overrides.get("email_provider", EMAIL_PROVIDER)
    EMAIL_SENDER = _overrides.get("email_sender", EMAIL_SENDER)
    EMAIL_FROM_NAME = _overrides.get("email_from_name", EMAIL_FROM_NAME)
    EMAIL_SUBJECT = _overrides.get("email_subject", EMAIL_SUBJECT)
    EMAIL_SMTP_SERVER = _overrides.get("email_smtp_server", EMAIL_SMTP_SERVER)
    EMAIL_SMTP_PORT = int(_overrides.get("email_smtp_port", EMAIL_SMTP_PORT))
    EMAIL_PASSWORD = _overrides.get("email_password", EMAIL_PASSWORD)
    EMAIL_RECIPIENTS = _overrides.get("email_recipients", EMAIL_RECIPIENTS)
    SCAN_ALERT_ENABLED = _overrides.get("scan_alert_enabled", SCAN_ALERT_ENABLED)
    SCAN_ALERT_RECIPIENTS = _overrides.get("scan_alert_recipients", SCAN_ALERT_RECIPIENTS)
    SENDGRID_API_KEY = _overrides.get("sendgrid_api_key", SENDGRID_API_KEY)
    DESKTOP_NOTIFICATIONS_ENABLED = _overrides.get("desktop_notifications", DESKTOP_NOTIFICATIONS_ENABLED)
    EXCEL_PATH = _overrides.get("excel_path", EXCEL_PATH)
    AUTO_SCRAPE_DETAILS = _overrides.get("auto_scrape_details", AUTO_SCRAPE_DETAILS)
    AUTO_SCRAPE_DETAILS_LIMIT = _overrides.get("auto_scrape_details_limit", AUTO_SCRAPE_DETAILS_LIMIT)
    FLASK_DEBUG = _overrides.get("flask_debug", FLASK_DEBUG)
    ANNOUNCEMENT_MODE = _overrides.get("announcement_mode", ANNOUNCEMENT_MODE)
    ANNOUNCEMENT_WINDOW_DAYS = _overrides.get("announcement_window_days", ANNOUNCEMENT_WINDOW_DAYS)
    SCAN_RANGE_START = _overrides.get("scan_range_start", SCAN_RANGE_START)
    SCAN_RANGE_END = _overrides.get("scan_range_end", SCAN_RANGE_END)
    MAX_FEED_PAGES = int(_overrides.get("max_feed_pages", MAX_FEED_PAGES))
    TOMBSTONE_TTL_DAYS = int(_overrides.get("tombstone_ttl_days", TOMBSTONE_TTL_DAYS))
    PROBE_LIMIT_PER_SITE = int(_overrides.get("probe_limit_per_site", PROBE_LIMIT_PER_SITE))
    PROBE_LIMIT_TOTAL = int(_overrides.get("probe_limit_total", PROBE_LIMIT_TOTAL))
    AUTH_ENABLED = _overrides.get("auth_enabled", AUTH_ENABLED)
    AUTH_PASSWORD_HASH = _overrides.get("auth_password_hash", AUTH_PASSWORD_HASH)
    AUTH_SESSION_HOURS = int(_overrides.get("auth_session_hours", AUTH_SESSION_HOURS))
    AUTH_MAX_ATTEMPTS = int(_overrides.get("auth_max_attempts", AUTH_MAX_ATTEMPTS))
    AUTH_LOCKOUT_MINUTES = int(_overrides.get("auth_lockout_minutes", AUTH_LOCKOUT_MINUTES))
    SESSION_COOKIE_SECURE = _overrides.get("session_cookie_secure", SESSION_COOKIE_SECURE)
    SECRET_KEY = _overrides.get("secret_key", SECRET_KEY)


def save_settings(updates):
    """Merge `updates` into settings.json, creating it if absent, and return the
    full dict. Shared by app.py and set_password.py so the file is always written
    the same way — and so a partial write can't drop unrelated keys."""
    data = {}
    if os.path.exists(_settings_path):
        with open(_settings_path, encoding="utf-8") as f:
            data = _json.load(f)
    data.update(updates)
    with open(_settings_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2, ensure_ascii=False)
    return data
