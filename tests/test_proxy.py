"""The proxy address: how it is read and what happens when it does not answer."""

from __future__ import annotations

import asyncio

import pytest

from mpparser.browser import Browser, BrowserError
from mpparser.proxy import ProxyError, describe, parse_proxy
from mpparser.runner import check_proxy
from mpparser.settings import ParseSettings


def test_a_bare_address_means_http():
    assert parse_proxy("10.0.0.1:3128") == {"server": "http://10.0.0.1:3128"}
    assert parse_proxy("  proxy.local:8080  ") == {"server": "http://proxy.local:8080"}


def test_the_protocol_is_kept():
    assert parse_proxy("socks5://10.0.0.1:1080") == {"server": "socks5://10.0.0.1:1080"}
    assert parse_proxy("https://proxy.local:443") == {"server": "https://proxy.local:443"}


def test_a_login_and_a_password_are_separated():
    assert parse_proxy("user:secret@10.0.0.1:3128") == {
        "server": "http://10.0.0.1:3128", "username": "user", "password": "secret"}
    # Символы логина пишут в процентах, браузеру они нужны как есть.
    assert parse_proxy("http://my%40mail:p%40ss@host:3128")["username"] == "my@mail"


def test_a_login_without_a_password_still_works():
    assert parse_proxy("user@10.0.0.1:3128") == {
        "server": "http://10.0.0.1:3128", "username": "user", "password": ""}


def test_no_proxy_means_a_direct_connection():
    assert parse_proxy("") is None and parse_proxy("   ") is None


@pytest.mark.parametrize("address, complaint", [
    ("10.0.0.1", "адрес и порт"),
    ("10.0.0.1:порт", "числом"),
    ("ftp://10.0.0.1:21", "не подходит"),
])
def test_an_address_that_cannot_be_used_is_explained(address, complaint):
    with pytest.raises(ProxyError, match=complaint):
        parse_proxy(address)


def test_a_broken_address_stops_the_run_before_the_browser():
    problems = ParseSettings(query="чайник", proxy="10.0.0.1").validate()
    assert any("адрес и порт" in problem for problem in problems)
    assert ParseSettings(query="чайник", proxy="user:secret@10.0.0.1:3128").validate() == []


def test_the_password_is_not_shown():
    assert describe("user:secret@10.0.0.1:3128") == "http://user@10.0.0.1:3128"
    assert describe("10.0.0.1:3128") == "http://10.0.0.1:3128"
    assert describe("") == ""


def test_the_browser_is_launched_through_the_proxy():
    browser = Browser(profile_dir=None, proxy="user:secret@10.0.0.1:3128")  # type: ignore[arg-type]
    assert browser.proxy == {"server": "http://10.0.0.1:3128", "username": "user", "password": "secret"}
    assert Browser(profile_dir=None).proxy is None  # type: ignore[arg-type]


class FakeBrowser:
    def __init__(self, answer: tuple[int, object]) -> None:
        self.answer = answer
        self.asked: list[str] = []

    async def get_json(self, url: str, timeout: float = 30) -> tuple[int, object]:
        self.asked.append(url)
        return self.answer


def test_a_working_proxy_reports_the_address_it_shows():
    browser = FakeBrowser((200, {"ip": "203.0.113.7"}))
    assert asyncio.run(check_proxy(browser, "10.0.0.1:3128")) == "203.0.113.7"  # type: ignore[arg-type]
    assert "ipify" in browser.asked[0]


@pytest.mark.parametrize("answer", [(0, None), (407, None), (200, {"error": "no"})])
def test_a_silent_proxy_is_reported_at_once(answer):
    with pytest.raises(BrowserError, match="не отвечает"):
        asyncio.run(check_proxy(FakeBrowser(answer), "user:secret@10.0.0.1:3128"))  # type: ignore[arg-type]
