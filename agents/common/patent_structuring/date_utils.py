import re


def parse_common_date_string(value: str) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None

    match = re.search(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", text)
    if match:
        normalized = _format_date_parts(match.group(1), match.group(2), match.group(3))
        if normalized:
            return normalized

    match = re.search(r"(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{4})", text)
    if match:
        normalized = _format_date_parts(match.group(3), match.group(2), match.group(1))
        if normalized:
            return normalized

    match = re.search(r"\b([A-Za-z]{3,9})\.?\s*(\d{1,2}),\s*(\d{4})", text, re.IGNORECASE)
    if match:
        month = _MONTH_MAP.get(match.group(1).lower())
        if month:
            normalized = _format_date_parts(match.group(3), str(month), match.group(2))
            if normalized:
                return normalized

    return None


def _format_date_parts(year_text: str, month_text: str, day_text: str) -> str | None:
    year = int(year_text)
    month = int(month_text)
    day = int(day_text)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}.{month:02d}.{day:02d}"


_MONTH_MAP: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
