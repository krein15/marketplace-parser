"""The marketplace contract: a marketplace declared outside the core reaches every part of the program."""

from __future__ import annotations

import re

import pytest

from mpparser import plugins
from mpparser.__main__ import build_parser, option_dest
from mpparser.fields import CORE_PRODUCT_FIELDS, Field, product_fields
from mpparser.inputs import parse_ids
from mpparser.marketplaces.base import MarketplaceParser
from mpparser.plugins import Marketplace, Option
from mpparser.settings import ParseSettings


class FakeParser(MarketplaceParser):
    key = "fake"
    title = "Барахолка"

    async def prepare(self) -> None: ...

    async def search(self, query, limit, sort): return []

    async def products_by_ids(self, articles): return []

    async def reviews(self, product, limit): return []


FAKE = Marketplace(
    key="fake",
    title="Барахолка",
    short_title="БХ",
    site="baraholka.test",
    parser=FakeParser,
    item_patterns=(re.compile(r"baraholka\.test/item/(\d+)"),),
    listing_pattern=re.compile(r"^(?:https?://)?baraholka\.test/\S+"),
    product_fields=(Field("district", "Район", width=16, only=("fake",)),),
    options=(
        Option(key="only_new", title="Только новые", summary_text="только новые", own_row=False),
        Option(key="condition", title="Состояние", kind="choice", default="any",
               choices=(("any", "Любое"), ("new", "Новое")), own_row=False),
        Option(key="cities", title="Города", kind="multi", default=["Тверь"],
               catalogue=(("Тверь", "tver"), ("Москва", "msk")), label="Города Барахолки"),
    ),
    order=90,
)


@pytest.fixture
def with_fake_marketplace(monkeypatch):
    """Pretend the plugin package is installed."""
    monkeypatch.setattr(plugins, "_external", lambda: ([FAKE], []))
    plugins.reset_cache()
    yield FAKE
    plugins.reset_cache()


def test_builtin_marketplaces_are_registered():
    assert set(plugins.registry()) >= {"wb", "ozon", "ym"}
    assert plugins.title_of("wb") == "Wildberries"


def test_installed_marketplace_appears_everywhere(with_fake_marketplace):
    assert "fake" in plugins.registry()
    assert plugins.short_titles()["fake"] == "БХ"
    assert "district" in [f.key for f in product_fields()]
    assert [f.key for f in product_fields()][-1] == "parsed_at"  # added columns stay before the trailing ones


def test_installed_marketplace_gets_command_line_flags(with_fake_marketplace):
    args = build_parser().parse_args(["--fake", "-q", "стол", "--fake-only-new", "--fake-condition", "new",
                                      "--fake-cities", "Тверь", "--fake-cities", "Москва"])
    assert args.fake is True
    assert getattr(args, option_dest("fake", "only_new")) is True
    assert getattr(args, option_dest("fake", "condition")) == "new"
    assert getattr(args, option_dest("fake", "cities")) == ["Тверь", "Москва"]


def test_links_of_an_installed_marketplace_are_recognised(with_fake_marketplace):
    parsed = parse_ids("https://baraholka.test/item/12345 https://baraholka.test/search?q=стол", "wb")
    assert parsed.for_marketplace("fake") == ["12345"]
    assert parsed.listings_for("fake") == ["https://baraholka.test/search?q=стол"]


def test_filters_of_an_installed_marketplace_reach_the_report(with_fake_marketplace):
    settings = ParseSettings(query="стол", marketplaces=["fake"],
                             options={"fake": {"only_new": True, "condition": "new", "cities": ["Москва"]}})
    assert settings.validate() == []
    assert settings.filters_summary() == [
        ("Города Барахолки", "Москва"),
        ("Фильтры Барахолка", "только новые, новое"),
    ]


def test_option_value_falls_back_to_the_declared_default(with_fake_marketplace):
    settings = ParseSettings(query="стол", marketplaces=["fake"])
    assert settings.option("fake", "cities") == ["Тверь"]
    assert settings.option("fake", "condition") == "any"
    assert settings.locations("fake", "cities") == [("Тверь", "tver")]


def test_unknown_marketplace_is_reported_not_crashed():
    problems = ParseSettings(query="стол", marketplaces=["fake"]).validate()
    assert any("не установлен" in problem for problem in problems)


def test_a_broken_plugin_does_not_stop_the_program(monkeypatch):
    monkeypatch.setattr(plugins, "_external", lambda: ([], ["Модуль «avito» не загрузился: нет такого модуля"]))
    plugins.reset_cache()
    try:
        assert "wb" in plugins.registry()
        assert plugins.load_errors() == ["Модуль «avito» не загрузился: нет такого модуля"]
    finally:
        plugins.reset_cache()


def test_columns_are_merged_before_the_trailing_ones():
    extra = (Field("district", "Район"),)
    merged = plugins._merge(CORE_PRODUCT_FIELDS, extra)
    assert [f.key for f in merged][-2:] == ["district", "parsed_at"]


@pytest.mark.parametrize(
    ("option", "value", "expected"),
    [
        (Option("x", "Только новые", summary_text="только новые"), True, "только новые"),
        (Option("x", "Только новые"), False, None),
        (Option("x", "Срок", kind="choice", default=0, choices=((0, "Любой"), (3, "До 3 дней")), own_row=False),
         3, "до 3 дней"),
        (Option("x", "Срок", kind="choice", default=0, choices=((0, "Любой"), (3, "До 3 дней"))), 0, None),
        (Option("x", "Города", kind="multi", default=[]), ["Москва", "Тверь"], "Москва, Тверь"),
        (Option("x", "Города", kind="multi", default=[]), [], None),
    ],
)
def test_option_summaries(option, value, expected):
    assert option.summary(value) == expected


def test_settings_from_an_older_version_are_migrated():
    raw = {"query": "стол", "wb_ids": "123", "avito_ids": "456", "region": "Казань", "ym_rating_4": True,
           "avito_seller": "private"}
    settings = ParseSettings.from_dict(raw)
    assert settings is not None
    assert settings.ids_text("wb") == "123" and settings.ids_text("avito") == "456"
    assert settings.options["wb"]["region"] == "Казань"
    assert settings.options["ym"]["rating_4"] is True
    assert settings.options["avito"]["seller"] == "private"
