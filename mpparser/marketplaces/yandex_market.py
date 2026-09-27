"""Yandex Market parser.

Market renders pages on the server and embeds everything this parser needs in the HTML:

* ``data-zone-data`` JSON of ``productSnippet`` elements: SKU and the three prices of a search result;
* apiary patches (``<noframes data-apiary="patch">``) with entity collections: ``productCardMeta``, ``price``,
  ``shopInfo`` on a card, ``reviewV2`` on a reviews page;
* JSON-LD (``application/ld+json``): picture, availability and breadcrumbs of a card.

Search results are loaded in chunks of 16 while the page is scrolled (the first chunk needs a click on
"Показать еще"), so search pages are opened in the browser. Cards and review pages arrive complete and are
downloaded with ``fetch`` from an opened Market page, which is much faster.

Three prices: the regular one, the one paid with Yandex Pay (shown largest on the site, stored as
``price_card`` like Ozon's card price) and the price before the discount.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from typing import Any
from urllib.parse import quote, urlencode

from patchright.async_api import Page

from ..fields import YM_DETAIL_FIELDS
from ..models import Product, Review
from ..settings import ParseSettings, SortOrder
from ..textutils import parse_float, parse_int, parse_ru_date
from .base import MarketplaceParser, ParserError

SITE = "https://market.yandex.ru"
TITLE = "Яндекс Маркет"
SORTS = {
    SortOrder.POPULAR: None,
    SortOrder.PRICE_ASC: "aprice",
    SortOrder.PRICE_DESC: "dprice",
    SortOrder.RATING: "rating",
    SortOrder.NEW: None,  # Market has no "new arrivals" order
}
CAPTCHA_MARKERS = ("showcaptcha", "SmartCaptcha", "checkcaptcha")
CAPTCHA_WAIT = 180
# Snippets inside these blocks are ads and recommendation carousels, not search results.
FOREIGN_BLOCKS = '[data-zone-name="madvIncut"], [data-zone-name="ScrollBox"], [data-zone-name="recomMadv"]'
MAX_IDLE_STEPS = 6  # scroll steps without new results before the list is considered complete
NO_BRAND = {"без бренда", "не определен", "не определён"}

_PATCH = re.compile(r'<noframes data-apiary="patch">(.*?)</noframes>', re.S)
_JSON_LD = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_RATING = re.compile(r"Рейтинг товара:\s*([\d.,]+)")
_RATING_COUNT = re.compile(r"Оценок:\s*\(?([\d\s  \xa0]+)")
_CARD_PATH = re.compile(r"/card/[^/?#]+/(\d+)")

_SNIPPETS_JS = """
(foreign) => [...document.querySelectorAll('[data-zone-name="productSnippet"]')]
    .filter(el => !el.closest(foreign))
    .map(el => ({
        zone: el.getAttribute('data-zone-data') || '',
        href: el.querySelector('a[data-auto="snippet-link"]')?.getAttribute('href') || '',
        title: el.querySelector('[data-auto="snippet-title"]')?.textContent || '',
        rating: el.querySelector('[data-zone-name="rating"]')?.textContent || '',
        image: el.querySelector('img')?.getAttribute('src') || '',
    }))
"""


# --- page data ---


def collections(page_html: str) -> dict[str, dict[str, Any]]:
    """Merge entity collections of all apiary patches on a page: ``{"shopInfo": {id: entity}, ...}``."""
    merged: dict[str, dict[str, Any]] = {}
    for raw in _PATCH.findall(page_html):
        try:
            patch = json.loads(html.unescape(raw))
        except ValueError:
            continue
        for name, entities in (patch.get("collections") or {}).items():
            if isinstance(entities, dict):
                merged.setdefault(name, {}).update(entities)
    return merged


def json_ld(page_html: str) -> list[dict[str, Any]]:
    result = []
    for raw in _JSON_LD.findall(page_html):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        result.extend(data if isinstance(data, list) else [data])
    return result


def card_url(href: str) -> str:
    """Strip tracking parameters: ``/card/<slug>/<sku>?cpc=…`` → ``https://market.yandex.ru/card/<slug>/<sku>``."""
    path = html.unescape(href).split("?", 1)[0]
    return path if path.startswith("http") else SITE + path


# --- search results ---


def filter_params(settings: ParseSettings) -> dict[str, str]:
    """URL parameters of the filters chosen in the program (the site uses the same ones in its links)."""
    params = {}
    if settings.price_min is not None:
        params["pricefrom"] = str(settings.price_min)
    if settings.price_max is not None:
        params["priceto"] = str(settings.price_max)
    if settings.ym_rating_4:
        params["qrfrom"] = "1"
    if settings.ym_delivery_days:
        params["delivery-interval"] = str(settings.ym_delivery_days)
    return params


def search_url(query: str, sort: SortOrder, settings: ParseSettings) -> str:
    params = {"text": query}
    if how := SORTS[sort]:
        params["how"] = how
    params.update(filter_params(settings))
    return f"{SITE}/search?{urlencode(params, quote_via=quote)}"


def parse_snippet(raw: dict[str, Any], position: int | None = None) -> Product | None:
    """Convert a search result collected by ``_SNIPPETS_JS``. Returns None for snippets without a SKU."""
    zone = raw.get("zone") or {}
    if isinstance(zone, str):
        try:
            zone = json.loads(zone)
        except ValueError:
            zone = {}
    # The card is addressed by the offer SKU (``oskuId``); ``marketSku`` of the same snippet may differ.
    match = _CARD_PATH.search(raw.get("href") or "")
    sku = match.group(1) if match else str(zone.get("oskuId") or zone.get("marketSku") or "")
    if not sku:
        return None

    product = Product(marketplace=TITLE, article=sku, position=position)
    product.name = (raw.get("title") or zone.get("title") or "").strip()
    extra = {p.get("priceType"): parse_float(p.get("priceValue")) for p in zone.get("additionalPrices") or []}
    base = parse_float(zone.get("price"))
    product.price = extra.get("withDiscount") or base
    product.price_card = extra.get("yaBank")
    if product.price_card and product.price and product.price_card >= product.price:
        product.price_card = None
    if base and product.price and base > product.price:
        product.price_old = base
    product.fill_discount()

    rating_text = raw.get("rating") or ""
    if match := _RATING.search(rating_text):
        product.rating = parse_float(match.group(1))
    if match := _RATING_COUNT.search(rating_text):
        product.reviews_count = parse_int(match.group(1))
    elif not rating_text.strip():
        product.reviews_count = 0  # no rating block: nobody has rated the product yet

    href = raw.get("href") or ""
    product.url = card_url(href) if "/card/" in href else f"{SITE}/card/x/{sku}"
    product.image = raw.get("image") or ""
    return product


# --- product card ---


def _first(entities: dict[str, Any] | None, key: str | None = None) -> dict[str, Any]:
    if not entities:
        return {}
    if key and key in entities:
        return entities[key]
    return next(iter(entities.values()))


def _amount(price: dict[str, Any] | None) -> float | None:
    return parse_float((price or {}).get("value")) if price else None


def parse_card(page_html: str, product: Product) -> bool:
    """Fill ``product`` from a card page. Returns False if the page is not a product card."""
    colls = collections(page_html)
    meta_entities = colls.get("productCardMeta") or {}
    meta_key = next(iter(meta_entities), None)
    meta = _first(meta_entities)
    ld_product = next((d for d in json_ld(page_html) if d.get("@type") == "Product"), {})
    if not meta and not ld_product:
        return False

    product.name = meta.get("title") or meta.get("rawTitle") or ld_product.get("name") or product.name
    brand = meta.get("vendorName") or ld_product.get("brand") or ""
    if isinstance(brand, dict):
        brand = brand.get("name", "")
    product.brand = "" if brand.lower() in NO_BRAND else brand

    prices = _first(colls.get("price"), meta_key)
    if prices:
        main = prices.get("mainPrice") or {}
        old = {p.get("type"): _amount(p.get("price")) for p in prices.get("oldPrices") or []}
        if main.get("subtype") == "ya-card" or main.get("type") == "extraDiscount":
            product.price_card = _amount(main.get("price"))
            product.price = old.get("regular") or product.price_card
        else:
            product.price = _amount(main.get("price"))
            product.price_card = None
        product.price_old = old.get("withoutDiscount")
        if product.price_old and product.price and product.price_old <= product.price:
            product.price_old = None
        if product.price_card and product.price and product.price_card >= product.price:
            product.price_card = None
        product.discount = None
        product.fill_discount()
    elif offer := ld_product.get("offers") or {}:
        product.price = parse_float(offer.get("price")) or product.price

    rating = ld_product.get("aggregateRating") or {}
    score = meta.get("rating") or rating.get("ratingValue")  # stored as a float: 4.599999904632568
    product.rating = round(float(score), 2) if score else product.rating
    count = meta.get("ratingCount", rating.get("ratingCount"))
    product.reviews_count = parse_int(str(count)) if count is not None else product.reviews_count

    shop = _first(colls.get("shopInfo"), meta_key)
    product.seller = shop.get("name") or product.seller
    product.seller_rating = parse_float(shop.get("rating")) or product.seller_rating

    if "OutOfStock" in str((ld_product.get("offers") or {}).get("availability", "")):
        product.stock = 0
    # The last crumbs may be "category + brand" (a listing filtered by ``glfilter``) and the product itself.
    crumbs = next((d for d in json_ld(page_html) if d.get("@type") == "BreadcrumbList"), {})
    names = [item.get("name", "") for item in (e.get("item") or {} for e in crumbs.get("itemListElement") or [])
             if "glfilter=" not in item.get("@id", "") and "/card/" not in item.get("@id", "")]
    if names := [n for n in names if n]:
        category = names[-1]
        if product.brand and category.lower().endswith(" " + product.brand.lower()):
            category = category[: -len(product.brand)].strip()
        product.category = category
    product.image = ld_product.get("image") or product.image
    if url := ld_product.get("url"):
        product.url = card_url(url)
    elif not product.url:
        product.url = f"{SITE}/card/x/{product.article}"
    return True


# --- reviews ---


def _variant(item: dict[str, Any]) -> str:
    params = (item.get("liteOffer") or {}).get("jumpTableParams") or []
    return ", ".join(f"{p['name']}: {p['value']}" for p in params if p.get("name") and p.get("value"))


def parse_review(item: dict[str, Any], product: Product) -> Review:
    analytics = item.get("analyticsData") or {}
    review_sku = str(analytics.get("oskuId") or analytics.get("skuId") or "")
    other_variant = review_sku and review_sku != product.article
    date_text = next((d.get("content", "") for d in item.get("descriptor") or [] if d.get("type") == "text"), "")
    return Review(
        marketplace=TITLE,
        article=product.article,
        product_name=product.name,
        date=parse_ru_date(date_text),
        rating=parse_int(str(item.get("rating") or analytics.get("grade") or "")),
        author=(item.get("author") or {}).get("nickname", ""),
        text=item.get("comment") or "",
        pros=item.get("pro") or "",
        cons=item.get("contra") or "",
        variant=_variant(item) if other_variant else "",
        photos=len(item.get("media") or []),
        likes=(item.get("votes") or {}).get("votesAgree"),
    )


def review_items(page_html: str) -> list[dict[str, Any]]:
    return list((collections(page_html).get("reviewV2") or {}).values())


class YandexMarketParser(MarketplaceParser):
    key = "ym"
    title = TITLE
    pause_range = (0.8, 1.6)

    page: Page | None = None

    async def prepare(self) -> None:
        self.reporter.log(f"{TITLE}: открываю сайт…")
        self.page = await self.browser.new_page()
        await self._goto(SITE + "/")

    async def _goto(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if await self._captcha_shown():
            await self._wait_for_captcha()

    async def _captcha_shown(self) -> bool:
        assert self.page is not None
        return any(marker in self.page.url for marker in CAPTCHA_MARKERS)

    async def _wait_for_captcha(self) -> None:
        """Market asked "Вы не робот?". A person solves it in the visible browser; the parser only waits."""
        if self.browser.headless:
            raise ParserError(f"{TITLE}: сайт попросил пройти проверку «Я не робот». Включите «Показывать браузер», "
                              "запустите сбор снова и пройдите проверку в открывшемся окне.")
        self.reporter.warn(f"{TITLE}: пройдите проверку «Я не робот» в окне браузера — сбор продолжится сам.")
        for _ in range(CAPTCHA_WAIT):
            self.reporter.check_cancel()
            await asyncio.sleep(1)
            if not await self._captcha_shown():
                await asyncio.sleep(2)
                return
        raise ParserError(f"{TITLE}: проверка «Я не робот» не пройдена за {CAPTCHA_WAIT // 60} мин.")

    async def _html(self, path: str) -> str:
        """Download a server-rendered page. Returns "" for pages that do not exist."""
        assert self.page is not None
        status = 0
        for attempt in range(4):
            self.reporter.check_cancel()
            status, text = await self.browser.fetch(self.page, SITE + path, {"accept": "text/html"})
            if status == 200 and not any(marker in text[:20_000] for marker in CAPTCHA_MARKERS):
                return text
            if status == 404:
                return ""
            if status in (200, 302, 403):  # captcha: open the page so that a person can solve it
                await self._goto(SITE + path)
                continue
            await asyncio.sleep(3 * (attempt + 1))
        raise ParserError(f"{TITLE}: сервер ответил ошибкой {status}")

    # --- search ---

    async def search(self, query: str, limit: int, sort: SortOrder) -> list[Product]:
        if sort == SortOrder.NEW:
            self.reporter.warn(f"{TITLE}: сортировки «Новинки» на сайте нет, использую «По популярности»")
        products = await self._collect_results(search_url(query, sort, self.settings), limit)
        if not products:
            self.reporter.warn(f"{TITLE}: по запросу «{query}» ничего не найдено")
        return products

    async def listing(self, url: str, limit: int) -> list[Product]:
        """Search results, a category or a shop page with the filters set in the link."""
        products = await self._collect_results(url, limit)
        if not products:
            self.reporter.warn(f"{TITLE}: по ссылке ничего не найдено — {url}")
        return products

    async def _collect_results(self, url: str, limit: int) -> list[Product]:
        assert self.page is not None
        await self._goto(url)
        products: list[Product] = []
        seen: set[str] = set()
        idle = 0
        while len(products) < limit and idle < MAX_IDLE_STEPS:
            added = 0
            for raw in await self.page.evaluate(_SNIPPETS_JS, FOREIGN_BLOCKS):
                product = parse_snippet(raw)
                if product is None or product.article in seen:
                    continue
                seen.add(product.article)
                product.position = len(products) + 1
                products.append(product)
                added += 1
                if len(products) >= limit:
                    break
            self.reporter.progress(len(products), limit, f"{TITLE}: товары {len(products)} из {limit}")
            idle = 0 if added else idle + 1
            if len(products) < limit:
                await self._load_more()
        return products

    async def _load_more(self) -> None:
        """Press "Показать еще" if it is there, otherwise bring the end of the list into view.

        The lazy loader sits between the last result and the footer, so the page is scrolled to a point
        a couple of screens above the bottom, then nudged down to make the loader cross the viewport.
        """
        assert self.page is not None
        self.reporter.check_cancel()
        button = self.page.get_by_role("button", name="Показать еще")
        try:
            if await button.count():
                await button.first.scroll_into_view_if_needed(timeout=5_000)
                await button.first.click(timeout=5_000)
                await asyncio.sleep(1.5)
                return
        except Exception:  # the button re-renders while it is being clicked; scrolling below also loads more
            pass
        await self.page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight - innerHeight * 2.5)")
        await asyncio.sleep(0.7)
        await self.page.evaluate("window.scrollBy(0, innerHeight)")
        await asyncio.sleep(1.3)

    # --- cards ---

    async def products_by_ids(self, articles: list[str]) -> list[Product]:
        products = []
        for index, article in enumerate(articles, 1):
            product = Product(marketplace=TITLE, article=article)
            if parse_card(await self._html(f"/card/x/{article}"), product):
                products.append(product)
            else:
                self.reporter.warn(f"{TITLE}: товар {article} не найден")
            self.reporter.progress(index, len(articles), f"{TITLE}: карточки {index} из {len(articles)}")
            await self.pause()
        return products

    async def enrich(self, products: list[Product], field_keys: set[str]) -> None:
        """Search results lack brand, seller and category: open the cards if those fields are wanted."""
        if not field_keys & YM_DETAIL_FIELDS:
            return
        pending = [p for p in products if not p.seller]
        for index, product in enumerate(pending, 1):
            parse_card(await self._html(f"/card/x/{product.article}"), product)
            self.reporter.progress(index, len(pending), f"{TITLE}: карточки {index} из {len(pending)}")
            await self.pause()

    # --- reviews ---

    async def reviews(self, product: Product, limit: int) -> list[Review]:
        """Reviews in the site's default "Полезные" order, 10 per page."""
        reviews: list[Review] = []
        seen: set[str] = set()
        page_number = 1
        while len(reviews) < limit:
            suffix = f"?page={page_number}" if page_number > 1 else ""
            items = [i for i in review_items(await self._html(f"/card/x/{product.article}/reviews{suffix}"))
                     if str(i.get("id")) not in seen]
            if not items:
                break
            for item in items[: limit - len(reviews)]:
                seen.add(str(item.get("id")))
                reviews.append(parse_review(item, product))
            page_number += 1
            await self.pause()
        return reviews
