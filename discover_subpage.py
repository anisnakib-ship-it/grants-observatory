"""Group-B tool: for a site whose homepage shows no grants, find the real
announcement/listing subpage. Reads the RAW homepage (nav not stripped),
collects same-domain links that look like a listing page (Duyurular, Haberler,
Destekler, Hibeler, Çağrılar...), fetches each candidate, and ranks them by
how many keyword hits — and dated keyword hits — they produce after strip_noise.

Usage:  python discover_subpage.py <site_id|url> [--max N]
"""
import sys
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import scraper, database

# Anchor text / href fragments that suggest a listing page worth scanning.
NAV_HINTS = [
    "duyuru", "haber", "ilan", "destek", "hibe", "cagri", "çağrı", "cagr",
    "proje", "fon", "basvuru", "başvuru", "program", "teşvik", "tesvik",
    "announcement", "news", "grant", "funding", "call", "tender", "burs",
]
# Fragments that are almost never a grant-listing page.
NAV_SKIP = [
    "iletisim", "iletişim", "hakkinda", "hakkımızda", "kvkk", "gizlilik",
    "login", "giris", "giriş", "uye", "üye", "facebook", "twitter", "instagram",
    "youtube", "linkedin", "/en/", "lang=en", "mailto:", "tel:", ".pdf",
]


def kw_stats(url):
    """Return (kw_hits, dated_hits) for a page, as the real scanner sees it."""
    try:
        html = scraper.fetch_page(url)
    except Exception as e:
        return None, None, str(e)[:60]
    soup = BeautifulSoup(html, "lxml")
    scraper.strip_noise(soup)
    seen = set()
    hits = dated = 0
    for link in soup.find_all("a", href=True):
        href = scraper.normalize_url(url, link["href"])
        if not href or href in seen:
            continue
        text = scraper.extract_text(link)
        if len(text.strip()) < 20 or text.strip().lower() in scraper._GENERIC_LINK_TEXTS:
            continue
        if href.lower().endswith((".pdf",".doc",".docx",".xls",".xlsx",".zip",".jpg",".png")):
            continue
        seen.add(href)
        ptext = scraper.extract_text(link.parent) if link.parent else ""
        if scraper.matches_keywords(text, context=ptext):
            hits += 1
            if database.extract_date(f"{text} {ptext}"):
                dated += 1
    return hits, dated, ""


def candidates_from(home_url):
    html = scraper.fetch_page(home_url)
    soup = BeautifulSoup(html, "lxml")  # RAW: keep nav
    home_host = urlparse(home_url).netloc.lower().lstrip("www.")
    found = {}
    for link in soup.find_all("a", href=True):
        href = scraper.normalize_url(home_url, link["href"])
        if not href:
            continue
        low = href.lower()
        if any(sk in low for sk in NAV_SKIP):
            continue
        host = urlparse(href).netloc.lower().lstrip("www.")
        if host != home_host:
            continue
        text = scraper.extract_text(link).lower()
        blob = text + " " + low
        if any(h in blob for h in NAV_HINTS):
            label = scraper.extract_text(link)[:40] or "(no text)"
            found.setdefault(href, label)
    return found


def main():
    args = sys.argv[1:]
    maxn = 10
    if "--max" in args:
        i = args.index("--max"); maxn = int(args[i+1]); del args[i:i+2]
    target = args[0]
    sites = database.get_all_sites(active_only=False)
    if target.isdigit():
        s = [x for x in sites if x["id"] == int(target)][0]
        site_url, site_name = s["url"], s["name"]
    else:
        site_url, site_name = target, "(ad-hoc)"

    print("=" * 90)
    print(f"DISCOVER: {site_name}\n  home: {site_url}")
    hh, hd, herr = kw_stats(site_url)
    if herr:
        print(f"  homepage fetch error: {herr}")
    else:
        print(f"  homepage itself: {hh} kw-hits ({hd} dated)")

    try:
        cands = candidates_from(site_url)
    except Exception as e:
        print(f"  !! could not read homepage nav: {e}")
        return
    print(f"  {len(cands)} candidate listing links found; probing top {min(maxn,len(cands))}...\n")

    scored = []
    for href, label in list(cands.items())[:maxn]:
        hits, dated, err = kw_stats(href)
        if err:
            scored.append((-1, -1, href, label, err))
        else:
            scored.append((dated, hits, href, label, ""))
    scored.sort(key=lambda r: (r[0], r[1]), reverse=True)

    print(f"  {'dated':>5} {'hits':>5}  candidate")
    for dated, hits, href, label, err in scored:
        if err:
            print(f"  {'ERR':>5} {'':>5}  {href}  <{err}>")
        else:
            print(f"  {dated:>5} {hits:>5}  {href}")
            print(f"  {'':>12}\"{label}\"")


if __name__ == "__main__":
    main()
