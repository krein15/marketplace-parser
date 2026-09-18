"""Parsing of user-supplied article lists: bare numbers and product links for both marketplaces."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

WB_LINK = re.compile(r"(?:wildberries\.[a-z]{2,3}|wb\.ru)/catalog/(\d{4,})", re.IGNORECASE)
OZON_LINK = re.compile(r"ozon\.[a-z]{2,3}/(?:product|context/detail/id)/(?:[^/?#\s]*-)?(\d{5,})", re.IGNORECASE)
NUMBER = re.compile(r"^\d{4,}$")
SEPARATORS = re.compile(r"[\s,;]+")


@dataclass
class ParsedIds:
    wb: list[str] = field(default_factory=list)
    ozon: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)

    def for_marketplace(self, key: str) -> list[str]:
        return getattr(self, key)


def parse_ids(text: str, default_marketplace: str) -> ParsedIds:
    """Split free text into article numbers.

    Links are routed to their marketplace regardless of the box they were pasted into;
    bare numbers belong to ``default_marketplace``. Duplicates are removed, order is kept.
    """
    result = ParsedIds()
    seen: set[tuple[str, str]] = set()

    def add(marketplace: str, article: str) -> None:
        if (marketplace, article) not in seen:
            seen.add((marketplace, article))
            result.for_marketplace(marketplace).append(article)

    for token in SEPARATORS.split(text.strip()):
        if not token:
            continue
        if match := WB_LINK.search(token):
            add("wb", match.group(1))
        elif match := OZON_LINK.search(token):
            add("ozon", match.group(1))
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
        for key in ("wb", "ozon"):
            target = merged.for_marketplace(key)
            target.extend(a for a in parsed.for_marketplace(key) if a not in target)
        merged.invalid.extend(parsed.invalid)
    return merged
