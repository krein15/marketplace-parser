"""Delivery regions.

WB prices, stock and delivery times depend on the ``dest`` parameter. The codes below are the most common
``dest`` values among WB pickup points of each city (source: static-basket-01.wbbasket.ru/vol0/data/
all-poo-fr-v12.json, collected 2026-09-17). Regenerate them with ``tools/update_wb_regions.py``.

Ozon and Yandex Market take the region from the delivery address saved in the browser profile (Market lets only
signed-in users change it; anonymous visitors get the region of their IP), so they are not listed here.
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


# Avito puts the location into the URL path: avito.ru/<slug>?q=… Cities first, then regions ("вся область").
# A name missing here can be typed as a slug in the program; an unknown slug makes Avito answer 404,
# which the parser reports and skips.
AVITO_LOCATIONS: dict[str, str] = {
    "Вся Россия": "all",
    "Москва": "moskva",
    "Санкт-Петербург": "sankt-peterburg",
    "Новосибирск": "novosibirsk",
    "Екатеринбург": "ekaterinburg",
    "Казань": "kazan",
    "Нижний Новгород": "nizhniy_novgorod",
    "Челябинск": "chelyabinsk",
    "Красноярск": "krasnoyarsk",
    "Самара": "samara",
    "Уфа": "ufa",
    "Ростов-на-Дону": "rostov-na-donu",
    "Омск": "omsk",
    "Краснодар": "krasnodar",
    "Воронеж": "voronezh",
    "Пермь": "perm",
    "Волгоград": "volgograd",
    "Тюмень": "tyumen",
    "Саратов": "saratov",
    "Тольятти": "tolyatti",
    "Ижевск": "izhevsk",
    "Барнаул": "barnaul",
    "Ульяновск": "ulyanovsk",
    "Иркутск": "irkutsk",
    "Хабаровск": "habarovsk",
    "Ярославль": "yaroslavl",
    "Владивосток": "vladivostok",
    "Махачкала": "mahachkala",
    "Томск": "tomsk",
    "Оренбург": "orenburg",
    "Кемерово": "kemerovo",
    "Новокузнецк": "novokuznetsk",
    "Рязань": "ryazan",
    "Астрахань": "astrahan",
    "Набережные Челны": "naberezhnye_chelny",
    "Пенза": "penza",
    "Липецк": "lipetsk",
    "Киров": "kirov",
    "Чебоксары": "cheboksary",
    "Тула": "tula",
    "Калининград": "kaliningrad",
    "Курск": "kursk",
    "Ставрополь": "stavropol",
    "Сочи": "sochi",
    "Улан-Удэ": "ulan-ude",
    "Тверь": "tver",
    "Магнитогорск": "magnitogorsk",
    "Иваново": "ivanovo",
    "Брянск": "bryansk",
    "Белгород": "belgorod",
    "Сургут": "surgut",
    "Владимир": "vladimir",
    "Архангельск": "arhangelsk",
    "Чита": "chita",
    "Смоленск": "smolensk",
    "Калуга": "kaluga",
    "Мурманск": "murmansk",
    "Якутск": "yakutsk",
    "Вологда": "vologda",
    "Кострома": "kostroma",
    "Тамбов": "tambov",
    "Петрозаводск": "petrozavodsk",
    "Псков": "pskov",
    "Великий Новгород": "velikiy_novgorod",
    "Сыктывкар": "syktyvkar",
    "Симферополь": "simferopol",
    "Севастополь": "sevastopol",
    "Московская область": "moskovskaya_oblast",
    "Ленинградская область": "leningradskaya_oblast",
    "Свердловская область": "sverdlovskaya_oblast",
    "Краснодарский край": "krasnodarskiy_kray",
    "Новосибирская область": "novosibirskaya_oblast",
    "Челябинская область": "chelyabinskaya_oblast",
    "Нижегородская область": "nizhegorodskaya_oblast",
    "Самарская область": "samarskaya_oblast",
    "Ростовская область": "rostovskaya_oblast",
    "Республика Татарстан": "tatarstan",
    "Республика Башкортостан": "bashkortostan",
}

DEFAULT_AVITO_LOCATION = "Вся Россия"
