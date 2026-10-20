"""Price history and comparison between runs."""

from __future__ import annotations

from datetime import datetime

import pytest

from mpparser.models import Product
from mpparser.monitoring import (
    CHEAPER,
    GONE,
    NEW,
    PRICIER,
    History,
    compare,
    job_key,
    update_history,
)
from mpparser.settings import InputMode, ParseSettings

YM, AVITO = "Яндекс Маркет", "Авито"


def product(article: str, price: float | None, marketplace: str = YM, position: int | None = None) -> Product:
    return Product(marketplace=marketplace, article=article, name=f"Товар {article}", price=price,
                   url=f"https://example.com/{article}", position=position)


@pytest.fixture
def history(tmp_path) -> History:
    return History(tmp_path / "history.sqlite")


def test_job_key_depends_on_what_is_collected():
    base = ParseSettings(query="Чайник  электрический", marketplaces=["ym"])
    assert job_key(base) == job_key(ParseSettings(query="чайник электрический", marketplaces=["ym"]))
    assert job_key(base) != job_key(ParseSettings(query="чайник", marketplaces=["ym"]))
    assert job_key(base) != job_key(ParseSettings(query="чайник электрический", marketplaces=["ym"], price_max=5))
    # Options that do not change the product list do not matter.
    assert job_key(base) == job_key(ParseSettings(query="чайник электрический", marketplaces=["ym"],
                                                  max_reviews=50, output_dir="D:/x"))
    assert job_key(ParseSettings(task_name="Чайники")) == "task:Чайники"
    ids = ParseSettings(mode=InputMode.IDS, marketplaces=["ym"], ids={"ym": "1\n2"})
    assert job_key(ids) == job_key(ParseSettings(mode=InputMode.IDS, marketplaces=["ym"], ids={"ym": "1 2"}))


def test_first_run_has_nothing_to_compare_with(history):
    comparison = compare(history.previous("job"), [product("1", 100)], [YM])
    assert comparison.previous_at is None and comparison.changes == []


def test_changes_between_runs(history):
    history.record("job", datetime(2026, 9, 26, 9, 0), [YM],
                   [product("1", 1000), product("2", 500), product("3", 300), product("4", 200)])
    current = [product("1", 900), product("2", 550), product("3", 300.4), product("5", 700)]
    comparison = compare(history.previous("job"), current, [YM])
    assert comparison.previous_at == datetime(2026, 9, 26, 9, 0)
    assert [(c.status, c.article) for c in comparison.changes] == [
        (CHEAPER, "1"), (PRICIER, "2"), (NEW, "5"), (GONE, "4")]
    cheaper = comparison.changes[0]
    assert (cheaper.old_price, cheaper.new_price, cheaper.delta, cheaper.delta_pct) == (1000, 900, -100, -10.0)
    assert comparison.count(CHEAPER) == 1 and comparison.count(GONE) == 1


def test_biggest_drops_come_first(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 1000), product("2", 1000)])
    comparison = compare(history.previous("job"), [product("1", 950), product("2", 500)], [YM])
    assert [c.article for c in comparison.changes] == ["2", "1"]


def test_marketplace_that_failed_is_not_compared(history):
    history.record("job", datetime(2026, 9, 26), [YM, AVITO], [product("1", 100), product("9", 50, AVITO)])
    # Today Avito failed: its listings must not show up as gone.
    comparison = compare(history.previous("job"), [product("1", 100)], [YM])
    assert comparison.marketplaces == [YM]
    assert comparison.changes == []


def test_only_completed_marketplaces_are_recorded(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 100), product("9", 50, AVITO)])
    assert list(history.previous("job").items) == [(YM, "1")]


def test_dynamics_over_runs(history):
    for day, price in ((24, 100), (25, None), (26, 90)):
        history.record("job", datetime(2026, 9, day), [YM], [product("1", price)] if price else [])
    dynamics = history.dynamics("job", [product("1", 90), product("2", 10)])
    assert dynamics.dates == [datetime(2026, 9, 24), datetime(2026, 9, 25), datetime(2026, 9, 26)]
    assert [prices for _, prices in dynamics.rows] == [[100, None, 90], [None, None, None]]


def test_update_history_compares_then_records(tmp_path):
    settings = ParseSettings(query="чайник", marketplaces=["ym"])
    path = tmp_path / "history.sqlite"
    first, _, _ = update_history(path, settings, datetime(2026, 9, 26), ["ym"], [product("1", 100)])
    assert first.previous_at is None
    second, dynamics, _ = update_history(path, settings, datetime(2026, 9, 27), ["ym"], [product("1", 80)])
    assert [(c.status, c.delta) for c in second.changes] == [(CHEAPER, -20)]
    assert len(dynamics.dates) == 2


def test_forget_removes_the_job(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 100)])
    history.forget("job")
    assert history.previous("job") is None


def test_labels_depend_on_how_products_were_collected(tmp_path):
    path = tmp_path / "history.sqlite"
    search = ParseSettings(query="чайник", marketplaces=["ym"])
    update_history(path, search, datetime(2026, 9, 26), ["ym"], [product("1", 100)])
    comparison, _, _ = update_history(path, search, datetime(2026, 9, 27), ["ym"], [product("2", 100)])
    assert comparison.search
    assert (comparison.label(GONE), comparison.title(NEW)) == ("Выпал из выдачи", "Новые в выдаче")

    ids = ParseSettings(mode=InputMode.IDS, marketplaces=["ym"], ids={"ym": "1 2"})
    update_history(path, ids, datetime(2026, 9, 26), ["ym"], [product("1", 100)])
    comparison, _, _ = update_history(path, ids, datetime(2026, 9, 27), ["ym"], [product("2", 100)])
    assert not comparison.search
    assert (comparison.label(GONE), comparison.title(GONE)) == ("Не найден", "Не найдены")

    listing = ParseSettings(mode=InputMode.IDS, marketplaces=["ym"],
                            ids={"ym": "https://market.yandex.ru/search?text=a"})
    comparison, _, _ = update_history(path, listing, datetime(2026, 9, 27), ["ym"], [product("2", 100)])
    assert comparison.search


# --- места в выдаче ---


def test_position_is_remembered_and_compared(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 100, position=3)])
    previous = history.previous("job")
    assert previous.items[(YM, "1")].position == 3

    changes = compare(previous, [product("1", 90, position=1)], [YM]).changes
    assert [(c.old_position, c.new_position, c.rise) for c in changes] == [(3, 1, 2)]


def test_a_product_that_left_the_results_keeps_its_last_place(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 100, position=5)])
    changes = compare(history.previous("job"), [], [YM]).changes
    assert [(c.status, c.old_position, c.new_position, c.rise) for c in changes] == [(GONE, 5, None, None)]


def test_positions_show_the_biggest_drops_first(history):
    for day, places in ((24, {"1": 2, "2": 9, "3": 4}), (25, {"1": 1, "2": 3, "3": 7})):
        history.record("job", datetime(2026, 9, day), [YM],
                       [product(article, 100, position=place) for article, place in places.items()])
    current = [product("1", 100, position=1), product("2", 100, position=3), product("3", 100, position=7)]
    positions = history.positions("job", current)

    assert positions.dates == [datetime(2026, 9, 24), datetime(2026, 9, 25)]
    assert [p.article for p, _ in positions.rows] == ["3", "1", "2"]  # −3, +1, +6
    assert [positions.rise(places) for _, places in positions.rows] == [-3, 1, 6]


def test_products_collected_by_article_have_no_positions(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 100)])
    assert history.positions("job", [product("1", 100)]).rows == []


def test_position_history_of_a_product_seen_once_has_no_movement(history):
    history.record("job", datetime(2026, 9, 26), [YM], [product("1", 100, position=4)])
    positions = history.positions("job", [product("1", 100, position=4)])
    assert positions.rise(positions.rows[0][1]) is None


def test_an_older_history_file_gets_the_position_column(tmp_path):
    """A database written before this version has no "position" column: it must open and keep working."""
    import sqlite3

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE runs (id INTEGER PRIMARY KEY, job TEXT NOT NULL, started_at TEXT NOT NULL,
                               marketplaces TEXT NOT NULL);
            CREATE TABLE prices (run_id INTEGER NOT NULL, marketplace TEXT NOT NULL, article TEXT NOT NULL,
                                 name TEXT, price REAL, url TEXT, PRIMARY KEY (run_id, marketplace, article));
        """)
        db.execute("INSERT INTO runs (id, job, started_at, marketplaces) "
                   "VALUES (1, 'job', '2026-09-26', '[\"Яндекс Маркет\"]')")
        db.execute("INSERT INTO prices VALUES (1, 'Яндекс Маркет', '1', 'Товар 1', 100, 'https://example.com/1')")

    history = History(path)
    assert history.previous("job").items[(YM, "1")].position is None
    history.record("job", datetime(2026, 9, 27), [YM], [product("1", 90, position=2)])
    assert history.previous("job").items[(YM, "1")].position == 2
