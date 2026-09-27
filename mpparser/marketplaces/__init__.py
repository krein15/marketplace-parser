from .avito import AvitoParser
from .base import Cancelled, MarketplaceParser, ParserError, Reporter
from .ozon import OzonParser
from .wildberries import WildberriesParser
from .yandex_market import YandexMarketParser

PARSERS: dict[str, type[MarketplaceParser]] = {
    WildberriesParser.key: WildberriesParser,
    OzonParser.key: OzonParser,
    YandexMarketParser.key: YandexMarketParser,
    AvitoParser.key: AvitoParser,
}

__all__ = [
    "PARSERS",
    "AvitoParser",
    "Cancelled",
    "MarketplaceParser",
    "OzonParser",
    "ParserError",
    "Reporter",
    "WildberriesParser",
    "YandexMarketParser",
]
