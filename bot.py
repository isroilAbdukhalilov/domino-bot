import os
import re
import sqlite3
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ChatMemberHandler, filters, ContextTypes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "listings.db")
MEDIA_GROUP_WAIT_SECONDS = 3

DISTRICT_ALIASES = {
    "юнусабад": "Юнусабадский", "мирабад": "Мирабадский", "мирабд": "Мирабадский",
    "шайхантахур": "Шайхантахурский", "чиланзар": "Чиланзарский", "сергели": "Сергелийский",
    "яккасарай": "Яккасарайский", "мирзо улугбек": "Мирзо-Улугбекский",
    "мирзо-улугбек": "Мирзо-Улугбекский", "учтепа": "Учтепинский", "бектемир": "Бектемирский",
    "яшнабад": "Яшнабадский", "алмазар": "Алмазарский",
    "yunusabad": "Юнусабадский", "yunusobod": "Юнусабадский",
    "mirabad": "Мирабадский", "mirobod": "Мирабадский", "mira": "Мирабадский",
    "shayxontohur": "Шайхантахурский", "shaykhantahur": "Шайхантахурский",
    "chilanzar": "Чиланзарский", "chilonzor": "Чиланзарский",
    "sergeli": "Сергелийский",
    "yakkasaray": "Яккасарайский", "yakkasaroy": "Яккасарайский",
    "mirzo ulugbek": "Мирзо-Улугбекский", "mirzo-ulugbek": "Мирзо-Улугбекский", "mirzo": "Мирзо-Улугбекский",
    "uchtepa": "Учтепинский", "bektemir": "Бектемирский",
    "yashnabad": "Яшнабадский", "yashnobod": "Яшнабадский",
    "almazar": "Алмазарский", "olmazor": "Алмазарский",
}
DISTRICTS = list(dict.fromkeys(DISTRICT_ALIASES.values()))

ROOM_PATTERN = re.compile(r"(\d+)\s*[-]?\s*(?:komnat\w*|xonali\w*|xona\w*|room\w*|комнат\w*)", re.IGNORECASE)
FLOOR_PATTERN = re.compile(r"(\d+)\s*[-]?\s*(?:etaj\w*|qavat\w*|floor\w*|этаж(?!ность))", re.IGNORECASE)

# Guided-search field definitions
FIELD_LABELS = {
    "rayon": "📍 Район",
    "jk": "🏘 ЖК",
    "komnata": "🚪 Комнат",
    "etaj": "🏢 Этаж",
    "etajnost": "🏗 Этажность",
    "ploshad": "📐 Площадь (до, м²)",
    "cena": "💰 Цена (до)",
    "orientir": "📌 Ориентир",
    "sostoyanie": "🔧 Состояние",
}
NUMERIC_FIELDS = {"komnata", "etaj", "etajnost", "ploshad", "cena"}

user_channel_choice = {}          # user_id -> chat_id or "ALL"
user_search_criteria = {}         # user_id -> {field: value}
user_awaiting_field = {}          # user_id -> field name currently being entered
pending_groups = {}                # (channel_id, media_group_id) -> {...}


# ---------- Database ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            added_date TEXT
        )
    """)

    # Migration: older deployments used a schema without channel_id/uid.
    # If we detect that old table shape, rebuild it fresh (safe: this bot
    # never relies on data surviving that migration - it's only ever meant
    # to hold live listings captured from channels going forward).
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(listings)").fetchall()}
    if existing_cols and "channel_id" not in existing_cols:
        logger.warning("Old listings schema detected (columns=%s) - rebuilding table.", existing_cols)
        conn.execute("DROP TABLE listings")
        conn.commit()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            uid TEXT PRIMARY KEY,
            channel_id INTEGER,
            message_id INTEGER,
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
    conn.close()


def register_channel(chat_id, title):
    conn = get_conn()
    conn.execute(
        "INSERT INTO channels (chat_id, title, added_date) VALUES (?,?,?) "
        "ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
        (chat_id, title or str(chat_id), datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()


def list_channels():
    conn = get_conn()
    rows = conn.execute("SELECT chat_id, title FROM channels ORDER BY title").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_listing(record):
    uid = f"{record['channel_id']}:{record['message_id']}"
    conn = get_conn()
    existing = conn.execute("SELECT photo_file_ids FROM listings WHERE uid=?", (uid,)).fetchone()

    photo_ids = record.get("photo_file_ids") or []
    if existing and existing["photo_file_ids"]:
        prev = existing["photo_file_ids"].split(",")
        photo_ids = list(dict.fromkeys(prev + photo_ids))

    conn.execute("""
        INSERT INTO listings (uid, channel_id, message_id, date, raw_text, type, district, rooms, floor, total_floors, area_m2, price, condition, phones, photo_file_ids)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(uid) DO UPDATE SET
            date=excluded.date, raw_text=excluded.raw_text, type=excluded.type,
            district=excluded.district, rooms=excluded.rooms, floor=excluded.floor,
            total_floors=excluded.total_floors, area_m2=excluded.area_m2, price=excluded.price,
            condition=excluded.condition, phones=excluded.phones,
            photo_file_ids=excluded.photo_file_ids
    """, (
        uid, record["channel_id"], record["message_id"], record.get("date"), record.get("raw_text"),
        record.get("type"), record.get("district"), record.get("rooms"), record.get("floor"),
        record.get("total_floors"), record.get("area_m2"), record.get("price"),
        record.get("condition"), record.get("phones"), ",".join(photo_ids) if photo_ids else None
    ))
    conn.commit()
    conn.close()


# ---------- Parsing original post text into search fields (never shown to user) ----------
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
    text_normalized = text.replace("-", " ")
    for d in DISTRICTS:
        d_normalized = d.replace("-", " ")
        if d_normalized in text_normalized:
            district_found = d
            break
    if not district_found:
        mo = re.search(r"([А-ЯЁ][а-яё\-]+(?:[\s\-][А-ЯЁ][а-яё]+)?ский)\s+район", text)
        if mo:
            district_found = mo.group(1)
    fields["district"] = district_found

    phones = re.findall(r"\+998\d{9}", text)
    fields["phones"] = ",".join(sorted(set(phones)))

    return fields


# ---------- Free-text query parsing (quick search path) ----------
def parse_query(text: str):
    text_lower = text.lower()
    criteria = {}

    m = ROOM_PATTERN.search(text_lower)
    if m:
        criteria["rooms"] = int(m.group(1))

    m = FLOOR_PATTERN.search(text_lower)
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

    for alias in sorted(DISTRICT_ALIASES, key=len, reverse=True):
        if alias in text_lower:
            criteria["district"] = DISTRICT_ALIASES[alias]
            break

    stripped = re.sub(r"\d+", " ", text_lower)
    noise_words = ["komnat", "xona", "room", "komnatniy", "etaj", "qavat", "floor",
                   "комнат", "этаж", "этажность", "до", "тыс", "у.е", "сум"]
    for kw in noise_words:
        stripped = stripped.replace(kw, " ")
    for alias in DISTRICT_ALIASES:
        stripped = stripped.replace(alias, " ")
    leftover = [w for w in re.findall(r"[а-яa-zё\-]+", stripped) if len(w) > 2]
    criteria["free_text"] = leftover

    return criteria


# ---------- Core search ----------
def search_listings(criteria, channel_id=None, limit=6):
    """criteria keys: rooms, floor, total_floors, max_price, district (exact),
    free_text (list of substrings), jk/orientir/sostoyanie (substrings)."""
    conn = get_conn()
    query = "SELECT * FROM listings WHERE 1=1"
    params = []
    has_any = False

    if channel_id is not None and channel_id != "ALL":
        query += " AND channel_id = ?"
        params.append(channel_id)

    if criteria.get("rooms") is not None:
        query += " AND rooms = ?"
        params.append(criteria["rooms"]); has_any = True
    if criteria.get("floor") is not None:
        query += " AND floor = ?"
        params.append(criteria["floor"]); has_any = True
    if criteria.get("total_floors") is not None:
        query += " AND total_floors = ?"
        params.append(criteria["total_floors"]); has_any = True
    if criteria.get("max_price") is not None:
        query += " AND price IS NOT NULL AND price <= ?"
        params.append(criteria["max_price"]); has_any = True
    if criteria.get("max_area") is not None:
        query += " AND area_m2 IS NOT NULL AND area_m2 <= ?"
        params.append(criteria["max_area"]); has_any = True
    if criteria.get("district") is not None:
        query += " AND district = ?"
        params.append(criteria["district"]); has_any = True

    for key in ("free_text_extra",):
        pass  # placeholder, substrings handled below

    substring_fields = []
    for word in criteria.get("free_text") or []:
        substring_fields.append(word)
    for key in ("jk", "orientir", "sostoyanie", "district_text"):
        val = criteria.get(key)
        if val:
            substring_fields.append(val)

    for word in substring_fields:
        query += " AND raw_text LIKE ? COLLATE NOCASE"
        params.append(f"%{word}%")
        has_any = True

    if not has_any:
        conn.close()
        return []

    query += " ORDER BY date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_post(channel_id, message_id, date, text, photo_ids):
    fields = parse_fields(text or "")
    record = {
        "channel_id": channel_id,
        "message_id": message_id,
        "date": date,
        "raw_text": text or "",
        "photo_file_ids": photo_ids,
        **fields,
    }
    upsert_listing(record)
    logger.info("Saved listing channel=%s msg=%s rooms=%s district=%s photos=%d",
                channel_id, message_id, fields.get("rooms"), fields.get("district"), len(photo_ids))


# ---------- Telegram handlers ----------
async def track_bot_added_to_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmu = update.my_chat_member
    if not cmu:
        return
    new_status = cmu.new_chat_member.status
    if new_status in ("administrator", "member"):
        register_channel(cmu.chat.id, cmu.chat.title)
        logger.info("Bot added to channel: %s (%s)", cmu.chat.title, cmu.chat.id)


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        post = update.channel_post
        if not post:
            return

        register_channel(post.chat.id, post.chat.title)

        text = post.text or post.caption or ""
        photo_ids = [post.photo[-1].file_id] if post.photo else []
        date_str = post.date.isoformat() if post.date else datetime.utcnow().isoformat()

        if post.media_group_id:
            key = (post.chat.id, post.media_group_id)
            if key not in pending_groups:
                pending_groups[key] = {
                    "channel_id": post.chat.id, "message_id": post.message_id,
                    "date": date_str, "text": text, "photo_ids": []
                }
                context.job_queue.run_once(finalize_group, MEDIA_GROUP_WAIT_SECONDS, data={"key": key})
            group = pending_groups[key]
            group["photo_ids"].extend(photo_ids)
            if text:
                group["text"] = text
        else:
            save_post(post.chat.id, post.message_id, date_str, text, photo_ids)
    except Exception:
        logger.exception("Error handling channel post")


async def finalize_group(context: ContextTypes.DEFAULT_TYPE):
    key = context.job.data["key"]
    group = pending_groups.pop(key, None)
    if not group:
        return
    save_post(group["channel_id"], group["message_id"], group["date"], group["text"], group["photo_ids"])


def channel_picker_keyboard(channels):
    buttons = [[InlineKeyboardButton(c["title"], callback_data=f"ch:{c['chat_id']}")] for c in channels]
    buttons.append([InlineKeyboardButton("🔎 Все каналы", callback_data="ch:ALL")])
    return InlineKeyboardMarkup(buttons)


def field_menu_keyboard(user_id):
    crit = user_search_criteria.get(user_id, {})
    rows = []
    for key, label in FIELD_LABELS.items():
        val = crit.get(key)
        text = f"{label}: {val}" if val else label
        rows.append([InlineKeyboardButton(text, callback_data=f"field:{key}")])
    rows.append([
        InlineKeyboardButton("🔍 Искать", callback_data="search:go"),
        InlineKeyboardButton("🔄 Сброс", callback_data="search:reset"),
    ])
    return InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = list_channels()
    if not channels:
        await update.message.reply_text(
            "Пока бот не добавлен ни в один канал. Добавьте бота администратором "
            "в канал с объявлениями, и он появится здесь."
        )
        return
    await update.message.reply_text(
        "Выберите канал, в котором искать объекты:",
        reply_markup=channel_picker_keyboard(channels)
    )


async def handle_channel_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]
    user_id = query.from_user.id
    user_channel_choice[user_id] = choice if choice == "ALL" else int(choice)
    user_search_criteria[user_id] = {}

    if choice == "ALL":
        label = "все каналы"
    else:
        conn = get_conn()
        row = conn.execute("SELECT title FROM channels WHERE chat_id=?", (int(choice),)).fetchone()
        conn.close()
        label = row["title"] if row else choice

    await query.edit_message_text(
        f"Готово! Ищем в: {label}\n\n"
        "Можете написать запрос текстом (например «Мирабад 2 комнаты 3 этаж»), "
        "или выбрать поля для точного поиска:",
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="Поиск по полям:",
        reply_markup=field_menu_keyboard(user_id)
    )


async def handle_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":", 1)[1]
    user_id = query.from_user.id
    user_awaiting_field[user_id] = field
    label = FIELD_LABELS.get(field, field)
    hint = " (число)" if field in NUMERIC_FIELDS else ""
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=f"Введите значение для «{label}»{hint}:"
    )


async def handle_search_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    user_id = query.from_user.id

    if action == "reset":
        user_search_criteria[user_id] = {}
        await context.bot.send_message(
            chat_id=query.message.chat_id, text="Критерии сброшены.",
            reply_markup=field_menu_keyboard(user_id)
        )
        return

    if action == "go":
        crit = user_search_criteria.get(user_id, {})
        criteria = {
            "rooms": crit.get("komnata"),
            "floor": crit.get("etaj"),
            "total_floors": crit.get("etajnost"),
            "max_price": crit.get("cena"),
            "max_area": crit.get("ploshad"),
        }
        if crit.get("rayon"):
            matched = None
            rayon_lower = str(crit["rayon"]).lower()
            for alias in sorted(DISTRICT_ALIASES, key=len, reverse=True):
                if alias in rayon_lower:
                    matched = DISTRICT_ALIASES[alias]
                    break
            if matched:
                criteria["district"] = matched
            else:
                criteria["district_text"] = crit["rayon"]
        if crit.get("jk"):
            criteria["jk"] = crit["jk"]
        if crit.get("orientir"):
            criteria["orientir"] = crit["orientir"]
        if crit.get("sostoyanie"):
            criteria["sostoyanie"] = crit["sostoyanie"]

        channel_id = user_channel_choice.get(user_id, "ALL")
        matches = search_listings(criteria, channel_id=channel_id)
        await send_results(context.bot, query.message.chat_id, matches)


async def send_results(bot, chat_id, matches):
    if not matches:
        await bot.send_message(chat_id=chat_id, text="😕 Ничего не найдено по вашему запросу.")
        return
    await bot.send_message(chat_id=chat_id, text=f"Найдено {len(matches)} объект(ов):")
    for l in matches:
        photo_ids = l["photo_file_ids"].split(",") if l.get("photo_file_ids") else []
        caption = l["raw_text"][:1024] if l["raw_text"] else None
        if photo_ids:
            if len(photo_ids) == 1:
                await bot.send_photo(chat_id=chat_id, photo=photo_ids[0], caption=caption)
            else:
                from telegram import InputMediaPhoto
                media = [InputMediaPhoto(pid, caption=caption if i == 0 else None)
                         for i, pid in enumerate(photo_ids[:10])]
                await bot.send_media_group(chat_id=chat_id, media=media)
        else:
            await bot.send_message(chat_id=chat_id, text=caption or "(без текста)")


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    # If we're mid-way through filling out a guided-search field, treat this as the value
    if user_id in user_awaiting_field:
        field = user_awaiting_field.pop(user_id)
        value = text.strip()
        if field in NUMERIC_FIELDS:
            digits = re.sub(r"[^\d]", "", value)
            value = int(digits) if digits else None
        user_search_criteria.setdefault(user_id, {})[field] = value
        await update.message.reply_text(
            f"Записано: {FIELD_LABELS.get(field, field)} = {value}",
            reply_markup=field_menu_keyboard(user_id)
        )
        return

    if user_id not in user_channel_choice:
        channels = list_channels()
        if not channels:
            await update.message.reply_text(
                "Пока бот не добавлен ни в один канал. Добавьте бота администратором в канал с объявлениями."
            )
            return
        await update.message.reply_text(
            "Сначала выберите канал для поиска:",
            reply_markup=channel_picker_keyboard(channels)
        )
        return

    channel_id = user_channel_choice[user_id]
    criteria = parse_query(text)
    matches = search_listings(criteria, channel_id=channel_id)
    await send_results(context.bot, update.message.chat_id, matches)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_channel_choice:
        channels = list_channels()
        if not channels:
            await update.message.reply_text("Бот пока не в каналах. Добавьте его администратором в канал.")
            return
        await update.message.reply_text("Сначала выберите канал:", reply_markup=channel_picker_keyboard(channels))
        return
    user_search_criteria.setdefault(user_id, {})
    await update.message.reply_text("Поиск по полям:", reply_markup=field_menu_keyboard(user_id))


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not found. Available env var names: %s", sorted(os.environ.keys()))
        raise RuntimeError("BOT_TOKEN environment variable is not set")

    init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(ChatMemberHandler(track_bot_added_to_channel, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))
    app.add_handler(CallbackQueryHandler(handle_channel_choice, pattern=r"^ch:"))
    app.add_handler(CallbackQueryHandler(handle_field_choice, pattern=r"^field:"))
    app.add_handler(CallbackQueryHandler(handle_search_action, pattern=r"^search:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))

    logger.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
