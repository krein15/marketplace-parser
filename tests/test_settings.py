"""Settings validation, persistence and the field registry."""

from __future__ import annotations

import mpparser.settings as settings_module
from mpparser.fields import default_keys, product_fields, resolve, review_fields
from mpparser.settings import InputMode, ParseSettings, SortOrder


def test_valid_settings_have_no_problems():
    assert ParseSettings(query="наушники").validate() == []


def test_query_mode_requires_a_query():
    assert "Введите поисковый запрос." in ParseSettings(query="  ").validate()


def test_ids_mode_requires_articles():
    assert ParseSettings(mode=InputMode.IDS).validate()
    assert ParseSettings(mode=InputMode.IDS, ids={"wb": "145726284"}).validate() == []


def test_ids_of_an_unselected_marketplace_do_not_count():
    settings = ParseSettings(mode=InputMode.IDS, marketplaces=["ozon"], ids={"wb": "145726284"})
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
    for fields in (product_fields(), review_fields()):
        keys = [f.key for f in resolve(fields, [])]
        assert keys == [f.key for f in fields if f.required]
        assert "marketplace" in keys and "article" in keys


def test_selected_fields_keep_the_registry_order():
    keys = [f.key for f in resolve(product_fields(), {"rating", "name"})]
    assert keys == ["marketplace", "article", "name", "rating"]


def test_default_field_keys_are_valid():
    for fields in (product_fields(), review_fields()):
        assert set(default_keys(fields)) <= {f.key for f in fields}


def test_price_filter_is_checked():
    assert "Минимальная цена больше максимальной." in ParseSettings(query="q", price_min=500, price_max=100).validate()
    assert ParseSettings(query="q", price_min=-1).validate()
    assert ParseSettings(query="q", price_min=100, price_max=500).validate() == []


def test_filters_summary_lists_only_active_filters():
    # The WB delivery region is always shown: prices and stock depend on it.
    assert ParseSettings(query="q").filters_summary() == [("Регион WB", "Москва")]
    settings = ParseSettings(query="q", marketplaces=["ym"], price_min=1000,
                             options={"ym": {"rating_4": True, "delivery_days": 3}})
    assert settings.filters_summary() == [
        ("Цена", "от 1 000 ₽"),
        ("Фильтры Маркета", "рейтинг от 4.0, до 3 дней"),
    ]


def test_columns_of_other_marketplaces_are_dropped():
    fields = product_fields()
    keys = [f.key for f in fields]
    wb_only = [f.key for f in resolve(fields, keys, ["wb"])]
    assert "stock" not in wb_only and "price_card" not in wb_only and "price" in wb_only
    assert "price_card" in [f.key for f in resolve(fields, keys, ["wb", "ozon"])]


def test_new_settings_survive_a_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_module, "app_data_dir", lambda: tmp_path)
    original = ParseSettings(query="q", price_max=10,
                             options={"avito": {"locations": ["Казань"], "seller": "private"}})
    original.save()
    loaded = ParseSettings.load()
    assert loaded == original
    assert loaded.option("avito", "seller") == "private"
