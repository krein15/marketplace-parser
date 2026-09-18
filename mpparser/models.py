"""Data models shared by all marketplace parsers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Product:
    marketplace: str
    article: str
    name: str = ""
    brand: str = ""
    seller: str = ""
    seller_rating: float | None = None
    price: float | None = None
    price_card: float | None = None
    price_old: float | None = None
    discount: int | None = None
    rating: float | None = None
    reviews_count: int | None = None
    stock: int | None = None
    category: str = ""
    position: int | None = None
    image: str = ""
    url: str = ""
    parsed_at: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))
    # Internal: key used to fetch reviews (WB groups reviews of all colour variants under one "root").
    reviews_key: str = ""

    def fill_discount(self) -> None:
        """Derive the discount percentage from the prices if the marketplace did not provide it."""
        if self.discount is None and self.price and self.price_old and self.price_old > self.price:
            self.discount = round((1 - self.price / self.price_old) * 100)


@dataclass
class Review:
    marketplace: str
    article: str
    product_name: str = ""
    date: datetime | None = None
    rating: int | None = None
    author: str = ""
    text: str = ""
    pros: str = ""
    cons: str = ""
    variant: str = ""
    photos: int = 0
    likes: int | None = None
    seller_answer: str = ""
