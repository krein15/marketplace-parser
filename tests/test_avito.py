"""Avito page data → Product / Review."""

from __future__ import annotations

from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from mpparser.marketplaces.avito import parse_item_page, parse_review, parse_search_item, search_url
from mpparser.models import Product
from mpparser.settings import ParseSettings, SellerType, SortOrder


def test_parse_search_item(avito_search):
    product = parse_search_item(avito_search["items"][0], position=1)
    assert product is not None
    assert product.marketplace == "Авито"
    assert product.article.isdigit()
    assert product.name
    assert product.price and product.price > 0
    assert product.url.startswith("https://www.avito.ru/")
    assert product.url.endswith(product.article)
    assert "?" not in product.url
    assert product.region  # "Самарская обл., Самара"
    assert product.seller == "Продавец 1"
    assert 0 < product.seller_rating <= 5
    assert product.seller_reviews > 0
    assert product.reviews_key.startswith("https://www.avito.ru/brands/")
    assert product.position == 1


def test_price_from_text_when_there_is_no_meta():
    raw = {"id": "123456789", "href": "/moskva/tovary/tort_123456789?context=x", "price": "",
           "price_text": "от 1 800 ₽ за кг", "date": "3 часа назад"}
    product = parse_search_item(raw, region="Москва")
    assert product.price == 1800
    assert product.region == "Москва"  # no location on the card: the searched city is used
    assert product.published is not None
    assert product.url == "https://www.avito.ru/moskva/tovary/tort_123456789"


def test_search_item_without_id_is_skipped():
    assert parse_search_item({"id": "", "href": ""}) is None


def test_parse_item_page(avito_item):
    product = Product(marketplace="Авито", article="", url="https://www.avito.ru/x")
    assert parse_item_page(avito_item, product)
    assert product.article == "2324430574"
    assert product.name == "Съедобная печать"
    assert product.price == 150
    assert product.published == datetime(product.published.year, 9, 17, 12, 21)
    assert product.views == 7109
    assert product.address.startswith("Самарская обл., Самара")
    assert product.seller == "Продавец 1"
    assert product.seller_type == "Частный исполнитель"
    assert product.seller_rating == 5.0
    assert product.seller_reviews == 189
    assert product.url == "https://www.avito.ru/samara/predlozheniya_uslug/sedobnaya_pechat_2324430574"


def test_company_seller_without_reviews():
    raw = {"id": "№ 555555555", "title": "Торт", "price": "2500", "seller": "ООО Торты",
           "seller_info": "ООО Торты\nКомпания\nНа Авито c 2020 года"}
    product = Product(marketplace="Авито", article="")
    assert parse_item_page(raw, product)
    assert (product.price, product.seller_type, product.seller_reviews) == (2500, "Компания", 0)


def test_missing_item_page_is_detected():
    assert not parse_item_page({"id": "", "title": ""}, Product(marketplace="Авито", article="1"))


def test_parse_seller_reviews(avito_reviews):
    product = Product(marketplace="Авито", article="2324430574", seller="Продавец 1")
    review = parse_review(avito_reviews[0], product)
    assert review.marketplace == "Авито"
    assert review.article == "2324430574"
    assert review.seller == "Продавец 1"
    assert review.product_name == "Съедобная печать"
    assert review.rating == 5
    assert isinstance(review.date, datetime) and review.date.month == 9
    assert review.author == "Покупатель 1"
    assert review.text and review.seller_answer
    assert review.seller_answer not in review.text  # the seller's answer is a separate column
    assert review.photos == 2


def test_failed_deal_is_marked_in_the_text():
    raw = {"author": "Анна", "subtitle": "2 июля · Покупатель", "stage": "Сделка сорвалась · Торт",
           "score": "1", "text": "Не пришла"}
    assert parse_review(raw, Product(marketplace="Авито", article="1")).text == "(Сделка сорвалась) Не пришла"


def test_search_url_with_filters():
    settings = ParseSettings(price_min=200, price_max=900, avito_seller=SellerType.PRIVATE, avito_delivery=True,
                             avito_title_only=True)
    url = search_url("ekaterinburg", "сахарная картинка", SortOrder.PRICE_ASC, settings)
    parts = urlsplit(url)
    assert parts.path == "/ekaterinburg"
    assert parse_qs(parts.query) == {"q": ["сахарная картинка"], "pmin": ["200"], "pmax": ["900"], "s": ["1"],
                                     "user": ["1"], "d": ["1"], "bt": ["1"]}


def test_search_url_without_filters():
    assert search_url("all", "торт", SortOrder.POPULAR, ParseSettings()) == "https://www.avito.ru/all?q=%D1%82%D0%BE%D1%80%D1%82"
