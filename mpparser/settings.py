"""User settings: what to collect and where to save it. Persisted between launches as JSON."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path

from . import APP_NAME
from .fields import PRODUCT_FIELDS, REVIEW_FIELDS, default_keys
from .regions import AVITO_LOCATIONS, DEFAULT_AVITO_LOCATION, DEFAULT_REGION

log = logging.getLogger(__name__)

MARKETPLACES = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет", "avito": "Авито"}
# Short names for file names and compact labels.
MARKETPLACE_SHORT = {"wb": "WB", "ozon": "Ozon", "ym": "ЯМ", "avito": "Авито"}


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

class SellerType(StrEnum):
    ALL = "all"
    PRIVATE = "private"
    COMPANY = "company"


SELLER_TYPE_TITLES = {SellerType.ALL: "Все", SellerType.PRIVATE: "Частные", SellerType.COMPANY: "Компании"}
DELIVERY_DAYS_TITLES = {0: "Любой", 3: "До 3 дней", 7: "До 7 дней"}

MAX_PRODUCTS_LIMIT = 10_000
MAX_REVIEWS_LIMIT = 1_000


def app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share"
    path = Path(base) / APP_NAME.replace(" ", "")
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


@dataclass
class ParseSettings:
    marketplaces: list[str] = field(default_factory=lambda: ["wb", "ozon"])
    mode: InputMode = InputMode.QUERY
    query: str = ""
    wb_ids: str = ""  # free text: articles and/or links, one per line
    ozon_ids: str = ""
    ym_ids: str = ""
    avito_ids: str = ""
    max_products: int = 100
    sort: SortOrder = SortOrder.POPULAR
    collect_reviews: bool = True
    max_reviews: int = 20
    region: str = DEFAULT_REGION
    # Filters. Price applies to every marketplace, the rest to the one named in the prefix.
    price_min: int | None = None
    price_max: int | None = None
    ym_rating_4: bool = False  # "Рейтинг от 4.0"
    ym_delivery_days: int = 0  # 0 — any, 3 or 7
    avito_locations: list[str] = field(default_factory=lambda: [DEFAULT_AVITO_LOCATION])
    avito_seller: SellerType = SellerType.ALL
    avito_delivery: bool = False  # only listings with Avito Delivery
    avito_title_only: bool = False  # search in titles only
    product_fields: list[str] = field(default_factory=lambda: default_keys(PRODUCT_FIELDS))
    review_fields: list[str] = field(default_factory=lambda: default_keys(REVIEW_FIELDS))
    output_dir: str = field(default_factory=lambda: str(default_output_dir()))
    open_when_done: bool = True
    show_browser: bool = False

    def validate(self) -> list[str]:
        """Return human-readable problems; an empty list means the settings can be run."""
        problems = []
        if not self.marketplaces:
            problems.append("Выберите хотя бы одну площадку.")
        if self.mode == InputMode.QUERY and not self.query.strip():
            problems.append("Введите поисковый запрос.")
        if self.mode == InputMode.IDS and not any(
            getattr(self, f"{mp}_ids").strip() for mp in self.marketplaces
        ):
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
        if "avito" in self.marketplaces and self.mode == InputMode.QUERY and not self.avito_locations:
            problems.append("Выберите хотя бы один город для Авито.")
        return problems

    def filters_summary(self) -> list[tuple[str, str]]:
        """Active filters as (label, value) rows for the report."""
        rows = []
        if self.price_min is not None or self.price_max is not None:
            low = f"от {self.price_min:,} ₽".replace(",", " ") if self.price_min is not None else ""
            high = f"до {self.price_max:,} ₽".replace(",", " ") if self.price_max is not None else ""
            rows.append(("Цена", " ".join(filter(None, (low, high)))))
        if "ym" in self.marketplaces:
            ym = [t for t, on in (("рейтинг от 4.0", self.ym_rating_4),
                                  (DELIVERY_DAYS_TITLES.get(self.ym_delivery_days, "").lower(),
                                   bool(self.ym_delivery_days))) if on]
            if ym:
                rows.append(("Фильтры Маркета", ", ".join(ym)))
        if "avito" in self.marketplaces:
            if self.mode == InputMode.QUERY:
                rows.append(("Города Авито", ", ".join(self.avito_locations)))
            avito = [t for t, on in ((f"продавцы: {SELLER_TYPE_TITLES[self.avito_seller].lower()}",
                                      self.avito_seller != SellerType.ALL),
                                     ("с Авито Доставкой", self.avito_delivery),
                                     ("только в названиях", self.avito_title_only)) if on]
            if avito:
                rows.append(("Фильтры Авито", ", ".join(avito)))
        return rows

    def avito_location_slugs(self) -> list[tuple[str, str]]:
        """Selected Avito locations as (name, URL slug). Unknown names are treated as slugs typed by the user."""
        return [(name, AVITO_LOCATIONS.get(name, name)) for name in self.avito_locations]

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
        known = {f.name for f in fields(cls)}
        settings = cls(**{k: v for k, v in raw.items() if k in known})
        try:
            settings.mode = InputMode(settings.mode)
            settings.sort = SortOrder(settings.sort)
            settings.avito_seller = SellerType(settings.avito_seller)
        except ValueError:
            return cls()
        return settings

    def save(self) -> None:
        try:
            self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)
