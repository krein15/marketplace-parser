"""Settings validation, persistence and the field registry."""

from __future__ import annotations

import mpparser.settings as settings_module
from mpparser.fields import PRODUCT_FIELDS, REVIEW_FIELDS, default_keys, resolve
from mpparser.settings import InputMode, ParseSettings, SortOrder


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
