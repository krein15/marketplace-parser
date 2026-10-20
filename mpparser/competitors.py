"""My products against the rest of the search results.

The user marks what is theirs — article numbers, product links, their brand or seller name — and the report
gains the «Конкуренты» sheet: my price next to the prices of the same search, who is cheaper and by how much,
how my rating and review count look against the others.

Everything is counted inside one marketplace: prices, ratings and places are comparable only there.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from . import plugins
from .inputs import parse_ids
from .models import Product

RIVALS_PER_PRODUCT = 5  # how many cheaper rivals the report lists for each of my products
MIN_GAP = 1.0  # rubles; a smaller price difference is rounding noise


def _names(text: str) -> set[str]:
    """Brand and seller names from a box where they are separated by commas or line breaks."""
    parts = (part for line in text.splitlines() for part in line.split(","))
    return {name.strip().lower() for name in parts if name.strip()}


@dataclass(frozen=True)
class Own:
    """What the user called their own: article numbers and brand or seller names in lower case."""

    articles: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.articles or self.names)

    def matches(self, product: Product) -> bool:
        if product.article in self.articles:
            return True
        mine = (product.brand.strip().lower(), product.seller.strip().lower())
        return any(name and name in self.names for name in mine)


def parse_own(articles: str, names: str) -> Own:
    """Read the two boxes: articles and product links in any order, brand or seller names in the other."""
    default = next(iter(plugins.keys()), "")
    parsed = parse_ids(articles, default)
    numbers = {article for found in parsed.items.values() for article in found}
    # A short or unusual article number is not something we can search for, but it may still be mine.
    numbers |= {token for token in parsed.invalid if "/" not in token}
    return Own(frozenset(numbers), frozenset(_names(names)))


@dataclass
class Spot:
    """One of my products against the rivals of the same marketplace, cheapest first."""

    product: Product
    rivals: list[Product] = field(default_factory=list)  # only those with a known price

    @property
    def price(self) -> float | None:
        return self.product.price

    @property
    def cheaper(self) -> list[Product]:
        """Rivals that undercut me, cheapest first."""
        if not self.price:
            return []
        return [r for r in self.rivals if r.price is not None and self.price - r.price >= MIN_GAP]

    @property
    def rank(self) -> int | None:
        """My place by price among the collected products, 1 means nobody is cheaper."""
        return len(self.cheaper) + 1 if self.price else None

    @property
    def total(self) -> int:
        """How many priced products the search gave on this marketplace, mine included."""
        return len(self.rivals) + (1 if self.price else 0)

    @property
    def cheapest(self) -> Product | None:
        return self.cheaper[0] if self.cheaper else None

    @property
    def gap(self) -> float | None:
        """How much more I ask than the cheapest rival."""
        cheapest = self.cheapest
        return self.price - cheapest.price if cheapest and self.price and cheapest.price else None

    @property
    def gap_pct(self) -> float | None:
        cheapest = self.cheapest
        if self.gap is None or not cheapest or not cheapest.price:
            return None
        return round(self.gap / cheapest.price * 100, 1)

    @property
    def median(self) -> float | None:
        prices = [r.price for r in self.rivals if r.price]
        return round(statistics.median(prices), 2) if prices else None

    @property
    def vs_median(self) -> float | None:
        """How far my price is from the middle of the search results, in percent."""
        if not self.price or not self.median:
            return None
        return round((self.price / self.median - 1) * 100, 1)

    @property
    def rating(self) -> float | None:
        ratings = [r.rating for r in self.rivals if r.rating]
        return round(statistics.fmean(ratings), 2) if ratings else None

    @property
    def reviews(self) -> int | None:
        """Median number of reviews of the rivals: how much social proof they have."""
        counts = [r.reviews_count for r in self.rivals if r.reviews_count]
        return round(statistics.median(counts)) if counts else None

    def gap_to(self, rival: Product) -> float | None:
        return self.price - rival.price if self.price and rival.price else None

    def gap_pct_to(self, rival: Product) -> float | None:
        """How much of my price the rival undercuts: a share of my own price, so it never leaves 0—100%."""
        gap = self.gap_to(rival)
        return round(gap / self.price * 100, 1) if gap is not None and self.price else None


@dataclass
class Competition:
    """My products and their rivals; empty when nothing was marked as mine or nothing of mine was found."""

    spots: list[Spot] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.spots)

    @property
    def cheaper(self) -> int:
        """How many rivals undercut at least one of my products."""
        return len({(r.marketplace, r.article) for spot in self.spots for r in spot.cheaper})

    @property
    def leading(self) -> int:
        """How many of my products nobody undercuts."""
        return sum(1 for spot in self.spots if spot.rank == 1)


def compare_with_rivals(products: Sequence[Product], own: Own) -> Competition:
    """Find my products among the collected ones and line every one of them up against its marketplace."""
    if not own:
        return Competition()
    by_marketplace: dict[str, list[Product]] = defaultdict(list)
    for product in products:
        by_marketplace[product.marketplace].append(product)

    spots = []
    for product in (p for p in products if own.matches(p)):
        rivals = [r for r in by_marketplace[product.marketplace] if r.price and not own.matches(r)]
        spots.append(Spot(product, sorted(rivals, key=lambda r: r.price or 0)))
    # The products where I am losing the most matter first.
    spots.sort(key=lambda spot: (-len(spot.cheaper), spot.product.marketplace, spot.product.article))
    return Competition(spots)
