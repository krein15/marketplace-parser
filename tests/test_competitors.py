"""My products against the search results: who is cheaper, by how much, and where I stand."""

from __future__ import annotations

import pytest

from mpparser.competitors import Own, compare_with_rivals, parse_own
from mpparser.models import Product

WB, OZON = "Wildberries", "Ozon"


def product(article: str, price: float | None, marketplace: str = WB, brand: str = "", seller: str = "",
            rating: float | None = None, reviews: int | None = None) -> Product:
    return Product(marketplace=marketplace, article=article, name=f"Товар {article}", price=price, brand=brand,
                   seller=seller, rating=rating, reviews_count=reviews,
                   url=f"https://example.com/{article}")


@pytest.fixture
def market() -> list[Product]:
    """Six products of one marketplace: mine is the third by price."""
    return [
        product("100", 1000, brand="Моя Марка", rating=4.5, reviews=120),
        product("1", 700, brand="Чужой", rating=4.9, reviews=900),
        product("2", 900, brand="Чужой", rating=4.0, reviews=300),
        product("3", 1200, brand="Третий", rating=4.8, reviews=50),
        product("4", 1500, brand="Третий", rating=3.9, reviews=10),
        product("5", None, brand="Без цены"),
    ]


def test_without_marks_nobody_is_mine(market):
    competition = compare_with_rivals(market, parse_own("", ""))
    assert not competition and competition.spots == []


def test_my_product_is_found_by_article_and_by_link():
    own = parse_own("100, https://www.wildberries.ru/catalog/55555/detail.aspx", "")
    assert own.articles == frozenset({"100", "55555"})
    assert own.matches(product("55555", 10)) and not own.matches(product("9", 10))


def test_my_product_is_found_by_brand_or_seller():
    own = parse_own("", "Моя Марка\nООО Ромашка, ")
    assert own.names == frozenset({"моя марка", "ооо ромашка"})
    assert own.matches(product("1", 10, brand="моя марка"))  # регистр не важен
    assert own.matches(product("2", 10, seller="ООО Ромашка"))
    assert not own.matches(product("3", 10, brand="Чужой", seller="Чужой"))


def test_a_product_without_a_brand_is_not_mine_by_an_empty_name():
    assert not Own(names=frozenset({""})).matches(product("1", 10))


def test_place_by_price_and_who_is_cheaper(market):
    spot = compare_with_rivals(market, parse_own("100", "")).spots[0]
    assert spot.rank == 3 and spot.total == 5  # пять товаров с ценой, мой третий
    assert [p.article for p in spot.cheaper] == ["1", "2"]
    assert spot.cheapest.article == "1"


def test_distance_to_the_cheapest_rival(market):
    spot = compare_with_rivals(market, parse_own("100", "")).spots[0]
    assert (spot.gap, spot.gap_pct) == (300, 42.9)


def test_middle_of_the_market_and_my_deviation(market):
    spot = compare_with_rivals(market, parse_own("100", "")).spots[0]
    assert spot.median == 1050  # 700, 900, 1200, 1500
    assert spot.vs_median == -4.8
    assert (spot.rating, spot.reviews) == (4.4, 175)


def test_rivals_of_another_marketplace_do_not_count(market):
    market.append(product("200", 10, marketplace=OZON, brand="Моя Марка"))
    competition = compare_with_rivals(market, parse_own("", "Моя Марка"))
    spots = {spot.product.marketplace: spot for spot in competition.spots}
    assert spots[OZON].rivals == [] and spots[OZON].rank == 1  # на Ozon мой товар один
    assert len(spots[WB].rivals) == 4


def test_my_other_products_are_not_rivals(market):
    market.append(product("101", 500, brand="Моя Марка"))
    spots = compare_with_rivals(market, parse_own("", "моя марка")).spots
    assert len(spots) == 2
    mine = next(s for s in spots if s.product.article == "100")
    assert [p.article for p in mine.cheaper] == ["1", "2"]  # 101 — тоже мой, он не конкурент


def test_a_product_without_a_price_has_no_place(market):
    spot = compare_with_rivals(market, parse_own("5", "")).spots[0]
    assert spot.rank is None and spot.cheaper == [] and spot.gap is None
    assert spot.median == 1000  # 700, 900, 1000, 1200, 1500 — товар с ценой 1000 тут уже конкурент


def test_the_products_i_am_losing_come_first(market):
    market.append(product("101", 600, brand="Моя Марка"))
    competition = compare_with_rivals(market, parse_own("", "Моя Марка"))
    assert [spot.product.article for spot in competition.spots] == ["100", "101"]
    assert competition.leading == 1  # дешевле 101 никого нет
    assert competition.cheaper == 2  # меня обходят два товара


def test_a_rival_cheaper_by_rounding_is_not_counted():
    market = [product("100", 1000.4), product("1", 1000.0)]
    assert compare_with_rivals(market, parse_own("100", "")).spots[0].cheaper == []


def test_advantage_of_one_rival(market):
    spot = compare_with_rivals(market, parse_own("100", "")).spots[0]
    rival = spot.cheaper[1]  # 900 ₽
    assert (spot.gap_to(rival), spot.gap_pct_to(rival)) == (100, 10.0)  # на 10% моей цены ниже меня
