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
from .regions import DEFAULT_REGION

log = logging.getLogger(__name__)

MARKETPLACES = {"wb": "Wildberries", "ozon": "Ozon"}


class InputMode(StrEnum):
    QUERY = "query"
    IDS = "ids"
    # CATEGORY = "category"  # extension point: add a mode here and a MarketplaceParser.category() implementation


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
    max_products: int = 100
    sort: SortOrder = SortOrder.POPULAR
    collect_reviews: bool = True
    max_reviews: int = 20
    region: str = DEFAULT_REGION
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
        return problems

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
        except ValueError:
            return cls()
        return settings

    def save(self) -> None:
        try:
            self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)
