"""User settings: what to collect and where to save it. Persisted between launches as JSON.

Settings that belong to one marketplace (its region, its filters, its list of links) are not named here:
they live in ``ids`` and ``options`` under the marketplace key, and the marketplace itself declares them
(see ``mpparser/plugins.py``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import APP_NAME, plugins
from .fields import default_keys, product_fields, review_fields

log = logging.getLogger(__name__)


class InputMode(StrEnum):
    QUERY = "query"
    IDS = "ids"  # product links and article numbers, plus links to search results with filters set on the site


class SortOrder(StrEnum):
    POPULAR = "popular"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING = "rating"
    NEW = "new"


SORT_TITLES = {
    SortOrder.POPULAR: "По популярности",
    SortOrder.PRICE_ASC: "Сначала дешёвые",
    SortOrder.PRICE_DESC: "Сначала дорогие",
    SortOrder.RATING: "По рейтингу",
    SortOrder.NEW: "Новинки",
}

MAX_PRODUCTS_LIMIT = 10_000
MAX_REVIEWS_LIMIT = 1_000
DEFAULT_MARKETPLACES = ["wb", "ozon"]


def marketplace_titles() -> dict[str, str]:
    return plugins.titles()


def marketplace_short() -> dict[str, str]:
    return plugins.short_titles()


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    path = Path(base) / APP_NAME.replace(" ", "")
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


@dataclass
class ParseSettings:
    marketplaces: list[str] = field(default_factory=lambda: list(DEFAULT_MARKETPLACES))
    mode: InputMode = InputMode.QUERY
    query: str = ""
    ids: dict[str, str] = field(default_factory=dict)  # marketplace key → free text: articles and/or links
    max_products: int = 100
    sort: SortOrder = SortOrder.POPULAR
    collect_reviews: bool = True
    max_reviews: int = 20
    # Price applies to every marketplace; everything else a marketplace asks for lives in ``options``.
    price_min: int | None = None
    price_max: int | None = None
    options: dict[str, dict[str, Any]] = field(default_factory=dict)  # marketplace key → its filter values
    product_fields: list[str] = field(default_factory=lambda: default_keys(product_fields()))
    review_fields: list[str] = field(default_factory=lambda: default_keys(review_fields()))
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    open_when_done: bool = True
    show_browser: bool = False
    task_name: str = ""  # set when the run is a saved monitoring task: its history is kept under this name

    # --- per-marketplace values ---

    def ids_text(self, marketplace: str) -> str:
        return self.ids.get(marketplace, "")

    def set_ids(self, marketplace: str, text: str) -> None:
        self.ids[marketplace] = text

    def option(self, marketplace: str, key: str) -> Any:
        """Value of a marketplace filter, falling back to the default declared by the marketplace."""
        stored = self.options.get(marketplace, {})
        if key in stored:
            return stored[key]
        plugin = plugins.get(marketplace)
        option = plugin.option(key) if plugin else None
        return option.default if option else None

    def set_option(self, marketplace: str, key: str, value: Any) -> None:
        self.options.setdefault(marketplace, {})[key] = value

    def locations(self, marketplace: str, key: str) -> list[tuple[str, str]]:
        """Chosen places of a "multi" filter as (name, site slug) pairs."""
        plugin = plugins.get(marketplace)
        option = plugin.option(key) if plugin else None
        names = self.option(marketplace, key) or []
        return [(name, option.slug_of(name) if option else name) for name in names]

    # --- checks and summaries ---

    def validate(self) -> list[str]:
        """Return human-readable problems; an empty list means the settings can be run."""
        problems = []
        known = plugins.keys()
        if unknown := [key for key in self.marketplaces if key not in known]:
            titles = ", ".join(unknown)
            problems.append(f"Площадки нет в программе: {titles}. Возможно, не установлен её модуль.")
        if not self.marketplaces:
            problems.append("Выберите хотя бы одну площадку.")
        if self.mode == InputMode.QUERY and not self.query.strip():
            problems.append("Введите поисковый запрос.")
        if self.mode == InputMode.IDS and not any(self.ids_text(mp).strip() for mp in self.marketplaces):
            problems.append("Добавьте артикулы или ссылки на товары.")
        if not 1 <= self.max_products <= MAX_PRODUCTS_LIMIT:
            problems.append(f"Количество товаров должно быть от 1 до {MAX_PRODUCTS_LIMIT}.")
        if self.collect_reviews and not 1 <= self.max_reviews <= MAX_REVIEWS_LIMIT:
            problems.append(f"Количество отзывов должно быть от 1 до {MAX_REVIEWS_LIMIT}.")
        if not self.output_dir.strip():
            problems.append("Укажите папку для сохранения.")
        if any(p is not None and p < 0 for p in (self.price_min, self.price_max)):
            problems.append("Цена не может быть отрицательной.")
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            problems.append("Минимальная цена больше максимальной.")
        problems += self._option_problems()
        return problems

    def _option_problems(self) -> list[str]:
        problems = []
        for key in self.marketplaces:
            plugin = plugins.get(key)
            if not plugin:
                continue
            for option in plugin.options:
                if not option.required or (option.query_only and self.mode != InputMode.QUERY):
                    continue
                if not self.option(key, option.key):
                    problems.append(option.required_message
                                    or f"{plugin.title}: выберите хотя бы одно значение — {option.title}.")
        return problems

    def filters_summary(self) -> list[tuple[str, str]]:
        """Active filters as (label, value) rows for the report."""
        rows = []
        if self.price_min is not None or self.price_max is not None:
            low = f"от {self.price_min:,} ₽".replace(",", " ") if self.price_min is not None else ""
            high = f"до {self.price_max:,} ₽".replace(",", " ") if self.price_max is not None else ""
            rows.append(("Цена", " ".join(filter(None, (low, high)))))
        for key in self.marketplaces:
            plugin = plugins.get(key)
            if not plugin:
                continue
            grouped = []
            for option in plugin.options:
                if option.query_only and self.mode != InputMode.QUERY:
                    continue
                text = option.summary(self.option(key, option.key))
                if not text:
                    continue
                if option.own_row:
                    rows.append((option.label or option.title, text))
                else:
                    grouped.append(text)
            if grouped:
                rows.append((plugin.filters_title(), ", ".join(grouped)))
        return rows

    # --- persistence ---

    @classmethod
    def path(cls) -> Path:
        return app_data_dir() / "settings.json"

    @classmethod
    def load(cls) -> ParseSettings:
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("Settings file is unreadable, using defaults: %s", exc)
            return cls()
        return cls.from_dict(raw) or cls()

    @classmethod
    def from_dict(cls, raw: dict) -> ParseSettings | None:
        """Settings from saved JSON; unknown keys are ignored. None if the values do not fit."""
        raw = _migrate(raw)
        known = {f.name for f in fields(cls)}
        try:
            settings = cls(**{k: v for k, v in raw.items() if k in known})
            settings.mode = InputMode(settings.mode)
            settings.sort = SortOrder(settings.sort)
        except (TypeError, ValueError):
            return None
        return settings

    def save(self) -> None:
        try:
            self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)


# Settings files written before marketplaces became plugins: "wb_ids" and "avito_seller" instead of
# ids["wb"] and options["avito"]["seller"].
LEGACY_OPTIONS = {
    "region": ("wb", "region"),
    "ym_rating_4": ("ym", "rating_4"),
    "ym_delivery_days": ("ym", "delivery_days"),
    "avito_locations": ("avito", "locations"),
    "avito_seller": ("avito", "seller"),
    "avito_delivery": ("avito", "delivery"),
    "avito_title_only": ("avito", "title_only"),
}


def _migrate(raw: dict) -> dict:
    if not isinstance(raw, dict):
        return raw
    raw = dict(raw)
    ids = dict(raw.get("ids") or {})
    options: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (raw.get("options") or {}).items()}
    for name in list(raw):
        if name.endswith("_ids") and isinstance(raw[name], str):
            ids.setdefault(name.removesuffix("_ids"), raw.pop(name))
        elif name in LEGACY_OPTIONS:
            marketplace, key = LEGACY_OPTIONS[name]
            options.setdefault(marketplace, {}).setdefault(key, raw.pop(name))
    if ids:
        raw["ids"] = ids
    if options:
        raw["options"] = options
    return raw
