import re
from config import (
    DISTRICT_ALIASES,
    STOP_WORDS,
    MAX_SEARCH_RESULTS,
)
from database import execute_search


def parse_search_query(text: str):
    """
    Converts a human search into structured filters.
    Example:
    'регнум 2 комнаты до 120'
    """

    text = text.lower().strip()

    criteria = {
        "rooms": None,
        "floor": None,
        "district": None,
        "max_price": None,
        "keywords": []
    }

    # -----------------------------
    # Rooms
    # -----------------------------
    m = re.search(r"(\d+)\s*[- ]?(?:ком|комн|комната|комнаты)", text)

    if m:
        criteria["rooms"] = int(m.group(1))

    # -----------------------------
    # Floor
    # -----------------------------
    m = re.search(r"(\d+)\s*[- ]?этаж", text)

    if m:
        criteria["floor"] = int(m.group(1))

    # -----------------------------
    # Max price
    # -----------------------------
    m = re.search(r"до\s*(\d+)", text)

    if m:
        criteria["max_price"] = int(m.group(1))

    # -----------------------------
    # District aliases
    # -----------------------------
    for alias, district in DISTRICT_ALIASES.items():

        if alias in text:
            criteria["district"] = district
            break

    # -----------------------------
    # Remaining keywords
    # -----------------------------
    words = re.findall(r"[а-яёa-z0-9\-]+", text)

    for word in words:

        if len(word) < 2:
            continue

        if word in STOP_WORDS:
            continue

        if word.isdigit():
            continue

        criteria["keywords"].append(word)

    return criteria


def search(criteria):

    sql = "SELECT * FROM listings WHERE 1=1"

    params = []

    # -----------------------------
    # Rooms
    # -----------------------------
    if criteria["rooms"] is not None:

        sql += " AND rooms=?"

        params.append(criteria["rooms"])

    # -----------------------------
    # Floor
    # -----------------------------
    if criteria["floor"] is not None:

        sql += " AND floor=?"

        params.append(criteria["floor"])

    # -----------------------------
    # District
    # -----------------------------
    if criteria["district"]:

        sql += " AND district=?"

        params.append(criteria["district"])

    # -----------------------------
    # Price
    # -----------------------------
    if criteria["max_price"]:

        sql += " AND price<=?"

        params.append(criteria["max_price"])

    # -----------------------------
    # Keyword Search
    # -----------------------------
    for word in criteria["keywords"]:

        sql += """
        AND (

            LOWER(raw_text) LIKE LOWER(?)

            OR LOWER(IFNULL(complex_name,'')) LIKE LOWER(?)

            OR LOWER(IFNULL(landmark,'')) LIKE LOWER(?)

            OR LOWER(IFNULL(metro,'')) LIKE LOWER(?)

            OR LOWER(IFNULL(street,'')) LIKE LOWER(?)

        )
        """

        like = f"%{word}%"

        params.extend([like, like, like, like, like])

    sql += " ORDER BY post_date DESC"

    sql += f" LIMIT {MAX_SEARCH_RESULTS}"

    return execute_search(sql, params)
