import re
from config import DISTRICT_ALIASES, DISTRICTS


def _find(pattern, text, cast=str):
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)

    if not m:
        return None

    try:
        return cast(m.group(1).strip())
    except:
        return m.group(1).strip()


def normalize_price(value):

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

    for district in DISTRICTS:

        if district.lower() in lower:
            return district

    return None


def extract_complex(text):

    patterns = [

        r"(?:ЖК|Ж/К)\s*[:\-]?\s*([^\n]+)",

        r"(?:Комплекс)\s*[:\-]?\s*([^\n]+)",

        r"(?:Жилой комплекс)\s*[:\-]?\s*([^\n]+)"

    ]

    for p in patterns:

        value = _find(p, text)

        if value:
            return value

    return None


def extract_landmark(text):

    patterns = [

        r"(?:Ориентир)\s*[:\-]?\s*([^\n]+)",

        r"(?:Ориентир:)\s*([^\n]+)"

    ]

    for p in patterns:

        value = _find(p, text)

        if value:
            return value

    return None


def extract_metro(text):

    patterns = [

        r"(?:Метро)\s*[:\-]?\s*([^\n]+)",

        r"(?:м\.)\s*([^\n]+)"

    ]

    for p in patterns:

        value = _find(p, text)

        if value:
            return value

    return None


def extract_street(text):

    patterns = [

        r"(?:Улица)\s*[:\-]?\s*([^\n]+)",

        r"(?:ул\.)\s*([^\n]+)"

    ]

    for p in patterns:

        value = _find(p, text)

        if value:
            return value

    return None


def parse_post(text):

    data = {}

    data["raw_text"] = text

    first = text.strip().split("\n")

    data["property_type"] = first[0] if first else ""

    data["complex_name"] = extract_complex(text)

    data["district"] = extract_district(text)

    data["landmark"] = extract_landmark(text)

    data["metro"] = extract_metro(text)

    data["street"] = extract_street(text)

    data["rooms"] = _find(

        r"Комнат[а-я]*\s*[:\-]?\s*(\d+)",

        text,

        int

    )

    data["floor"] = _find(

        r"(?<!Э)Этаж\s*[:\-]?\s*(\d+)",

        text,

        int

    )

    data["total_floors"] = _find(

        r"Этажность\s*[:\-]?\s*(\d+)",

        text,

        int

    )

    data["area"] = _find(

        r"(?:Площадь|Общая площадь)\s*[:\-]?\s*([\d.,]+)",

        text,

        float

    )

    price = _find(

        r"Цена\s*[:\-]?\s*([^\n]+)",

        text

    )

    data["price"] = normalize_price(price)

    data["condition"] = _find(

        r"Состояние\s*[:\-]?\s*([^\n]+)",

        text

    )

    phones = re.findall(

        r"\+998\d{9}",

        text

    )

    data["phones"] = ",".join(sorted(set(phones)))

    return data
