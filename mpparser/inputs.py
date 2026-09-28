"""Parsing of user-supplied lists: article numbers, product links and links to search results.

Which link belongs to which marketplace is decided by the marketplaces themselves: each one declares its
patterns in its :class:`~mpparser.plugins.Marketplace` description. A link to a single product becomes an
article number; a link to a list of products — search results, a category, a shop or a seller page with
filters already set on the site — goes to ``listings`` and is collected like a search query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import plugins

NUMBER = re.compile(r"^\d{4,}$")
SEPARATORS = re.compile(r"[\s,;]+")


@dataclass
class ParsedIds:
    items: dict[str, list[str]] = field(default_factory=dict)  # marketplace key → article numbers
    listings: dict[str, list[str]] = field(default_factory=dict)  # marketplace key → links to search results
    invalid: list[str] = field(default_factory=list)

    def for_marketplace(self, key: str) -> list[str]:
        return self.items.get(key, [])

    def listings_for(self, key: str) -> list[str]:
        return self.listings.get(key, [])

    def count(self, key: str) -> int:
        return len(self.for_marketplace(key)) + len(self.listings_for(key))

    def total(self) -> int:
        return sum(self.count(key) for key in {*self.items, *self.listings})


def _full_url(link: str) -> str:
    return link if link.lower().startswith("http") else "https://" + link


def parse_ids(text: str, default_marketplace: str) -> ParsedIds:
    """Split free text into article numbers and listing links.

    Links are routed to their marketplace regardless of the box they were pasted into;
    bare numbers belong to ``default_marketplace``. Duplicates are removed, order is kept.
    """
    result = ParsedIds()
    seen: set[tuple[str, str]] = set()
    marketplaces = plugins.registry().values()

    def add(marketplace: str, article: str) -> None:
        if (marketplace, article) not in seen:
            seen.add((marketplace, article))
            result.items.setdefault(marketplace, []).append(article)

    def add_listing(marketplace: str, link: str) -> None:
        url = _full_url(link)
        if ("listing", url) not in seen:
            seen.add(("listing", url))
            result.listings.setdefault(marketplace, []).append(url)

    for token in SEPARATORS.split(text.strip()):
        if not token:
            continue
        for marketplace in marketplaces:
            if article := marketplace.article_of(token):
                add(marketplace.key, article)
                break
            if marketplace.is_listing(token):
                add_listing(marketplace.key, token)
                break
        else:
            if NUMBER.match(token):
                add(default_marketplace, token)
            else:
                result.invalid.append(token)
    return result


def collect_ids(texts: dict[str, str]) -> ParsedIds:
    """Merge the per-marketplace text boxes into one ParsedIds."""
    merged = ParsedIds()
    for marketplace, text in texts.items():
        parsed = parse_ids(text, marketplace)
        for key, articles in parsed.items.items():
            target = merged.items.setdefault(key, [])
            target.extend(a for a in articles if a not in target)
        for key, links in parsed.listings.items():
            target = merged.listings.setdefault(key, [])
            target.extend(u for u in links if u not in target)
        merged.invalid.extend(parsed.invalid)
    return merged
