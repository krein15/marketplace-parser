"""Ozon widget JSON → Product / Review."""

from __future__ import annotations

from datetime import datetime

from mpparser.marketplaces.ozon import (
    next_search_path,
    parse_product_page,
    parse_review,
    parse_tile,
    widget,
    widgets,
)
from mpparser.models import Product


def first_tile(page: dict) -> dict:
    return widgets(page, "tileGridDesktop")[0]["items"][0]


def test_parse_search_tile(ozon_search):
    product = parse_tile(first_tile(ozon_search), position=1)
    assert product.marketplace == "Ozon"
    assert product.article.isdigit()
    assert product.name
    assert product.price and product.price > 0
    assert product.rating is None or 0 < product.rating <= 5
    assert product.url.startswith("https://www.ozon.ru/product/")
    assert "?" not in product.url  # tracking parameters are stripped
    assert product.image.startswith("https://")
    assert product.position == 1


def test_tile_prices_and_discount():
    tile = {
        "sku": 123,
        "mainState": [
            {"type": "priceV2", "priceV2": {
                "price": [{"text": "3 436 ₽", "textStyle": "PRICE"},
                          {"text": "5 999 ₽", "textStyle": "ORIGINAL_PRICE"}],
                "discount": "−42%"}},
            {"type": "textDS", "textDS": {"text": "287 шт осталось"}},
            {"type": "textDS", "id": "name", "textDS": {"text": "JBL Tune 720"}},
            {"type": "labelListV2", "labelListV2": {"items": [
                {"type": "icon", "icon": {"icon": {"icon": "ic_s_star_filled_compact"}}},
                {"type": "text", "text": {"text": "4.9"}},
                {"type": "icon", "icon": {"icon": {"icon": "ic_s_dialog_filled_compact"}}},
                {"type": "text", "text": {"text": "11 824"}},
            ]}},
        ],
        "action": {"link": "/product/jbl-tune-720-123/?at=abc"},
    }
    product = parse_tile(tile)
    assert (product.price, product.price_old, product.discount) == (3436.0, 5999.0, 42)
    assert (product.name, product.rating, product.reviews_count, product.stock) == ("JBL Tune 720", 4.9, 11824, 287)
    assert product.url == "https://www.ozon.ru/product/jbl-tune-720-123/"


def test_parse_product_page(ozon_product):
    product = Product(marketplace="Ozon", article=str(widget(ozon_product, "webReviewProductScore")["itemId"]))
    assert parse_product_page(ozon_product, product) is True
    assert product.name
    assert product.price and product.price > 0
    assert product.seller
    assert product.category
    assert product.rating and product.reviews_count is not None
    assert product.url.startswith("https://www.ozon.ru/product/")


def test_product_page_of_a_missing_article_is_detected():
    assert parse_product_page({"widgetStates": {}}, Product(marketplace="Ozon", article="1")) is False


def test_card_price_and_availability():
    page = {"widgetStates": {"webPrice-1-default-1": '{"isAvailable": false, "price": "1 688 ₽",'
                                                     ' "cardPrice": "1 519 ₽", "originalPrice": "1 688 ₽"}'}}
    product = Product(marketplace="Ozon", article="1")
    assert parse_product_page(page, product) is True
    assert (product.price, product.price_card, product.stock) == (1688.0, 1519.0, 0)
    assert product.price_old is None  # equal to the current price, so it is not a discount


def test_parse_reviews(ozon_reviews):
    state = widget(ozon_reviews, "webListReviews")
    product = Product(marketplace="Ozon", article=str(state["itemId"]), name="Товар")
    reviews = [parse_review(item, product, state.get("products") or {}) for item in state["reviews"]]
    assert reviews
    assert all(isinstance(r.date, datetime) for r in reviews)
    assert all(r.marketplace == "Ozon" and r.article == product.article for r in reviews)
    assert all(r.rating in range(1, 6) for r in reviews)
    assert any(r.text for r in reviews)


def test_review_of_another_variant_is_marked():
    review = parse_review(
        {"itemId": "222", "publishedAt": 1788678048, "content": {"comment": "ok", "score": 5},
         "author": {"firstName": "Иван"}},
        Product(marketplace="Ozon", article="111"),
        {"222": {"variants": [{"name": "Цвет товара", "value": "белый"}]}},
    )
    assert review.variant == "белый"
    assert (review.author, review.rating, review.article) == ("Иван", 5, "111")


def test_pagination_path(ozon_search):
    assert next_search_path(ozon_search)


def test_pagination_stops_without_a_paginator():
    assert next_search_path({"widgetStates": {}}) is None
