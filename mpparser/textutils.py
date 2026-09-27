"""Helpers for turning marketplace display strings ("3 436 ₽", "−42%", "11 824") into numbers."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _compact(text: str) -> str:
    # Marketplaces use regular, thin and non-breaking spaces as thousands separators.
    return re.sub(r"[\s   ]", "", text)


def parse_float(text: str | None) -> float | None:
    if not text:
        return None
    match = _NUMBER.search(_compact(str(text)))
    return float(match.group().replace(",", ".")) if match else None


def parse_int(text: str | None) -> int | None:
    value = parse_float(text)
    return int(value) if value is not None else None


def parse_count(text: str | None) -> int | None:
    """Parse counters that may be abbreviated: "97 K", "1,2 тыс."."""
    value = parse_float(text)
    if value is None:
        return None
    lowered = str(text).lower()
    if "k" in lowered or "к" in lowered or "тыс" in lowered:
        value *= 1_000
    elif "m" in lowered or "млн" in lowered:
        value *= 1_000_000
    return int(value)


MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_RU_DATE = re.compile(r"(\d{1,2})\s+([а-яё]+)(?:\s+(\d{4}))?", re.IGNORECASE)
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_AGO = re.compile(r"(\d+)?\s*(минут|час|дн|день|недел)[а-яё]*\s+назад")
_AGO_UNITS = {"минут": timedelta(minutes=1), "час": timedelta(hours=1), "дн": timedelta(days=1),
              "день": timedelta(days=1), "недел": timedelta(weeks=1)}


def parse_ru_date(text: str | None, now: datetime | None = None) -> datetime | None:
    """Parse dates the way Russian sites print them.

    "2 июля", "12 декабря 2025", "17 сентября в 12:21", "сегодня в 10:15", "вчера", "3 часа назад", "неделю назад".
    A date without a year belongs to the current year, or to the previous one if it would be in the future.
    The time is kept when the text has it; otherwise the result is midnight.
    """
    if not text:
        return None
    now = now or datetime.now()
    today = now.date()
    lowered = text.strip().lower()
    if match := _AGO.search(lowered):
        return (now - int(match.group(1) or 1) * _AGO_UNITS[match.group(2)]).replace(second=0, microsecond=0)
    if "сегодня" in lowered:
        day = today
    elif "вчера" in lowered:
        day = today - timedelta(days=1)
    else:
        match = _RU_DATE.search(lowered)
        if not match or match.group(2) not in MONTHS:
            return None
        explicit_year = match.group(3)
        try:
            day = date(int(explicit_year or today.year), MONTHS[match.group(2)], int(match.group(1)))
            if not explicit_year and day > today:
                day = day.replace(year=today.year - 1)
        except ValueError:  # 31 июня, or 29 февраля moved to a non-leap year
            return None
    hour = minute = 0
    if (time := _TIME.search(lowered)) and int(time.group(1)) < 24 and int(time.group(2)) < 60:
        hour, minute = int(time.group(1)), int(time.group(2))
    return datetime(day.year, day.month, day.day, hour, minute)
