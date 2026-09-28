"""Article lists and display-string parsing."""

from __future__ import annotations

from datetime import datetime

import pytest

from mpparser.inputs import collect_ids, parse_ids
from mpparser.textutils import parse_count, parse_float, parse_int, parse_ru_date


def test_links_go_to_their_marketplace_regardless_of_the_box():
    parsed = parse_ids(
        "https://www.ozon.ru/product/naushniki-5413455528/?at=abc\n"
        "https://www.wildberries.ru/catalog/145726284/detail.aspx?size=1",
        "wb",
    )
    assert parsed.for_marketplace("wb") == ["145726284"]
    assert parsed.for_marketplace("ozon") == ["5413455528"]


def test_bare_numbers_belong_to_the_selected_marketplace():
    assert parse_ids("145726284, 839226871", "wb").for_marketplace("wb") == ["145726284", "839226871"]
    assert parse_ids("145726284 839226871", "ozon").for_marketplace("ozon") == ["145726284", "839226871"]


def test_duplicates_are_removed_and_order_kept():
    parsed = parse_ids("111111\n222222\n111111\nhttps://www.wildberries.ru/catalog/222222/detail.aspx", "wb")
    assert parsed.for_marketplace("wb") == ["111111", "222222"]


def test_unrecognised_tokens_are_reported():
    parsed = parse_ids("наушники, 12, https://example.com/product/123", "wb")
    assert parsed.for_marketplace("wb") == []
    assert parsed.invalid == ["наушники", "12", "https://example.com/product/123"]


def test_old_ozon_link_format():
    parsed = parse_ids("https://www.ozon.ru/context/detail/id/5413455528/", "wb")
    assert parsed.for_marketplace("ozon") == ["5413455528"]


def test_yandex_market_links():
    parsed = parse_ids(
        "https://market.yandex.ru/card/sakharnaya-kartinka/4485111241?do-waremd5=abc&cpc=xyz\n"
        "https://market.yandex.ru/product--naushniki/1250834536?sku=227857122946363392&cpa=1",
        "wb",
    )
    assert parsed.for_marketplace("ym") == ["4485111241", "227857122946363392"]
    assert parsed.for_marketplace("wb") == []


def test_collect_ids_merges_both_boxes():
    merged = collect_ids({"wb": "145726284", "ozon": "5413455528, 145726284"})
    assert merged.for_marketplace("wb") == ["145726284"]
    assert merged.for_marketplace("ozon") == ["5413455528", "145726284"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3 436 ₽", 3436.0), ("3 436 ₽", 3436.0), ("1 234,5", 1234.5), ("−42%", 42.0), ("", None), (None, None)],
)
def test_parse_float(text, expected):
    assert parse_float(text) == expected


def test_parse_int_truncates():
    assert parse_int("11 824 отзыва") == 11824


@pytest.mark.parametrize(("text", "expected"), [("97 K", 97000), ("1,2 тыс.", 1200), ("340", 340)])
def test_parse_count_expands_abbreviations(text, expected):
    assert parse_count(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2 июля", datetime(2026, 7, 2)),
        ("27 сентября", datetime(2026, 9, 27)),
        ("12 декабря", datetime(2025, 12, 12)),  # would be in the future → last year
        ("12 декабря 2024", datetime(2024, 12, 12)),
        ("· 17 сентября в 12:21", datetime(2026, 9, 17, 12, 21)),
        ("16 сентября · Клиент", datetime(2026, 9, 16)),
        ("вчера", datetime(2026, 9, 26)),
        ("сегодня в 10:15", datetime(2026, 9, 27, 10, 15)),
        ("3 часа назад", datetime(2026, 9, 27, 11, 30)),
        ("25 минут назад", datetime(2026, 9, 27, 14, 5)),
        ("2 дня назад", datetime(2026, 9, 25, 14, 30)),
        ("неделю назад", datetime(2026, 9, 20, 14, 30)),
        ("31 июня", None),
        ("недавно", None),
        ("", None),
    ],
)
def test_parse_ru_date(text, expected):
    assert parse_ru_date(text, now=datetime(2026, 9, 27, 14, 30, 45)) == expected


def test_yandex_market_listing_links():
    parsed = parse_ids("https://market.yandex.ru/search?text=чайник&pricefrom=2000\n"
                       "https://market.yandex.ru/catalog--elektrochainiki/54956/list?hid=90586", "ym")
    assert parsed.for_marketplace("ym") == []
    assert len(parsed.listings_for("ym")) == 2


def test_collect_ids_merges_listings():
    merged = collect_ids({"ym": "https://market.yandex.ru/search?text=a https://market.yandex.ru/catalog--x/1"})
    assert merged.listings == {"ym": ["https://market.yandex.ru/search?text=a",
                                      "https://market.yandex.ru/catalog--x/1"]}
