"""Alibaba.com answers → Product / Review, and what the program sends to the site."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from mpparser.currency import Rate
from mpparser.fields import product_fields, resolve, review_fields
from mpparser.inputs import parse_ids
from mpparser.marketplaces.alibaba import (
    APP_KEY,
    detail_data,
    filter_params,
    format_tiers,
    full_image,
    listing_params,
    mtop_url,
    parse_detail,
    parse_offer,
    parse_price_range,
    parse_review,
    review_items,
    search_offers,
    strip_tags,
    supplier_type,
)
from mpparser.models import Product
from mpparser.runner import SHARED_DETAILS
from mpparser.settings import ParseSettings

RATE = Rate(code="USD", value=80.0, day=date(2026, 11, 6))


# --- search ---


def test_parse_search_offer(alibaba_search):
    offers, pages = search_offers(alibaba_search)
    assert offers and pages > 1
    product = parse_offer(offers[0], RATE, position=1)
    assert product is not None
    assert product.marketplace == "Alibaba.com"
    assert product.article.isdigit()
    assert product.name and "<" not in product.name  # icons and highlighting are stripped
    assert product.price_usd and product.price_usd <= product.price_usd_max
    assert product.price == pytest.approx(product.price_usd * 80, abs=0.01)  # roubles at the rate of the day
    assert product.min_order and product.min_order > 0
    assert product.seller
    assert 0 < product.seller_rating <= 5
    assert product.supplier_years and product.supplier_years > 0
    assert product.origin == "Китай"
    assert product.url.startswith("https://www.alibaba.com/product-detail/")
    assert "?" not in product.url  # tracking parameters are stripped
    assert product.image.startswith("https://") and "_300x300" not in product.image
    assert product.reviews_key.isdigit()  # the company id: reviews are asked for with it
    assert product.position == 1


def test_offer_values():
    offer = {"productId": "1601444932750", "title": "<img src='x.png'></img><span> </span>Термос &amp; кружка",
             "price": "1,35-4,04\xa0$", "moq": "Мин. заказ: 1\xa0000 шт.", "soldOrder": "Продано 7\xa0610 шт.",
             "goldSupplierYears": "10 лет", "countryCode": "VN", "companyId": "239794681",
             "productUrl": "//www.alibaba.com/product-detail/Cup_1601444932750.html?priceId=abc"}
    product = parse_offer(offer, RATE)
    assert product.name == "Термос & кружка"
    assert (product.price_usd, product.price_usd_max, product.price) == (1.35, 4.04, 108.0)
    assert (product.min_order, product.sold, product.supplier_years) == (1000, 7610, 10)
    assert product.origin == "Вьетнам"
    assert product.url == "https://www.alibaba.com/product-detail/Cup_1601444932750.html"
    assert product.rating is None and product.reviews_count is None  # the store's rating is not the product's


def test_offer_without_id_is_skipped():
    assert parse_offer({"title": "Реклама"}, RATE) is None


@pytest.mark.parametrize(("text", "expected"), [
    ("3,15 $", (3.15, 3.15)),
    ("1,35-4,04\xa0$", (1.35, 4.04)),
    ("0,2962-1,74 $", (0.2962, 1.74)),
    ("1\xa0234,50-2\xa0000 $", (1234.5, 2000.0)),
    ("$1.99-3.49", (1.99, 3.49)),
    ("US$1,234.50", (1234.5, 1234.5)),
    ("", (None, None)),
    ("Цена по запросу", (None, None)),
])
def test_parse_price_range(text, expected):
    assert parse_price_range(text) == expected


def test_strip_tags_and_images():
    assert strip_tags("<span>Термо</span>  кружка ") == "Термо кружка"
    assert full_image("//s.alicdn.com/kf/H48.jpg_300x300.jpg") == "https://s.alicdn.com/kf/H48.jpg"
    assert full_image("") == ""


def test_filter_params_convert_roubles_to_dollars():
    settings = ParseSettings(marketplaces=["alibaba"], query="кружка", price_min=800, price_max=4000)
    settings.set_option("alibaba", "china_only", True)
    settings.set_option("alibaba", "verified", True)
    settings.set_option("alibaba", "store_rating", 45)
    settings.set_option("alibaba", "max_moq", 100)
    assert filter_params(settings, RATE) == {"pricef": "10.00", "pricet": "50.00", "country": "CN",
                                             "assessmentCompany": "true", "reviewScore": "4.5~", "moqt": "100"}
    settings.set_option("alibaba", "store_rating", 40)
    assert filter_params(settings, RATE)["reviewScore"] == "4~"
    assert filter_params(ParseSettings(marketplaces=["alibaba"], query="кружка"), RATE) == {}


def test_listing_params_keep_the_site_filters():
    params = listing_params("https://www.alibaba.com/trade/search?spm=a2700&SearchText=thermos+cup&pricef=2&pricet=5"
                            "&country=CN&tab=all&page=3")
    assert params == {"query": "thermos cup", "from": "tradeSearch", "pricef": "2", "pricet": "5", "country": "CN"}
    assert listing_params("alibaba.com/showroom/stainless-steel-cup.html")["query"] == "stainless steel cup"
    assert listing_params("https://www.alibaba.com/trade/search?tab=all") == {}


def test_links_are_recognised():
    parsed = parse_ids("https://www.alibaba.com/product-detail/Stainless-Steel-Cup_1601444932750.html?spm=x\n"
                       "https://www.alibaba.com/trade/search?SearchText=cup&country=CN", "wb")
    assert parsed.for_marketplace("alibaba") == ["1601444932750"]
    assert parsed.listings_for("alibaba") == ["https://www.alibaba.com/trade/search?SearchText=cup&country=CN"]
    assert not parsed.invalid


# --- product card ---


def test_parse_detail(alibaba_detail):
    data = detail_data(alibaba_detail)
    assert data is not None
    product = Product(marketplace="Alibaba.com", article="0")
    assert parse_detail(data, product, RATE)
    assert product.name
    assert product.price_usd and product.price_usd < product.price_usd_max  # the bigger the order, the cheaper
    assert product.price == pytest.approx(product.price_usd * 80, abs=0.01)
    assert product.price_tiers.count("$") >= 2 and "шт." in product.price_tiers
    assert product.min_order and product.min_order > 0
    assert 0 < product.rating <= 5
    assert product.reviews_count > 0
    assert product.seller and product.supplier_type
    assert product.category
    assert product.origin  # "Zhejiang, China"
    assert product.reviews_key.isdigit()
    assert product.url.endswith("_0.html")


def test_detail_without_product_reviews():
    data = {"globalData": {"product": {"subject": "Кружка", "moq": 50, "price": {"productLadderPrices": [
        {"dollarPrice": 7.45, "localMin": "50", "localMax": "999"},
        {"dollarPrice": 6.25, "localMin": "1 000", "localMax": "-1"}], "unit": "шт."},
        "productBasicProperties": [{"attrNameId": 2, "attrValue": "OEM"},
                                   {"attrNameId": 1, "attrValue": "Shanghai, China"}]},
        "review": {"storeReview": {"averageStar": 4.7, "totalReviewCount": 42}},
        "seller": {"companyName": "Shanghai Spirit", "companyId": 200471005,
                   "companyBusinessType": "Manufacturer,Trading Company", "companyJoinYears": "16"}}}
    product = Product(marketplace="Alibaba.com", article="1601")
    assert parse_detail(data, product, RATE)
    assert (product.rating, product.reviews_count) == (None, 0)  # the runner skips its reviews
    assert product.seller_rating == 4.7
    assert product.brand == ""  # "OEM" means "your brand here", not a brand
    assert product.origin == "Shanghai, China"
    assert product.supplier_type == "Производитель, торговая компания"
    assert product.supplier_years == 16
    assert product.price_tiers == "50–999 шт. — 7,45 $; от 1 000 шт. — 6,25 $"
    assert (product.price_usd, product.price_usd_max, product.price) == (6.25, 7.45, 500.0)


def test_not_a_product_card():
    assert detail_data("<html><body>Страница не найдена</body></html>") is None
    assert detail_data("<script>window.detailData = {broken</script>") is None
    assert detail_data('<script>window.detailData = {"globalData": {}};</script>') is None


def test_format_tiers_and_supplier_type():
    assert format_tiers([{"dollarPrice": 1234.5, "localMin": "1", "localMax": "-1"}]) == "от 1 шт. — 1 234,50 $"
    assert format_tiers([]) == ""
    assert supplier_type("Trading Company") == "Торговая компания"
    assert supplier_type("Buying Office,Other") == "Закупочный офис, другое"
    assert supplier_type("") == ""


# --- reviews ---


def test_parse_review(alibaba_reviews):
    items = review_items(alibaba_reviews)
    assert items
    product = Product(marketplace="Alibaba.com", article="1601", name="Термос")
    review = parse_review(items[0], product)
    assert review.marketplace == "Alibaba.com"
    assert review.article == "1601"
    assert review.product_name == "Термос"
    assert isinstance(review.date, datetime)
    assert 1 <= review.rating <= 5
    assert review.text  # translated into Russian by the site
    assert "(" in review.author  # "A***s (Колумбия)"


def test_review_values():
    item = {"reviewTime": "2026/11/03", "latitudeScore": {"score": "4"}, "reviewContent": " Отлично ",
            "oriReviewContent": "Great", "simpleReviewUserVO": {"anonymousName": "j***n", "fullNameCountry": "США"},
            "reviewImages": ["a.jpg", "b.jpg"]}
    review = parse_review(item, Product(marketplace="Alibaba.com", article="1"))
    assert (review.date, review.rating, review.text, review.author, review.photos) == (
        datetime(2026, 11, 3), 4, "Отлично", "j***n (США)", 2)
    assert parse_review({"oriReviewContent": "Great"}, Product(marketplace="Alibaba.com", article="1")).text == "Great"


def test_mtop_request_is_signed_like_the_site_does():
    url = mtop_url("mtop.test.api", '{"productId":1}', "abc123", "1793900996758")
    params = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
    expected = hashlib.md5(f'abc123&1793900996758&{APP_KEY}&{{"productId":1}}'.encode()).hexdigest()
    assert params["sign"] == expected
    assert params["data"] == '{"productId":1}'
    assert url.startswith("https://acs.h.alibaba.com/h5/mtop.test.api/1.0/?")


# --- the report ---


def test_columns_alibaba_does_not_fill_are_dropped():
    keys = [f.key for f in product_fields()]
    alone = [f.key for f in resolve(product_fields(), keys, ["alibaba"])]
    assert "price_usd" in alone and "min_order" in alone
    assert "discount" not in alone and "price_old" not in alone
    together = [f.key for f in resolve(product_fields(), keys, ["alibaba", "wb"])]
    assert "discount" in together  # WB fills it
    assert "price_usd" not in [f.key for f in resolve(product_fields(), keys, ["wb"])]
    review_keys = [f.key for f in resolve(review_fields(), [f.key for f in review_fields()], ["alibaba"])]
    assert "text" in review_keys and "pros" not in review_keys


def test_twins_from_other_queries_share_the_card():
    assert {"min_order", "price_tiers", "supplier_type", "price_usd"} <= set(SHARED_DETAILS)
    assert not {"position", "query", "article"} & set(SHARED_DETAILS)


def test_report_in_dollars_and_roubles(tmp_path):
    from openpyxl import load_workbook

    from mpparser.export.excel import export_to_excel

    product = parse_offer({"productId": "1601", "title": "Термос", "price": "1,35-4,04 $",
                           "moq": "Мин. заказ: 50 шт.", "companyName": "Ningbo Cowa"}, RATE, position=1)
    product.supplier_type = "Производитель"
    settings = ParseSettings(marketplaces=["alibaba"], query="термос")
    path = tmp_path / "report.xlsx"
    export_to_excel(path, settings, [product], [], datetime(2026, 11, 6, 10, 0),
                    notes=[("Курс доллара (Alibaba.com)", RATE.describe())])
    workbook = load_workbook(path)
    summary = {row[0]: row[1] for row in workbook["Сводка"].iter_rows(values_only=True) if row[0]}
    assert summary["Курс доллара (Alibaba.com)"] == "80,00 ₽ (ЦБ РФ на 06.11.2026)"
    sheet = workbook["Товары"]
    header = [cell.value for cell in sheet[1]]
    assert "Скидка, %" not in header and "Цена до скидки, ₽" not in header
    row = dict(zip(header, sheet[2], strict=True))
    assert row["Цена, ₽"].value == 108.0
    assert row["Цена от, $"].value == 1.35 and row["Цена от, $"].number_format == '#,##0.00 "$"'
    assert row["Мин. заказ"].value == 50
    assert row["Тип поставщика"].value == "Производитель"
