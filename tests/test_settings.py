"""Settings validation, persistence and the field registry."""

from __future__ import annotations

import mpparser.settings as settings_module
from mpparser.fields import PRODUCT_FIELDS, REVIEW_FIELDS, default_keys, resolve
from mpparser.settings import InputMode, ParseSettings, SellerType, SortOrder


def test_valid_settings_have_no_problems():
    assert ParseSettings(query="наушники").validate() == []


def test_query_mode_requires_a_query():
    assert "Введите поисковый запрос." in ParseSettings(query="  ").validate()


def test_ids_mode_requires_articles():
    assert ParseSettings(mode=InputMode.IDS).validate()
    assert ParseSettings(mode=InputMode.IDS, wb_ids="145726284").validate() == []


def test_ids_of_an_unselected_marketplace_do_not_count():
    settings = ParseSettings(mode=InputMode.IDS, marketplaces=["ozon"], wb_ids="145726284")
    assert settings.validate()


def test_marketplaces_and_limits_are_checked():
    assert ParseSettings(query="q", marketplaces=[]).validate()
    assert ParseSettings(query="q", max_products=0).validate()
    assert ParseSettings(query="q", max_reviews=0).validate()
    assert ParseSettings(query="q", collect_reviews=False, max_reviews=0).validate() == []


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "app_data_dir", lambda: tmp_path)
    original = ParseSettings(query="наушники", marketplaces=["ozon"], sort=SortOrder.PRICE_ASC,
                             mode=InputMode.IDS, max_products=7, product_fields=["price"])
    original.save()
    loaded = ParseSettings.load()
    assert loaded == original
    assert isinstance(loaded.sort, SortOrder) and isinstance(loaded.mode, InputMode)


def test_load_falls_back_to_defaults_on_broken_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "app_data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("{not json", encoding="utf-8")
    assert ParseSettings.load() == ParseSettings()


def test_unknown_keys_in_the_file_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "app_data_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text('{"query": "q", "removed_option": 1}', encoding="utf-8")
    assert ParseSettings.load().query == "q"


def test_required_fields_are_always_exported():
    for fields in (PRODUCT_FIELDS, REVIEW_FIELDS):
        keys = [f.key for f in resolve(fields, [])]
        assert keys == [f.key for f in fields if f.required]
        assert "marketplace" in keys and "article" in keys


def test_selected_fields_keep_the_registry_order():
    keys = [f.key for f in resolve(PRODUCT_FIELDS, {"rating", "name"})]
    assert keys == ["marketplace", "article", "name", "rating"]


def test_default_field_keys_are_valid():
    for fields in (PRODUCT_FIELDS, REVIEW_FIELDS):
        assert set(default_keys(fields)) <= {f.key for f in fields}


def test_price_filter_is_checked():
    assert "Минимальная цена больше максимальной." in ParseSettings(query="q", price_min=500, price_max=100).validate()
    assert ParseSettings(query="q", price_min=-1).validate()
    assert ParseSettings(query="q", price_min=100, price_max=500).validate() == []


def test_avito_needs_a_location_in_query_mode():
    assert ParseSettings(query="q", marketplaces=["avito"], avito_locations=[]).validate()
    assert ParseSettings(query="q", marketplaces=["avito"]).validate() == []  # "Вся Россия" by default


def test_avito_locations_map_to_slugs_and_accept_raw_slugs():
    settings = ParseSettings(avito_locations=["Екатеринбург", "Вся Россия", "berezovskiy"])
    assert settings.avito_location_slugs() == [("Екатеринбург", "ekaterinburg"), ("Вся Россия", "all"),
                                               ("berezovskiy", "berezovskiy")]


def test_filters_summary_lists_only_active_filters():
    assert ParseSettings(query="q").filters_summary() == []
    settings = ParseSettings(query="q", marketplaces=["ym", "avito"], price_min=1000, ym_rating_4=True,
                             ym_delivery_days=3, avito_locations=["Москва", "Казань"],
                             avito_seller=SellerType.COMPANY)
    assert settings.filters_summary() == [
        ("Цена", "от 1 000 ₽"),
        ("Фильтры Маркета", "рейтинг от 4.0, до 3 дней"),
        ("Города Авито", "Москва, Казань"),
        ("Фильтры Авито", "продавцы: компании"),
    ]


def test_columns_of_other_marketplaces_are_dropped():
    keys = [f.key for f in PRODUCT_FIELDS]
    wb_only = [f.key for f in resolve(PRODUCT_FIELDS, keys, ["wb"])]
    assert "region" not in wb_only and "price_card" not in wb_only and "price" in wb_only
    assert "region" in [f.key for f in resolve(PRODUCT_FIELDS, keys, ["wb", "avito"])]


def test_new_settings_survive_a_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "app_data_dir", lambda: tmp_path)
    original = ParseSettings(query="q", avito_locations=["Казань"], avito_seller=SellerType.PRIVATE, price_max=10)
    original.save()
    loaded = ParseSettings.load()
    assert loaded == original and isinstance(loaded.avito_seller, SellerType)
