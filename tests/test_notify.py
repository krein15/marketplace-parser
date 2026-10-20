"""Telegram notifications: what the message says and how it is sent."""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime
from io import BytesIO

import pytest

from mpparser import notify as notify_module
from mpparser.competitors import compare_with_rivals, parse_own
from mpparser.models import Product
from mpparser.monitoring import CHEAPER, GONE, Change, Comparison, Positions
from mpparser.notify import NotifyError, Telegram, message, notify
from mpparser.runner import RunResult
from mpparser.settings import ParseSettings

WB = "Wildberries"


def product(article: str, price: float | None, name: str = "", brand: str = "") -> Product:
    return Product(marketplace=WB, article=article, name=name or f"Товар {article}", price=price, brand=brand,
                   url=f"https://wb/{article}")


@pytest.fixture
def result(tmp_path) -> RunResult:
    path = tmp_path / "Отчёт.xlsx"
    path.write_bytes(b"excel")
    return RunResult(path=path, products=[product("1", 950)], started_at=datetime(2026, 10, 20, 9, 0),
                     completed=["wb"])


@pytest.fixture
def telegram(tmp_path, monkeypatch) -> Telegram:
    monkeypatch.setattr(notify_module, "app_data_dir", lambda: tmp_path)
    return Telegram(token="123:AA", chat="42")


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Collect what would have gone to Telegram instead of going there."""
    calls: list[dict] = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout=None):
        calls.append({"url": request.full_url, "body": request.data,
                      "type": request.headers.get("Content-type", "")})
        return Answer()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


# --- the text ---


def test_the_message_names_the_run_and_the_file(result):
    text = message(ParseSettings(task_name="Наушники каждый день"), result)
    assert text.startswith("<b>Наушники каждый день</b>")
    assert "20.10 в 09:00 · товаров — 1" in text
    assert "Файл: Отчёт.xlsx" in text


def test_a_run_without_a_task_is_named_by_its_query(result):
    assert message(ParseSettings(query="  беспроводные наушники "), result).startswith(
        "<b>беспроводные наушники</b>")


def test_changes_are_listed_with_the_biggest_first(result):
    result.comparison = Comparison(previous_at=datetime(2026, 10, 19, 9, 0), marketplaces=[WB], changes=[
        Change(CHEAPER, WB, "1", "Наушники", "https://wb/1", old_price=1200, new_price=950),
        Change(GONE, WB, "2", "Пропавшие", "https://wb/2", old_price=500),
    ])
    text = message(ParseSettings(), result)
    assert "<b>Изменения</b> с 19.10 09:00" in text
    assert "подешевели — 1 · выпали из выдачи — 1" in text
    assert "• Наушники — 1 200 ₽ → 950 ₽ (-20.8%)" in text
    assert "Пропавшие" not in text  # у выпавшего нет новой цены, в список он не идёт


def test_my_products_and_who_is_cheaper(result):
    result.products = [product("1", 950, brand="Моя Марка"), product("2", 700), product("3", 1500)]
    result.competition = compare_with_rivals(result.products, parse_own("", "Моя Марка"))
    text = message(ParseSettings(), result)
    assert "<b>Мои товары</b>: 1 · дешевле меня — 1" in text
    assert "место по цене 2 из 3" in text and "дешевле всех — 700 ₽" in text


def test_places_in_search_are_counted(result):
    result.positions = Positions([datetime(2026, 10, 19), datetime(2026, 10, 20)],
                                 [(product("1", 950), [5, 2]), (product("2", 700), [1, 9]),
                                  (product("3", 500), [3, 3])])
    assert "<b>Позиции</b>: поднялись — 1 · опустились — 1" in message(ParseSettings(), result)


def test_errors_and_a_stopped_run_are_mentioned(result):
    result.cancelled = True
    result.errors = ["Ozon: сайт не ответил"]
    text = message(ParseSettings(), result)
    assert "Сбор остановлен" in text and "Ozon: сайт не ответил" in text


def test_names_cannot_break_the_markup(result):
    result.products = [product("1", 950, name="Наушники <b>Pro</b> & Co", brand="Моя Марка"), product("2", 700)]
    result.competition = compare_with_rivals(result.products, parse_own("", "Моя Марка"))
    assert "Наушники &lt;b&gt;Pro&lt;/b&gt; &amp; Co" in message(ParseSettings(), result)


def test_a_long_name_is_cut(result):
    result.products = [product("1", 950, name="Наушники " * 20, brand="Моя"), product("2", 700)]
    result.competition = compare_with_rivals(result.products, parse_own("", "Моя"))
    assert "…" in message(ParseSettings(), result)


# --- sending ---


def test_the_bot_settings_survive_a_restart(telegram):
    telegram.save()
    assert Telegram.load() == telegram and telegram.configured()
    assert not Telegram(token="123:AA").configured()


def test_the_message_and_the_file_are_sent(telegram, result, sent):
    telegram.save()
    assert notify(ParseSettings(notify=True), result) is True
    assert [call["url"].rsplit("/", 1)[-1] for call in sent] == ["sendMessage", "sendDocument"]
    assert b"chat_id=42" in sent[0]["body"]
    assert "multipart/form-data" in sent[1]["type"]
    assert "Отчёт.xlsx".encode() in sent[1]["body"] and b"excel" in sent[1]["body"]


def test_the_file_can_be_left_out(telegram, result, sent):
    telegram.save()
    notify(ParseSettings(notify=True, notify_file=False), result)
    assert len(sent) == 1


def test_nothing_is_sent_when_notifications_are_off(telegram, result, sent):
    telegram.save()
    assert notify(ParseSettings(notify=False), result) is False
    assert sent == []


def test_a_bot_that_is_not_set_up_is_reported(result, sent, tmp_path, monkeypatch):
    monkeypatch.setattr(notify_module, "app_data_dir", lambda: tmp_path)
    warnings: list[str] = []
    assert notify(ParseSettings(notify=True), result, warnings.append) is False
    assert sent == [] and "не настроен" in warnings[0]


def test_a_refusal_of_telegram_is_explained(telegram, result, monkeypatch):
    def refuse(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {},
                                     BytesIO(json.dumps({"description": "chat not found"}).encode()))

    monkeypatch.setattr("urllib.request.urlopen", refuse)
    telegram.save()
    warnings: list[str] = []
    assert notify(ParseSettings(notify=True), result, warnings.append) is False
    assert "chat not found" in warnings[0]


def test_an_unreachable_telegram_does_not_break_the_run(telegram, result, monkeypatch):
    def fail(request, timeout=None):
        raise OSError("нет сети")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(NotifyError, match="недоступен"):
        telegram.send("привет")


def test_a_file_too_big_for_telegram_is_refused(telegram, result, monkeypatch, sent):
    monkeypatch.setattr(notify_module, "MAX_FILE", 1)
    telegram.save()
    warnings: list[str] = []
    assert notify(ParseSettings(notify=True), result, warnings.append) is False
    assert len(sent) == 1 and "50 МБ" in warnings[0]  # текст ушёл, файл — нет
