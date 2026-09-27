"""Avito parser.

Avito refuses to serve headless browsers, so the parser works in a browser window (``needs_window``) and opens
pages the way a person does, with pauses of several seconds. Data is read from ``data-marker`` attributes,
which Avito keeps for its own UI tests and rarely changes.

* Search: ``avito.ru/<location>?q=…``, 50 listings per page, the next page is linked from
  ``pagination-button/nextPage``. Filters are URL parameters: ``pmin``/``pmax`` (price), ``s`` (sorting),
  ``user=1|2`` (private sellers / companies), ``d=1`` (Avito Delivery), ``bt=1`` (search in titles only).
* Listing page: title, price, publication date, views, address, seller name, type, rating and review count.
* Reviews belong to the seller, not to a listing: they are read from the seller's page, once per seller.

When Avito answers with its "Доступ ограничен" page, the parser brings the window to the front and waits
for a person to pass the check. It never tries to solve a captcha itself.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any
from urllib.parse import quote_plus

from patchright.async_api import Page

from ..fields import AVITO_DETAIL_FIELDS
from ..inputs import avito_item_url
from ..models import Product, Review
from ..settings import ParseSettings, SellerType, SortOrder
from ..textutils import parse_float, parse_int, parse_ru_date
from .base import MarketplaceParser, ParserError

log = logging.getLogger(__name__)

SITE = "https://www.avito.ru"
TITLE = "Авито"
SORTS = {SortOrder.POPULAR: None, SortOrder.PRICE_ASC: "1", SortOrder.PRICE_DESC: "2", SortOrder.NEW: "104",
         SortOrder.RATING: None}
SELLER_PARAMS = {SellerType.ALL: None, SellerType.PRIVATE: "1", SellerType.COMPANY: "2"}
BLOCK_MARKERS = ("Доступ ограничен", "Access denied", "Доступ временно ограничен")
BLOCK_WAIT = 300
MAX_SEARCH_PAGES = 100
SELLER_TYPES = re.compile(
    r"^(Частное лицо|Частный исполнитель|Компания|Магазин|Агентство|Застройщик|Автодилер|Собственник|Посредник)",
    re.IGNORECASE,
)
_SCORE = re.compile(r"^\d[.,]\d$")
_REVIEWS = re.compile(r"(\d[\d\s\xa0]*)\s*отзыв")

_SEARCH_JS = """
() => [...document.querySelectorAll('[data-marker="item"]')].map(e => {
    const text = sel => e.querySelector(sel)?.innerText?.trim() || '';
    const seller = e.querySelector('a[href*="/brands/"], a[href*="/user/"]');
    return {
        id: e.getAttribute('data-item-id') || '',
        title: text('[data-marker="item-title"]'),
        href: e.querySelector('a[data-marker="item-title"]')?.getAttribute('href') || '',
        price: e.querySelector('[itemprop="price"]')?.getAttribute('content') || '',
        price_text: text('[data-marker="item-price-value"]') || text('[data-marker="item-price"]'),
        location: text('[data-marker="item-location"]') || text('[data-marker="item-address"]'),
        date: text('[data-marker="item-date"]'),
        seller: seller?.innerText?.trim() || '',
        seller_href: seller?.getAttribute('href') || '',
        seller_score: text('[data-marker="seller-info/score"]') || text('[data-marker="seller-rating/score"]'),
        seller_summary: text('[data-marker="seller-info/summary"]'),
        image: e.querySelector('img[itemprop="image"]')?.getAttribute('src') || '',
    };
})
"""

_PAGE_JS = """
() => ({
    next: document.querySelector('[data-marker="pagination-button/nextPage"]')?.getAttribute('href') || '',
    total: document.querySelector('[data-marker="page-title/count"]')?.innerText || '',
    not_found: !!document.querySelector('[data-marker="search-empty"], [data-marker="empty-search"]'),
})
"""

_ITEM_JS = """
() => {
    const text = sel => document.querySelector(sel)?.innerText?.trim() || '';
    const seller = document.querySelector('[data-marker="seller-info/name"] a, a[data-marker="seller-link/link"]');
    return {
        id: text('[data-marker="item-view/item-id"]'),
        title: text('[data-marker="item-view/title-info"]') || text('h1'),
        price: document.querySelector('[itemprop="price"]')?.getAttribute('content') || '',
        price_text: text('[data-marker="item-view/item-price"]'),
        date: text('[data-marker="item-view/item-date"]'),
        views: text('[data-marker="item-view/total-views"]'),
        address: text('#item-view-address p span') || text('[itemprop="address"]'),
        seller: text('[data-marker="seller-info/name"]'),
        seller_info: text('[data-marker="item-view/seller-info"]'),
        seller_reviews: text('[data-marker="rating-caption/rating"]'),
        seller_href: seller?.getAttribute('href') || '',
        image: document.querySelector('meta[property="og:image"]')?.getAttribute('content') || '',
        canonical: document.querySelector('link[rel="canonical"]')?.getAttribute('href') || '',
    };
}
"""

_REVIEWS_JS = """
() => {
    const ids = [...new Set([...document.querySelectorAll('[data-marker^="review("]')]
        .map(e => e.getAttribute('data-marker').match(/^review\\((\\d+)\\)/)?.[1]).filter(Boolean))];
    return ids.map(i => {
        const q = s => document.querySelector(`[data-marker="review(${i})${s}"]`);
        const answer = q('/answer');
        const texts = [...document.querySelectorAll(`[data-marker="review(${i})/text-section/text"]`)]
            .filter(t => !answer || !answer.contains(t)).map(t => t.innerText.trim());
        const answerText = answer ? [...answer.querySelectorAll('[data-marker$="/text-section/text"]')]
            .map(t => t.innerText.trim()).join('\\n') : '';
        return {
            index: i,
            author: q('/header/title')?.innerText?.trim() || '',
            subtitle: q('/header/subtitle')?.innerText?.trim() || '',
            stage: q('/stage')?.innerText?.trim() || '',
            item_title: q('/itemTitle')?.innerText?.trim() || '',
            score: q('/score')?.querySelector('meta[itemprop="ratingValue"]')?.getAttribute('content') || '',
            text: texts.join('\\n'),
            answer: answerText,
            photos: document.querySelectorAll(`[data-marker^="review(${i})/image("][data-marker$="/image"]`).length,
        };
    });
}
"""


# --- search results ---


def search_url(slug: str, query: str, sort: SortOrder, settings: ParseSettings) -> str:
    params = [("q", query)]
    if settings.price_min is not None:
        params.append(("pmin", str(settings.price_min)))
    if settings.price_max is not None:
        params.append(("pmax", str(settings.price_max)))
    if value := SORTS.get(sort):
        params.append(("s", value))
    if value := SELLER_PARAMS.get(settings.avito_seller):
        params.append(("user", value))
    if settings.avito_delivery:
        params.append(("d", "1"))
    if settings.avito_title_only:
        params.append(("bt", "1"))
    return f"{SITE}/{slug}?" + "&".join(f"{key}={quote_plus(value)}" for key, value in params)


def absolute(href: str) -> str:
    return href if href.startswith("http") else SITE + href


def _price(meta: str, text: str) -> float | None:
    return parse_float(meta) if meta else parse_float(text)


def _seller_name(block: str) -> str:
    """The seller block reads "Алена\\n\\n5,0\\n5,0\\n·\\n\\n189 отзывов": the name is its first line."""
    return next((line.strip() for line in block.splitlines() if line.strip()), "")


def parse_search_item(raw: dict[str, Any], position: int | None = None, region: str = "") -> Product | None:
    item_id = raw.get("id") or ""
    if not item_id and (match := re.search(r"_(\d{6,})(?:\?|$)", raw.get("href") or "")):
        item_id = match.group(1)
    if not item_id:
        return None
    product = Product(marketplace=TITLE, article=item_id, position=position)
    product.name = raw.get("title") or ""
    product.price = _price(raw.get("price") or "", raw.get("price_text") or "")
    product.url = avito_item_url(absolute(raw["href"])) if raw.get("href") else f"{SITE}/{item_id}"
    product.image = raw.get("image") or ""
    product.region = raw.get("location") or region
    product.published = parse_ru_date(raw.get("date"))
    product.seller = _seller_name(raw.get("seller") or "")
    product.seller_rating = parse_float(raw.get("seller_score"))
    if match := _REVIEWS.search(raw.get("seller_summary") or ""):
        product.seller_reviews = parse_int(match.group(1))
    product.reviews_key = absolute(raw["seller_href"]).split("?", 1)[0] if raw.get("seller_href") else ""
    return product


# --- listing page ---


def parse_item_page(raw: dict[str, Any], product: Product) -> bool:
    """Fill ``product`` from a listing page. Returns False if the page is not a listing."""
    if not raw.get("title") and not raw.get("id"):
        return False
    if match := re.search(r"(\d{6,})", raw.get("id") or ""):
        product.article = match.group(1)
    product.name = raw.get("title") or product.name
    product.price = _price(raw.get("price") or "", raw.get("price_text") or "") or product.price
    product.published = parse_ru_date(raw.get("date")) or product.published
    product.views = parse_int(raw.get("views")) if raw.get("views") else product.views
    if address := raw.get("address"):
        product.address = address
        product.region = product.region or address
    info = [line.strip() for line in (raw.get("seller_info") or "").splitlines() if line.strip()]
    product.seller = raw.get("seller") or (info[0] if info else "") or product.seller
    for line in info:
        if not product.seller_type and SELLER_TYPES.match(line):
            product.seller_type = SELLER_TYPES.match(line).group(1)
        elif _SCORE.match(line):
            product.seller_rating = parse_float(line)
    if match := _REVIEWS.search(raw.get("seller_reviews") or " ".join(info)):
        product.seller_reviews = parse_int(match.group(1))
    elif info:
        product.seller_reviews = product.seller_reviews or 0
    if raw.get("seller_href"):
        product.reviews_key = absolute(raw["seller_href"]).split("?", 1)[0]
    product.image = product.image or raw.get("image") or ""
    if raw.get("canonical"):
        product.url = raw["canonical"]
    return True


# --- seller reviews ---


def parse_review(raw: dict[str, Any], product: Product) -> Review:
    text = raw.get("text") or ""
    stage = (raw.get("stage") or "").split("·", 1)[0].strip()
    if stage and stage != "Сделка состоялась":  # "Сделка сорвалась", "Не договорились": context for the text
        text = f"({stage}) {text}".strip()
    return Review(
        marketplace=TITLE,
        article=product.article,
        product_name=raw.get("item_title") or "",
        date=parse_ru_date(raw.get("subtitle")),
        rating=parse_int(raw.get("score")),
        author=raw.get("author") or "",
        text=text,
        photos=int(raw.get("photos") or 0),
        seller_answer=raw.get("answer") or "",
        seller=product.seller,
    )


class AvitoParser(MarketplaceParser):
    key = "avito"
    title = TITLE
    pause_range = (3.5, 7.0)
    needs_window = True

    page: Page | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sellers_done: set[str] = set()

    async def prepare(self) -> None:
        self.reporter.log(f"{TITLE}: открываю сайт…")
        self.page = await self.browser.new_page()
        await self._open(SITE + "/")

    async def _open(self, url: str) -> int:
        """Open a page and wait until the listings are drawn. Returns the HTTP status."""
        assert self.page is not None
        self.reporter.check_cancel()
        response = await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(random.uniform(1.5, 2.5))
        if await self._blocked():
            await self._wait_for_person()
        return response.status if response else 0

    async def _blocked(self) -> bool:
        assert self.page is not None
        title = await self.page.title()
        return any(marker in title for marker in BLOCK_MARKERS)

    async def _wait_for_person(self) -> None:
        """Avito shows "Доступ ограничен" with a captcha: a person passes it in the window, the parser waits."""
        assert self.page is not None
        await self.browser.bring_to_front(self.page)
        self.reporter.warn(f"{TITLE}: сайт просит подтвердить, что вы не робот. Нажмите «Продолжить» в окне "
                           f"браузера и пройдите проверку — сбор продолжится сам.")
        for _ in range(BLOCK_WAIT):
            self.reporter.check_cancel()
            await asyncio.sleep(1)
            if not await self._blocked():
                await asyncio.sleep(3)
                return
        raise ParserError(f"{TITLE}: доступ к сайту ограничен, проверка не пройдена за {BLOCK_WAIT // 60} мин. "
                          "Попробуйте позже: обычно ограничение снимается через несколько часов.")

    # --- search ---

    async def search(self, query: str, limit: int, sort: SortOrder) -> list[Product]:
        """Search every chosen location; ``limit`` applies to each of them."""
        if sort == SortOrder.RATING:
            self.reporter.warn(f"{TITLE}: сортировки по рейтингу на сайте нет, использую «По умолчанию»")
        locations = self.settings.avito_location_slugs()
        products: list[Product] = []
        for number, (name, slug) in enumerate(locations, 1):
            if len(locations) > 1:
                self.reporter.log(f"{TITLE}: {name} ({number} из {len(locations)})")
            found = await self._collect(search_url(slug, query, sort, self.settings), limit, region=name)
            if not found:
                self.reporter.warn(f"{TITLE}: {name} — по запросу «{query}» ничего не найдено")
            known = {p.article for p in products}
            products += [p for p in found if p.article not in known]
        return products

    async def listing(self, url: str, limit: int) -> list[Product]:
        """Search results, a category or a seller's page with the filters set in the link."""
        products = await self._collect(url, limit)
        if not products:
            self.reporter.warn(f"{TITLE}: по ссылке ничего не найдено — {url}")
        return products

    async def _collect(self, url: str, limit: int, region: str = "") -> list[Product]:
        assert self.page is not None
        products: list[Product] = []
        seen: set[str] = set()
        pages = 0
        next_url: str | None = url
        while next_url and len(products) < limit and pages < MAX_SEARCH_PAGES:
            status = await self._open(next_url)
            pages += 1
            if status == 404:
                self.reporter.warn(f"{TITLE}: страница не найдена — {next_url}")
                break
            await self._scroll_through()
            info = await self.page.evaluate(_PAGE_JS)
            if pages == 1 and info.get("total"):
                self.reporter.log(f"{TITLE}: найдено объявлений — {info['total']}")
            for raw in await self.page.evaluate(_SEARCH_JS):
                product = parse_search_item(raw, region=region)
                if product is None or product.article in seen:
                    continue
                seen.add(product.article)
                product.position = len(products) + 1
                products.append(product)
                if len(products) >= limit:
                    break
            self.reporter.progress(len(products), limit, f"{TITLE}: объявления {len(products)} из {limit}")
            next_url = absolute(info["next"]) if info.get("next") else None
            if next_url and len(products) < limit:
                await self.pause()
        return products

    async def _scroll_through(self) -> None:
        """Scroll the page down like a reader does, so lazy images and prices are drawn."""
        assert self.page is not None
        for _ in range(random.randint(3, 5)):
            await self.page.mouse.wheel(0, random.randint(1400, 2200))
            await asyncio.sleep(random.uniform(0.4, 0.9))

    # --- listing pages ---

    async def products_by_ids(self, articles: list[str]) -> list[Product]:
        products = []
        for index, article in enumerate(articles, 1):
            url = article if article.startswith("http") else f"{SITE}/{article}"
            product = Product(marketplace=TITLE, article=re.sub(r".*_(\d+)$", r"\1", article), url=url)
            if await self._open_item(product):
                products.append(product)
            else:
                self.reporter.warn(f"{TITLE}: объявление не найдено или снято — {article}")
            self.reporter.progress(index, len(articles), f"{TITLE}: объявления {index} из {len(articles)}")
            if index < len(articles):
                await self.pause()
        return products

    async def _open_item(self, product: Product) -> bool:
        assert self.page is not None
        if await self._open(product.url) == 404:
            return False
        return parse_item_page(await self.page.evaluate(_ITEM_JS), product)

    async def enrich(self, products: list[Product], field_keys: set[str]) -> None:
        """Search results lack the address, views and seller type (and the date, for services):
        open the listings if those fields are wanted. About 5 seconds per listing."""
        wanted = field_keys & AVITO_DETAIL_FIELDS
        if not wanted:
            return
        every = bool(wanted - {"published"})
        pending = [p for p in products if every or p.published is None]
        if pending:
            self.reporter.log(f"{TITLE}: открываю объявления для доп. полей — {len(pending)} шт., "
                              f"около {len(pending) * 6 // 60 + 1} мин.")
        for index, product in enumerate(pending, 1):
            await self._open_item(product)
            self.reporter.progress(index, len(pending), f"{TITLE}: объявления {index} из {len(pending)}")
            await self.pause()

    # --- reviews ---

    async def reviews(self, product: Product, limit: int) -> list[Review]:
        """Reviews of the listing's seller, newest first. Each seller is visited once per run."""
        seller_url = product.reviews_key
        if not seller_url or seller_url in self._sellers_done or product.seller_reviews == 0:
            return []
        self._sellers_done.add(seller_url)
        assert self.page is not None
        await self._open(seller_url)
        reviews: list[Review] = []
        seen: set[str] = set()
        idle = 0
        while len(reviews) < limit and idle < 2:
            added = 0
            for raw in await self.page.evaluate(_REVIEWS_JS):
                if raw["index"] in seen or len(reviews) >= limit:
                    continue
                seen.add(raw["index"])
                reviews.append(parse_review(raw, product))
                added += 1
            idle = 0 if added else idle + 1
            if len(reviews) < limit:
                await self._more_reviews()
        return reviews

    async def _more_reviews(self) -> None:
        """Scroll down the review list and press "Показать ещё" if the page has it."""
        assert self.page is not None
        await self.page.mouse.wheel(0, random.randint(1500, 2500))
        await asyncio.sleep(random.uniform(1.0, 1.8))
        button = self.page.get_by_role("button", name=re.compile(r"Показать ещё|Ещё отзывы", re.IGNORECASE))
        try:
            if await button.count():
                await button.first.click(timeout=5_000)
                await asyncio.sleep(random.uniform(1.5, 2.5))
        except Exception:  # the button disappears while the next portion is loading
            log.debug("Avito: 'show more' click failed", exc_info=True)
