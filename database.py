import sqlite3
import json

DB_NAME = "malta_properties.db"

def init_db():
    """Tworzy tabelę w bazie SQLite, jeśli jeszcze nie istnieje."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            price_eur INTEGER,
            locality TEXT,
            property_type TEXT,
            bedrooms INTEGER,
            seller_type TEXT,
            is_freehold BOOLEAN,
            has_airspace BOOLEAN,
            has_sea_view BOOLEAN,
            is_shell_form BOOLEAN,
            key_features TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def save_listings_to_db(listings: list[dict]):
    """Zapisuje listę sparsowanych obiektów do bazy. Ignoruje duplikaty na podstawie URL."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    inserted_count = 0
    for item in listings:
        try:
            cursor.execute('''
                INSERT INTO listings (
                    url, title, price_eur, locality, property_type, 
                    bedrooms, seller_type, is_freehold, has_airspace, 
                    has_sea_view, is_shell_form, key_features
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                item["url"],
                item["title"],
                item.get("price_eur"),
                item.get("locality"),
                item.get("property_type"),
                item.get("bedrooms"),
                item.get("seller_type"),
                item.get("is_freehold", False),
                item.get("has_airspace", False),
                item.get("has_sea_view", False),
                item.get("is_shell_form", False),
                json.dumps(item.get("key_features", []), ensure_ascii=False)
            ))
            inserted_count += 1
        except sqlite3.IntegrityError:
            # Ogłoszenie z tym URL już istnieje w bazie
            pass

    conn.commit()
    conn.close()
    print(f"💾 Zapisano {inserted_count} nowych ogłoszeń w bazie '{DB_NAME}'!")

if __name__ == "__main__":
    init_db()
    
    # Wczytujemy sparsowane dane i zapisujemy do bazy
    with open("parsed_listings.json", "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
        
    save_listings_to_db(parsed_data)