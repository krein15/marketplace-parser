"""Article lists and display-string parsing."""

from __future__ import annotations

import pytest

from mpparser.inputs import collect_ids, parse_ids
from mpparser.textutils import parse_count, parse_float, parse_int


def test_links_go_to_their_marketplace_regardless_of_the_box():
    parsed = parse_ids(
        "https://www.ozon.ru/product/naushniki-5413455528/?at=abc\n"
        "https://www.wildberries.ru/catalog/145726284/detail.aspx?size=1",
        "wb",
    )
    assert parsed.wb == ["145726284"]
    assert parsed.ozon == ["5413455528"]


def test_bare_numbers_belong_to_the_selected_marketplace():
    assert parse_ids("145726284, 839226871", "wb").wb == ["145726284", "839226871"]
    assert parse_ids("145726284 839226871", "ozon").ozon == ["145726284", "839226871"]


def test_duplicates_are_removed_and_order_kept():
    parsed = parse_ids("111111\n222222\n111111\nhttps://www.wildberries.ru/catalog/222222/detail.aspx", "wb")
    assert parsed.wb == ["111111", "222222"]


def test_unrecognised_tokens_are_reported():
    parsed = parse_ids("наушники, 12, https://example.com/product/123", "wb")
    assert parsed.wb == []
    assert parsed.invalid == ["наушники", "12", "https://example.com/product/123"]


def test_old_ozon_link_format():
    assert parse_ids("https://www.ozon.ru/context/detail/id/5413455528/", "wb").ozon == ["5413455528"]


def test_collect_ids_merges_both_boxes():
    merged = collect_ids({"wb": "145726284", "ozon": "5413455528, 145726284"})
    assert merged.wb == ["145726284"]
    assert merged.ozon == ["5413455528", "145726284"]


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
