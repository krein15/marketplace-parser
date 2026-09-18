from .base import Cancelled, MarketplaceParser, ParserError, Reporter
from .ozon import OzonParser
from .wildberries import WildberriesParser

PARSERS: dict[str, type[MarketplaceParser]] = {
    WildberriesParser.key: WildberriesParser,
    OzonParser.key: OzonParser,
}

__all__ = ["PARSERS", "Cancelled", "MarketplaceParser", "OzonParser", "ParserError", "Reporter", "WildberriesParser"]
