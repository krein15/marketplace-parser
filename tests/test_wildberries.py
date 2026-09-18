"""Wildberries JSON → Product / Review."""

from __future__ import annotations

from datetime import datetime

import pytest

from mpparser.marketplaces.wildberries import MediaHosts, parse_feedback, parse_product
from mpparser.models import Product

UPSTREAMS = {
    "origin": {
        "mediabasket_route_map": [
            {"method": "range", "hosts": [
                {"vol_range_from": 0, "vol_range_to": 143, "host": "basket-01.wbbasket.ru"},
                {"vol_range_from": 1314, "vol_range_to": 1601, "host": "basket-10.wbbasket.ru"},
            ]},
        ]
    }
}


def test_parse_search_product(wb_search):
    product = parse_product(wb_search["products"][0], position=1)
    assert product.marketplace == "Wildberries"
    assert product.article.isdigit()
    assert product.name
    assert product.price and product.price > 0
    assert product.url.endswith(f"/catalog/{product.article}/detail.aspx")
    assert product.reviews_key.isdigit()
    assert product.position == 1
    assert product.stock is None  # WB reports a placeholder quantity for anonymous visitors
    if product.price_old:
        assert product.price_old > product.price
        assert product.discount == round((1 - product.price / product.price_old) * 100)


def test_parse_detail_product(wb_detail):
    product = parse_product(wb_detail["products"][0])
    assert product.name and product.price
    assert product.position is None


def test_prices_are_converted_from_kopecks():
    product = parse_product({"id": 1, "sizes": [{"price": {"product": 55600, "basic": 80300}}]})
    assert (product.price, product.price_old, product.discount) == (556.0, 803.0, 31)


def test_price_is_taken_from_the_first_size_in_stock():
    product = parse_product({"id": 1, "sizes": [{}, {"price": {"product": 10000, "basic": 10000}}]})
    assert product.price == 100.0
    assert product.price_old is None  # no discount, so the crossed-out price is not exported


def test_legacy_card_format_is_supported():
    product = parse_product({"id": 1, "salePriceU": 12300, "priceU": 45600, "nmFeedbacks": 7})
    assert (product.price, product.price_old, product.reviews_count) == (123.0, 456.0, 7)


def test_parse_feedbacks(wb_feedbacks):
    product = Product(marketplace="Wildberries", article=str(wb_feedbacks["feedbacks"][0]["nmId"]), name="Товар")
    reviews = [parse_feedback(item, product) for item in wb_feedbacks["feedbacks"]]
    assert all(r.marketplace == "Wildberries" and r.article == product.article for r in reviews)
    assert all(isinstance(r.date, datetime) for r in reviews)
    assert all(r.rating in range(1, 6) for r in reviews)
    assert all(r.product_name == "Товар" for r in reviews)


def test_feedback_of_another_variant_is_marked():
    product = Product(marketplace="Wildberries", article="111")
    review = parse_feedback({"nmId": 222, "color": "белый", "productValuation": 5}, product)
    assert review.variant == "белый, арт. 222"
    assert review.article == "111"


def test_feedback_seller_answer_and_photos():
    review = parse_feedback(
        {"nmId": 1, "text": "ok", "answer": {"text": "Спасибо за отзыв!"}, "photos": [{}, {}],
         "votes": {"pluses": 3}},
        Product(marketplace="Wildberries", article="1"),
    )
    assert review.seller_answer == "Спасибо за отзыв!"
    assert (review.photos, review.likes) == (2, 3)


@pytest.mark.parametrize(
    ("article", "expected"),
    [
        ("150017236", "https://basket-10.wbbasket.ru/vol1500/part150017/150017236"),
        ("1234567", "https://basket-01.wbbasket.ru/vol12/part1234/1234567"),
    ],
)
def test_media_host_ranges(article, expected):
    assert MediaHosts(UPSTREAMS).base_url(article) == expected


def test_media_host_is_unknown_for_a_new_range():
    assert MediaHosts(UPSTREAMS).base_url("1470151551") is None
    assert MediaHosts(None).base_url("150017236") is None
