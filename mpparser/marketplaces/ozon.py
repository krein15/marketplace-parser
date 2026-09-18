"""Ozon parser.

Ozon renders pages from JSON "widget states" served by ``/api/entrypoint-api.bx/page/json/v2?url=<page path>``.
The endpoint is behind Ozon's anti-bot check, so it is called with ``fetch`` from an opened ozon.ru page.

* Search: ``tileGridDesktop`` widgets; the next chunk path comes from the paginator widget.
* Product card: ``webProductHeading``, ``webPrice``, ``webReviewProductScore``, ``webCurrentSeller``...
* Reviews: ``webListReviews`` on ``/product/<sku>/reviews/``, 30 per page, ``paging.nextButton`` for the next page.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from patchright.async_api import Page

from ..fields import OZON_DETAIL_FIELDS
from ..models import Product, Review
from ..settings import SortOrder
from ..textutils import parse_float, parse_int
from .base import MarketplaceParser, ParserError

SITE = "https://www.ozon.ru"
API = "/api/entrypoint-api.bx/page/json/v2?url="
ANTIBOT_TIMEOUT = 45
BLOCKED_TITLES = ("Доступ ограничен", "нет соединения")
MAX_SEARCH_CHUNKS = 300

SORTS = {
    SortOrder.POPULAR: "score",
    SortOrder.PRICE_ASC: "price",
    SortOrder.PRICE_DESC: "price_desc",
    SortOrder.RATING: "rating",
    SortOrder.NEW: "new",
}

STOCK_TEXT = re.compile(r"(\d[\d\s   ]*)\s*шт", re.IGNORECASE)


def widgets(page: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Decode all widget states whose id starts with ``prefix`` (ids look like ``webPrice-3121879-default-1``)."""
    result = []
    for key, raw in (page.get("widgetStates") or {}).items():
        if key.split("-", 1)[0] == prefix:
            try:
                result.append(json.loads(raw) if isinstance(raw, str) else raw)
            except ValueError:
                continue
    return result


def widget(page: dict[str, Any], prefix: str) -> dict[str, Any]:
    found = widgets(page, prefix)
    return found[0] if found else {}


def clean_url(link: str) -> str:
    link = link.split("?", 1)[0]
    return link if link.startswith("http") else SITE + link


def parse_tile(item: dict[str, Any], position: int | None = None) -> Product:
    """Convert a search result tile."""
    product = Product(marketplace="Ozon", article=str(item.get("sku") or item.get("id")), position=position)
    for atom in item.get("mainState") or []:
        kind = atom.get("type")
        body = atom.get(kind) or {}
        if kind == "priceV2":
            for price in body.get("price") or []:
                if price.get("textStyle") == "PRICE":
                    product.price = parse_float(price.get("text"))
                elif price.get("textStyle") == "ORIGINAL_PRICE":
                    product.price_old = parse_float(price.get("text"))
            product.discount = parse_int(body.get("discount"))
        elif kind in ("textDS", "textAtom"):
            text = body.get("text", "")
            automation_id = (body.get("testInfo") or {}).get("automatizationId", "")
            if atom.get("id") == "name" or automation_id == "tile-name":
                product.name = text
            elif "осталось" in text and (match := STOCK_TEXT.search(text)):
                product.stock = parse_int(match.group(1))
        elif kind == "labelListV2":
            _parse_labels(body.get("items") or [], product)

    link = (item.get("action") or {}).get("link")
    if link:
        product.url = clean_url(link)
    images = (item.get("tileImage") or {}).get("items") or []
    if images:
        product.image = (images[0].get("image") or {}).get("link", "")
    product.fill_discount()
    return product


def _parse_labels(items: list[dict[str, Any]], product: Product) -> None:
    """Rating and review count are icon + text pairs: ★ 4.9  💬 11 824."""
    for icon, label in itertools.pairwise(items):
        if icon.get("type") != "icon" or label.get("type") != "text":
            continue
        name = ((icon.get("icon") or {}).get("icon") or {}).get("icon", "")
        text = (label.get("text") or {}).get("text", "")
        if "star" in name:
            product.rating = parse_float(text)
        elif "dialog" in name:
            product.reviews_count = parse_int(text)


def parse_product_page(page: dict[str, Any], product: Product) -> bool:
    """Fill ``product`` from a product page. Returns False if the page is not a product card."""
    heading = widget(page, "webProductHeading")
    price = widget(page, "webPrice")
    if not heading and not price:
        return False

    product.name = heading.get("title") or product.name
    if price:
        if price.get("isAvailable") is False:
            product.stock = 0
        product.price = parse_float(price.get("price")) or product.price
        product.price_card = parse_float(price.get("cardPrice"))
        product.price_old = parse_float(price.get("originalPrice")) or product.price_old
        if product.price_old and product.price and product.price_old <= product.price:
            product.price_old = None
        product.discount = None
        product.fill_discount()

    score = widget(page, "webReviewProductScore")
    if score:
        product.rating = score.get("totalScore") or product.rating
        product.reviews_count = score.get("reviewsCount", product.reviews_count)

    seller = widget(page, "webCurrentSeller")
    if seller:
        cell = seller.get("sellerCell") or {}
        product.seller = ((cell.get("centerBlock") or {}).get("title") or {}).get("text", "")
        product.seller_rating = parse_float(((seller.get("rating") or {}).get("title") or {}).get("text"))

    tracking = page.get("layoutTrackingInfo") or {}
    if isinstance(tracking, str):
        try:
            tracking = json.loads(tracking)
        except ValueError:
            tracking = {}
    product.category = tracking.get("categoryName", "") or product.category
    # Ozon breadcrumbs end with the brand when the product has one: "Электроника / … / Наушники / Zuzy".
    crumbs = [c.get("text", "") for c in widget(page, "breadCrumbs").get("breadcrumbs") or []]
    if len(crumbs) >= 2 and crumbs[-1] != product.category:
        product.brand = crumbs[-1]

    gallery = widget(page, "webGallery")
    product.image = gallery.get("coverImage") or product.image
    for link in (page.get("seo") or {}).get("link") or []:
        if link.get("rel") == "canonical":
            product.url = link.get("href", product.url)
    if not product.url:
        product.url = f"{SITE}/product/{product.article}/"
    return True


def parse_review(item: dict[str, Any], product: Product, variants: dict[str, Any]) -> Review:
    content = item.get("content") or {}
    published = item.get("publishedAt") or item.get("createdAt")
    variant = ""
    item_id = str(item.get("itemId", ""))
    if item_id and item_id != product.article and item_id in variants:
        values = [v.get("value", "") for v in variants[item_id].get("variants") or []]
        variant = ", ".join(v for v in values if v) or f"арт. {item_id}"
    return Review(
        marketplace="Ozon",
        article=product.article,
        product_name=product.name,
        date=datetime.fromtimestamp(published) if published else None,
        rating=content.get("score"),
        author=(item.get("author") or {}).get("firstName", ""),
        text=content.get("comment") or "",
        pros=content.get("positive") or "",
        cons=content.get("negative") or "",
        variant=variant,
        photos=len(content.get("photos") or []),
        likes=(item.get("usefulness") or {}).get("useful"),
    )


def next_search_path(page: dict[str, Any]) -> str | None:
    for name in ("infiniteVirtualPaginator", "megaPaginator", "paginator"):
        for state in widgets(page, name):
            if state.get("nextPage"):
                return state["nextPage"]
    return page.get("nextPage") or None


class OzonParser(MarketplaceParser):
    key = "ozon"
    title = "Ozon"
    pause_range = (0.5, 1.2)

    page: Page | None = None

    async def prepare(self) -> None:
        self.reporter.log("Ozon: открываю сайт и прохожу проверку…")
        self.page = await self.browser.new_page()
        await self._open_home()

    async def _open_home(self) -> None:
        """Wait until Ozon's JS challenge ("Antibot Challenge Page") lets the browser in.

        Calling the API before the challenge completes gets the profile flagged: Ozon then shows
        "Похоже, нет соединения" for every page. Such a profile recovers after its Ozon cookies are cleared.
        """
        assert self.page is not None
        for attempt in range(2):
            await self.page.goto(SITE + "/", wait_until="domcontentloaded", timeout=60_000)
            state = await self._wait_for_site()
            if state == "ready":
                await asyncio.sleep(1.5)  # let the challenge scripts finish setting cookies
                return
            if state == "blocked" and attempt == 0:
                self.reporter.log("Ozon: сайт заблокировал сессию, сбрасываю куки и пробую снова…")
                await self.browser.context.clear_cookies(domain=".ozon.ru")
                await self.browser.context.clear_cookies(domain="www.ozon.ru")
                continue
            break
        raise ParserError("Ozon: не удалось пройти проверку сайта. Включите «Показывать браузер» и попробуйте снова.")

    async def _wait_for_site(self) -> str:
        assert self.page is not None
        title = ""
        for _ in range(ANTIBOT_TIMEOUT * 2):
            self.reporter.check_cancel()
            title = await self.page.title()
            if any(marker in title for marker in BLOCKED_TITLES):
                return "blocked"
            if "ozon" in title.lower() and "challenge" not in title.lower():
                return "ready"
            await asyncio.sleep(0.5)
        return "timeout"

    async def _page_json(self, path: str) -> dict[str, Any]:
        assert self.page is not None
        url = API + quote(path, safe="")
        status = 0
        for attempt in range(4):
            self.reporter.check_cancel()
            status, text = await self.browser.fetch(self.page, url, {"accept": "application/json"})
            if status == 200:
                data = json.loads(text)
                if redirect := data.get("redirect"):  # e.g. a short product link resolved to its full path
                    url = API + quote(redirect.removeprefix(SITE), safe="")
                    continue
                return data
            if status in (403, 307):
                self.reporter.log("Ozon: повторная проверка сайта…")
                await self._open_home()
            elif status == 404:
                return {}
            elif status == 429 or status >= 500 or status == 0:
                await asyncio.sleep(3 * (attempt + 1))
            else:
                break
        raise ParserError(f"Ozon: сервер ответил ошибкой {status}")

    async def search(self, query: str, limit: int, sort: SortOrder) -> list[Product]:
        path: str | None = f"/search/?text={quote(query)}&from_global=true"
        if SORTS[sort] != "score":
            path += f"&sorting={SORTS[sort]}"
        products: list[Product] = []
        seen: set[str] = set()
        chunks = 0
        while path and len(products) < limit and chunks < MAX_SEARCH_CHUNKS:
            data = await self._page_json(path)
            chunks += 1
            added = 0
            for grid in widgets(data, "tileGridDesktop") + widgets(data, "searchResultsV2"):
                for item in grid.get("items") or []:
                    sku = str(item.get("sku") or "")
                    if not sku or sku in seen:
                        continue
                    seen.add(sku)
                    products.append(parse_tile(item, position=len(products) + 1))
                    added += 1
                    if len(products) >= limit:
                        break
                if len(products) >= limit:
                    break
            self.reporter.progress(len(products), limit, f"Ozon: товары {len(products)} из {limit}")
            next_path = next_search_path(data)
            if not added and next_path == path:
                break
            path = next_path
            await self.pause()
        if not products:
            self.reporter.warn(f"Ozon: по запросу «{query}» ничего не найдено")
        return products

    async def products_by_ids(self, articles: list[str]) -> list[Product]:
        products = []
        for index, article in enumerate(articles, 1):
            product = Product(marketplace="Ozon", article=article)
            if parse_product_page(await self._page_json(f"/product/{article}/"), product):
                products.append(product)
            else:
                self.reporter.warn(f"Ozon: артикул {article} не найден")
            self.reporter.progress(index, len(articles), f"Ozon: карточки {index} из {len(articles)}")
            await self.pause()
        return products

    async def enrich(self, products: list[Product], field_keys: set[str]) -> None:
        """Search tiles lack seller, brand, card price and category: open the cards if those fields are wanted."""
        if not field_keys & OZON_DETAIL_FIELDS:
            return
        pending = [p for p in products if not p.seller]
        for index, product in enumerate(pending, 1):
            parse_product_page(await self._page_json(f"/product/{product.article}/"), product)
            self.reporter.progress(index, len(pending), f"Ozon: карточки {index} из {len(pending)}")
            await self.pause()

    async def reviews(self, product: Product, limit: int) -> list[Review]:
        reviews: list[Review] = []
        path: str | None = f"/product/{product.article}/reviews/"
        while path and len(reviews) < limit:
            state = widget(await self._page_json(path), "webListReviews")
            items = state.get("reviews") or []
            variants = state.get("products") or {}
            reviews += [parse_review(item, product, variants) for item in items[: limit - len(reviews)]]
            next_params = (state.get("paging") or {}).get("nextButton")
            path = f"/product/{product.article}/reviews/{next_params}" if items and next_params else None
            if path:
                await self.pause()
        return reviews
