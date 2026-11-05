"""Alibaba.com parser.

Alibaba.com is the export marketplace of Chinese factories and trading companies: wholesale prices in dollars that
depend on the quantity, and a minimum order. Unlike 1688 and Taobao it serves anonymous visitors from abroad, so
everything below works without an account:

* search: ``/search/api/proTextSearch`` — JSON with up to 120 offers per page; the site's own filters
  (``pricef``, ``moqt``, ``country``…) are its parameters, and a Russian query is translated by the site itself;
* product card: ``window.detailData`` in the HTML of ``/product-detail/<slug>_<id>.html`` — price tiers in
  dollars, product rating, brand, origin, breadcrumbs, supplier type;
* reviews: the signed mtop API ``mtop.alibaba.icbu.review.complete.pc.productreview``, the one the card calls.

The site shows prices in the visitor's currency and language. The ``sc_g_cfg_f`` cookie asks for dollars and
``language=ru`` for titles and reviews machine-translated into Russian. Dollar prices are converted to roubles at
the Bank of Russia rate; the dollars stay in columns of their own.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from patchright.async_api import Page

from ..currency import CBR_URL, Rate, RateError, choose_rate
from ..fields import Field
from ..models import Product, Review
from ..plugins import Marketplace, Option
from ..settings import ParseSettings, SortOrder, app_data_dir
from ..textutils import parse_float, parse_int
from .base import MarketplaceParser, ParserError

SITE = "https://www.alibaba.com"
TITLE = "Alibaba.com"
KEY = "alibaba"
SEARCH_API = "/search/api/proTextSearch"
MTOP = "https://acs.h.alibaba.com/h5"
APP_KEY = "12574478"  # the web application key the site's own pages sign mtop requests with
REVIEWS_API = "mtop.alibaba.icbu.review.complete.pc.productreview"
PAGE_SIZE = 60
REVIEWS_PAGE_SIZE = 20
MAX_PAGES = 100
LANGUAGE = "ru"
COOKIES = [
    # Prices in dollars and pages in Russian whatever country the visitor comes from.
    {"name": "sc_g_cfg_f", "value": "sc_b_currency=USD&sc_b_locale=ru_RU", "domain": ".alibaba.com", "path": "/"},
    {"name": "buyer_ship_to_info", "value": "local_country=RU", "domain": ".alibaba.com", "path": "/"},
]
CAPTCHA_MARKERS = ("_____tmd_____", "/punish", "x5secdata", "rgv587_flag")
CAPTCHA_WAIT = 180

# A link to one product: /product-detail/<slug>_<id>.html
ITEM_LINK = re.compile(r"alibaba\.com/product-detail/(?:[^/?#\s]*?_)?(\d{8,})\.html", re.IGNORECASE)
# Search results with the filters set on the site, and showroom pages (/showroom/thermos-cup.html).
LISTING_LINK = re.compile(r"^(?:https?://)?(?:www\.|russian\.)?alibaba\.com/(?:trade/search|search/page|showroom/)\S*",
                          re.IGNORECASE)
_SHOWROOM = re.compile(r"/showroom/([^/?#]+?)(?:_\d+)?\.html", re.IGNORECASE)
# Search results lack these values: each product card has to be opened, which is slower.
DETAIL_FIELDS = {"rating", "reviews_count", "brand", "category", "price_tiers", "supplier_type", "origin"}
# Parameters of a search link that describe the page, not the products; everything else is a filter of the site.
NOT_FILTERS = {"spm", "tab", "from", "page", "pagesize", "language", "searchtext", "query", "keywords",
               "searchscene", "pro", "testoption", "requestid", "starttime", "intentionid"}
NO_BRAND = {"oem", "odm", "oem/odm", "oem & odm", "customized", "custom", "custom logo", "no brand", "none", "n/a",
            "без бренда", "нет", "на заказ", "индивидуальный", "индивидуальный логотип", "персонализированный"}
SUPPLIER_TYPES = {
    "manufacturer": "производитель",
    "trading company": "торговая компания",
    "distributor/wholesaler": "дистрибьютор",
    "buying office": "закупочный офис",
    "agent": "агент",
    "other": "другое",
}
COUNTRIES = {
    "CN": "Китай", "HK": "Гонконг", "TW": "Тайвань", "US": "США", "VN": "Вьетнам", "IN": "Индия", "TR": "Турция",
    "KR": "Южная Корея", "JP": "Япония", "TH": "Таиланд", "PK": "Пакистан", "MY": "Малайзия", "ID": "Индонезия",
    "DE": "Германия", "IT": "Италия", "GB": "Великобритания", "FR": "Франция", "BD": "Бангладеш", "RU": "Россия",
}

_TAGS = re.compile(r"<[^>]+>")
_SIZE_SUFFIX = re.compile(r"(\.(?:jpe?g|png|webp))_\d+x\d+\.\w+$", re.IGNORECASE)
_PRICE_PART = re.compile(r"\d[\d\s\xa0 .,]*")
_REVIEW_DATE = re.compile(r"(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})")


# --- text helpers ---


def strip_tags(text: str | None) -> str:
    """Search titles carry icons and highlighting: ``<img …></img><span> </span>Термокружка``."""
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub("", text or ""))).strip()


def _number(text: str) -> float | None:
    """One price as the site prints it: "3,15", "1 234,50", "$1,234.50", "0,2962"."""
    text = re.sub(r"[\s\xa0 ]", "", text).strip(".,")
    if not text:
        return None
    if "," in text and "." in text:  # the later mark is the decimal one
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(decimal, ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def parse_price_range(text: str | None) -> tuple[float | None, float | None]:
    """ "1,35-4,04 $" → (1.35, 4.04); "3,15 $" → (3.15, 3.15); "$1.99-3.49" → (1.99, 3.49)."""
    numbers = [n for n in (_number(part) for part in _PRICE_PART.findall(text or "")) if n is not None]
    if not numbers:
        return None, None
    return min(numbers), max(numbers)


def full_image(url: str | None) -> str:
    """ "//s.alicdn.com/…/H48.jpg_300x300.jpg" → "https://s.alicdn.com/…/H48.jpg" """
    if not url:
        return ""
    url = "https:" + url if url.startswith("//") else url
    return _SIZE_SUFFIX.sub(r"\1", url)


def product_url(link: str | None, article: str) -> str:
    if not link:
        return f"{SITE}/product-detail/_{article}.html"
    link = link.split("?", 1)[0]
    return "https:" + link if link.startswith("//") else link


def supplier_type(raw: str | None) -> str:
    """ "Manufacturer,Trading Company" → "Производитель, торговая компания"."""
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    text = ", ".join(SUPPLIER_TYPES.get(p.lower(), p) for p in parts)
    return text[:1].upper() + text[1:]


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " $"


def format_tiers(ladder: list[dict[str, Any]], unit: str = "шт.") -> str:
    """Price tiers by quantity: "50–999 шт. — 7,45 $; 1 000–4 999 шт. — 6,50 $; от 5 000 шт. — 6,25 $"."""
    parts = []
    for tier in ladder:
        price = tier.get("dollarPrice", tier.get("price"))
        low = tier.get("localMin") or str(tier.get("min") or "")
        high = tier.get("localMax") or str(tier.get("max") or "")
        if price is None or not low:
            continue
        quantity = f"от {low}" if high in ("", "-1") else f"{low}–{high}"
        parts.append(f"{quantity} {unit} — {_money(float(price))}")
    return "; ".join(parts)


# --- search ---


def filter_params(settings: ParseSettings, rate: Rate | None) -> dict[str, str]:
    """The site's filter parameters for the filters chosen in the program. Prices are in roubles there."""
    params = {}
    if rate is not None:
        if settings.price_min is not None:
            params["pricef"] = f"{rate.from_rub(settings.price_min):.2f}"
        if settings.price_max is not None:
            params["pricet"] = f"{rate.from_rub(settings.price_max):.2f}"
    if settings.option(KEY, "china_only"):
        params["country"] = "CN"
    if settings.option(KEY, "verified"):
        params["assessmentCompany"] = "true"
    if score := settings.option(KEY, "store_rating"):
        params["reviewScore"] = f"{score / 10:g}~"
    if moq := settings.option(KEY, "max_moq"):
        params["moqt"] = str(moq)
    return params


def search_params(query: str, settings: ParseSettings, rate: Rate | None) -> dict[str, str]:
    return {"query": query, "from": "tradeSearch", **filter_params(settings, rate)}


def listing_params(url: str) -> dict[str, str]:
    """Query and filters of a link to search results; {} if the link names nothing to search for."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    raw = {key: values[-1] for key, values in parse_qs(parts.query).items() if values and values[-1]}
    lowered = {key.lower(): value for key, value in raw.items()}
    query = lowered.get("searchtext") or lowered.get("query") or lowered.get("keywords") or ""
    if not query and (match := _SHOWROOM.search(parts.path)):
        query = match.group(1).replace("-", " ")
    params = {key: value for key, value in raw.items() if key.lower() not in NOT_FILTERS}
    if not query and not params.get("categoryId"):
        return {}
    return {"query": query, "from": "tradeSearch", **params}


def search_offers(data: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Offers of a search API answer and the number of result pages."""
    model = data.get("model") or {}
    pages = parse_int(str((model.get("paginationData") or {}).get("totalPage") or "")) or 0
    return list(model.get("offers") or []), pages


def parse_offer(offer: dict[str, Any], rate: Rate | None, position: int | None = None) -> Product | None:
    """Convert a search result. Returns None for an offer without a product id."""
    article = str(offer.get("productId") or "")
    if not article:
        return None
    product = Product(marketplace=TITLE, article=article, position=position)
    product.name = strip_tags(offer.get("title"))
    product.price_usd, product.price_usd_max = parse_price_range(offer.get("price"))
    if rate is not None:
        product.price = rate.to_rub(product.price_usd)
    product.min_order = parse_int(offer.get("moq"))
    product.sold = parse_int(offer.get("soldOrder"))
    product.seller = offer.get("companyName") or ""
    product.seller_rating = parse_float(offer.get("reviewScore"))
    product.supplier_years = parse_int(offer.get("goldSupplierYears"))
    country = str(offer.get("countryCode") or "")
    product.origin = COUNTRIES.get(country, country)
    images = offer.get("multiImage") or []
    product.image = full_image(images[0] if images else offer.get("imageUrl"))
    product.url = product_url(offer.get("productUrl"), article)
    if company := offer.get("companyId"):
        product.reviews_key = str(company)
    return product


# --- product card ---


def detail_data(page_html: str) -> dict[str, Any] | None:
    """The ``window.detailData`` object of a product page, or None if the page is not a product card."""
    marker = page_html.find("window.detailData")
    start = page_html.find("{", marker) if marker >= 0 else -1
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html, start)
    except ValueError:
        return None
    return data if isinstance(data, dict) and (data.get("globalData") or {}).get("product") else None


def _property(product: dict[str, Any], attr_id: int) -> str:
    for prop in product.get("productBasicProperties") or []:
        if prop.get("attrNameId") == attr_id:
            return str(prop.get("attrValue") or "").strip()
    return ""


def parse_detail(data: dict[str, Any], product: Product, rate: Rate | None) -> bool:
    """Fill ``product`` from ``window.detailData``. Returns False if the data is not a product card."""
    root = data.get("globalData") or {}
    item = root.get("product") or {}
    if not item:
        return False
    seller = root.get("seller") or {}

    product.name = item.get("subject") or product.name
    price = item.get("price") or {}
    ladder = price.get("productLadderPrices") or []
    dollars = [float(t["dollarPrice"]) for t in ladder if t.get("dollarPrice") is not None]
    if dollars:
        product.price_usd, product.price_usd_max = min(dollars), max(dollars)
        if rate is not None:
            product.price = rate.to_rub(product.price_usd)
        product.price_tiers = format_tiers(ladder, price.get("unit") or "шт.")
    product.min_order = parse_int(str(item.get("moq") or "")) or product.min_order

    review = root.get("review") or {}
    if own := review.get("productReview"):
        product.rating = parse_float(str(own.get("averageStar") or "")) or None
        product.reviews_count = parse_int(str(own.get("totalReviewCount") or "0")) or 0
    else:  # only the store's rating is there: nobody has reviewed this product yet
        product.rating, product.reviews_count = None, 0
    store = (review.get("storeReview") or {}).get("averageStar") or \
        (seller.get("supplierRatingReviews") or {}).get("averageStar")
    product.seller_rating = parse_float(str(store or "")) or product.seller_rating

    brand = _property(item, 2)  # "Наименование" / "Brand Name"
    product.brand = "" if brand.lower() in NO_BRAND else brand
    product.origin = _property(item, 1) or product.origin  # "Происхождение товара": "Guangdong, China"
    product.seller = seller.get("companyName") or product.seller
    product.supplier_type = supplier_type(seller.get("companyBusinessType"))
    product.supplier_years = product.supplier_years or parse_int(str(seller.get("companyJoinYears") or ""))
    if company := seller.get("companyId"):
        product.reviews_key = str(company)
    sales = (root.get("trade") or {}).get("salesVolume")
    product.sold = parse_int(sales) if sales else product.sold

    crumbs = ((root.get("seo") or {}).get("breadCrumb") or {}).get("pathList") or []
    names = [(c.get("hrefObject") or {}).get("name", "") for c in crumbs]
    product.category = next((n for n in reversed(names) if n), product.category)
    media = item.get("mediaItems") or []
    if media and not product.image:
        product.image = full_image((media[0].get("imageUrl") or {}).get("big"))
    if not product.url:
        product.url = product_url(None, product.article)
    return True


# --- reviews ---


def mtop_url(api: str, payload: str, token: str, timestamp: str) -> str:
    """A request to the site's mtop gateway, signed the way its own pages sign them."""
    sign = hashlib.md5(f"{token}&{timestamp}&{APP_KEY}&{payload}".encode()).hexdigest()
    params = {"jsv": "2.7.5", "appKey": APP_KEY, "t": timestamp, "sign": sign, "api": api, "v": "1.0",
              "type": "originaljson", "dataType": "json", "data": payload}
    return f"{MTOP}/{api}/1.0/?{urlencode(params)}"


def review_items(answer: dict[str, Any]) -> list[dict[str, Any]]:
    target = (answer.get("data") or {}).get("target") or {}
    return list(target.get("productReviewList") or [])


def _review_date(text: str | None) -> datetime | None:
    match = _REVIEW_DATE.search(text or "")
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _photos(item: dict[str, Any]) -> int:
    return sum(len(value) for key, value in item.items()
               if isinstance(value, list) and re.search(r"image|photo|media|video", key, re.IGNORECASE))


def parse_review(item: dict[str, Any], product: Product) -> Review:
    user = item.get("simpleReviewUserVO") or {}
    country = user.get("fullNameCountry") or user.get("country") or ""
    author = user.get("anonymousName") or ""
    return Review(
        marketplace=TITLE,
        article=product.article,
        product_name=product.name,
        date=_review_date(item.get("reviewTime")),
        rating=parse_int(str((item.get("latitudeScore") or {}).get("score") or "")),
        author=f"{author} ({country})" if author and country else author or country,
        text=(item.get("reviewContent") or item.get("oriReviewContent") or "").strip(),
        photos=_photos(item),
    )


def is_captcha(text: str) -> bool:
    return any(marker in text[:5_000] for marker in CAPTCHA_MARKERS)


class AlibabaParser(MarketplaceParser):
    key = KEY
    title = TITLE
    pause_range = (0.6, 1.4)

    page: Page | None = None
    rate: Rate | None = None
    _sort_warned = False

    async def prepare(self) -> None:
        self.reporter.log(f"{TITLE}: открываю сайт…")
        await self.browser.context.add_cookies(COOKIES)
        try:
            self.rate = choose_rate(await self.browser.get_text(CBR_URL, encoding="cp1251"), "USD", app_data_dir())
        except RateError as exc:
            raise ParserError(f"{TITLE}: {exc}") from exc
        self.reporter.log(f"{TITLE}: курс доллара — {self.rate.describe()}")
        if self.rate.stale:
            self.reporter.warn(f"{TITLE}: сайт ЦБ не ответил, цены пересчитаны по последнему сохранённому курсу.")
        self.report_notes.append((f"Курс доллара ({TITLE})", self.rate.describe()))
        self.page = await self.browser.new_page()
        await self._goto(SITE + "/")

    # --- the site and its checks ---

    async def _goto(self, url: str) -> None:
        assert self.page is not None
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        if is_captcha(self.page.url):
            await self._wait_for_captcha()

    async def _wait_for_captcha(self) -> None:
        """The site asked to drag a slider. A person does it in the visible browser; the parser only waits."""
        assert self.page is not None
        if self.browser.headless:
            raise ParserError(f"{TITLE}: сайт попросил сдвинуть ползунок «Я не робот». Включите «Показывать "
                              "браузер», запустите сбор снова и сдвиньте ползунок в открывшемся окне.")
        await self.browser.bring_to_front(self.page)
        self.reporter.warn(f"{TITLE}: сдвиньте ползунок в окне браузера — сбор продолжится сам.")
        for _ in range(CAPTCHA_WAIT):
            self.reporter.check_cancel()
            await asyncio.sleep(1)
            if not is_captcha(self.page.url):
                await asyncio.sleep(2)
                return
        raise ParserError(f"{TITLE}: проверка «Я не робот» не пройдена за {CAPTCHA_WAIT // 60} мин.")

    async def _get(self, url: str, accept: str) -> str:
        """Download ``url`` from inside the opened site page. Returns "" for pages that do not exist."""
        assert self.page is not None
        status, captcha = 0, False
        for attempt in range(4):
            self.reporter.check_cancel()
            status, text = await self.browser.fetch(self.page, url, {"accept": accept})
            captcha = is_captcha(text)
            if status == 200 and not captcha:
                return text
            if status == 404:
                return ""
            if captcha or status in (403, 302):
                await self._goto(SITE + "/")  # shows the slider to the person, if the site wants one
                continue
            await asyncio.sleep(3 * (attempt + 1))
        if captcha:
            raise ParserError(f"{TITLE}: сайт не пускает без проверки «Я не робот». Включите «Показывать браузер» "
                              "и запустите сбор снова — если появится ползунок, сдвиньте его.")
        raise ParserError(f"{TITLE}: сервер ответил ошибкой {status}")

    async def _json(self, url: str) -> dict[str, Any]:
        text = await self._get(url, "application/json")
        try:
            data = json.loads(text) if text else {}
        except ValueError as exc:
            raise ParserError(f"{TITLE}: сайт вернул неожиданный ответ") from exc
        return data if isinstance(data, dict) else {}

    async def _mtop(self, api: str, data: dict[str, Any]) -> dict[str, Any]:
        """Call the mtop gateway. The first call of a session only gets a token cookie, so it is repeated."""
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        for _ in range(3):
            cookies = await self.browser.context.cookies(SITE)
            token = next((c["value"] for c in cookies if c["name"] == "_m_h5_tk"), "").split("_")[0]
            answer = await self._json(mtop_url(api, payload, token, str(int(time.time() * 1000))))
            if not any("TOKEN" in str(ret) for ret in answer.get("ret") or []):
                return answer
        raise ParserError(f"{TITLE}: не удалось получить отзывы — сайт не выдал ключ доступа")

    # --- search ---

    async def search(self, query: str, limit: int, sort: SortOrder) -> list[Product]:
        if sort != SortOrder.POPULAR and not self._sort_warned:
            self.reporter.warn(f"{TITLE}: сортировки на сайте нет — товары идут в порядке выдачи сайта")
            self._sort_warned = True
        products = await self._collect(search_params(query, self.settings, self.rate), limit)
        if not products:
            self.reporter.warn(f"{TITLE}: по запросу «{query}» ничего не найдено")
        return products

    async def listing(self, url: str, limit: int) -> list[Product]:
        """Search results with the filters set in the link."""
        params = listing_params(url)
        if not params:
            self.reporter.warn(f"{TITLE}: в ссылке нет поискового запроса — {url}")
            return []
        products = await self._collect(params, limit)
        if not products:
            self.reporter.warn(f"{TITLE}: по ссылке ничего не найдено — {url}")
        return products

    async def _collect(self, params: dict[str, str], limit: int) -> list[Product]:
        products: list[Product] = []
        seen: set[str] = set()
        skip_ads = bool(self.option("no_ads"))
        page_number, pages = 1, 1
        while len(products) < limit and page_number <= min(pages, MAX_PAGES):
            query = {**params, "page": page_number, "pageSize": PAGE_SIZE, "language": LANGUAGE}
            offers, pages = search_offers(await self._json(f"{SITE}{SEARCH_API}?{urlencode(query)}"))
            if not offers:
                break
            for offer in offers:
                if skip_ads and offer.get("isShowAd"):
                    continue
                product = parse_offer(offer, self.rate)
                if product is None or product.article in seen:
                    continue
                seen.add(product.article)
                product.position = len(products) + 1
                products.append(product)
                if len(products) >= limit:
                    break
            self.reporter.progress(len(products), limit, f"{TITLE}: товары {len(products)} из {limit}")
            page_number += 1
            await self.pause()
        return products

    # --- cards ---

    async def _card(self, product: Product) -> bool:
        data = detail_data(await self._get(f"{SITE}/product-detail/_{product.article}.html", "text/html"))
        return bool(data) and parse_detail(data, product, self.rate)

    async def products_by_ids(self, articles: list[str]) -> list[Product]:
        products = []
        for index, article in enumerate(articles, 1):
            product = Product(marketplace=TITLE, article=article)
            if await self._card(product):
                products.append(product)
            else:
                self.reporter.warn(f"{TITLE}: товар {article} не найден")
            self.reporter.progress(index, len(articles), f"{TITLE}: карточки {index} из {len(articles)}")
            await self.pause()
        return products

    async def enrich(self, products: list[Product], field_keys: set[str]) -> None:
        """Search results lack the product rating, brand, category and price tiers: open the cards if wanted."""
        if not field_keys & DETAIL_FIELDS:
            return
        pending = [p for p in products if not p.supplier_type]
        for index, product in enumerate(pending, 1):
            await self._card(product)
            self.reporter.progress(index, len(pending), f"{TITLE}: карточки {index} из {len(pending)}")
            await self.pause()

    # --- reviews ---

    async def reviews(self, product: Product, limit: int) -> list[Review]:
        """Product reviews in the site's "Наиболее актуальные" order, translated into Russian by the site."""
        if not product.reviews_key:  # the company id is required; only the card has it for a bare article
            await self._card(product)
        if not product.reviews_key:
            return []
        reviews: list[Review] = []
        seen: set[str] = set()
        page_number = 1
        while len(reviews) < limit:
            answer = await self._mtop(REVIEWS_API, {
                "clusterId": 0, "currentPage": page_number, "pageSize": REVIEWS_PAGE_SIZE, "language": LANGUAGE,
                "currency": "USD", "countryCode": "RU", "companyId": int(product.reviews_key),
                "productId": int(product.article)})
            items = [i for i in review_items(answer) if str(i.get("reviewId")) not in seen]
            if not items:
                break
            for item in items[: limit - len(reviews)]:
                seen.add(str(item.get("reviewId")))
                reviews.append(parse_review(item, product))
            if len(items) < REVIEWS_PAGE_SIZE:
                break
            page_number += 1
            await self.pause()
        return reviews


MARKETPLACE = Marketplace(
    key=KEY,
    title=TITLE,
    short_title="Alibaba",
    site="alibaba.com · опт из Китая",
    color=("#FF6A00", "#FF8533"),
    excel_color="FF6A00",
    parser=AlibabaParser,
    ids_placeholder="1601444932750\nhttps://www.alibaba.com/product-detail/…_1601444932750.html\n"
                    "ссылка на выдачу с фильтрами",
    item_patterns=(ITEM_LINK,),
    listing_pattern=LISTING_LINK,
    product_fields=(
        Field("price_usd", "Цена от, $", "usd", width=11, only=(KEY,)),
        Field("price_usd_max", "Цена до, $", "usd", width=11, default=False, only=(KEY,)),
        Field("min_order", "Мин. заказ", "int", width=11, only=(KEY,)),
        Field("price_tiers", "Оптовые цены", "wrap", width=36, default=False, only=(KEY,)),
        Field("sold", "Продано", "int", width=11, default=False, only=(KEY,)),
        Field("supplier_type", "Тип поставщика", width=22, only=(KEY,)),
        Field("supplier_years", "Лет на Alibaba", "int", width=10, default=False, only=(KEY,)),
        Field("origin", "Откуда", width=20, default=False, only=(KEY,)),
    ),
    empty_fields=("price_old", "discount", "pros", "cons"),  # wholesale prices have no "before discount"
    # A switch and a menu per line: two switches side by side do not fit into a half-width filter card.
    options=(
        Option(key="china_only", title="Только Китай", summary_text="поставщики из Китая", own_row=False,
               query_only=True),
        Option(
            key="store_rating",
            title="Рейтинг магазина",
            kind="choice",
            default=0,
            choices=((0, "Любой"), (40, "От 4,0"), (45, "От 4,5")),
            summary_format="рейтинг магазина {value}",
            own_row=False,
            query_only=True,
            cli_metavar="40|45",
        ),
        Option(key="verified", title="Проверенные", summary_text="проверенные поставщики", own_row=False,
               query_only=True),
        Option(
            key="max_moq",
            title="Мин. заказ",
            kind="choice",
            default=0,
            choices=((0, "Любой"), (10, "До 10 шт."), (50, "До 50 шт."), (100, "До 100 шт."), (500, "До 500 шт.")),
            summary_format="мин. заказ {value}",
            own_row=False,
            query_only=True,
            cli_metavar="ШТ",
        ),
        Option(key="no_ads", title="Без рекламы", summary_text="без рекламы", own_row=False),
    ),
    order=40,
    filters_label="Фильтры Alibaba.com",
    notes="Цены — в долларах и в рублях по курсу ЦБ. Названия и отзывы переводит на русский сам сайт.",
)
