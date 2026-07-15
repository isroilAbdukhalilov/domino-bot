import re

from config import DISTRICT_ALIASES


def find(pattern, text, cast=str):
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)

    if not m:
        return None

    value = m.group(1).strip()

    try:
        return cast(value)
    except:
        return value


def clean_price(value):

    if value is None:
        return None

    digits = re.sub(r"[^\d]", "", value)

    if digits == "":
        return None

    return int(digits)


def extract_district(text):

    lower = text.lower()

    for alias, district in DISTRICT_ALIASES.items():

        if alias in lower:
            return district

    m = re.search(
        r"([А-ЯЁа-яё\-\s]+район)",
        text,
        re.IGNORECASE
    )

    if m:
        return m.group(1).strip()

    return None


def parse_post(text):

    data = {}

    data["raw_text"] = text

    lines = [
        i.strip()
        for i in text.splitlines()
        if i.strip()
    ]

    data["property_type"] = lines[0] if lines else ""

    data["complex_name"] = find(

        r"ЖК[:\s]*([^\n]+)",

        text

    )

    data["district"] = extract_district(text)

    data["landmark"] = find(

        r"Ориентир[:\s]*([^\n]+)",

        text

    )

    data["street"] = find(

        r"Улица[:\s]*([^\n]+)",

        text

    )

    data["metro"] = find(

        r"Метро[:\s]*([^\n]+)",

        text

    )

    data["rooms"] = find(

        r"Комнат[:\s]*(\d+)",

        text,

        int

    )

    data["floor"] = find(

        r"Этаж[:\s]*(\d+)",

        text,

        int

    )

    data["total_floors"] = find(

        r"Этажность[:\s]*(\d+)",

        text,

        int

    )

    area = find(

        r"Общая площадь[:\s]*([\d.,]+)",

        text

    )

    if area:

        area = area.replace(",", ".")

        data["area"] = float(area)

    else:

        data["area"] = None

    data["price"] = clean_price(

        find(

            r"Цена[:\s]*([^\n]+)",

            text

        )

    )

    data["condition"] = find(

        r"Состояние[:\s]*([^\n]+)",

        text

    )

    phones = re.findall(

        r"\+998\d{9}",

        text

    )

    data["phones"] = ",".join(

        sorted(set(phones))

    )

    data["with_furniture"] = (

        "мебелью" in text.lower()

    )

    data["new_building"] = (

        "новостройка" in text.lower()

    )

    data["direct_sale"] = (

        "прямой" in text.lower()

    )

    rent = find(

        r"С арендатором[:\s]*([^\n]+)",

        text

    )

    data["tenant_income"] = rent

    return data
