"""Runs a full collection job: marketplaces → products → extra fields → reviews → Excel."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .browser import Browser, BrowserError
from .export.excel import build_file_name, export_to_excel
from .inputs import collect_ids
from .marketplaces import PARSERS, Cancelled, ParserError, Reporter
from .models import Product, Review
from .settings import InputMode, ParseSettings, app_data_dir

log = logging.getLogger(__name__)

# Share of the progress bar given to each stage of one marketplace.
STAGES = {"products": 0.45, "enrich": 0.2, "reviews": 0.35}


@dataclass
class RunResult:
    path: Path | None = None
    products: list[Product] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    started_at: datetime = field(default_factory=datetime.now)


async def run(settings: ParseSettings, reporter: Reporter) -> RunResult:
    result = RunResult()
    ids = collect_ids({mp: getattr(settings, f"{mp}_ids") for mp in settings.marketplaces})
    if settings.mode == InputMode.IDS and ids.invalid:
        reporter.warn(f"Пропущены нераспознанные строки: {', '.join(ids.invalid[:10])}")

    field_keys = set(settings.product_fields)
    share = 1 / len(settings.marketplaces)
    try:
        reporter.log("Запускаю браузер…")
        async with Browser(app_data_dir() / "browser-profile", headless=not settings.show_browser) as browser:
            for index, key in enumerate(settings.marketplaces):
                parser = PARSERS[key](browser, settings, reporter)
                base = index * share
                try:
                    reporter.set_span(base, base)
                    reporter.progress(0, 1, f"{parser.title}: подготовка")
                    await parser.prepare()

                    reporter.set_span(base, base + share * STAGES["products"])
                    if settings.mode == InputMode.QUERY:
                        products = await parser.search(settings.query.strip(), settings.max_products, settings.sort)
                    else:
                        articles = ids.for_marketplace(key)
                        if not articles:
                            continue
                        products = await parser.products_by_ids(articles)
                    reporter.log(f"{parser.title}: собрано товаров — {len(products)}")
                    result.products += products

                    reporter.set_span(base + share * STAGES["products"],
                                      base + share * (STAGES["products"] + STAGES["enrich"]))
                    await parser.enrich(products, field_keys)

                    if settings.collect_reviews:
                        reporter.set_span(base + share * (1 - STAGES["reviews"]), base + share)
                        with_reviews = [p for p in products if p.reviews_count != 0]
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
        path = Path(settings.output_dir) / build_file_name(settings, result.started_at)
        export_to_excel(path, settings, result.products, result.reviews, result.started_at)
        result.path = path
        reporter.log(f"Файл сохранён: {path}", "success")
    elif not result.errors and not result.cancelled:
        reporter.warn("Товары не найдены — файл не создан.")
    reporter.set_span(1, 1)
    reporter.progress(1, 1, "Готово" if not result.cancelled else "Остановлено")
    return result
