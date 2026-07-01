"""Load sites from the Daily Check Excel file into the database."""
import pandas as pd
import config
import database


CATEGORY_MAP = {
    "stgm": "NGO/Vakif",
    "siviltoplumdestek": "NGO/Vakif",
    "emb-japan": "Buyukelcilik",
    "tapv": "NGO/Vakif",
    "embassy.gov.au": "Buyukelcilik",
    "usembassy": "Buyukelcilik",
    "ttgv": "NGO/Vakif",
    "kamer": "NGO/Vakif",
    "eximbank": "Kamu Bankasi",
    "yesilay": "NGO/Vakif",
    "sgk.gov": "Kamu Kurumu",
    "iskur": "Kamu Kurumu",
    "enhancerproject": "Agregator",
    "sanayi.gov": "Bakanlik",
    "kosgeb": "Kamu Kurumu",
    "ticaret.gov": "Bakanlik",
    "ab-ilan": "Agregator",
    "ab.gov": "Bakanlik",
    "ua.gov": "Kamu Kurumu",
    "mfa.gov": "Bakanlik",
    "csb.gov": "Bakanlik",
    "enerji.gov": "Bakanlik",
    "tarimorman": "Bakanlik",
    "ufukavrupa": "AB/Uluslararasi",
    "tubitak": "Kamu Kurumu",
    "cbc.ab.gov": "AB/Uluslararasi",
    "yatirimadestek": "Kalkinma Ajansi",
    "tkdk": "Kamu Kurumu",
    "linkedin": "Sosyal Medya",
    "gsb.gov": "Bakanlik",
    "cfcu.gov": "Kamu Kurumu",
    "aile.gov": "Bakanlik",
    "ktb.gov": "Bakanlik",
    "tskb": "Kamu Bankasi",
    "sbb.gov": "Kamu Kurumu",
    "dokap": "Kalkinma Ajansi",
    "gap.gov": "Kalkinma Ajansi",
    "eeas.europa": "AB/Uluslararasi",
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
            return "Kalkinma Ajansi"
    return "Diger"


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
