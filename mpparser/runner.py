"""Runs a full collection job: marketplaces → products → extra fields → reviews → history → Excel."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import plugins
from .browser import Browser, BrowserError
from .competitors import Competition, compare_with_rivals, parse_own
from .export.excel import build_file_name, export_to_excel
from .inputs import collect_ids
from .marketplaces import Cancelled, MarketplaceParser, ParserError, Reporter
from .models import Product, Review
from .monitoring import STATUS_ORDER, Comparison, Positions, update_history
from .notify import notify
from .proxy import IP_SERVICE, describe
from .settings import InputMode, ParseSettings, app_data_dir

log = logging.getLogger(__name__)

# Share of the progress bar given to each stage of one marketplace.
STAGES = {"products": 0.45, "enrich": 0.2, "reviews": 0.35}
# Что одинаково у одного товара, найденного разными запросами: карточку запрашиваем один раз.
SHARED_DETAILS = ("name", "brand", "seller", "seller_rating", "price", "price_card", "price_old",
                  "discount", "rating", "reviews_count", "stock", "category", "image", "url",
                  "reviews_key")


@dataclass
class RunResult:
    path: Path | None = None
    products: list[Product] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    started_at: datetime = field(default_factory=datetime.now)
    completed: list[str] = field(default_factory=list)  # marketplaces collected without errors
    comparison: Comparison | None = None
    competition: Competition | None = None
    positions: Positions | None = None


def _merge(products: list[Product], more: list[Product]) -> list[Product]:
    """Append products that are not in the list yet: the same item may be found by two links."""
    known = {p.article for p in products}
    return products + [p for p in more if p.article not in known]


async def _listing(parser: MarketplaceParser, link: str, limit: int, reporter: Reporter) -> list[Product]:
    try:
        products = await parser.listing(link, limit)
    except NotImplementedError as exc:
        reporter.warn(str(exc))
        return []
    reporter.log(f"{parser.title}: по ссылке на выдачу собрано товаров — {len(products)}")
    return products


async def _search_all(parser: MarketplaceParser, settings: ParseSettings, reporter: Reporter,
                      base: float, share: float) -> list[Product]:
    """Collect every query of the run. A product found by two queries stays in the report twice:
    its place and its rivals differ from query to query, and that is what the run is about."""
    queries = settings.queries
    stage = share * STAGES["products"]
    products: list[Product] = []
    for number, query in enumerate(queries):
        reporter.set_span(base + stage * number / len(queries), base + stage * (number + 1) / len(queries))
        found = await parser.search(query, settings.max_products, settings.sort)
        for product in found:
            product.query = query
        if len(queries) > 1:
            reporter.log(f"{parser.title}: «{query}» — товаров {len(found)}")
        products += found
    return products


def _once_per_product(products: list[Product]) -> list[Product]:
    """One object per (marketplace, article): the card and the reviews are fetched once, not once per query."""
    first: dict[tuple[str, str], Product] = {}
    for product in products:
        first.setdefault((product.marketplace, product.article), product)
    return list(first.values())


def _share_details(products: list[Product]) -> None:
    """Give the twins from other queries what was fetched for the first of them."""
    first: dict[tuple[str, str], Product] = {}
    for product in products:
        twin = first.setdefault((product.marketplace, product.article), product)
        if twin is not product:
            for name in SHARED_DETAILS:
                setattr(product, name, getattr(twin, name))


def _log_competition(reporter: Reporter, competition: Competition) -> None:
    if not competition:
        return
    nobody = f", никто не дешевле — {competition.leading}" if competition.leading else ""
    reporter.log(f"Моих товаров в выдаче: {len(competition.spots)}; "
                 f"дешевле меня — {competition.cheaper}{nobody}")


def _log_changes(reporter: Reporter, comparison: Comparison) -> None:
    if comparison.previous_at is None:
        reporter.log("Первый запуск с такими параметрами: изменения цен появятся со следующего.")
        return
    counts = ", ".join(f"{comparison.title(s).lower()} — {comparison.count(s)}" for s in STATUS_ORDER)
    reporter.log(f"Изменения с {comparison.previous_at:%d.%m %H:%M}: {counts}")


async def check_proxy(browser: Browser, address: str) -> str:
    """Ask the network who we are now. A dead proxy must be seen at once, not as an error per marketplace."""
    status, payload = await browser.get_json(IP_SERVICE, timeout=20)
    if status == 200 and isinstance(payload, dict) and payload.get("ip"):
        return str(payload["ip"])
    raise BrowserError(f"Прокси {describe(address)} не отвечает. Проверьте адрес, логин и пароль.")


async def run(settings: ParseSettings, reporter: Reporter) -> RunResult:
    result = RunResult()
    ids = collect_ids({mp: settings.ids_text(mp) for mp in settings.marketplaces})
    if settings.mode == InputMode.IDS and ids.invalid:
        reporter.warn(f"Пропущены нераспознанные строки: {', '.join(ids.invalid[:10])}")

    field_keys = set(settings.product_fields)
    share = 1 / len(settings.marketplaces)
    try:
        reporter.log("Запускаю браузер…" + (f" Прокси: {describe(settings.proxy)}"
                                           if settings.proxy.strip() else ""))
        parsers = plugins.parsers()
        needs_window = any(parsers[key].needs_window for key in settings.marketplaces)
        if needs_window and not settings.show_browser:
            reporter.log("Авито работает только в окне браузера — оно откроется на время сбора. "
                         "Если сайт попросит проверку «я не робот», пройдите её в этом окне.")
        headless = not settings.show_browser and not needs_window
        async with Browser(app_data_dir() / "browser-profile", headless=headless,
                           proxy=settings.proxy) as browser:
            if settings.proxy.strip():
                address = await check_proxy(browser, settings.proxy)
                reporter.log(f"Прокси работает, внешний адрес — {address}")
            for index, key in enumerate(settings.marketplaces):
                parser = parsers[key](browser, settings, reporter)
                base = index * share
                try:
                    reporter.set_span(base, base)
                    reporter.progress(0, 1, f"{parser.title}: подготовка")
                    await parser.prepare()

                    reporter.set_span(base, base + share * STAGES["products"])
                    if settings.mode == InputMode.QUERY:
                        products = await _search_all(parser, settings, reporter, base, share)
                    else:
                        articles, links = ids.for_marketplace(key), ids.listings_for(key)
                        if not articles and not links:
                            continue
                        products = await parser.products_by_ids(articles) if articles else []
                        for link in links:
                            products = _merge(products, await _listing(parser, link, settings.max_products,
                                                                       reporter))
                    reporter.log(f"{parser.title}: собрано товаров — {len(products)}")
                    result.products += products

                    reporter.set_span(base + share * STAGES["products"],
                                      base + share * (STAGES["products"] + STAGES["enrich"]))
                    once = _once_per_product(products)
                    await parser.enrich(once, field_keys)
                    _share_details(products)

                    if settings.collect_reviews:
                        reporter.set_span(base + share * (1 - STAGES["reviews"]), base + share)
                        with_reviews = [p for p in once if p.reviews_count != 0]
                        for number, product in enumerate(with_reviews, 1):
                            reporter.progress(number - 1, len(with_reviews),
                                              f"{parser.title}: отзывы, товар {number} из {len(with_reviews)}")
                            reviews = await parser.reviews(product, settings.max_reviews)
                            result.reviews += reviews
                            await parser.pause()
                        count = sum(1 for r in result.reviews if r.marketplace == parser.title)
                        reporter.log(f"{parser.title}: собрано отзывов — {count}")
                    reporter.set_span(base + share, base + share)
                    reporter.progress(1, 1, f"{parser.title}: готово")
                    if products:  # an empty result is more likely a site glitch than "everything disappeared"
                        result.completed.append(key)
                except (ParserError, BrowserError) as exc:
                    result.errors.append(str(exc))
                    reporter.log(str(exc), "error")
                except Cancelled:
                    raise
                except Exception as exc:
                    log.exception("%s failed", parser.title)
                    message = f"{parser.title}: непредвиденная ошибка — {exc}"
                    result.errors.append(message)
                    reporter.log(message, "error")
    except Cancelled:
        result.cancelled = True
        reporter.warn("Сбор остановлен пользователем. Сохраняю то, что успели собрать.")
    except BrowserError as exc:
        result.errors.append(str(exc))
        reporter.log(str(exc), "error")

    if result.products:
        dynamics = None
        if not result.cancelled and result.completed:
            try:
                result.comparison, dynamics, result.positions = update_history(
                    app_data_dir() / "history.sqlite", settings,
                    result.started_at, result.completed, result.products)
                _log_changes(reporter, result.comparison)
            except sqlite3.Error as exc:  # the report matters more than the history
                log.exception("History update failed")
                reporter.warn(f"Не удалось обновить историю цен: {exc}")
        result.competition = compare_with_rivals(result.products,
                                                 parse_own(settings.own_ids, settings.own_names))
        _log_competition(reporter, result.competition)
        path = Path(settings.output_dir) / build_file_name(settings, result.started_at)
        export_to_excel(path, settings, result.products, result.reviews, result.started_at, result.comparison,
                        dynamics, result.positions, result.competition)
        result.path = path
        reporter.log(f"Файл сохранён: {path}", "success")
        if notify(settings, result, reporter.warn):
            reporter.log("Отчёт отправлен в Telegram")
    elif not result.errors and not result.cancelled:
        reporter.warn("Товары не найдены — файл не создан.")
    reporter.set_span(1, 1)
    reporter.progress(1, 1, "Готово" if not result.cancelled else "Остановлено")
    return result
