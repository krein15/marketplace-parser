"""Excel report and settings."""

from __future__ import annotations

from datetime import datetime

import pytest
from openpyxl import load_workbook

from mpparser.export.excel import build_file_name, export_to_excel, price_buckets
from mpparser.models import Product, Review
from mpparser.monitoring import CHEAPER, GONE, Change, Comparison, Dynamics
from mpparser.settings import InputMode, ParseSettings


@pytest.fixture
def products() -> list[Product]:
    return [
        Product(marketplace="Wildberries", article="1", name="Наушники WB", brand="Lirano", seller="Магазин",
                price=556.0, price_old=803.0, discount=31, rating=4.9, reviews_count=2569, position=1,
                url="https://www.wildberries.ru/catalog/1/detail.aspx", image="https://basket-01.wbbasket.ru/1.webp",
                category="Наушники", parsed_at=datetime(2026, 9, 18, 10, 30)),
        Product(marketplace="Ozon", article="2", name="Наушники Ozon", brand="JBL", seller="Продавец",
                price=3436.0, price_card=3100.0, price_old=5999.0, discount=42, rating=4.7, reviews_count=150,
                stock=287, position=1, url="https://www.ozon.ru/product/2/", category="Наушники"),
    ]


@pytest.fixture
def reviews() -> list[Review]:
    return [
        Review(marketplace="Wildberries", article="1", product_name="Наушники WB", date=datetime(2026, 9, 17, 20, 43),
               rating=5, author="Сержиккк", text="=) отличные наушники\nбас мощный", pros="цена", cons="",
               photos=2, likes=3, seller_answer="Спасибо!"),
        Review(marketplace="Ozon", article="2", product_name="Наушники Ozon", date=datetime(2026, 9, 13, 22, 43),
               rating=4, author="Ариша", text="Хорошие\x07наушники", variant="белый"),
    ]


def export(tmp_path, products, reviews, **overrides):
    settings = ParseSettings(query="наушники", **overrides)
    path = tmp_path / build_file_name(settings, datetime(2026, 9, 18, 10, 0))
    export_to_excel(path, settings, products, reviews, datetime(2026, 9, 18, 10, 0))
    return path


def test_file_name_contains_marketplaces_query_and_time():
    settings = ParseSettings(query="беспроводные наушники")
    assert build_file_name(settings, datetime(2026, 9, 18, 10, 0)) == \
        "WB+Ozon_беспроводные наушники_2026-09-18_10-00-00.xlsx"


def test_file_name_of_an_article_run_and_forbidden_characters():
    assert "артикулы" in build_file_name(ParseSettings(mode=InputMode.IDS), datetime(2026, 9, 18, 10, 0))
    name = build_file_name(ParseSettings(query='чехол/кейс: "про"'), datetime(2026, 9, 18, 10, 0))
    assert not set(name) & set('<>:"/\\|?*')


def test_sheets_and_headers(tmp_path, products, reviews):
    workbook = load_workbook(export(tmp_path, products, reviews))
    assert workbook.sheetnames == ["Сводка", "Товары", "Отзывы"]
    headers = [cell.value for cell in workbook["Товары"][1]]
    assert headers[:2] == ["Площадка", "Артикул"]
    assert "Цена, ₽" in headers
    assert workbook["Товары"].max_row == 3
    assert workbook["Товары"].freeze_panes == "C2"
    assert workbook["Товары"].tables


def test_reviews_sheet_is_skipped_when_reviews_are_off(tmp_path, products, reviews):
    workbook = load_workbook(export(tmp_path, products, reviews, collect_reviews=False))
    assert "Отзывы" not in workbook.sheetnames


def test_only_selected_columns_are_exported(tmp_path, products, reviews):
    workbook = load_workbook(export(tmp_path, products, reviews, product_fields=["price", "rating"]))
    assert [cell.value for cell in workbook["Товары"][1]] == ["Площадка", "Артикул", "Цена, ₽", "Рейтинг"]


def test_review_text_starting_with_equals_is_not_a_formula(tmp_path, products, reviews):
    sheet = load_workbook(export(tmp_path, products, reviews))["Отзывы"]
    text_column = [cell.value for cell in sheet[1]].index("Текст отзыва") + 1
    cell = sheet.cell(row=2, column=text_column)
    assert cell.data_type == "s"
    assert cell.value.startswith("=)")


def test_illegal_characters_are_stripped(tmp_path, products, reviews):
    sheet = load_workbook(export(tmp_path, products, reviews))["Отзывы"]
    text_column = [cell.value for cell in sheet[1]].index("Текст отзыва") + 1
    assert sheet.cell(row=3, column=text_column).value == "Хорошиенаушники"


def test_links_are_hyperlinks(tmp_path, products, reviews):
    sheet = load_workbook(export(tmp_path, products, reviews))["Товары"]
    url_column = [cell.value for cell in sheet[1]].index("Ссылка") + 1
    cell = sheet.cell(row=2, column=url_column)
    assert cell.hyperlink.target == "https://www.wildberries.ru/catalog/1/detail.aspx"
    assert cell.value == "Открыть"


def test_summary_statistics(tmp_path, products, reviews):
    sheet = load_workbook(export(tmp_path, products, reviews))["Сводка"]
    values = [[cell.value for cell in row] for row in sheet.iter_rows()]
    flat = {str(row[0]): row for row in values}
    assert flat["Собрано товаров"][1] == 2
    assert flat["Собрано отзывов"][1] == 2
    wb_row = next(row for row in values if row[0] == "Wildberries")
    assert wb_row[1] == 1  # products
    assert wb_row[2] == 556.0  # min price
    ozon_row = next(row for row in values if row[0] == "Ozon")
    assert ozon_row[3] == 3436.0  # average price


def test_export_without_reviews_still_writes_products(tmp_path, products):
    workbook = load_workbook(export(tmp_path, products, []))
    assert workbook["Отзывы"].max_row == 1  # header only


@pytest.mark.parametrize("prices", [[100.0], [100.0, 100.0, 100.0]])
def test_price_buckets_survive_degenerate_input(prices):
    buckets = price_buckets(prices)
    assert len(buckets) == 8
    assert buckets[0][0] <= min(prices)
    assert buckets[-1][1] is None


def test_price_buckets_cover_the_range():
    buckets = price_buckets([500.0, 1500.0, 9000.0, 25000.0])
    assert buckets[0][0] == 0
    assert all(low < high for low, high in buckets[:-1])


def test_changes_and_dynamics_sheets(tmp_path, products, reviews):
    comparison = Comparison(previous_at=datetime(2026, 9, 17, 9, 0), marketplaces=["Wildberries"], changes=[
        Change(CHEAPER, "Wildberries", "1", "Наушники WB", "https://wb/1", 600.0, 556.0),
        Change(GONE, "Wildberries", "7", "Старые наушники", "https://wb/7", 900.0, None),
    ])
    dynamics = Dynamics([datetime(2026, 9, 17, 9, 0), datetime(2026, 9, 18, 10, 0)],
                        [(products[0], [600.0, 556.0])])
    settings = ParseSettings(query="наушники", task_name="Наушники ежедневно")
    path = tmp_path / build_file_name(settings, datetime(2026, 9, 18, 10, 0))
    assert "Наушники ежедневно" in path.name
    export_to_excel(path, settings, products, reviews, datetime(2026, 9, 18, 10, 0), comparison, dynamics)
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Сводка", "Изменения", "Товары", "Отзывы", "Динамика цен"]
    changes = workbook["Изменения"]
    assert [c.value for c in changes[4]][:3] == ["Статус", "Площадка", "Артикул"]
    assert [c.value for c in changes[5]][:8] == [CHEAPER, "Wildberries", "1", "Наушники WB", 600, 556, -44, -7.3]
    assert changes["I5"].hyperlink.target == "https://wb/1"
    assert changes["A6"].value == "Выпал из выдачи" and changes["F6"].value is None
    assert "рекламных мест" in changes["A3"].value
    history = workbook["Динамика цен"]
    assert [c.value for c in history[4]][:5] == ["Площадка", "Артикул", "Название", "17.09 09:00", "18.09 10:00"]
    assert [c.value for c in history[5]][3:5] == [600, 556]
    summary = [row[0] for row in workbook["Сводка"].iter_rows(values_only=True)]
    assert "Изменения с прошлого запуска" in summary and "Подешевели" in summary and "Задание" in summary
    assert "Выпали из выдачи" in summary


def test_first_run_changes_sheet_explains_itself(tmp_path, products, reviews):
    path = tmp_path / "report.xlsx"
    export_to_excel(path, ParseSettings(query="q"), products, reviews, datetime(2026, 9, 18), Comparison(None))
    workbook = load_workbook(path)
    assert "первый запуск" in workbook["Изменения"]["A2"].value
    assert "Динамика цен" not in workbook.sheetnames
