"""Wildberries parser.

* Search and product cards: WB's internal API (``/__internal/u-search``, ``/__internal/u-card``). It is
  protected by the ``x_wbaas_token`` cookie and requires the ``deviceid`` header, so requests are
  sent from inside an opened wildberries.ru page with the headers captured from the site's own requests.
* Reviews: ``feedbacks{1,2}.wb.ru`` (not protected).
* Images and categories: WB media CDN, whose host depends on the article number (see ``/api/v3/upstreams``).
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from patchright.async_api import Page, Request

from ..models import Product, Review
from ..regions import wb_dest
from ..settings import SortOrder
from .base import MarketplaceParser, ParserError

SITE = "https://www.wildberries.ru"
SEARCH_PATH = "/__internal/u-search/exactmatch/ru/common/v18/search"  # refreshed from live traffic
DETAIL_PATH = "/__internal/u-card/cards/v4/detail"
FEEDBACK_HOSTS = ("https://feedbacks1.wb.ru", "https://feedbacks2.wb.ru")
UPSTREAMS_URL = "https://cdn.wbbasket.ru/api/v3/upstreams"

SEARCH_PAGE_SIZE = 100
SEARCH_MAX_PAGES = 100  # WB does not return results beyond page 100
DETAIL_BATCH = 50

SORTS = {
    SortOrder.POPULAR: "popular",
    SortOrder.PRICE_ASC: "priceup",
    SortOrder.PRICE_DESC: "pricedown",
    SortOrder.RATING: "rate",
    SortOrder.NEW: "newly",
}

COMMON_PARAMS = {
    "ab_testing": "false",
    "appType": 1,
    "curr": "rub",
    "hide_dflags": 1048576,
    "hide_vflags": 4294967296,
    "lang": "ru",
    "locale": "ru",
    "spp": 30,
}

TOKEN_TIMEOUT = 45


def product_url(article: str | int) -> str:
    return f"{SITE}/catalog/{article}/detail.aspx"


def parse_product(item: dict[str, Any], position: int | None = None) -> Product:
    """Convert a product from WB search/detail JSON. Prices come in kopecks.

    Stock is not taken: for anonymous visitors WB reports a placeholder quantity (39) for every product.
    """
    article = str(item["id"])
    price = price_old = None
    for size in item.get("sizes") or []:
        info = size.get("price")
        if info and info.get("product"):
            price, price_old = info["product"] / 100, info.get("basic", 0) / 100 or None
            break
    if price is None and item.get("salePriceU"):  # legacy card format
        price, price_old = item["salePriceU"] / 100, (item.get("priceU") or 0) / 100 or None

    product = Product(
        marketplace="Wildberries",
        article=article,
        name=item.get("name", ""),
        brand=item.get("brand", ""),
        seller=item.get("supplier", ""),
        seller_rating=item.get("supplierRating"),
        price=price,
        price_old=price_old if price_old and price and price_old > price else None,
        rating=item.get("reviewRating") or item.get("rating") or None,
        reviews_count=item.get("feedbacks") if item.get("feedbacks") is not None else item.get("nmFeedbacks"),
        position=position,
        url=product_url(article),
        reviews_key=str(item.get("root") or ""),
    )
    product.fill_discount()
    return product


def parse_feedback(item: dict[str, Any], product: Product) -> Review:
    variant = ", ".join(v for v in (item.get("color"), item.get("size")) if v)
    if str(item.get("nmId", product.article)) != product.article:
        variant = ", ".join(v for v in (variant, f"арт. {item['nmId']}") if v)
    created = item.get("createdDate")
    return Review(
        marketplace="Wildberries",
        article=product.article,
        product_name=product.name,
        date=datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        if created
        else None,
        rating=item.get("productValuation"),
        author=(item.get("wbUserDetails") or {}).get("name", ""),
        text=item.get("text") or "",
        pros=item.get("pros") or "",
        cons=item.get("cons") or "",
        variant=variant,
        photos=len(item.get("photos") or item.get("photo") or []),
        likes=(item.get("votes") or {}).get("pluses"),
        seller_answer=(item.get("answer") or {}).get("text", ""),
    )


class MediaHosts:
    """Maps an article to its CDN host: ``vol = article // 100000`` falls into one of the host ranges."""

    def __init__(self, upstreams: dict[str, Any] | None) -> None:
        self.ranges: list[tuple[int, int, str]] = []
        for route in ((upstreams or {}).get("origin") or {}).get("mediabasket_route_map") or []:
            if route.get("method") == "range":
                self.ranges += [(h["vol_range_from"], h["vol_range_to"], h["host"]) for h in route["hosts"]]

    def base_url(self, article: str) -> str | None:
        nm = int(article)
        vol, part = nm // 100_000, nm // 1_000
        for start, end, host in self.ranges:
            if start <= vol <= end:
                return f"https://{host}/vol{vol}/part{part}/{nm}"
        return None


class WildberriesParser(MarketplaceParser):
    key = "wb"
    title = "Wildberries"
    pause_range = (0.2, 0.5)

    page: Page | None = None
    headers: dict[str, str]
    search_path: str = SEARCH_PATH
    media: MediaHosts

    async def prepare(self) -> None:
        self.reporter.log("Wildberries: открываю сайт и прохожу проверку…")
        self.page = await self.browser.new_page()
        await self._capture_session()
        _, upstreams = await self.browser.get_json(UPSTREAMS_URL)
        self.media = MediaHosts(upstreams)
        if not self.media.ranges:
            self.reporter.warn("Wildberries: не удалось получить карту CDN — ссылки на фото и категории будут пустыми.")

    async def _capture_session(self) -> None:
        """Load a search page and copy the headers of the site's own API request."""
        assert self.page is not None
        captured: asyncio.Future[Request] = asyncio.get_running_loop().create_future()

        def on_request(request: Request) -> None:
            if "/__internal/u-search/" in request.url and "deviceid" in request.headers and not captured.done():
                captured.set_result(request)

        self.page.on("request", on_request)
        try:
            warmup = self.settings.query.strip() or "товары"
            await self.page.goto(f"{SITE}/catalog/0/search.aspx?search={quote(warmup)}", wait_until="domcontentloaded",
                                 timeout=60_000)
            request = await asyncio.wait_for(captured, TOKEN_TIMEOUT)
        except TimeoutError as exc:
            raise ParserError(
                "Wildberries: не удалось пройти защиту сайта. Проверьте интернет или включите «Показывать браузер»."
            ) from exc
        finally:
            self.page.remove_listener("request", on_request)

        self.headers = {k: v for k, v in request.headers.items() if k == "deviceid" or k.startswith("x-")}
        if match := re.search(r"(/__internal/u-search/[^?]+/search)\?", request.url):
            self.search_path = match.group(1)

    async def _api(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.page is not None
        url = f"{path}?{urlencode({**COMMON_PARAMS, **params}, quote_via=quote)}"
        for attempt in range(4):
            self.reporter.check_cancel()
            status, text = await self.browser.fetch(self.page, url, self.headers)
            if status == 200:
                return json.loads(text) if text else {}
            if status in (401, 403, 498):  # token expired
                self.reporter.log("Wildberries: обновляю токен доступа…")
                await self._capture_session()
            elif status == 429 or status >= 500 or status == 0:
                await asyncio.sleep(2 * (attempt + 1))
            else:
                break
        raise ParserError(f"Wildberries: сервер ответил ошибкой {status}")

    def _dest(self) -> int:
        return wb_dest(self.settings.region)

    async def search(self, query: str, limit: int, sort: SortOrder) -> list[Product]:
        products: list[Product] = []
        seen: set[str] = set()
        page_no = 1
        while len(products) < limit and page_no <= SEARCH_MAX_PAGES:
            data = await self._search_page(query, sort, page_no)
            items = data.get("products") or (data.get("data") or {}).get("products") or []
            if page_no == 1:
                if len(items) <= 1:
                    # WB occasionally answers a freshly issued token with an almost empty result set.
                    await asyncio.sleep(2)
                    await self._capture_session()
                    retry = await self._search_page(query, sort, page_no)
                    retry_items = retry.get("products") or []
                    if len(retry_items) > len(items):
                        data, items = retry, retry_items
                self.reporter.log(f"Wildberries: найдено товаров по запросу — {data.get('total', len(items))}")
            for item in items:
                product = parse_product(item, position=len(products) + 1)
                if product.article not in seen:
                    seen.add(product.article)
                    products.append(product)
                if len(products) >= limit:
                    break
            self.reporter.progress(len(products), limit, f"Wildberries: товары {len(products)} из {limit}")
            if len(items) < SEARCH_PAGE_SIZE:
                break
            page_no += 1
            await self.pause()
        return products

    async def _search_page(self, query: str, sort: SortOrder, page_no: int) -> dict[str, Any]:
        return await self._api(self.search_path, {
            "dest": self._dest(),
            "inheritFilters": "true",
            "page": page_no,
            "query": query,
            "resultset": "catalog",
            "sort": SORTS[sort],
            "suppressSpellcheck": "false",
        })

    async def products_by_ids(self, articles: list[str]) -> list[Product]:
        found: dict[str, Product] = {}
        for start in range(0, len(articles), DETAIL_BATCH):
            batch = articles[start:start + DETAIL_BATCH]
            data = await self._api(DETAIL_PATH, {"dest": self._dest(), "nm": ";".join(batch)})
            items = data.get("products") or (data.get("data") or {}).get("products") or []
            for item in items:
                product = parse_product(item)
                found[product.article] = product
            done = min(start + DETAIL_BATCH, len(articles))
            self.reporter.progress(done, len(articles), f"Wildberries: карточки {done} из {len(articles)}")
            await self.pause()
        missing = [a for a in articles if a not in found]
        if missing:
            self.reporter.warn(f"Wildberries: не найдены артикулы: {', '.join(missing)}")
        return [found[a] for a in articles if a in found]

    async def enrich(self, products: list[Product], field_keys: set[str]) -> None:
        if not self.media.ranges:
            return
        for product in products:
            if base := self.media.base_url(product.article):
                product.image = f"{base}/images/big/1.webp"
        if "category" not in field_keys:
            return

        semaphore = asyncio.Semaphore(8)
        done = 0

        async def load_card(product: Product) -> None:
            nonlocal done
            base = self.media.base_url(product.article)
            async with semaphore:
                self.reporter.check_cancel()
                _, card = await self.browser.get_json(f"{base}/info/ru/card.json") if base else (0, None)
            if card:
                product.category = card.get("subj_name", "")
            done += 1
            self.reporter.progress(done, len(products), f"Wildberries: категории {done} из {len(products)}")

        await asyncio.gather(*(load_card(p) for p in products))

    async def reviews(self, product: Product, limit: int) -> list[Review]:
        if not product.reviews_key:
            return []
        data = None
        for host in FEEDBACK_HOSTS:
            self.reporter.check_cancel()
            _, data = await self.browser.get_json(f"{host}/feedbacks/v1/{product.reviews_key}")
            if data and data.get("feedbacks"):
                break
        items = sorted((data or {}).get("feedbacks") or [], key=lambda f: f.get("createdDate", ""), reverse=True)
        return [parse_feedback(item, product) for item in items[:limit]]
