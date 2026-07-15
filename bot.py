import json
import os
import re
import sqlite3
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------- Config ----------
DB_PATH = os.environ.get("DB_PATH", "listings.db")
SEED_JSON_PATH = os.path.join(os.path.dirname(__file__), "listings.json")
MEDIA_GROUP_WAIT_SECONDS = 3  # wait this long to collect all photos of one post

DISTRICT_ALIASES = {
    "юнусабад": "Юнусабадский", "мирабад": "Мирабадский", "мирабд": "Мирабадский",
    "шайхантахур": "Шайхантахурский", "чиланзар": "Чиланзарский", "сергели": "Сергелийский",
    "яккасарай": "Яккасарайский", "мирзо улугбек": "Мирзо-Улугбекский",
    "мирзо-улугбек": "Мирзо-Улугбекский", "учтепа": "Учтепинский", "бектемир": "Бектемирский",
    "яшнабад": "Яшнабадский", "алмазар": "Алмазарский",
}

DISTRICTS = list(dict.fromkeys(DISTRICT_ALIASES.values()))


# ---------- Database ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY,
            date TEXT,
            raw_text TEXT,
            type TEXT,
            district TEXT,
            rooms INTEGER,
            floor INTEGER,
            total_floors INTEGER,
            area_m2 INTEGER,
            price INTEGER,
            condition TEXT,
            phones TEXT,
            photo_file_ids TEXT
        )
    """)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) AS c FROM listings").fetchone()["c"]
    if count == 0 and os.path.exists(SEED_JSON_PATH):
        logger.info("Seeding database from listings.json (historical export)...")
        with open(SEED_JSON_PATH, "r", encoding="utf-8") as f:
            seed = json.load(f)
        for l in seed:
            conn.execute("""
                INSERT OR IGNORE INTO listings
                (id, date, raw_text, type, district, rooms, floor, total_floors, area_m2, price, condition, phones, photo_file_ids)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                l.get("id"), l.get("date"), l.get("raw_text"), l.get("type"),
                l.get("district"), l.get("rooms"), l.get("floor"), l.get("total_floors"),
                l.get("area_m2"), l.get("price"), l.get("condition"), l.get("phones"), None
            ))
        conn.commit()
        logger.info("Seeded %d historical listings.", len(seed))
    conn.close()


def upsert_listing(record):
    conn = get_conn()
    existing = conn.execute("SELECT photo_file_ids FROM listings WHERE id=?", (record["id"],)).fetchone()

    photo_ids = record.get("photo_file_ids") or []
    if existing and existing["photo_file_ids"]:
        prev = existing["photo_file_ids"].split(",")
        photo_ids = list(dict.fromkeys(prev + photo_ids))

    conn.execute("""
        INSERT INTO listings (id, date, raw_text, type, district, rooms, floor, total_floors, area_m2, price, condition, phones, photo_file_ids)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            date=excluded.date, raw_text=excluded.raw_text, type=excluded.type,
            district=excluded.district, rooms=excluded.rooms, floor=excluded.floor,
            total_floors=excluded.total_floors, area_m2=excluded.area_m2, price=excluded.price,
            condition=excluded.condition, phones=excluded.phones,
            photo_file_ids=excluded.photo_file_ids
    """, (
        record["id"], record.get("date"), record.get("raw_text"), record.get("type"),
        record.get("district"), record.get("rooms"), record.get("floor"),
        record.get("total_floors"), record.get("area_m2"), record.get("price"),
        record.get("condition"), record.get("phones"), ",".join(photo_ids) if photo_ids else None
    ))
    conn.commit()
    conn.close()


# ---------- Parsing (only used for search fields, never shown to the user) ----------
def parse_fields(text: str):
    def find(pattern, cast=str):
        mo = re.search(pattern, text, re.IGNORECASE)
        if mo:
            try:
                return cast(mo.group(1).strip())
            except Exception:
                return mo.group(1).strip()
        return None

    fields = {}
    fields["type"] = text.strip().split("\n")[0].strip() if text.strip() else None
    fields["rooms"] = find(r"Комнат[а-я]*\s*[:\-]?\s*(\d+)", int)
    fields["floor"] = find(r"(?<!Э)Этаж\s*[:\-]?\s*(\d+)", int)
    fields["total_floors"] = find(r"Этажность\s*[:\-]?\s*(\d+)", int)
    fields["area_m2"] = find(r"(?:Общая\s+)?[Пп]лощадь\s*[:\-]?\s*(\d+)", int)

    price_raw = find(r"Цена\s*[:\-]?\s*([\d\s.,]+)")
    if price_raw:
        digits = re.sub(r"[^\d]", "", price_raw)
        fields["price"] = int(digits) if digits else None
    else:
        fields["price"] = None

    fields["condition"] = find(r"Состояние\s*[:\-]?\s*(.+)")

    district_found = None
    for d in DISTRICTS:
        if d in text:
            district_found = d
            break
    if not district_found:
        mo = re.search(r"([А-ЯЁ][а-яё\-]+(?:\s[А-ЯЁ][а-яё]+)?ский)\s+район", text)
        if mo:
            district_found = mo.group(1)
    fields["district"] = district_found

    phones = re.findall(r"\+998\d{9}", text)
    fields["phones"] = ",".join(sorted(set(phones)))

    return fields


# ---------- Query parsing for client searches ----------
def parse_query(text: str):
    text_lower = text.lower()
    criteria = {}

    m = re.search(r"(\d+)\s*[- ]?\s*комн", text_lower)
    if m:
        criteria["rooms"] = int(m.group(1))

    m = re.search(r"(\d+)\s*[- ]?\s*этаж(?!ность)", text_lower)
    if m:
        criteria["floor"] = int(m.group(1))

    m = re.search(r"до\s*(\d[\d\s]*)\s*(?:тыс|000)?", text_lower)
    if m:
        digits = re.sub(r"\s", "", m.group(1))
        if digits:
            val = int(digits)
            if "тыс" in text_lower and val < 10000:
                val *= 1000
            criteria["max_price"] = val

    for alias, canonical in DISTRICT_ALIASES.items():
        if alias in text_lower:
            criteria["district"] = canonical
            break

    return criteria


def search_listings(criteria, limit=6):
    conn = get_conn()
    query = "SELECT * FROM listings WHERE 1=1"
    params = []
    if criteria.get("rooms") is not None:
        query += " AND rooms = ?"
        params.append(criteria["rooms"])
    if criteria.get("floor") is not None:
        query += " AND floor = ?"
        params.append(criteria["floor"])
    if criteria.get("max_price") is not None:
        query += " AND price IS NOT NULL AND price <= ?"
        params.append(criteria["max_price"])
    if criteria.get("district") is not None:
        query += " AND district = ?"
        params.append(criteria["district"])
    query += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------- Media group buffering (to collect all photos of one post) ----------
pending_groups = {}  # media_group_id -> {"text": str, "photo_ids": [...], "date": str, "id": int}


async def finalize_group(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    group_id = job.data["group_id"]
    group = pending_groups.pop(group_id, None)
    if not group:
        return
    save_post(group["id"], group["date"], group["text"], group["photo_ids"])


def save_post(msg_id, date, text, photo_ids):
    fields = parse_fields(text or "")
    record = {
        "id": msg_id,
        "date": date,
        "raw_text": text or "",
        "photo_file_ids": photo_ids,
        **fields,
    }
    upsert_listing(record)
    logger.info("Saved listing id=%s rooms=%s district=%s photos=%d",
                msg_id, fields.get("rooms"), fields.get("district"), len(photo_ids))


# ---------- Handlers ----------
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return

    text = post.text or post.caption or ""
    photo_ids = [post.photo[-1].file_id] if post.photo else []
    date_str = post.date.isoformat() if post.date else datetime.utcnow().isoformat()

    if post.media_group_id:
        group_id = post.media_group_id
        if group_id not in pending_groups:
            pending_groups[group_id] = {
                "id": post.message_id, "date": date_str, "text": text, "photo_ids": []
            }
            context.job_queue.run_once(
                finalize_group, MEDIA_GROUP_WAIT_SECONDS, data={"group_id": group_id}
            )
        group = pending_groups[group_id]
        group["photo_ids"].extend(photo_ids)
        if text:
            group["text"] = text  # caption usually only on first photo
    else:
        save_post(post.message_id, date_str, text, photo_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! 👋\n\n"
        "Напишите, что вы ищете, например:\n"
        "«Мирабад 2 комнаты 3 этаж»\n"
        "«Чиланзар 3 комнаты до 100 тыс»\n\n"
        "Я найду подходящие объекты из базы."
    )


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    criteria = parse_query(text)
    matches = search_listings(criteria)

    if not matches:
        await update.message.reply_text(
            "😕 Ничего не найдено по вашему запросу. Попробуйте изменить критерии."
        )
        return

    await update.message.reply_text(f"Найдено {len(matches)} объект(ов):")
    for l in matches:
        photo_ids = l["photo_file_ids"].split(",") if l.get("photo_file_ids") else []
        caption = l["raw_text"][:1024] if l["raw_text"] else None
        if photo_ids:
            if len(photo_ids) == 1:
                await update.message.reply_photo(photo_ids[0], caption=caption)
            else:
                from telegram import InputMediaPhoto
                media = [InputMediaPhoto(pid, caption=caption if i == 0 else None)
                         for i, pid in enumerate(photo_ids[:10])]
                await update.message.reply_media_group(media)
        else:
            await update.message.reply_text(caption or "(без текста)")


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not found. Available env var names: %s", sorted(os.environ.keys()))
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
