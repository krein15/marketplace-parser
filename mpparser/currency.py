"""Exchange rates of the Bank of Russia (ЦБ РФ).

A marketplace that sells in another currency (Alibaba.com sells in dollars) converts its prices to roubles, so that
they stand in the same "Цена, ₽" column as everybody else's and take part in the price history and the comparison
with rivals. The rate of the day is taken from the Bank of Russia and kept in the data folder: if the site
cannot be reached, the last known rate is used and the report says so.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
SOURCE = "ЦБ РФ"

_DATE = re.compile(r'<ValCurs[^>]*\bDate="(\d{2}\.\d{2}\.\d{4})"')
_VALUTE = re.compile(r"<Valute\b.*?</Valute>", re.S)
_FIELD = re.compile(r"<(CharCode|Nominal|Value)>([^<]*)</")


class RateError(RuntimeError):
    """No rate today and none saved before: prices cannot be converted."""


@dataclass(frozen=True)
class Rate:
    code: str  # "USD"
    value: float  # roubles for one unit of the currency
    day: date  # the date the Bank of Russia set the rate for
    stale: bool = False  # the site did not answer; this is the last saved rate

    def to_rub(self, amount: float | None) -> float | None:
        return round(amount * self.value, 2) if amount is not None else None

    def from_rub(self, amount: float | None) -> float | None:
        return round(amount / self.value, 2) if amount is not None else None

    def describe(self) -> str:
        """How the rate reads in the log and in the report: "12,26 ₽ (ЦБ РФ на 06.11.2026)"."""
        value = f"{self.value:.2f}".replace(".", ",")
        note = ", сохранённый: сайт ЦБ не ответил" if self.stale else ""
        return f"{value} ₽ ({SOURCE} на {self.day:%d.%m.%Y}{note})"


def parse_cbr(xml_text: str, code: str) -> Rate | None:
    """The rate of ``code`` from the daily XML of the Bank of Russia. None if the currency is not there."""
    day_match = _DATE.search(xml_text)
    if not day_match:
        return None
    day = datetime.strptime(day_match.group(1), "%d.%m.%Y").date()
    for block in _VALUTE.findall(xml_text):
        values = dict(_FIELD.findall(block))
        if values.get("CharCode") != code:
            continue
        try:
            value = float(values["Value"].replace(",", ".")) / int(values.get("Nominal") or 1)
        except (KeyError, ValueError, ZeroDivisionError):
            return None
        return Rate(code=code, value=value, day=day) if value > 0 else None
    return None


def _cache_path(data_dir: Path) -> Path:
    return data_dir / "rates.json"


def save_rate(data_dir: Path, rate: Rate) -> None:
    path = _cache_path(data_dir)
    try:
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        saved = {}
    saved[rate.code] = {"value": rate.value, "day": rate.day.isoformat()}
    try:
        path.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not save the exchange rate: %s", exc)


def saved_rate(data_dir: Path, code: str) -> Rate | None:
    try:
        entry = json.loads(_cache_path(data_dir).read_text(encoding="utf-8"))[code]
        return Rate(code=code, value=float(entry["value"]), day=date.fromisoformat(entry["day"]), stale=True)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def choose_rate(xml_text: str | None, code: str, data_dir: Path) -> Rate:
    """Today's rate from the downloaded XML, else the saved one. Saves a fresh rate for the next time."""
    rate = parse_cbr(xml_text, code) if xml_text else None
    if rate:
        save_rate(data_dir, rate)
        return rate
    if saved := saved_rate(data_dir, code):
        return saved
    raise RateError(f"Не удалось узнать курс {code}: сайт ЦБ РФ не ответил, а сохранённого курса ещё нет.")
