"""Marketplace plugins.

A marketplace is described by one :class:`Marketplace` object: its name and colour, the parser class, how its
links are recognised, which extra Excel columns it fills and which filters it offers. The core reads this
description — it has no knowledge of any particular marketplace, so adding one (or shipping one in a separate
package) does not require changes here.

Built-in marketplaces are declared in ``mpparser/marketplaces/*.py``. External ones are picked up from the
``mpparser.marketplaces`` entry point group: a package that declares

    [project.entry-points."mpparser.marketplaces"]
    avito = "mpparser_avito:marketplace"

appears in the program as soon as it is installed, and disappears when it is removed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Literal

from .fields import Field

if TYPE_CHECKING:
    from .marketplaces.base import MarketplaceParser

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "mpparser.marketplaces"

OptionKind = Literal["switch", "choice", "multi", "setup"]


@dataclass(frozen=True)
class Option:
    """One marketplace filter: a switch, a menu, a list of places, or a button that opens the site."""

    key: str  # stored in ParseSettings.options[marketplace][key]
    title: str  # label in the window and help text in the command line
    kind: OptionKind = "switch"
    default: Any = False
    choices: tuple[tuple[Any, str], ...] = ()  # (value, label) for "choice"
    catalogue: tuple[tuple[str, str], ...] = ()  # (name, site slug) for "multi"
    hint: str = ""  # small grey line above the control
    width: int = 150
    query_only: bool = False  # the filter only applies to search queries, not to links
    required: bool = False  # "multi": at least one value must be chosen
    required_message: str = ""
    own_row: bool = True  # in the report: a row of its own, not merged into "Filters of <marketplace>"
    label: str = ""  # row label in the report for an own-row option; defaults to the title
    summary_text: str = ""  # how the switch reads in the report; defaults to the lowercased title
    summary_format: str = ""  # how a chosen menu value reads in the report, e.g. "продавцы: {value}"
    always_in_summary: bool = False  # show in the report even when left at the default
    url: str = ""  # "setup": page opened in the browser
    instruction: str = ""  # "setup": what the user does there
    button: str = "Выбрать адрес…"  # "setup": button label
    cli_flag: str = ""  # defaults to --<marketplace>-<key>
    cli_metavar: str = ""

    def flag(self, marketplace: str) -> str:
        return self.cli_flag or f"--{marketplace}-{self.key.replace('_', '-')}"

    def choice_titles(self) -> list[str]:
        return [title for _, title in self.choices]

    def title_of(self, value: Any) -> str:
        return next((title for option_value, title in self.choices if option_value == value), str(value))

    def value_of(self, title: str) -> Any:
        return next((value for value, option_title in self.choices if option_title == title), self.default)

    def slug_of(self, name: str) -> str:
        """Site slug of a catalogue entry; an unknown name is taken as a slug typed by the user."""
        return dict(self.catalogue).get(name, name)

    def summary(self, value: Any) -> str | None:
        """How the chosen value reads in the report, or None when the filter is off."""
        if self.kind == "switch":
            return (self.summary_text or self.title.lower()) if value else None
        if self.kind == "choice":
            if value == self.default and not self.always_in_summary:
                return None
            title = self.title_of(value)
            if self.summary_format:
                return self.summary_format.format(value=title.lower())
            # A row of its own keeps the title as it is ("Москва"); a merged one reads as part of a list.
            return title if self.own_row else title[:1].lower() + title[1:]
        if self.kind == "multi":
            return ", ".join(value) if value else None
        return None


@dataclass(frozen=True)
class Marketplace:
    """Everything the core needs to know about one marketplace."""

    key: str
    title: str
    parser: type[MarketplaceParser]
    short_title: str = ""  # compact label for file names and column hints
    site: str = ""  # shown under the checkbox
    color: tuple[str, str] = ("#4F46E5", "#6366F1")  # brand colour for light and dark themes
    excel_color: str = "2B2D42"  # the same colour for the Excel report
    ids_placeholder: str = ""  # example of a link in the window
    item_patterns: tuple[re.Pattern[str], ...] = ()  # link to one product → article number
    listing_pattern: re.Pattern[str] | None = None  # link to search results, a category or a seller
    item_url: Callable[[str], str] | None = None  # normalises a product link when the article is the link itself
    article_from: Callable[[str], str | None] | None = None  # when patterns alone cannot tell the article
    product_fields: tuple[Field, ...] = ()  # columns only this marketplace fills
    review_fields: tuple[Field, ...] = ()
    options: tuple[Option, ...] = ()
    order: int = 100  # position in the window and in the report
    filters_label: str = ""  # report row for the grouped filters; defaults to "Фильтры <title>"
    notes: str = ""  # shown in the window: what to keep in mind about this marketplace

    def option(self, key: str) -> Option | None:
        return next((o for o in self.options if o.key == key), None)

    def filters_title(self) -> str:
        return self.filters_label or f"Фильтры {self.title}"

    def defaults(self) -> dict[str, Any]:
        return {o.key: o.default for o in self.options if o.kind != "setup"}

    def article_of(self, token: str) -> str | None:
        """Article number (or normalised link) if the token is a link to a single product here."""
        if self.article_from:
            return self.article_from(token)
        for pattern in self.item_patterns:
            if match := pattern.search(token):
                article = next((group for group in match.groups() if group), None)
                if article:
                    return article
                return self.item_url(token) if self.item_url else token
        return None

    def is_listing(self, token: str) -> bool:
        return bool(self.listing_pattern and self.listing_pattern.match(token))


@dataclass
class _Registry:
    marketplaces: dict[str, Marketplace] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _builtin() -> list[Marketplace]:
    # Imported here and not at module level: the parsers import settings, which reads this registry.
    from .marketplaces import ozon, wildberries, yandex_market

    return [wildberries.MARKETPLACE, ozon.MARKETPLACE, yandex_market.MARKETPLACE]


def _external() -> tuple[list[Marketplace], list[str]]:
    found, errors = [], []
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        try:
            marketplace = entry.load()
            if callable(marketplace):
                marketplace = marketplace()
            if not isinstance(marketplace, Marketplace):
                raise TypeError(f"{entry.value} вернул {type(marketplace).__name__}, а не Marketplace")
        except Exception as exc:
            log.warning("Plugin %s failed to load: %s", entry.name, exc)
            errors.append(f"Модуль «{entry.name}» не загрузился: {exc}")
        else:
            found.append(marketplace)
    return found, errors


@cache
def _load() -> _Registry:
    registry = _Registry()
    external, errors = _external()
    registry.errors = errors
    for marketplace in sorted([*_builtin(), *external], key=lambda m: (m.order, m.title)):
        registry.marketplaces[marketplace.key] = marketplace
    return registry


def registry() -> dict[str, Marketplace]:
    """All available marketplaces: the built-in ones plus every installed plugin."""
    return dict(_load().marketplaces)


def load_errors() -> list[str]:
    """Messages about plugins that are installed but failed to load."""
    return list(_load().errors)


def get(key: str) -> Marketplace | None:
    return _load().marketplaces.get(key)


def keys() -> list[str]:
    return list(_load().marketplaces)


def titles() -> dict[str, str]:
    return {key: mp.title for key, mp in _load().marketplaces.items()}


def short_titles() -> dict[str, str]:
    return {key: mp.short_title or mp.title for key, mp in _load().marketplaces.items()}


def parsers() -> dict[str, type[MarketplaceParser]]:
    return {key: mp.parser for key, mp in _load().marketplaces.items()}


def title_of(key: str) -> str:
    marketplace = get(key)
    return marketplace.title if marketplace else key


def with_options() -> list[Marketplace]:
    return [mp for mp in _load().marketplaces.values() if mp.options]


def product_fields(core: Sequence[Field]) -> list[Field]:
    """Core columns with the plugin ones inserted before the trailing columns (like "Дата сбора")."""
    extra = [f for mp in _load().marketplaces.values() for f in mp.product_fields]
    return _merge(core, extra)


def review_fields(core: Sequence[Field]) -> list[Field]:
    extra = [f for mp in _load().marketplaces.values() for f in mp.review_fields]
    return _merge(core, extra)


def _merge(core: Sequence[Field], extra: Sequence[Field]) -> list[Field]:
    head = [f for f in core if not f.tail]
    tail = [f for f in core if f.tail]
    return [*head, *extra, *tail]


def reset_cache() -> None:
    """Forget the loaded plugins. For tests that install a marketplace of their own."""
    _load.cache_clear()
