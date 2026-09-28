"""Registry of Excel columns the user can switch on and off.

Columns that only one marketplace fills are declared by that marketplace (see ``mpparser/plugins.py``) and
appear here through :func:`product_fields` / :func:`review_fields`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Kind = Literal["text", "wrap", "int", "rating", "money", "percent", "url", "datetime"]


@dataclass(frozen=True)
class Field:
    key: str  # attribute name on Product / Review
    title: str  # column header and checkbox label
    kind: Kind = "text"
    width: int = 14
    default: bool = True
    required: bool = False  # always exported, checkbox is locked
    only: tuple[str, ...] = ()  # marketplace keys that provide the value, empty when all of them do
    tail: bool = False  # keep at the end of the table, after the columns added by marketplaces


CORE_PRODUCT_FIELDS: list[Field] = [
    Field("marketplace", "Площадка", width=13, required=True),
    Field("article", "Артикул", width=13, required=True),
    Field("position", "Позиция", "int", width=10),
    Field("name", "Название", "wrap", width=48),
    Field("brand", "Бренд", width=18),
    Field("seller", "Продавец", width=24),
    Field("seller_rating", "Рейтинг продавца", "rating", width=11, default=False),
    Field("price", "Цена, ₽", "money", width=12),
    Field("price_card", "Цена по карте / Пэй, ₽", "money", width=13, only=("ozon", "ym")),
    Field("price_old", "Цена до скидки, ₽", "money", width=13),
    Field("discount", "Скидка, %", "percent", width=10),
    Field("rating", "Рейтинг", "rating", width=10),
    Field("reviews_count", "Отзывов", "int", width=11),
    Field("stock", "Остаток, шт", "int", width=11, default=False, only=("ozon",)),
    Field("category", "Категория", width=22, default=False),
    Field("image", "Фото", "url", width=12, default=False),
    Field("url", "Ссылка", "url", width=12),
    Field("parsed_at", "Дата сбора", "datetime", width=17, default=False, tail=True),
]

CORE_REVIEW_FIELDS: list[Field] = [
    Field("marketplace", "Площадка", width=13, required=True),
    Field("article", "Артикул", width=13, required=True),
    Field("product_name", "Товар", "wrap", width=36, default=False),
    Field("date", "Дата", "datetime", width=17),
    Field("rating", "Оценка", "int", width=9),
    Field("author", "Автор", width=18),
    Field("text", "Текст отзыва", "wrap", width=60),
    Field("pros", "Достоинства", "wrap", width=36),
    Field("cons", "Недостатки", "wrap", width=36),
    Field("variant", "Вариант", width=20, default=False),
    Field("photos", "Фото, шт", "int", width=9, default=False),
    Field("likes", "Полезно", "int", width=9, default=False),
    Field("seller_answer", "Ответ продавца", "wrap", width=40, default=False, only=("wb",)),
]


def product_fields() -> list[Field]:
    """Product columns of the core plus the ones declared by the installed marketplaces."""
    from . import plugins

    return plugins.product_fields(CORE_PRODUCT_FIELDS)


def review_fields() -> list[Field]:
    from . import plugins

    return plugins.review_fields(CORE_REVIEW_FIELDS)


def default_keys(fields: Sequence[Field]) -> list[str]:
    return [f.key for f in fields if f.default or f.required]


def resolve(fields: Sequence[Field], keys: list[str] | set[str],
            marketplaces: list[str] | None = None) -> list[Field]:
    """Return the selected fields in registry order; required fields are always included.

    With ``marketplaces`` given, columns that none of them fills are dropped (no empty "Город" in a WB report).
    """
    wanted = set(keys)
    return [f for f in fields if (f.required or f.key in wanted)
            and (marketplaces is None or not f.only or set(f.only) & set(marketplaces))]
