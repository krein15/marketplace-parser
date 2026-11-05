"""Exchange rates of the Bank of Russia: parsing, conversion and the saved rate when the site is down."""

from __future__ import annotations

from datetime import date

import pytest

from mpparser.currency import Rate, RateError, choose_rate, parse_cbr, saved_rate

CBR_XML = (
    '<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="06.11.2026" name="Foreign Currency Market">'
    '<Valute ID="R01010"><NumCode>036</NumCode><CharCode>AUD</CharCode><Nominal>1</Nominal>'
    "<Name>Австралийский доллар</Name><Value>57,3708</Value><VunitRate>57,3708</VunitRate></Valute>"
    '<Valute ID="R01375"><NumCode>156</NumCode><CharCode>CNY</CharCode><Nominal>1</Nominal>'
    "<Name>Юань</Name><Value>12,2570</Value><VunitRate>12,257</VunitRate></Valute>"
    '<Valute ID="R01820"><NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal>'
    "<Name>Японских иен</Name><Value>53,1000</Value><VunitRate>0,531</VunitRate></Valute>"
    "</ValCurs>"
)


def test_parse_cbr():
    rate = parse_cbr(CBR_XML, "CNY")
    assert rate == Rate(code="CNY", value=12.257, day=date(2026, 11, 6))


def test_nominal_is_taken_into_account():
    """The yen is quoted per 100 units."""
    assert parse_cbr(CBR_XML, "JPY").value == pytest.approx(0.531)


def test_unknown_currency_and_broken_page():
    assert parse_cbr(CBR_XML, "XXX") is None
    assert parse_cbr("<html>Сервис недоступен</html>", "CNY") is None


def test_conversion():
    rate = Rate(code="CNY", value=12.257, day=date(2026, 11, 6))
    assert rate.to_rub(10) == 122.57
    assert rate.to_rub(None) is None
    assert rate.from_rub(1225.7) == 100
    assert rate.describe() == "12,26 ₽ (ЦБ РФ на 06.11.2026)"


def test_fresh_rate_is_saved_and_used_when_the_site_is_down(tmp_path):
    fresh = choose_rate(CBR_XML, "CNY", tmp_path)
    assert not fresh.stale

    fallback = choose_rate(None, "CNY", tmp_path)
    assert fallback.value == fresh.value
    assert fallback.day == fresh.day
    assert fallback.stale
    assert "сохранённый" in fallback.describe()


def test_no_rate_at_all(tmp_path):
    assert saved_rate(tmp_path, "CNY") is None
    with pytest.raises(RateError):
        choose_rate(None, "CNY", tmp_path)
