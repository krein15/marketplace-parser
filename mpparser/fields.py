"""Registry of Excel columns the user can switch on and off."""

from __future__ import annotations

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
    only: str | None = None  # "wb" / "ozon" when only one marketplace provides the value


PRODUCT_FIELDS: list[Field] = [
    Field("marketplace", "Площадка", width=13, required=True),
    Field("article", "Артикул", width=13, required=True),
    Field("position", "Позиция", "int", width=10),
    Field("name", "Название", "wrap", width=48),
    Field("brand", "Бренд", width=18),
    Field("seller", "Продавец", width=24),
    Field("seller_rating", "Рейтинг продавца", "rating", width=11, default=False),
    Field("price", "Цена, ₽", "money", width=12),
    Field("price_card", "Цена по карте, ₽", "money", width=13, only="ozon"),
    Field("price_old", "Цена до скидки, ₽", "money", width=13),
    Field("discount", "Скидка, %", "percent", width=10),
    Field("rating", "Рейтинг", "rating", width=10),
    Field("reviews_count", "Отзывов", "int", width=11),
    Field("stock", "Остаток, шт", "int", width=11, default=False, only="ozon"),
    Field("category", "Категория", width=22, default=False),
    Field("image", "Фото", "url", width=12, default=False),
    Field("url", "Ссылка", "url", width=12),
    Field("parsed_at", "Дата сбора", "datetime", width=17, default=False),
]

REVIEW_FIELDS: list[Field] = [
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
    Field("seller_answer", "Ответ продавца", "wrap", width=40, default=False, only="wb"),
]

# Ozon search results do not include these values: each product card has to be opened, which is slower.
OZON_DETAIL_FIELDS = {"brand", "seller", "seller_rating", "price_card", "category"}
# WB search results do not include the category: it is read from the product card on the CDN.
WB_CARD_FIELDS = {"category"}


def default_keys(fields: list[Field]) -> list[str]:
    return [f.key for f in fields if f.default or f.required]


def resolve(fields: list[Field], keys: list[str] | set[str]) -> list[Field]:
    """Return the selected fields in registry order; required fields are always included."""
    wanted = set(keys)
    return [f for f in fields if f.required or f.key in wanted]
