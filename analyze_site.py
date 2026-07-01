"""Site structure profiler — a 'training' tool for understanding how each
monitored site lays out its grant/announcement listings, so we can derive a
good per-site css_selector and check that release dates are detectable.

It fetches a site exactly as scraper.scrape_site sees it (same fetch_page +
strip_noise), then reports, for every keyword-matching link:
  - the ancestor chain (tag.class#id) so a common container jumps out
  - whether a publish date is detectable in the link/parent text
plus page-level signals (html size, link count, JS-rendering suspicion).

Usage:
  python analyze_site.py <site_id|url> [--raw] [--all-links]
  python analyze_site.py --list
"""
import sys
from collections import Counter

from bs4 import BeautifulSoup

import config
import database
import scraper


def _tag_sig(el):
    """Short signature for an element: tag.class1.class2#id (classes capped)."""
    if el is None or el.name is None:
        return "?"
    sig = el.name
    classes = el.get("class") or []
    if classes:
        sig += "." + ".".join(classes[:3])
    if el.get("id"):
        sig += "#" + el.get("id")
    return sig


def _ancestor_chain(el, depth=4):
    chain = []
    cur = el.parent
    while cur is not None and cur.name and depth > 0:
        chain.append(_tag_sig(cur))
        cur = cur.parent
        depth -= 1
    return " < ".join(chain)


def analyze(site, show_raw=False, all_links=False):
    url = site["url"]
    name = site["name"]
    sel = site.get("css_selector") or ""
    print("=" * 100)
    print(f"[{site['id']}] {name}")
    print(f"    {url}")
    if sel:
        print(f"    current css_selector: {sel}")

    try:
        html = scraper.fetch_page(url)
    except Exception as e:
        print(f"    !! FETCH FAILED: {type(e).__name__}: {str(e)[:160]}")
        return

    raw_len = len(html)
    soup_raw = BeautifulSoup(html, "lxml")
    total_links = len(soup_raw.find_all("a", href=True))
    body_text = soup_raw.get_text(" ", strip=True)
    print(f"    html: {raw_len:,} bytes | <a> links: {total_links} | visible text: {len(body_text):,} chars")
    if total_links < 5 or len(body_text) < 400:
        print("    ** WARNING: very little content — site may be JS-rendered (consider Playwright).")

    if show_raw:
        return

    # Mirror scrape_site: strip noise, optionally scope to css_selector.
    scraper.strip_noise(soup_raw)
    containers = soup_raw.select(sel) if sel else [soup_raw]

    matches = []
    seen = set()
    for container in containers:
        for link in container.find_all("a", href=True):
            href = scraper.normalize_url(url, link["href"])
            if not href or href in seen:
                continue
            text = scraper.extract_text(link)
            if len(text.strip()) < 20 or text.strip().lower() in scraper._GENERIC_LINK_TEXTS:
                continue
            if href.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".jpg", ".png")):
                continue
            seen.add(href)
            parent_text = scraper.extract_text(link.parent) if link.parent else ""
            kw = scraper.matches_keywords(text, context=parent_text)
            date = database.extract_date(f"{text} {parent_text}")
            matches.append((link, text, href, parent_text, kw, date))

    hits = [m for m in matches if m[4]]
    dated = [m for m in hits if m[5]]
    print(f"    candidate links (>=20 chars): {len(matches)} | keyword hits: {len(hits)} | of those with a parseable date: {len(dated)}")

    # Which ancestor container is shared by the most keyword hits? That's the
    # natural css_selector candidate.
    print("\n    -- container frequency among keyword hits (best css_selector candidates) --")
    chain_counter = Counter()
    for link, text, href, ptext, kw, date in hits:
        cur = link.parent
        d = 0
        while cur is not None and cur.name and d < 4:
            chain_counter[_tag_sig(cur)] += 1
            cur = cur.parent
            d += 1
    for sigc, n in chain_counter.most_common(12):
        if n >= 2:
            print(f"       {n:>3}x  {sigc}")

    sample = (matches if all_links else hits)[:25]
    print(f"\n    -- {'all candidate' if all_links else 'keyword-matching'} links (sample {len(sample)}) --")
    for link, text, href, ptext, kw, date in sample:
        flag = ("DATE " + date) if date else "no-date"
        kws = ",".join(kw) if kw else "-"
        print(f"       [{flag}] {text[:70]}")
        print(f"               kw={kws}")
        print(f"               ^ {_ancestor_chain(link)}")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "--list":
        for s in database.get_all_sites(active_only=False):
            print(f"{s['id']:>4} | {s['category']:<18} | {s['name']}")
        return

    show_raw = "--raw" in args
    all_links = "--all-links" in args
    target = args[0]

    sites = database.get_all_sites(active_only=False)
    if target.isdigit():
        chosen = [s for s in sites if s["id"] == int(target)]
    elif target.startswith("http"):
        chosen = [dict(id=0, name="(ad-hoc)", url=target, css_selector="", category="")]
    else:
        chosen = [s for s in sites if target.lower() in (s["name"] or "").lower()]

    if not chosen:
        print(f"No site matched '{target}'. Use --list to see ids.")
        return
    for s in chosen:
        analyze(dict(s), show_raw=show_raw, all_links=all_links)


if __name__ == "__main__":
    main()
