import json
import os
import re
import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ---------- Load data ----------
DATA_PATH = os.path.join(os.path.dirname(__file__), "listings.json")
with open(DATA_PATH, "r", encoding="utf-8") as f:
    LISTINGS = json.load(f)

DISTRICT_ALIASES = {
    "юнусабад": "Юнусабадский",
    "мирабад": "Мирабадский",
    "мирабд": "Мирабадский",
    "шайхантахур": "Шайхантахурский",
    "чиланзар": "Чиланзарский",
    "сергели": "Сергелийский",
    "яккасарай": "Яккасарайский",
    "мирзо улугбек": "Мирзо-Улугбекский",
    "мирзо-улугбек": "Мирзо-Улугбекский",
    "учтепа": "Учтепинский",
    "бектемир": "Бектемирский",
    "яшнабад": "Яшнабадский",
    "алмазар": "Алмазарский",
}


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

    # leftover free-text keywords for fallback full-text search
    # strip out numbers/keywords already consumed, keep meaningful words
    stripped = re.sub(r"\d+", "", text_lower)
    for kw in ["комн", "этаж", "этажность", "до", "тыс", "у.е", "у.е.", "сум"]:
        stripped = stripped.replace(kw, "")
    leftover_words = [w for w in re.findall(r"[а-яa-z\-]+", stripped) if len(w) > 3]
    criteria["free_text"] = leftover_words

    return criteria


def search_listings(criteria, limit=6):
    results = []
    for l in LISTINGS:
        if criteria.get("rooms") is not None and l.get("rooms") != criteria["rooms"]:
            continue
        if criteria.get("floor") is not None and l.get("floor") != criteria["floor"]:
            continue
        if criteria.get("max_price") is not None:
            if l.get("price") is None or l["price"] > criteria["max_price"]:
                continue
        if criteria.get("district") is not None and l.get("district") != criteria["district"]:
            continue
        if criteria.get("free_text"):
            raw_lower = l.get("raw_text", "").lower()
            if not any(w in raw_lower for w in criteria["free_text"]):
                # only reject on free_text if we have no other criteria matched
                if not any(k in criteria for k in ("rooms", "floor", "max_price", "district")):
                    continue
        results.append(l)
    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    return results[:limit]


def format_listing(l):
    lines = [f"🏠 {l.get('type', 'Квартира')}"]
    if l.get("district"):
        lines.append(f"📍 Район: {l['district']}")
    if l.get("rooms"):
        lines.append(f"🚪 Комнат: {l['rooms']}")
    if l.get("floor") and l.get("total_floors"):
        lines.append(f"🏢 Этаж: {l['floor']}/{l['total_floors']}")
    if l.get("area_m2"):
        lines.append(f"📐 Площадь: {l['area_m2']} м²")
    if l.get("condition"):
        lines.append(f"🔧 Состояние: {l['condition']}")
    if l.get("price"):
        lines.append(f"💰 Цена: {l['price']:,} у.е.".replace(",", " "))
    if l.get("phones"):
        lines.append(f"📲 {l['phones']}")
    return "\n".join(lines)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! 👋\n\n"
        "Напишите, что вы ищете, например:\n"
        "«Мирабад 2 комнаты 3 этаж»\n"
        "«Чиланзар 3 комнаты до 100 тыс»\n\n"
        "Я найду подходящие объекты из базы."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    criteria = parse_query(text)
    matches = search_listings(criteria)

    if not matches:
        await update.message.reply_text(
            "😕 Ничего не найдено по вашему запросу. Попробуйте изменить критерии."
        )
        return

    header = f"Найдено {len(matches)} объект(ов):\n"
    await update.message.reply_text(header)
    for l in matches:
        await update.message.reply_text(format_listing(l))


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        env_keys = sorted(os.environ.keys())
        logger.error("BOT_TOKEN not found. Available env var names: %s", env_keys)
        raise RuntimeError("BOT_TOKEN environment variable is not set")
    else:
        logger.info("BOT_TOKEN found, length=%d, starts_with=%s", len(token), token[:6])

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
