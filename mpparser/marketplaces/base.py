"""Common interface of marketplace parsers."""

from __future__ import annotations

import asyncio
import random
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable

from ..browser import Browser
from ..models import Product, Review
from ..settings import ParseSettings, SortOrder


class Cancelled(Exception):
    """Raised when the user presses "Stop"."""


class ParserError(RuntimeError):
    """A marketplace-level failure with a message that can be shown to the user as is."""


class Reporter:
    """Progress and log sink. The GUI and CLI pass their own callbacks."""

    def __init__(
        self,
        on_log: Callable[[str, str], None] | None = None,
        on_progress: Callable[[float, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._on_log = on_log or (lambda *_: None)
        self._on_progress = on_progress or (lambda *_: None)
        self._cancel_event = cancel_event or threading.Event()
        self._span = (0.0, 1.0)

    def log(self, message: str, level: str = "info") -> None:
        self._on_log(level, message)

    def warn(self, message: str) -> None:
        self.log(message, "warning")

    def set_span(self, start: float, end: float) -> None:
        """Map the progress of the next stage onto [start, end] of the overall bar."""
        self._span = (start, end)

    def progress(self, done: int, total: int, text: str) -> None:
        start, end = self._span
        fraction = min(done / total, 1.0) if total else 1.0
        self._on_progress(start + (end - start) * fraction, text)

    def check_cancel(self) -> None:
        if self._cancel_event.is_set():
            raise Cancelled


class MarketplaceParser(ABC):
    key: str
    title: str
    # Delay between requests, seconds (min, max). Keeps the load on the site polite.
    pause_range: tuple[float, float] = (0.3, 0.8)

    def __init__(self, browser: Browser, settings: ParseSettings, reporter: Reporter) -> None:
        self.browser = browser
        self.settings = settings
        self.reporter = reporter

    async def pause(self) -> None:
        self.reporter.check_cancel()
        await asyncio.sleep(random.uniform(*self.pause_range))

    @abstractmethod
    async def prepare(self) -> None:
        """Open the site and pass its anti-bot checks."""

    @abstractmethod
    async def search(self, query: str, limit: int, sort: SortOrder) -> list[Product]:
        """Products from search results, in the order the site shows them."""

    @abstractmethod
    async def products_by_ids(self, articles: list[str]) -> list[Product]:
        """Products by article numbers; unknown articles are reported and skipped."""

    async def category(self, url: str, limit: int, sort: SortOrder) -> list[Product]:
        """Products from a category page. Extension point for category parsing."""
        raise NotImplementedError(f"{self.title}: сбор по категориям пока не поддерживается")

    async def enrich(self, products: list[Product], field_keys: set[str]) -> None:  # noqa: B027 - optional hook
        """Fill fields that are missing from listings and require extra requests. No-op by default."""

    @abstractmethod
    async def reviews(self, product: Product, limit: int) -> list[Review]:
        """Up to ``limit`` reviews of a product: newest first on WB, Ozon's "new and useful" order on Ozon."""
