"""One-time, idempotent category cleanup requested 2026-07-09.

Renames the site categories to proper Turkish and fixes a handful of
mis-categorised sites. Safe to re-run: renames only touch rows still holding
an old value, and the per-site fixes just re-assert the target category.

Run once on each host after deploying:  python3 apply_category_updates.py
"""
import database

# Old ASCII category code -> new Turkish label (applied to every site row).
RENAME = {
    "Bakanlik": "Bakanlık",
    "Kalkinma Ajansi": "Kalkınma Ajansı",
    "AB/Uluslararasi": "Avrupa Birliği",
    "NGO/Vakif": "Sivil Toplum Kuruluşu",
    "Agregator": "Duyuru",
    "Buyukelcilik": "Büyükelçilik",
    "Kamu Bankasi": "Kamu Bankası",
    # "Kamu Kurumu" is unchanged; "Sosyal Medya" is retired via the RECAT below.
}

# Per-site corrections (exact site name -> correct category, already Turkish).
RECAT = {
    "Sistem Global Danışmanlık": "Diğer",
    "Doğu Karadeniz Projesi Bölge Kalkınma İdaresi Başkanlığı": "Bakanlık",
    "Gap Bölge Kalkınma İdaresi Başkanlığı": "Bakanlık",
    "Enhancer Project": "Avrupa Birliği",
    "Interreg": "Avrupa Birliği",
    "Kalkınma Ajansları Genel Müdürlüğü": "Kamu Kurumu",
}


def main():
    conn = database.get_connection()
    try:
        renamed = 0
        for old, new in RENAME.items():
            cur = conn.execute("UPDATE sites SET category = ? WHERE category = ?", (new, old))
            renamed += cur.rowcount
        recategorised = 0
        missing = []
        for name, cat in RECAT.items():
            cur = conn.execute("UPDATE sites SET category = ? WHERE name = ?", (cat, name))
            if cur.rowcount:
                recategorised += cur.rowcount
            else:
                missing.append(name)
        conn.commit()

        print(f"Category rows renamed: {renamed}")
        print(f"Sites re-categorised:  {recategorised}")
        if missing:
            print("WARNING — these site names were not found (skipped):")
            for m in missing:
                print(f"  - {m}")
        print("\nCurrent category distribution:")
        for r in conn.execute(
            "SELECT category, COUNT(*) n FROM sites GROUP BY category ORDER BY n DESC"
        ).fetchall():
            print(f"  {r['n']:3}  {r['category']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
