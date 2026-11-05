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
    query: str = ""  # the query that found it, when a run collects several queries at once
    image: str = ""
    url: str = ""
    # Classifieds (Avito): where the listing is, when it was published, who sells it.
    region: str = ""
    address: str = ""
    published: datetime | None = None
    views: int | None = None
    seller_type: str = ""
    seller_reviews: int | None = None
    # Wholesale (Alibaba.com): dollar prices that depend on the quantity, the minimum order, who the supplier is.
    price_usd: float | None = None  # the lowest price, paid for the largest quantity
    price_usd_max: float | None = None  # the price of the minimum order
    min_order: int | None = None
    price_tiers: str = ""  # "50–999 шт. — 7,45 $; от 1 000 шт. — 6,50 $"
    sold: int | None = None
    supplier_type: str = ""  # manufacturer or trading company
    supplier_years: int | None = None
    origin: str = ""  # where the goods come from: "Guangdong, China"
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
    seller: str = ""  # Avito reviews are about the seller, not the listing
