"""Load sites from the Daily Check Excel file into the database."""
import pandas as pd
import config
import database


CATEGORY_MAP = {
    "stgm": "Sivil Toplum Kuruluşu",
    "siviltoplumdestek": "Sivil Toplum Kuruluşu",
    "emb-japan": "Büyükelçilik",
    "tapv": "Sivil Toplum Kuruluşu",
    "embassy.gov.au": "Büyükelçilik",
    "usembassy": "Büyükelçilik",
    "ttgv": "Sivil Toplum Kuruluşu",
    "kamer": "Sivil Toplum Kuruluşu",
    "eximbank": "Kamu Bankası",
    "yesilay": "Sivil Toplum Kuruluşu",
    "sgk.gov": "Kamu Kurumu",
    "iskur": "Kamu Kurumu",
    "enhancerproject": "Avrupa Birliği",
    "sanayi.gov": "Bakanlık",
    "kosgeb": "Kamu Kurumu",
    "ticaret.gov": "Bakanlık",
    "ab-ilan": "Duyuru",
    "ab.gov": "Bakanlık",
    "ua.gov": "Kamu Kurumu",
    "mfa.gov": "Bakanlık",
    "csb.gov": "Bakanlık",
    "enerji.gov": "Bakanlık",
    "tarimorman": "Bakanlık",
    "ufukavrupa": "Avrupa Birliği",
    "tubitak": "Kamu Kurumu",
    "cbc.ab.gov": "Avrupa Birliği",
    "yatirimadestek": "Kalkınma Ajansı",
    "tkdk": "Kamu Kurumu",
    "linkedin": "Diğer",
    "gsb.gov": "Bakanlık",
    "cfcu.gov": "Kamu Kurumu",
    "aile.gov": "Bakanlık",
    "ktb.gov": "Bakanlık",
    "tskb": "Kamu Bankası",
    "sbb.gov": "Kamu Kurumu",
    "dokap": "Bakanlık",
    "gap.gov": "Bakanlık",
    "eeas.europa": "Avrupa Birliği",
    "ikg.gov": "Kamu Kurumu",
}

DEVELOPMENT_AGENCY_KEYWORDS = [
    "ahika", "ankaraka", "baka", "bakka", "bebka", "cka", "dika", "dogaka",
    "daka", "doka", "marka", "fka", "geka", "gmka", "ika", "istka", "izka",
    "karacadag", "kudaka", "kuzka", "mevka", "oran", "oka", "serka",
    "trakyaka", "zafer",
]


def categorize(url):
    url_lower = url.lower()
    for key, cat in CATEGORY_MAP.items():
        if key in url_lower:
            return cat
    for kw in DEVELOPMENT_AGENCY_KEYWORDS:
        if kw in url_lower:
            return "Kalkınma Ajansı"
    return "Diğer"


def seed_from_excel(filepath):
    df = pd.read_excel(filepath, sheet_name=0)
    count = 0
    for _, row in df.iterrows():
        name = str(row.get("Kurum", "")).strip().replace("\n", " ")
        url = str(row.get("Web Site", "")).strip()
        if not url or url == "nan":
            continue
        category = categorize(url)
        database.add_site(name, url, category)
        count += 1
    print(f"Seeded {count} sites into the database.")


if __name__ == "__main__":
    database.init_db()
    seed_from_excel(config.EXCEL_PATH)
