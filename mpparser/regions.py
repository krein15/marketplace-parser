"""Delivery regions.

WB prices, stock and delivery times depend on the ``dest`` parameter. The codes below are the most common
``dest`` values among WB pickup points of each city (source: static-basket-01.wbbasket.ru/vol0/data/
all-poo-fr-v12.json, collected 2026-09-17). Regenerate them with ``tools/update_wb_regions.py``.

Ozon takes the region from the delivery address saved in the browser profile, so it is not listed here.
"""

from __future__ import annotations

WB_REGIONS: dict[str, int] = {
    "Москва": -1257403,
    "Санкт-Петербург": -1205339,
    "Новосибирск": -365401,
    "Екатеринбург": 123589409,
    "Казань": -2133467,
    "Нижний Новгород": 12358540,
    "Челябинск": -1579610,
    "Красноярск": -5854093,
    "Самара": -284542,
    "Уфа": -5523261,
    "Ростов-на-Дону": 1259570240,
    "Омск": -3902910,
    "Краснодар": 12358082,
    "Воронеж": 12358289,
    "Пермь": -1268696,
    "Волгоград": -4039467,
    "Тюмень": 12358487,
    "Саратов": -3892924,
    "Иркутск": -5827226,
    "Хабаровск": -1785055,
    "Владивосток": 1259571108,
    "Калининград": -331412,
    "Сочи": -1116490,
    "Ярославль": -3351788,
    "Томск": -2688921,
}

DEFAULT_REGION = "Москва"


def wb_dest(region: str) -> int:
    return WB_REGIONS.get(region, WB_REGIONS[DEFAULT_REGION])
