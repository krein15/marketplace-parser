"""Helpers for turning marketplace display strings ("3 436 ₽", "−42%", "11 824") into numbers."""

from __future__ import annotations

import re

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
