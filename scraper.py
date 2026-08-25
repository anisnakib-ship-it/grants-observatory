import hashlib
import ipaddress
import json
import logging
import re
import socket
import ssl
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context
from bs4 import BeautifulSoup

import config
import database

logger = logging.getLogger(__name__)

# We intentionally skip TLS verification for Turkish gov sites with broken
# certificate chains; silence the resulting noise.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class LegacyTLSAdapter(HTTPAdapter):
    """Allow connecting to old gov servers that only offer weak DH/ciphers
    (e.g. cfcu.gov.tr) by lowering OpenSSL's security level."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


# Browser-like headers reduce trivial bot blocks.
_BASE_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


def normalize_url(base_url, href):
    if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return urljoin(base_url, href)


def extract_text(element):
    text = element.get_text(separator=" ", strip=True)
    # Clean non-breaking spaces and double-encoded entities (e.g. KOSGEB titles
    # rendered as literal "&nbsp").
    text = text.replace("\xa0", " ").replace("&nbsp;", " ").replace("&nbsp", " ")
    return " ".join(text.split())


# Fold Turkish-specific letters to ASCII so matching is immune to the İ/I/ı/i
# casing problem (e.g. "MÜHENDİS ALIMI".lower() != "mühendis alımı").
_TR_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "î": "i", "û": "u",
})


def _fold(s):
    return s.translate(_TR_FOLD).lower()


# Link texts that are never real grant titles.
_GENERIC_LINK_TEXTS = {
    "devamını oku", "detay", "tıklayınız", "read more", "click here",
    "daha fazla", "görüntüle", "incele", "detaylar", "detaylı bilgi",
    "tümünü gör", "tümü", "diğer", "ana sayfa", "iletişim", "hakkımızda",
}

# Social / URL-shortener hosts that are never a grant page. Embedded Twitter
# timelines and YouTube widgets otherwise leak t.co / pic.twitter.com / video
# links into the results (e.g. Zafer's homepage tweet feed).
_SOCIAL_HOSTS = {
    "t.co", "twitter.com", "x.com", "pic.twitter.com",
    "facebook.com", "fb.com", "instagram.com",
    "youtube.com", "youtu.be", "linkedin.com",
}

# Standing / landing pages: a program SECTION index ("/destekler", "/koop-des",
# "/acik-destek-programlari", "/destek-programlari-arsivi") rather than a dated
# announcement. These carry no date and recur forever, so they shouldn't count as
# a "today" program. The signal is the LAST URL path segment being a plural
# section slug; real announcements have a deeper, specific slug
# (".../2026-yili-...-teknik-destek-programi", singular) which won't match.
_LANDING_SEG_RE = re.compile(
    r"^("
    r"destekler|"
    r"destek-programlari(-arsivi)?|"
    r"(.+-)?acik-destek-programlari|"
    r"diger-kurumlarin-destek-programlari|"
    r"hibe-programlari|"
    r"tum-destek-programlari|"
    r"programlarimiz|"
    r"koop-des"
    r")$"
)


def _is_landing_page(url):
    """True if the URL's last path segment is a program-section landing slug."""
    path = urlparse(url or "").path.rstrip("/")
    last = path.rsplit("/", 1)[-1].lower() if path else ""
    return bool(last) and bool(_LANDING_SEG_RE.match(last))


def _is_program_catalog(url):
    """True for a standing programme page — an institution's permanent catalogue
    entry for a support programme rather than a dated announcement about one.

    These carry no publish date because there is nothing to date; the programme
    simply exists. See config.PROGRAM_CATALOG_PATH_MARKERS."""
    if not url:
        return False
    lowered = url.lower()
    return any(m in lowered for m in getattr(config, "PROGRAM_CATALOG_PATH_MARKERS", []))


def _is_news_article(url):
    """True if the URL is a specific article inside a news/announcement section
    (e.g. '/haber/<slug>/<id>'), i.e. a real story rather than the section index."""
    segs = [s for s in urlparse(url or "").path.split("/") if s]
    for i, seg in enumerate(segs):
        if seg.lower() in config.NEWS_PATH_SEGMENTS and i < len(segs) - 1:
            return True   # a news segment followed by a specific article slug
    return False


def _has_news_hard_negative(text):
    """News mode still rejects job ads and procurement notices."""
    folded = _fold(text)
    return any(_fold(nk) in folded for nk in config.NEWS_HARD_NEGATIVES)

# Tag names + CSS classes that are navigation/chrome rather than content.
_NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript", "form"]
_NOISE_SELECTORS = [
    ".menu", "#menu", ".nav", "#nav", ".navbar", ".navigation", ".main-menu",
    ".sidebar", "#sidebar", ".side-menu", ".breadcrumb", ".breadcrumbs",
    ".footer", "#footer", ".header", "#header", ".dropdown", ".submenu",
    ".social", ".cookie", ".cookies", ".pagination",
]


# Substrings in an element's class/id that mark it as navigation/chrome.
# Matching is anchored to a word boundary (start, or a non-letter such as
# '-' '_' ' ') so it catches real nav classes ("main-nav", "mega-menu",
# "navbar", "menu-item") but NOT content containers that merely embed the word
# as part of a longer camelCase/compound token ("carouselMenu", "siteMenuArea",
# "buyukMenu") — those false positives used to silently delete real listings.
_NOISE_CLASS_SUBSTR = (
    "nav", "menu", "breadcrumb", "sidebar", "dropdown", "megamenu",
    "social", "cookie", "pagination", "footer", "site-header",
)
_NOISE_CLASS_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(s) for s in _NOISE_CLASS_SUBSTR) + r")",
    re.IGNORECASE,
)


# Structural roots that must never be decomposed: WordPress/CMS themes (Avada,
# etc.) routinely stamp tokens like "menu", "footer", "dropdown" onto <body>
# (e.g. "menu-text-align-center", "avada-footer-fx-none"), so a naive substring
# match would delete the entire page.
_NEVER_STRIP_TAGS = {"html", "body", "head", "main", "article", "[document]"}


def strip_noise(soup):
    """Remove navigation/chrome elements in place so link harvesting only
    sees real content links."""
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    for sel in _NOISE_SELECTORS:
        try:
            for el in soup.select(sel):
                el.decompose()
        except Exception:
            continue
    # Substring match on class/id tokens for menu/nav wrappers that don't use
    # a standard tag or exact class name. Two guards keep this from eating real
    # content: never touch structural roots, and never decompose an element that
    # holds the majority of the page's links (it's a content container, not
    # chrome — the symptom of the old whole-<body>-wipe bug).
    total_links = len(soup.find_all("a", href=True)) or 1
    for el in soup.find_all(True):
        if el.name in _NEVER_STRIP_TAGS:
            continue
        try:
            hay = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
            if not hay.strip() or not _NOISE_CLASS_RE.search(hay):
                continue
            if len(el.find_all("a", href=True)) / total_links > 0.5:
                continue
            el.decompose()
        except Exception:
            continue


# Turkish consonant softening (ünsüz yumuşaması): a stem's final hard consonant
# mutates before a vowel suffix, so the bare keyword stops being a substring of
# the inflected word: "destek" -> "desteği" (folds to "destegi", no "destek").
# We accept a softened-final variant so genitive/accusative forms still match.
# Folded space only (ç->c, ğ->g already applied by _fold).
_SOFTEN_FINAL = {"k": "g", "p": "b", "t": "d"}


def _kw_variants(kw):
    """Folded keyword plus its softened-final-consonant variant, if applicable."""
    f = _fold(kw)
    if f and f[-1] in _SOFTEN_FINAL:
        return (f, f[:-1] + _SOFTEN_FINAL[f[-1]])
    return (f,)


def matches_keywords(text, context=""):
    # Positive keywords must appear in the link text itself; the surrounding
    # context is only consulted for the negative (exclusion) check.
    pos = _fold(text)
    neg = _fold(f"{text} {context}")

    if any(_fold(nk) in neg for nk in config.NEGATIVE_KEYWORDS):
        return []
    # Bare generic negatives ("kadro"/"atama"/"memur") are only disqualifying when
    # they are the SUBJECT of the item, so match them against the title (`pos`)
    # only — not the description, where they appear incidentally.
    if any(_fold(nk) in pos for nk in getattr(config, "NEGATIVE_KEYWORDS_TITLE_ONLY", [])):
        return []

    strong = [kw for kw in config.GRANT_KEYWORDS_STRONG
              if any(v in pos for v in _kw_variants(kw))]
    weak = [kw for kw in config.GRANT_KEYWORDS_WEAK
            if any(v in pos for v in _kw_variants(kw))]

    # A single strong keyword is enough.
    if strong:
        return strong + weak

    # Otherwise require several weak keywords (in the link text).
    if len(weak) >= config.MIN_WEAK_KEYWORD_MATCHES:
        return weak

    return []


def check_public_url(url):
    """Return an error message if `url` isn't a fetchable public web address.

    Guards the one place a fetch target is typed by a person rather than read
    from the sites table: the manual-entry preview. This host also runs several
    unrelated apps on loopback and the LAN, so without this an operator could
    paste `http://127.0.0.1:3000` and have the server pull an internal app's
    page into the form.

    Every resolved address must be global — a name can resolve to several, and
    checking only the first would let a dual-homed host through. This resolves
    and then requests, so a name that changes answers in between is not covered;
    that is a deliberate limit, since the caller is an authenticated operator
    rather than the open internet.
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        return "Link must start with http:// or https://"
    host = parsed.hostname
    if not host:
        return "That link has no host name"
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"Could not resolve {host}"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return f"Could not resolve {host}"
        if not ip.is_global or ip.is_multicast:
            return "That address is not a public website"
    return ""


def _get(url, allow_redirects=True):
    """One GET, retrying through the legacy-TLS adapter if the handshake fails."""
    try:
        return requests.get(
            url, headers=_BASE_HEADERS, timeout=config.REQUEST_TIMEOUT, verify=False,
            allow_redirects=allow_redirects,
        )
    except requests.exceptions.SSLError:
        # Retry through an adapter that tolerates legacy/weak TLS handshakes.
        session = requests.Session()
        session.mount("https://", LegacyTLSAdapter())
        return session.get(
            url, headers=_BASE_HEADERS, timeout=config.REQUEST_TIMEOUT, verify=False,
            allow_redirects=allow_redirects,
        )


def _decode(response):
    """Response body as text.

    Trust an explicit charset from the HTTP header; only fall back to charset
    detection when the server didn't declare one. apparent_encoding is a
    chardet guess that mis-fires on pages like KOSGEB — UTF-8 bytes served with
    a correct "charset=utf-8" header but a stale "<meta charset=iso-8859-9>" —
    which the detector latches onto, decoding Turkish letters into mojibake
    ("KREDİ" -> "KREDÄ°") and silently breaking keyword matching.
    """
    if "charset=" not in response.headers.get("Content-Type", "").lower():
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def fetch_page(url):
    """Fetch a URL from the sites table. Redirects are followed freely — these
    targets are configured by an administrator, not supplied per request. For a
    URL a person typed, use fetch_public_page instead."""
    response = _get(url)
    response.raise_for_status()
    return _decode(response)


# A redirect chain long enough to need more hops than this is not a program page.
_MAX_REDIRECTS = 4


def fetch_public_page(url):
    """Fetch a person-supplied URL, re-checking the target at EVERY hop.

    Validating only the typed URL is not enough: requests follows redirects by
    default, so a public page under someone else's control can answer 302
    Location: http://127.0.0.1:3000/ and the guard never sees the address that
    is actually fetched. The attacker fully controls that response, which makes
    it the easy way through — so redirects are followed by hand here, with
    check_public_url run against each new target before it is requested.

    Raises ValueError with a message meant for the operator.
    """
    seen = url
    for _ in range(_MAX_REDIRECTS + 1):
        error = check_public_url(seen)
        if error:
            raise ValueError(error)
        response = _get(seen, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location", "")
            if not location:
                raise ValueError("That page redirected without saying where")
            # Relative redirects are normal; resolve against the current hop.
            seen = urljoin(seen, location)
            continue
        response.raise_for_status()
        return _decode(response)
    raise ValueError("That link redirects too many times")


def compute_hash(content):
    return hashlib.md5(content.encode("utf-8", errors="ignore")).hexdigest()


def scrape_site(site):
    site_id = site["id"]
    url = site["url"]
    name = site["name"]
    css_selector = site.get("css_selector", "")

    # Track rows inserted this scan so a mid-scrape failure (e.g. "database is
    # locked") still hands them to the today-filter; otherwise already-inserted
    # links escape the date check and leak into the inbox as date-less pending.
    new_grants = []

    try:
        html = fetch_page(url)
        content_hash = compute_hash(html)

        # If content hasn't changed, skip detailed parsing
        if content_hash == site.get("content_hash", "") and site.get("last_status") == "ok":
            database.update_site_status(site_id, "ok", content_hash=content_hash)
            return {"site": name, "status": "unchanged", "new_grants": 0}

        soup = BeautifulSoup(html, "lxml")

        # Drop navigation/chrome so menus and sidebars don't masquerade as
        # grant links (the #1 source of false positives).
        strip_noise(soup)

        # Determine the section to scan
        if css_selector:
            containers = soup.select(css_selector)
        else:
            # General approach: look at all link-containing elements
            containers = [soup]

        # First pass: collect candidate links and tally how many share the same
        # parent text. A parent shared by many links is a menu/listing chrome.
        candidates = []
        parent_counts = {}
        seen_hrefs = set()
        for container in containers:
            for link in container.find_all("a", href=True):
                href = normalize_url(url, link["href"])
                if not href or href in seen_hrefs:
                    continue
                if urlparse(href).netloc.lower().removeprefix("www.") in _SOCIAL_HOSTS:
                    continue
                if _is_landing_page(href):
                    continue
                link_text = extract_text(link)
                stripped = link_text.strip()
                if len(stripped) < 20:
                    continue
                if stripped.lower() in _GENERIC_LINK_TEXTS:
                    continue
                # "View all" index links ("Tüm Destek Programları", "Tüm
                # Çağrılar", ...) are navigation, not individual announcements.
                if stripped.lower().startswith(("tüm ", "tum ")) and len(stripped) < 40:
                    continue
                if href.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".jpg", ".png")):
                    continue
                seen_hrefs.add(href)
                parent_text = extract_text(link.parent) if link.parent else ""
                candidates.append((link_text, href, parent_text))
                parent_counts[parent_text] = parent_counts.get(parent_text, 0) + 1

        for link_text, href, parent_text in candidates:
            # Match positive keywords on the LINK TEXT; use surrounding text only
            # for the negative (tender/personnel/closed) check.
            matched_keywords = matches_keywords(link_text, context=parent_text)
            item_type = "funding" if matched_keywords else ""

            # A standing programme page is a different KIND of row, not a dated
            # announcement, and the dashboard has to say so: an undated row is
            # otherwise labelled "~ NEW-TODAY / first seen today", which reads as a
            # claim the programme was announced today. Tag it so the card can show
            # it as a permanent programme with no date instead.
            if item_type == "funding" and _is_program_catalog(href):
                item_type = "program"

            # News mode: a link with no grant keywords but living in a news section
            # ("/haber/<slug>") is tracked as agency activity, tagged item_type=news.
            if (not matched_keywords and getattr(config, "TRACK_AGENCY_NEWS", False)
                    and _is_news_article(href)
                    and not _has_news_hard_negative(f"{link_text} {parent_text}")):
                item_type = "news"

            # Menu/nav heuristic: a parent shared by many links is usually chrome.
            # But a keyword-matched grant or a specific news article is real content
            # even when it sits in a listing widget, so don't drop those.
            if (parent_text and parent_counts.get(parent_text, 0) >= 5) and not item_type:
                continue

            if item_type:
                pub = database.extract_date(f"{link_text} {parent_text}")
                grant_id = database.add_grant(
                    site_id=site_id,
                    title=link_text[:500],
                    url=href,
                    description=parent_text[:1000],
                    keywords_matched=matched_keywords,
                    published_date=pub,
                    item_type=item_type,
                )
                if grant_id:
                    new_grants.append({
                        "id": grant_id,
                        "site_id": site_id,
                        "site_name": name,
                        "title": link_text[:500],
                        "url": href,
                        "keywords": matched_keywords,
                        "published_date": pub,
                        "item_type": item_type,
                    })

        database.update_site_status(site_id, "ok", content_hash=content_hash)
        return {"site": name, "status": "ok", "new_grants": len(new_grants), "grants": new_grants}

    except requests.exceptions.Timeout:
        database.update_site_status(site_id, "error", error="Timeout")
        return {"site": name, "status": "error", "error": "Timeout",
                "new_grants": len(new_grants), "grants": new_grants}
    except requests.exceptions.ConnectionError as e:
        database.update_site_status(site_id, "error", error="Connection failed")
        return {"site": name, "status": "error", "error": "Connection failed",
                "new_grants": len(new_grants), "grants": new_grants}
    except Exception as e:
        error_msg = str(e)[:200]
        database.update_site_status(site_id, "error", error=error_msg)
        return {"site": name, "status": "error", "error": error_msg,
                "new_grants": len(new_grants), "grants": new_grants}


# Structured-metadata selectors that carry a real publish date (highest trust).
_PUBLISH_META_SOURCES = [
    ('meta[property="article:published_time"]', "content"),
    ('meta[property="og:published_time"]', "content"),
    ('meta[itemprop="datePublished"]', "content"),
    ('meta[name="date"]', "content"),
    ('meta[name="publish-date"]', "content"),
    ('meta[name="pubdate"]', "content"),
    ('meta[name="DC.date.issued"]', "content"),
]


def _normalize_date(value):
    """Return YYYY-MM-DD from a date/datetime string, or '' if none.

    Handles ISO 8601 datetimes with a time component (e.g.
    '2026-06-26T16:18:26+03:00') by taking the date part - database.extract_date
    misses these because its ISO pattern requires a trailing word boundary that
    a following 'T' defeats. Falls back to extract_date for 'dd.mm.yyyy' and
    Turkish-word dates ('19 Haziran 2026').
    """
    if not value:
        return ""
    value = str(value).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return database.extract_date(value)


def _feed_date(text):
    """Parse a feed item date (RSS pubDate RFC-822, or ISO) -> YYYY-MM-DD."""
    if not text:
        return ""
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt:
            return dt.date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    return _normalize_date(text)


def _jsonld_published_date(soup):
    """Return YYYY-MM-DD from any JSON-LD datePublished, or '' if none."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        # JSON-LD may be a single object, a list, or wrapped in @graph.
        nodes = data if isinstance(data, list) else [data]
        for node in list(nodes):
            if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                nodes = nodes + node["@graph"]
        for node in nodes:
            if isinstance(node, dict) and node.get("datePublished"):
                d = _normalize_date(node["datePublished"])
                if d:
                    return d
    return ""


# Teaser / carousel containers. A "similar announcements", "latest news" or
# slider block repeats OTHER articles, each carrying its own <time>. Those dates
# describe the teasers, never the page they sit on.
#
# This is not a corner case: on TÜBİTAK every <time> on an announcement page
# belongs to a swiper carousel, and on Ufuk Avrupa every one sits in a
# similar-news row. Neither article marks up its own date at all, so any rule
# that picks one of those values — first, earliest or latest — is reading another
# article's date and reporting it with confidence.
_TEASER_CLASS_SUBSTR = (
    "similar", "related", "benzer", "other-news",
    "carousel", "swiper", "slide", "views-row",
    "teaser", "widget", "popular", "recent", "latest",
)
_TEASER_CLASS_RE = re.compile(
    r"(?<![A-Za-z])(?:" + "|".join(re.escape(x) for x in _TEASER_CLASS_SUBSTR) + r")",
    re.IGNORECASE,
)


def _in_teaser(el):
    """True if el sits inside a repeated teaser/carousel block."""
    node = getattr(el, "parent", None)
    for _ in range(8):
        if node is None or not hasattr(node, "get"):
            return False
        hay = " ".join(node.get("class") or []) + " " + (node.get("id") or "")
        if hay.strip() and _TEASER_CLASS_RE.search(hay):
            return True
        node = node.parent
    return False


# An element whose class or id NAMES it as the item's date: Drupal renders
# "node-date", Ufuk Avrupa uses "date-info". That is a far stronger signal than
# position on the page, because it says what the date IS rather than merely where
# it sits — the distinction that makes the difference between an article's own
# date and the event date printed in its headline.
#
# The lookarounds keep "date" a whole token, so "node-date"/"date-info"/"h-date"
# match while "update", "validate" and "datetime" do not.
# Sites render an empty date field as the Unix epoch: oka.gov.tr prints
# "01 OCAK 1970" inside class="tarih". That parses perfectly and is perfectly
# useless, so treat an implausibly old date as no date rather than storing it.
_EARLIEST_PLAUSIBLE_DATE = "2000-01-01"


def _plausible(d):
    return bool(d) and d >= _EARLIEST_PLAUSIBLE_DATE


_DATE_CLASS_RE = re.compile(r"(?<![A-Za-z])(?:date|tarih)(?![A-Za-z])", re.IGNORECASE)


def _marked_date(soup):
    """Date read from an element whose class/id marks it as the item's date."""
    for el in soup.find_all(True):
        hay = " ".join(el.get("class") or []) + " " + (el.get("id") or "")
        if not hay.strip() or not _DATE_CLASS_RE.search(hay):
            continue
        if _in_teaser(el):
            continue
        # A date-ish class can also sit on a wrapper holding the whole article, so
        # only trust an element whose text is short enough to BE a date.
        text = extract_text(el)
        if not text or len(text) > 40:
            continue
        d = database.extract_date(text)
        if _plausible(d):
            return d
    return ""


def extract_published_date(soup):
    """Find a page's publish date using a trust-ordered set of signals.

    Returns (date_str, confidence) where confidence is:
      'high'   - structured metadata (meta tags / JSON-LD datePublished)
      'medium' - earliest <time datetime> on the page
      'low'    - a date scraped from body text (could be a deadline, etc.)
      ''       - nothing found
    HTTP Date / Last-Modified headers are intentionally never used: they
    reflect when the server sent the file, not when the item was published.
    """
    # 1. Structured metadata - trust it.
    for sel, attr in _PUBLISH_META_SOURCES:
        el = soup.select_one(sel)
        if el and el.get(attr):
            d = _normalize_date(el.get(attr))
            if _plausible(d):
                return d, "high"
    d = _jsonld_published_date(soup)
    if _plausible(d):
        return d, "high"

    # 2. <time datetime> elements - take the EARLIEST, not the first.
    #    The first <time> is often a "latest news" / sidebar item that is
    #    newer than the article itself (this is the Ufuk Avrupa false-date bug).
    # Skip any <time> inside a teaser block: it dates another article. If that
    # leaves nothing, say so rather than guessing — returning '' lets the caller
    # fall through to the labelled/body date, which IS the page's own.
    time_dates = []
    for el in soup.select("time[datetime]"):
        if _in_teaser(el):
            continue
        d = _normalize_date(el.get("datetime") or "")
        if _plausible(d):
            time_dates.append(d)
    if time_dates:
        return min(time_dates), "medium"

    # 3. An element whose class/id names it the date. Comes after <time> but
    #    before any positional guess: TÜBİTAK and Ufuk Avrupa mark up their own
    #    date this way while marking up no <time> for it at all, so without this
    #    the only remaining signal is "first date on the page" — which on Ufuk
    #    Avrupa is the event date sitting in the headline.
    d = _marked_date(soup)
    if d:
        return d, "medium"

    return "", ""


# A date printed next to an explicit publish label, e.g. KOSGEB's
# "Tarih: 08 Ağustos 2026". Anchoring on the label beats "first date near the top
# of the page": it survives however much nav chrome precedes the article, and it
# cannot pick up an unrelated date from a sidebar teaser.
_PUB_DATE_CUE_RE = re.compile(
    r"(?:yayı?n(?:lanma)?\s*tarihi|duyuru\s*tarihi|eklenme\s*tarihi|tarih)"
    r"\s*[:\-]\s*"
    r"(\d{4}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[\./\-]\d{1,2}[\./\-]\d{2,4}"
    r"|\d{1,2}\s+[A-Za-zÇŞĞÜÖİçşğüöı]+\s+\d{4})",
    re.I,
)
# Labels that end in the same word but mark a DEADLINE or a later edit rather than
# publication. "Son Başvuru Tarihi:" already fails _PUB_DATE_CUE_RE (the bare
# "tarih" branch needs the colon immediately after), but spellings vary across 65
# sites, so reject on the preceding word too.
_NOT_PUB_CUE_RE = re.compile(
    r"(?:son|başvuru|basvuru|bitiş|bitis|teslim|kapanış|kapanis"
    r"|güncelleme|guncelleme|revizyon)\s*$",
    re.I,
)


# An event listing prints the same bare label for when the event HAPPENS:
# "Tarih: 26 Haziran 2026 Saat: 10.00 - 15.00 Yer: ..." (GMKA training notices),
# "Tarih: 27 Nisan 2026 Pazartesi Saat 10:00 Yer Adana ..." (ÇKA info days), or
# "Tarih: 4 Mayıs 2026, Pazartesi Saat: 10.30 ..." (DOGAKA). A time or place right
# after the date marks a schedule, not a publication.
#
# Everything between the date and the time/place word varies: the weekday may be
# absent, and it may be separated by a comma or a dash as well as a space. Allow
# that punctuation — requiring a bare space is what let the DOGAKA event date
# through as a publish date.
_EVENT_CTX_RE = re.compile(
    r"^[\s,;.\-–—]*"
    r"(?:pazartesi|salı|sali|çarşamba|carsamba|perşembe|persembe"
    r"|cuma|cumartesi|pazar)?"
    r"[\s,;.\-–—]*(?:saat|yer)\b",
    re.I,
)


def _cue_published_date(text):
    """Publish date read from an explicit on-page label, or '' if none."""
    for m in _PUB_DATE_CUE_RE.finditer(text):
        if _NOT_PUB_CUE_RE.search(text[max(0, m.start() - 24):m.start()]):
            continue
        if _EVENT_CTX_RE.match(text[m.end():m.end() + 30]):
            continue
        d = database.extract_date(m.group(1))
        if d:
            return d
    return ""


_URL_DATE_YMD = re.compile(r"(?:^|[/_-])(\d{4})[-/_](\d{1,2})[-/_](\d{1,2})(?:[/_.-]|$)")
_URL_DATE_MDY = re.compile(r"(?:^|[/_])(\d{1,2})-(\d{1,2})-(\d{4})(?:[/_.-]|$)")


def _url_date(url):
    """Extract a date embedded in a URL path, e.g. '/2026/04/27/' or
    '/04-27-2026/' (BAKKA-style). Returns YYYY-MM-DD or ''."""
    if not url:
        return ""
    m = _URL_DATE_YMD.search(url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _URL_DATE_MDY.search(url)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and 1 <= b <= 12:        # DD-MM-YYYY
            mo, d = b, a
        elif b > 12 and 1 <= a <= 12:      # MM-DD-YYYY (BAKKA: 04-27-2026)
            mo, d = a, b
        elif 1 <= a <= 12 and 1 <= b <= 12:  # ambiguous -> assume DD-MM (TR convention)
            mo, d = b, a
        else:
            return ""
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return ""


"""Site name suffixes a CMS appends to <title>, e.g.
"2026 Çağrısı | KOSGEB" or "Duyuru - T.C. Sanayi Bakanlığı". Split on the LAST
separator so a title containing a dash keeps it."""
_TITLE_TAIL_RE = re.compile(r"\s+[|–—\-]\s+[^|–—\-]{2,60}$")


def extract_page_title(soup):
    """The announcement's own heading, best-effort.

    Trust order: og:title (what the site itself says the page is called), then
    the first non-empty <h1>, then <title> minus the site-name tail. Scans never
    needed this — they take the title from the listing link that led to the page
    — so it exists for the manual-entry preview, where there is no listing.
    """
    og = soup.select_one('meta[property="og:title"], meta[name="og:title"]')
    if og and (og.get("content") or "").strip():
        return _WS_CLEAN(og["content"].strip())[:500]
    for h1 in soup.find_all("h1"):
        text = extract_text(h1).strip()
        if len(text) >= 8:
            return _WS_CLEAN(text)[:500]
    if soup.title and (soup.title.string or "").strip():
        raw = _WS_CLEAN(soup.title.string.strip())
        trimmed = _TITLE_TAIL_RE.sub("", raw).strip()
        # Only accept the trim if something substantial survives; a page titled
        # just "KOSGEB - Duyuru" would otherwise collapse to "KOSGEB".
        return (trimmed if len(trimmed) >= 8 else raw)[:500]
    return ""


def _WS_CLEAN(text):
    return re.sub(r"\s+", " ", text or "").strip()


def summarize_text(text, limit=400):
    """A short description from the page body: whole sentences up to `limit`,
    never a mid-word cut. Falls back to a hard slice if the text has no
    sentence breaks (common on pages that are one long run-on paragraph)."""
    text = _WS_CLEAN(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if stop > limit // 3:
        return cut[: stop + 1].strip()
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).strip() + "…"


def scrape_grant_details(grant_url, fetcher=None):
    """Visit a grant page and extract detailed information.

    `fetcher` overrides how the page is retrieved. The manual-entry preview
    passes fetch_public_page, because its URL comes from whoever is filling the
    form rather than from the sites table; scans keep the default.
    """
    try:
        html = (fetcher or fetch_page)(grant_url)
        soup = BeautifulSoup(html, "lxml")

        # Publish date by trust order (read before stripping noise tags).
        published_date, pub_confidence = extract_published_date(soup)
        # Read the heading before the noise-tag strip below removes <header>.
        page_title = extract_page_title(soup)

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
            tag.decompose()

        details = {
            "detailed_description": "",
            "deadline": "",
            "funding_amount": "",
            "eligibility": "",
            "application_url": "",
            "contact_info": "",
            "published_date": published_date,
            # For the manual-entry preview; update_grant_details ignores it.
            "page_title": page_title,
        }

        # Extract main content - try common content selectors
        content_selectors = [
            "article", "main", ".content", "#content",
            ".entry-content", ".post-content", ".page-content",
            ".article-body", ".news-detail", ".haber-detay",
            ".detail", "#detail", ".text", ".icerik",
        ]
        main_content = None
        for sel in content_selectors:
            found = soup.select_one(sel)
            if found and len(found.get_text(strip=True)) > 100:
                main_content = found
                break
        if not main_content:
            # Fallback: use body
            main_content = soup.find("body") or soup

        full_text = extract_text(main_content)
        # Limit to reasonable length
        details["detailed_description"] = full_text[:5000]

        all_text_lower = full_text.lower()

        # 3. Low-confidence fallback: a date sitting in the body text. Too
        #    unreliable to use as THE published_date (it may be a deadline or an
        #    event date), but the today-filter uses it defensively: a PAST body
        #    date is strong evidence the item is not today's announcement.
        if not published_date:
            # Scan the WHOLE cleaned page top (title/byline bars included), not just
            # the selected content block — many templates print the date in a title
            # bar outside the article, which full_text (main_content only) misses.
            #
            # An explicitly LABELLED date is read first. The 2500-char window is
            # only positional — it takes whichever date appears earliest — and on
            # KOSGEB the summary paragraph sits ahead of the "Tarih:" line and
            # states the application window: "başvuruları, 20 Nisan – 8 Mayıs 2026
            # tarihleri arasında alınacak". "20 Nisan" carries no year so it does
            # not match, and the window returns 8 Mayıs — the DEADLINE — as the
            # publish date, 49 characters before the label giving the real one.
            #
            # The label also survives however much chrome precedes the article,
            # which the window does not: KOSGEB prints "Tarih:" at offset
            # ~2200-2400, so a slightly longer summary pushes it out of range and
            # the page reads as undated. That is not merely skipped — the
            # today-filter rejects it and tombstones the link for
            # TOMBSTONE_TTL_DAYS, burying a real programme.
            #
            # Ordering the label first is safe only because _cue_published_date
            # rejects deadline and event-schedule labels; without those guards it
            # picks up training and info-day dates. Measured over 94 pages with no
            # structured date: wherever both signals fire they agree, so the order
            # changes nothing except the KOSGEB case it is here to fix.
            whole_text = extract_text(soup)
            body_guess = (_cue_published_date(whole_text)
                          or database.extract_date(whole_text[:2500])
                          or database.extract_date(full_text[:2500]))
            if body_guess:
                details["text_date"] = body_guess
                pub_confidence = "low"
            # Some sites carry no on-page date but link dated documents, e.g. an
            # application guide under /04-27-2026/ (BAKKA). A dated document URL is
            # strong evidence of when the program is from.
            for a_tag in (main_content.find_all("a", href=True) if main_content else []):
                ud = _url_date(normalize_url(grant_url, a_tag["href"]) or a_tag["href"])
                if ud:
                    details["url_date"] = ud
                    break

        # --- Extract deadline ---
        # A call often lists SEVERAL application rounds, each with its own "Son
        # başvuru tarihi" (e.g. EIT Urban Mobility: Şubat/Mayıs/Ağustos/Kasım).
        # Collect every dated deadline near a deadline cue, then pick the one that
        # matters — the next upcoming round (earliest date still >= today); if all
        # rounds have passed, the latest. Taking the FIRST match used to flag a
        # multi-round call "expired" off an already-passed opening round while
        # later rounds were still open. The cue allows a short filler ("ise", ":")
        # between the cue and the date so "...tarihi ise 12 Ekim 2026" is caught.
        _DATE = (r"(\d{1,2}[\./\-]\d{1,2}[\./\-]\d{2,4}"
                 r"|\d{1,2}\s+[A-Za-zÇŞĞÜÖİçşğüöı]+\s+\d{4})")
        _CUE = (r"(?:son\s+başvuru(?:\s+tarihi)?|başvuru\s+tarihi|son\s+tarih"
                r"|kapanış(?:\s+tarihi)?|deadline|application\s+deadline)")
        deadline_re = re.compile(_CUE + r"[^\d\n]{0,12}?" + _DATE, re.IGNORECASE)
        found_deadlines = []   # (iso_date, raw_text)
        for m in deadline_re.finditer(full_text):
            raw = m.group(1).strip()
            iso = database.extract_date(raw)
            if iso:
                found_deadlines.append((iso, raw))
        if found_deadlines:
            today_iso = date.today().isoformat()
            upcoming = sorted(d for d in found_deadlines if d[0] >= today_iso)
            chosen = upcoming[0] if upcoming else max(found_deadlines, key=lambda d: d[0])
            details["deadline"] = chosen[1][:200]
        else:
            # No clean date near a cue: fall back to a free-text deadline phrase.
            for pattern in (
                r"başvuru\s+süresi\s*[:\-]?\s*(.{10,60})",
                r"application\s+deadline\s*[:\-]?\s*(.{10,60})",
            ):
                match = re.search(pattern, full_text, re.IGNORECASE)
                if match:
                    details["deadline"] = match.group(1).strip()[:200]
                    break

        # --- Extract funding amount ---
        amount_patterns = [
            r"(?:destek\s+(?:miktarı|tutarı)|hibe\s+(?:miktarı|tutarı)|bütçe|budget|funding\s+amount|grant\s+amount|toplam\s+(?:kaynak|bütçe))\s*[:\-]?\s*(.{5,100})",
            r"(\d[\d\.,]*\s*(?:TL|EUR|USD|€|\$|Euro|Dolar|Avro))",
            r"((?:TL|EUR|USD|€|\$)\s*\d[\d\.,]*)",
        ]
        for pattern in amount_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                details["funding_amount"] = match.group(1).strip()[:200]
                break

        # --- Extract eligibility ---
        eligibility_patterns = [
            r"(?:kimler\s+başvurabilir|başvuru\s+(?:şartları|koşulları)|eligibility|eligible\s+applicants|hedef\s+(?:kitle|grup)|who\s+can\s+apply)\s*[:\-]?\s*(.{10,500})",
            r"(?:başvuru\s+sahipleri|applicants?)\s*[:\-]?\s*(.{10,300})",
        ]
        for pattern in eligibility_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                details["eligibility"] = match.group(1).strip()[:500]
                break

        # --- Extract application URL ---
        for a_tag in main_content.find_all("a", href=True):
            a_text = extract_text(a_tag).lower()
            a_href = a_tag["href"]
            if any(kw in a_text for kw in ["başvur", "apply", "application", "müracaat", "form"]):
                details["application_url"] = normalize_url(grant_url, a_href) or ""
                break

        # --- Extract contact info ---
        contact_patterns = [
            r"(?:iletişim|contact|bilgi\s+için|for\s+(?:more\s+)?information)\s*[:\-]?\s*(.{10,300})",
            r"([\w\.\-]+@[\w\.\-]+\.\w{2,})",  # email
            r"(?:tel|phone|telefon)\s*[:\-]?\s*([\+\d\s\(\)\-]{7,20})",
        ]
        for pattern in contact_patterns:
            match = re.search(pattern, full_text, re.IGNORECASE)
            if match:
                details["contact_info"] = match.group(1).strip()[:300]
                break

        # Set last, not with the rest of the dict: the body-text fallback above
        # downgrades pub_confidence to 'low' after the dict is built.
        details["published_confidence"] = pub_confidence
        return {"status": "ok", "details": details}

    except ValueError as e:
        # fetch_public_page's refusals (a redirect into a private address, too
        # many hops) — already worded for the operator, so pass them through.
        return {"status": "error", "error": str(e)[:200]}
    except requests.exceptions.Timeout:
        return {"status": "error", "error": "Timeout fetching grant page"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}


def is_fresh_announcement(published_date):
    """Decide whether a newly-found program counts as a fresh 'announced
    recently' item, per config.ANNOUNCEMENT_MODE."""
    mode = getattr(config, "ANNOUNCEMENT_MODE", "new")
    if mode == "new":
        return True

    parsed = None
    if published_date:
        try:
            parsed = datetime.strptime(published_date[:10], "%Y-%m-%d").date()
        except ValueError:
            parsed = None

    if parsed is not None:
        window = int(getattr(config, "ANNOUNCEMENT_WINDOW_DAYS", 2))
        delta = (date.today() - parsed).days
        # Released within the window and not a (mis-parsed) future date.
        return 0 <= delta <= window

    # No usable release date: hybrid keeps it, released-only drops it.
    return mode == "hybrid"


def enrich_grant_details(grant):
    """Scrape and persist detail fields for a single new grant.
    `grant` is a dict carrying at least 'id' and 'url'."""
    grant_id = grant.get("id")
    url = grant.get("url")
    if not grant_id or not url:
        return False
    result = scrape_grant_details(url)
    if result["status"] == "ok":
        database.update_grant_details(grant_id, result["details"])
        return True
    logger.debug(f"  detail scrape failed for grant {grant_id}: {result.get('error')}")
    return False


def auto_enrich_new_grants(new_grants):
    """Concurrently fetch detail pages for newly found grants."""
    if not config.AUTO_SCRAPE_DETAILS or not new_grants:
        return 0
    to_scrape = new_grants[: config.AUTO_SCRAPE_DETAILS_LIMIT]
    logger.info(f"Auto-scraping details for {len(to_scrape)} new grant(s)...")
    enriched = 0
    with ThreadPoolExecutor(max_workers=config.DETAIL_SCRAPE_WORKERS) as executor:
        futures = [executor.submit(enrich_grant_details, g) for g in to_scrape]
        for future in as_completed(futures):
            try:
                if future.result():
                    enriched += 1
            except Exception as e:
                logger.warning(f"  detail scrape error: {e}")
    logger.info(f"Auto-scrape complete: enriched {enriched}/{len(to_scrape)} grant(s).")
    return enriched


def _feed_page_urls(feed_url, page):
    """Candidate URLs for a given feed page, best-guess first.

    Page 1 is always the bare feed. Later pages depend on the platform, and we
    cannot tell them apart from the URL: WordPress uses ?paged=N (1-based, the
    ab-ilan.com / *.org.tr feeds), Drupal uses ?page=N (0-based, the *.gov.tr
    feeds). Returning both lets the caller try each in turn.

    A site that recognises neither simply ignores the parameter and re-serves
    page 1. That is not detectable from the response itself, so the caller
    identifies it the only way available: every link on the page has already been
    seen, so the candidate yields nothing new and paging stops."""
    if page <= 1:
        return [feed_url]
    sep = "&" if "?" in feed_url else "?"
    return [f"{feed_url}{sep}paged={page}", f"{feed_url}{sep}page={page - 1}"]


def _parse_feed_item(it, base_url):
    """Pull (title, link, pub, desc) out of one RSS <item>/Atom <entry>, or
    return None if it has no title/link."""
    tnode = it.find("title")
    title = (tnode.get_text() if tnode else "").strip()
    if not title:
        return None
    # Link: RSS <link>text</link>, Atom <link href=...>, else <guid>.
    lnode = it.find("link")
    if lnode is not None and lnode.get("href"):
        link = lnode.get("href")
    elif lnode is not None and lnode.get_text().strip():
        link = lnode.get_text().strip()
    else:
        gnode = it.find("guid")
        link = gnode.get_text().strip() if gnode else ""
    link = normalize_url(base_url, link) or link
    if not link or _is_landing_page(link):
        return None

    dnode = (it.find("pubDate") or it.find("published") or it.find("updated")
             or it.find("date"))
    pub = _feed_date(dnode.get_text()) if dnode else ""

    snode = it.find("description") or it.find("summary") or it.find("content")
    desc = ""
    if snode:
        desc = BeautifulSoup(snode.get_text(), "lxml").get_text(" ", strip=True)[:1000]
    return title, link, pub, desc


def scrape_feed(site):
    """Ingest a site's programs from its RSS/Atom feed. Feeds give a reliable
    publish date (pubDate) and a clean, pre-listed set of items - far better than
    scraping the page. Grants are tagged from_feed=True so the today-filter trusts
    the feed date instead of re-deriving it from the detail page.

    A single feed page holds only ~10 items, so on a high-volume source a whole
    day scrolls off page 1 within hours. In today-only mode we therefore walk
    ?paged=2,3,... until an entire page predates the scan range start (feeds are
    newest-first) or MAX_FEED_PAGES is reached, so back-dated / busy-day scans
    still see every in-range announcement."""
    site_id = site["id"]
    name = site["name"]
    feed_url = (site.get("feed_url") or "").strip()
    if not feed_url:
        return scrape_site(site)

    today = date.today().isoformat()
    range_start = (getattr(config, "SCAN_RANGE_START", "") or today)[:10]
    # Only page back through the feed when today-only mode bounds what we keep;
    # in archive mode fall back to the historical single-page behavior.
    max_pages = int(getattr(config, "MAX_FEED_PAGES", 6)) if getattr(config, "SCAN_TODAY_ONLY", False) else 1

    collected = []          # list of (title, link, pub, desc)
    seen_links = set()
    try:
        for page in range(1, max_pages + 1):
            # Items this page contributed, as (title, link, pub, desc), from
            # whichever paging convention this particular feed honours.
            fresh = []
            for candidate in _feed_page_urls(feed_url, page):
                try:
                    xml = fetch_page(candidate)
                except Exception:
                    if page == 1:
                        raise       # a broken feed is a real error
                    continue        # this convention 404s; try the next one
                soup = BeautifulSoup(xml, "xml")
                for it in (soup.find_all("item") or soup.find_all("entry")):
                    parsed = _parse_feed_item(it, candidate)
                    if parsed and parsed[1] not in seen_links:
                        fresh.append(parsed)
                if fresh:
                    break
                # Nothing new here: either the feed ended, or the site ignored the
                # paging parameter and handed back a page we have already read.
                # Either way this candidate is spent — try the next convention.
            # Stop paging once no convention turns up anything new, or the whole
            # page predates the range we keep (newest-first feed => no later page
            # can be in range).
            if not fresh:
                break
            page_dates = []
            for parsed in fresh:
                seen_links.add(parsed[1])
                if parsed[2]:
                    page_dates.append(parsed[2])
                collected.append(parsed)
            if page_dates and max(page_dates) < range_start:
                break
    except Exception as e:
        database.update_site_status(site_id, "error", error=f"feed: {str(e)[:150]}")
        return {"site": name, "status": "error", "error": str(e)[:150], "new_grants": 0}

    # See scrape_site: keep partial inserts reachable by the today-filter if the
    # loop fails partway (e.g. "database is locked").
    new_grants = []
    try:
        for title, link, pub, desc in collected:
            # Same relevance filter as page scraping: keep only grant-like items.
            matched = matches_keywords(title, context=desc)
            if not matched:
                continue
            grant_id = database.add_grant(
                site_id=site_id, title=title[:500], url=link,
                description=desc, keywords_matched=matched, published_date=pub,
            )
            if grant_id:
                new_grants.append({
                    "id": grant_id, "site_id": site_id, "site_name": name,
                    "title": title[:500], "url": link,
                    "published_date": pub, "from_feed": True,
                })
    except Exception as e:
        database.update_site_status(site_id, "error", error=f"feed: {str(e)[:150]}")
        return {"site": name, "status": "error", "error": str(e)[:150],
                "new_grants": len(new_grants), "grants": new_grants}

    database.update_site_status(site_id, "ok")
    return {"site": name, "status": "ok", "new_grants": len(new_grants), "grants": new_grants}


_DAY_MONTH_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-zÇŞĞÜÖİçşğüöı]+)")


def _title_announce_date(title, today):
    """Date parsed from a title. Tries a full date first; if the title carries a
    year-less 'DD <TurkishMonth>' (e.g. '23 Mart ... Açıklandı', common on agency
    listings), assume the current year. Returns YYYY-MM-DD or ''."""
    full = database.extract_date(title or "")
    if full:
        return full
    for m in _DAY_MONTH_RE.finditer(title or ""):
        word = m.group(2).replace("İ", "i").replace("I", "ı").lower()
        mo = database._MONTHS.get(word)
        if mo:
            day = int(m.group(1))
            if 1 <= day <= 31:
                return f"{today[:4]}-{mo:02d}-{day:02d}"
    return ""


def apply_today_filter(new_grants):
    """Keep only programs whose PROVABLE publish date falls in the configured date
    range [SCAN_RANGE_START, SCAN_RANGE_END] (each defaults to today), and enrich
    the kept ones. Date-less items are dropped. Returns (kept, dropped); the caller
    deletes the dropped ones.
    """
    today = date.today().isoformat()
    start = (config.SCAN_RANGE_START or today)[:10]
    end = (config.SCAN_RANGE_END or today)[:10]
    if start > end:                       # tolerate a swapped range
        start, end = end, start

    def in_range(d):
        return bool(d) and start <= d[:10] <= end

    kept, dropped, deferred = [], [], []
    if not new_grants:
        return kept, dropped, deferred

    feed_items = [g for g in new_grants if g.get("from_feed")]
    scrape_items = [g for g in new_grants if not g.get("from_feed")]

    # --- Feed items: the feed's pubDate IS the reliable publish date. Trust it;
    #     no need to re-derive from the detail page. ---
    feed_keep = []
    for g in feed_items:
        pub = (g.get("published_date") or "")[:10]
        if in_range(pub):
            feed_keep.append(g)          # feed pubDate is inside the range
        else:
            dropped.append(g)            # out of range, or no date
    # Enrich kept feed items for deadline/amount, then lock in the feed date so
    # detail-page enrichment can't blank or override it.
    auto_enrich_new_grants(feed_keep)
    for g in feed_keep:
        database.set_published_date(g["id"], (g.get("published_date") or "")[:10])
        kept.append(g)

    # --- Scraped items: derive the reliable date from the detail page. ---
    def probe(g):
        """Fetch a candidate's detail page. Returns (g, details, pub, read_ok).

        read_ok distinguishes "the page said nothing about a date" from "we never
        got to read the page". They look identical here — both yield no date — but
        they must NOT be judged the same: a date-less page is evidence and earns a
        rejection, whereas a timeout is the absence of evidence. Recording the
        latter as a verdict tombstones a link we never looked at, hiding a possibly
        real program for TOMBSTONE_TTL_DAYS on nothing more than a network blip.
        """
        try:
            res = scrape_grant_details(g["url"])
        except Exception as e:
            logger.debug(f"  today-filter fetch failed for {g.get('id')}: {e}")
            return g, None, "", False
        if res.get("status") == "ok":
            details = res["details"]
            return g, details, (details.get("published_date") or ""), True
        logger.debug(f"  today-filter probe error for {g.get('id')}: {res.get('error')}")
        return g, None, "", False

    # Bound the probe before running it. Concurrency is already capped by
    # DETAIL_SCRAPE_WORKERS, but the TOTAL is not: one site whose layout changed
    # (strip_noise stops recognising its nav, so menu links start matching
    # keywords) can emit hundreds of candidates, all fetched from that same host.
    #
    # Over-ceiling links are DEFERRED, never rejected — see the config comment.
    # Within a site the original page order is preserved, so the items we do probe
    # are the ones nearest the top of the listing, i.e. the newest.
    per_site_cap = int(getattr(config, "PROBE_LIMIT_PER_SITE", 40) or 0)
    total_cap = int(getattr(config, "PROBE_LIMIT_TOTAL", 400) or 0)
    to_probe, seen_per_site = [], {}
    for g in scrape_items:
        sid = g.get("site_id")
        used = seen_per_site.get(sid, 0)
        if (per_site_cap and used >= per_site_cap) or (total_cap and len(to_probe) >= total_cap):
            deferred.append(g)
            continue
        seen_per_site[sid] = used + 1
        to_probe.append(g)

    if deferred:
        offenders = {}
        for g in deferred:
            key = g.get("site_name") or f"site {g.get('site_id')}"
            offenders[key] = offenders.get(key, 0) + 1
        top = ", ".join(
            f"{n}x {s}" for s, n in sorted(offenders.items(), key=lambda kv: -kv[1])[:3]
        )
        logger.warning(
            f"Probe ceiling hit: deferred {len(deferred)} link(s) unjudged "
            f"(per-site {per_site_cap}, total {total_cap}) — {top}. They will be "
            f"re-examined next scan. A site producing this many candidates usually "
            f"means its layout changed and link extraction is over-matching."
        )

    results = []
    if to_probe:
        with ThreadPoolExecutor(max_workers=config.DETAIL_SCRAPE_WORKERS) as executor:
            futures = [executor.submit(probe, g) for g in to_probe]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    logger.warning(f"  today-filter probe error: {e}")

    unread = 0
    for g, details, pub, read_ok in results:
        reliable = pub[:10] if pub else ""
        # Fallback: many sites print the date only as human-readable text in the
        # title or body ("15 Haziran 2026"), which extract_published_date
        # (structured metadata only) misses. Use a date parsed from the title
        # first, then the top of the body — but ONLY if it is past/today. A FUTURE
        # text date is almost always a deadline, not a publish date, so we ignore
        # it and let the new-today proxy apply.
        if not reliable:
            # Priority: the item's OWN signals (its URL, its title, the date shown
            # with its body) before the least-reliable one (a date lifted from some
            # linked document on the page, which may be unrelated/older).
            for cand in (_url_date(g.get("url", "") or ""),
                         _title_announce_date(g.get("title", "") or "", today),
                         (details or {}).get("text_date", ""),
                         (details or {}).get("url_date", "")):
                if cand and cand <= today:
                    reliable = cand
                    break

        # Keep ONLY items with a provable publish date inside the range. Date-less
        # items are dropped (an audit showed the old "date-less = today" proxy kept
        # old programs and standing catalog pages, none actually in range).
        if not in_range(reliable):
            # A standing programme page is undated by nature, so the date rule
            # cannot judge it: it was being rejected — and tombstoned — on every
            # scan for lacking evidence it can never have. Keep it instead, once.
            # add_grant dedups on the normalized URL, so the row is inserted a
            # single time and later scans neither re-add nor re-notify it.
            #
            # Requires read_ok: a page we could not fetch is not known to be
            # undated, it is simply unread, and that still earns a retry rather
            # than a keep. Requires an empty date too, so a catalogue-path page
            # that DOES carry a date (an agency's archived past programmes) stays
            # subject to the normal range check and is still rejected.
            if read_ok and not reliable and _is_program_catalog(g.get("url", "")):
                if details:
                    database.update_grant_details(g["id"], details)
                kept.append(g)
                continue
            # An unreadable page yields no verdict, only a retry. The URL/title
            # fallbacks above are page-independent, so if one of them produced a
            # date we DO have evidence and reject on it as normal; it is only the
            # case of no evidence at all — fetch failed AND nothing derivable from
            # the link itself — that defers. Deferred links are deleted without a
            # tombstone and re-examined next scan, so a site that is briefly down
            # costs one re-probe instead of a 30-day blackout on everything it
            # published that day.
            if not read_ok and not reliable:
                unread += 1
                deferred.append(g)
            else:
                dropped.append(g)
            continue
        if details:
            database.update_grant_details(g["id"], details)
        database.set_published_date(g["id"], reliable)
        kept.append(g)

    if unread:
        logger.warning(
            f"{unread} link(s) deferred unjudged: their detail page could not be "
            f"read and neither the URL nor the title carried a date. They keep no "
            f"verdict and are re-examined next scan. A large count here usually "
            f"means a source was down or rate-limiting during this scan."
        )

    logger.info(
        f"Date-filter [{start}..{end}]: kept {len(kept)} in-range "
        f"({len(feed_keep)} via feed), dropped {len(dropped)}, "
        f"deferred {len(deferred)} ({unread} unreadable)."
    )
    return kept, dropped, deferred


def run_scan():
    logger.info("Starting scan of all active sites...")
    scan_id = database.create_scan_log()
    sites = database.get_all_sites(active_only=True)

    results = []
    total_new_grants = 0
    total_errors = 0
    all_new_grants = []

    def _ingest(site):
        # Sites with a validated feed are ingested from it (reliable dates,
        # clean items); the rest are scraped from their HTML.
        return scrape_feed(site) if (site.get("feed_url") or "").strip() else scrape_site(site)

    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        future_to_site = {executor.submit(_ingest, site): site for site in sites}
        for future in as_completed(future_to_site):
            result = future.result()
            results.append(result)
            total_new_grants += result.get("new_grants", 0)
            if result.get("status") == "error":
                total_errors += 1
            if result.get("grants"):
                all_new_grants.extend(result["grants"])
            logger.info(f"  {result['site']}: {result['status']} ({result.get('new_grants', 0)} new)")

    # The scan's purpose is to surface programs announced TODAY. When today-only
    # mode is on, fetch each new link's page to read its reliable publish date and
    # KEEP only today's announcements (hybrid rule); the rest are deleted so the
    # platform holds today's set only. Otherwise fall back to the archive behavior.
    if getattr(config, "SCAN_TODAY_ONLY", False):
        fresh, dropped, deferred = apply_today_filter(all_new_grants)
        if deferred:
            # Unjudged, so no verdict is recorded: a plain delete lets the next
            # scan rediscover and evaluate them properly. Tombstoning here would
            # bury links we never actually looked at.
            database.delete_grants([g["id"] for g in deferred])
        if dropped:
            # Tombstone before deleting: the row is what add_grant's dedup keys
            # on, so a plain delete makes every rejected link look new again next
            # scan and re-triggers its detail fetch. See tombstone_and_delete.
            recorded, _ = database.tombstone_and_delete(
                [g["id"] for g in dropped], reason="out_of_range"
            )
            ttl = int(getattr(config, "TOMBSTONE_TTL_DAYS", 30) or 0)
            window = f"for {ttl}d" if ttl > 0 else "permanently"
            logger.info(
                f"Tombstoned {recorded} rejected link(s) {window}; later scans "
                f"skip them without re-fetching."
            )
    else:
        fresh = [g for g in all_new_grants if is_fresh_announcement(g.get("published_date", ""))]
        auto_enrich_new_grants(fresh)

    database.update_scan_log(scan_id, len(sites), len(fresh), total_errors)
    logger.info(
        f"Scan complete: {len(sites)} sites, {len(all_new_grants)} new links, "
        f"{len(fresh)} announced today, {total_errors} errors"
    )

    return {
        "sites_scanned": len(sites),
        "new_grants_found": len(fresh),
        "new_links_found": len(all_new_grants),
        "errors": total_errors,
        "new_grants": fresh,
        "results": results,
    }
