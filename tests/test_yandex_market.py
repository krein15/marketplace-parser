"""Yandex Market pages → Product / Review."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from mpparser.marketplaces.yandex_market import (
    card_url,
    collections,
    parse_card,
    parse_review,
    parse_snippet,
    review_items,
    search_url,
)
from mpparser.models import Product
from mpparser.settings import ParseSettings, SortOrder


def snippet(zone: dict, rating: str = "", href: str = "/card/tort/4485111241?cpc=abc&do-waremd5=x") -> dict:
    return {"zone": json.dumps(zone), "href": href, "title": " Сахарная картинка ", "rating": rating, "image": ""}


def test_parse_search_snippet(ym_snippets):
    product = parse_snippet(ym_snippets[0], position=1)
    assert product is not None
    assert product.marketplace == "Яндекс Маркет"
    assert product.article.isdigit()
    assert product.name
    assert product.price and product.price > 0
    assert product.price_card is None or product.price_card < product.price
    assert product.url.startswith("https://market.yandex.ru/card/")
    assert product.url.endswith(product.article)  # tracking parameters are stripped
    assert product.image.startswith("https://")
    assert product.position == 1


def test_article_is_the_offer_sku_from_the_link():
    """The card is addressed by ``oskuId``; ``marketSku`` of the same snippet may be a different number."""
    zone = {"marketSku": "5931006884", "oskuId": "5489401925", "price": 3500}
    product = parse_snippet(snippet(zone, href="/card/naushniki/5489401925?cpc=1"))
    assert product.article == "5489401925"
    assert parse_snippet(snippet(zone, href="")).article == "5489401925"


def test_three_prices_of_a_snippet():
    zone = {"oskuId": "4485111241", "price": 850, "additionalPrices": [
        {"priceType": "yaBank", "priceValue": "382"}, {"priceType": "withDiscount", "priceValue": "502"}]}
    product = parse_snippet(snippet(zone, rating="Рейтинг товара: 5.0 из 5Оценок: (1 020) · 75 купили"))
    assert (product.price, product.price_card, product.price_old, product.discount) == (502, 382, 850, 41)
    assert product.name == "Сахарная картинка"
    assert product.rating == 5.0
    assert product.reviews_count == 1020
    assert product.url == "https://market.yandex.ru/card/tort/4485111241"


def test_snippet_without_discount_or_rating():
    product = parse_snippet(snippet({"oskuId": "4485111241", "price": 502}))
    assert (product.price, product.price_card, product.price_old, product.discount) == (502, None, None, None)
    assert product.rating is None
    assert product.reviews_count == 0  # nobody has rated it yet: the runner skips its reviews


def test_snippet_without_sku_is_skipped():
    assert parse_snippet({"zone": "{}", "href": "", "title": "Реклама"}) is None


def test_parse_card(ym_card):
    product = Product(marketplace="Яндекс Маркет", article="0")
    assert parse_card(ym_card, product)
    assert product.name
    assert product.seller
    assert product.price and product.price > 0
    assert product.price_card is None or product.price_card < product.price
    assert product.price_old is None or product.price_old > product.price
    assert 0 < product.rating <= 5
    assert product.rating == round(product.rating, 2)
    assert product.reviews_count > 0
    assert product.category and "glfilter" not in product.category
    assert product.image.startswith("https://")
    assert product.url.startswith("https://market.yandex.ru/card/")


def test_card_prices_with_yandex_pay():
    patch = {"collections": {
        "productCardMeta": {"1": {"title": "Картинка", "vendorName": "Без бренда", "rating": 4.599999904632568,
                                  "ratingCount": 20}},
        "price": {"1": {"mainPrice": {"price": {"value": 382}, "type": "extraDiscount", "subtype": "ya-card"},
                        "oldPrices": [{"price": {"value": 502}, "type": "regular"},
                                      {"price": {"value": 850}, "type": "withoutDiscount"}]}},
        "shopInfo": {"1": {"name": "PRINT CAKE", "rating": 4.8}},
    }}
    page = f'<noframes data-apiary="patch">{json.dumps(patch, ensure_ascii=False)}</noframes>'
    product = Product(marketplace="Яндекс Маркет", article="4485111241")
    assert parse_card(page, product)
    assert (product.price, product.price_card, product.price_old, product.discount) == (502, 382, 850, 41)
    assert product.brand == ""  # "Без бренда" is not a brand
    assert (product.seller, product.seller_rating) == ("PRINT CAKE", 4.8)
    assert (product.rating, product.reviews_count) == (4.6, 20)


def test_category_without_brand_crumb():
    crumbs = {"@type": "BreadcrumbList", "itemListElement": [
        {"item": {"@id": "https://market.yandex.ru/category/audiotekhnika", "name": "Наушники и аудиотехника"}},
        {"item": {"@id": "https://market.yandex.ru/catalog--jbl/26992150/list?hid=90555", "name": "Наушники JBL"}},
    ]}
    meta = {"collections": {"productCardMeta": {"1": {"title": "JBL Tune 520BT", "vendorName": "JBL"}}}}
    page = (f'<script type="application/ld+json">{json.dumps(crumbs, ensure_ascii=False)}</script>'
            f'<noframes data-apiary="patch">{json.dumps(meta, ensure_ascii=False)}</noframes>')
    product = Product(marketplace="Яндекс Маркет", article="1")
    assert parse_card(page, product)
    assert (product.brand, product.category) == ("JBL", "Наушники")


def test_missing_card_is_detected():
    assert not parse_card("<html><title>Нет такой страницы</title></html>", Product("Яндекс Маркет", "1"))


def test_parse_reviews(ym_reviews):
    items = review_items(ym_reviews)
    assert items
    product = Product(marketplace="Яндекс Маркет", article="0", name="Наушники")
    review = parse_review(items[0], product)
    assert review.marketplace == "Яндекс Маркет"
    assert review.product_name == "Наушники"
    assert 1 <= review.rating <= 5
    assert isinstance(review.date, datetime)
    assert review.author
    assert review.text or review.pros or review.cons
    assert review.likes is not None


def test_review_of_another_variant_names_it():
    item = {"id": 1, "rating": 4, "descriptor": [{"type": "text", "content": "2 июля 2025"}],
            "analyticsData": {"oskuId": "222"}, "author": {"nickname": "Анна"}, "comment": "Хорошо",
            "liteOffer": {"jumpTableParams": [{"name": "Цвет товара", "value": "белый"}]}}
    product = Product(marketplace="Яндекс Маркет", article="111")
    assert parse_review(item, product).variant == "Цвет товара: белый"
    assert parse_review(item, Product(marketplace="Яндекс Маркет", article="222")).variant == ""
    assert parse_review(item, product).date == datetime(2025, 7, 2)


def test_collections_merge_all_patches():
    first = '<noframes data-apiary="patch">{"collections": {"reviewV2": {"1": {"id": 1}}}}</noframes>'
    second = '<noframes data-apiary="patch">{"collections": {"reviewV2": {"2": {"id": 2}}}}</noframes>'
    assert list(collections(first + "<div></div>" + second)["reviewV2"]) == ["1", "2"]


def test_card_url_strips_tracking():
    assert card_url("/card/tort/123?cpc=x&amp;cpa=1") == "https://market.yandex.ru/card/tort/123"


def test_filters_become_url_parameters():
    settings = ParseSettings(price_min=2000, price_max=3000, ym_rating_4=True, ym_delivery_days=3)
    parts = urlsplit(search_url("чайник", SortOrder.PRICE_ASC, settings))
    assert parts.path == "/search"
    assert parse_qs(parts.query) == {"text": ["чайник"], "how": ["aprice"], "pricefrom": ["2000"],
                                     "priceto": ["3000"], "qrfrom": ["1"], "delivery-interval": ["3"]}
