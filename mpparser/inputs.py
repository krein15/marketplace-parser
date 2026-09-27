"""Parsing of user-supplied lists: article numbers, product links and links to search results.

A link to a single product becomes an article number (for Avito: the listing URL). A link to a list of products —
search results, a category, a shop or a seller page with filters already set on the site — goes to ``listings``
and is collected like a search query.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WB_LINK = re.compile(r"(?:wildberries\.[a-z]{2,3}|wb\.ru)/catalog/(\d{4,})", re.IGNORECASE)
OZON_LINK = re.compile(r"ozon\.[a-z]{2,3}/(?:product|context/detail/id)/(?:[^/?#\s]*-)?(\d{5,})", re.IGNORECASE)
# Yandex Market: /card/<slug>/<sku> or an older /product--<slug>/<model>?sku=<sku> link.
YM_LINK = re.compile(
    r"market\.yandex\.[a-z]{2,3}/(?:card/[^/?#\s]+/(\d{5,})|[^\s]*?[?&]sku=(\d{5,}))", re.IGNORECASE
)
YM_ANY = re.compile(r"^(?:https?://)?(?:m\.)?market\.yandex\.[a-z]{2,3}/\S+", re.IGNORECASE)
# Avito listing: avito.ru/<location>/<category>/<title>_<id>, or the short avito.ru/<id>.
AVITO_ITEM = re.compile(
    r"^(?:https?://)?(?:www\.|m\.)?avito\.ru/(?:[^/?#\s]+/[^/?#\s]+/[^/?#\s]*_(\d{6,})|(\d{6,}))/?(?:[?#]\S*)?$",
    re.IGNORECASE,
)
AVITO_ANY = re.compile(r"^(?:https?://)?(?:www\.|m\.)?avito\.ru/\S+", re.IGNORECASE)
NUMBER = re.compile(r"^\d{4,}$")
SEPARATORS = re.compile(r"[\s,;]+")
MARKETPLACE_KEYS = ("wb", "ozon", "ym", "avito")


@dataclass
class ParsedIds:
    wb: list[str] = field(default_factory=list)
    ozon: list[str] = field(default_factory=list)
    ym: list[str] = field(default_factory=list)
    avito: list[str] = field(default_factory=list)
    listings: dict[str, list[str]] = field(default_factory=dict)
    invalid: list[str] = field(default_factory=list)

    def for_marketplace(self, key: str) -> list[str]:
        return getattr(self, key)

    def listings_for(self, key: str) -> list[str]:
        return self.listings.get(key, [])

    def count(self, key: str) -> int:
        return len(self.for_marketplace(key)) + len(self.listings_for(key))


def _full_url(link: str) -> str:
    return link if link.lower().startswith("http") else "https://" + link


def avito_item_url(link: str) -> str:
    """Listing URL without tracking parameters, on the desktop host."""
    path = re.sub(r"^(?:https?://)?(?:www\.|m\.)?avito\.ru", "", link, flags=re.IGNORECASE).split("?", 1)[0]
    return "https://www.avito.ru" + path.split("#", 1)[0].rstrip("/")


def parse_ids(text: str, default_marketplace: str) -> ParsedIds:
    """Split free text into article numbers and listing links.

    Links are routed to their marketplace regardless of the box they were pasted into;
    bare numbers belong to ``default_marketplace``. Duplicates are removed, order is kept.
    """
    result = ParsedIds()
    seen: set[tuple[str, str]] = set()

    def add(marketplace: str, article: str) -> None:
        if (marketplace, article) not in seen:
            seen.add((marketplace, article))
            result.for_marketplace(marketplace).append(article)

    def add_listing(marketplace: str, link: str) -> None:
        url = _full_url(link)
        if ("listing", url) not in seen:
            seen.add(("listing", url))
            result.listings.setdefault(marketplace, []).append(url)

    for token in SEPARATORS.split(text.strip()):
        if not token:
            continue
        if match := WB_LINK.search(token):
            add("wb", match.group(1))
        elif match := OZON_LINK.search(token):
            add("ozon", match.group(1))
        elif match := YM_LINK.search(token):
            add("ym", match.group(1) or match.group(2))
        elif YM_ANY.match(token):
            add_listing("ym", token)
        elif match := AVITO_ITEM.match(token):
            add("avito", avito_item_url(token) if match.group(1) else match.group(2))
        elif AVITO_ANY.match(token):
            add_listing("avito", token)
        elif NUMBER.match(token):
            add(default_marketplace, token)
        else:
            result.invalid.append(token)
    return result


def collect_ids(texts: dict[str, str]) -> ParsedIds:
    """Merge the per-marketplace text boxes into one ParsedIds."""
    merged = ParsedIds()
    for marketplace, text in texts.items():
        parsed = parse_ids(text, marketplace)
        for key in MARKETPLACE_KEYS:
            target = merged.for_marketplace(key)
            target.extend(a for a in parsed.for_marketplace(key) if a not in target)
            links = merged.listings.setdefault(key, [])
            links.extend(u for u in parsed.listings_for(key) if u not in links)
        merged.invalid.extend(parsed.invalid)
    return merged
