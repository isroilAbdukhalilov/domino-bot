import sqlite3
from config import DB_PATH


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS listings (

        id INTEGER PRIMARY KEY,

        telegram_message_id INTEGER UNIQUE,

        post_date TEXT,

        raw_text TEXT,

        property_type TEXT,

        complex_name TEXT,

        district TEXT,

        landmark TEXT,

        metro TEXT,

        street TEXT,

        rooms INTEGER,

        floor INTEGER,

        total_floors INTEGER,

        area REAL,

        price INTEGER,

        condition TEXT,

        phones TEXT,

        photo_file_ids TEXT

    )
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_district
    ON listings(district)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_rooms
    ON listings(rooms)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_price
    ON listings(price)
    """)

    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_complex
    ON listings(complex_name)
    """)

    conn.commit()
    conn.close()


def save_listing(data):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""

    INSERT INTO listings(

        telegram_message_id,
        post_date,
        raw_text,
        property_type,
        complex_name,
        district,
        landmark,
        metro,
        street,
        rooms,
        floor,
        total_floors,
        area,
        price,
        condition,
        phones,
        photo_file_ids

    )

    VALUES(

        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?

    )

    ON CONFLICT(telegram_message_id)

    DO UPDATE SET

        post_date=excluded.post_date,
        raw_text=excluded.raw_text,
        property_type=excluded.property_type,
        complex_name=excluded.complex_name,
        district=excluded.district,
        landmark=excluded.landmark,
        metro=excluded.metro,
        street=excluded.street,
        rooms=excluded.rooms,
        floor=excluded.floor,
        total_floors=excluded.total_floors,
        area=excluded.area,
        price=excluded.price,
        condition=excluded.condition,
        phones=excluded.phones,
        photo_file_ids=excluded.photo_file_ids

    """, (

        data.get("telegram_message_id"),
        data.get("post_date"),
        data.get("raw_text"),
        data.get("property_type"),
        data.get("complex_name"),
        data.get("district"),
        data.get("landmark"),
        data.get("metro"),
        data.get("street"),
        data.get("rooms"),
        data.get("floor"),
        data.get("total_floors"),
        data.get("area"),
        data.get("price"),
        data.get("condition"),
        data.get("phones"),
        data.get("photo_file_ids")

    ))

    conn.commit()
    conn.close()


def get_listing_by_id(message_id):

    conn = get_connection()

    row = conn.execute(

        "SELECT * FROM listings WHERE telegram_message_id=?",

        (message_id,)

    ).fetchone()

    conn.close()

    return dict(row) if row else None


def get_all_listings():

    conn = get_connection()

    rows = conn.execute(

        "SELECT * FROM listings ORDER BY post_date DESC"

    ).fetchall()

    conn.close()

    return [dict(r) for r in rows]


def execute_search(query, parameters):

    conn = get_connection()

    rows = conn.execute(query, parameters).fetchall()

    conn.close()

    return [dict(r) for r in rows]


def total_listings():

    conn = get_connection()

    count = conn.execute(

        "SELECT COUNT(*) FROM listings"

    ).fetchone()[0]

    conn.close()

    return count


def delete_listing(message_id):

    conn = get_connection()

    conn.execute(

        "DELETE FROM listings WHERE telegram_message_id=?",

        (message_id,)

    )

    conn.commit()

    conn.close()
