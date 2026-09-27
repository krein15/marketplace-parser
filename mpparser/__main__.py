"""Command-line interface: ``python -m mpparser --query "наушники" --wb --ozon --ym``.

Handy for automation; the GUI (``app.py``) covers the same options. Saved monitoring tasks run with
``--task "<name>"`` — that is what Windows Task Scheduler starts every day. Such runs log to
``logs/tasks.log``, as the built .exe has no console.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from .fields import PRODUCT_FIELDS, REVIEW_FIELDS
from .marketplaces import Reporter
from .regions import DEFAULT_REGION, WB_REGIONS
from .runner import run
from .settings import MARKETPLACES, InputMode, ParseSettings, SellerType, SortOrder, app_data_dir
from .tasks import get_task, load_tasks, mark_run

log = logging.getLogger("mpparser.cli")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mpparser", description="Сбор товаров, цен и отзывов с Wildberries, Ozon, Яндекс Маркета и Авито в Excel"
    )
    parser.add_argument("--wb", action="store_true", help="собирать с Wildberries")
    parser.add_argument("--ozon", action="store_true", help="собирать с Ozon")
    parser.add_argument("--ym", action="store_true", help="собирать с Яндекс Маркета")
    parser.add_argument("--avito", action="store_true", help="собирать с Авито")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-q", "--query", help="поисковый запрос")
    source.add_argument("--ids", action="store_true", help="режим артикулов (см. --wb-ids / --ozon-ids / --ym-ids)")
    source.add_argument("--task", metavar="НАЗВАНИЕ", help="запустить сохранённое задание мониторинга")
    source.add_argument("--list-tasks", action="store_true", help="показать сохранённые задания")
    parser.add_argument("--wb-ids", default="", help="артикулы или ссылки WB через запятую")
    parser.add_argument("--ozon-ids", default="", help="артикулы или ссылки Ozon через запятую")
    parser.add_argument("--ym-ids", default="", help="SKU или ссылки Яндекс Маркета через запятую")
    parser.add_argument("--avito-ids", default="", help="ссылки на объявления или на выдачу Авито через запятую")
    filters = parser.add_argument_group("фильтры")
    filters.add_argument("--price-min", type=int, help="цена от, ₽ (все площадки)")
    filters.add_argument("--price-max", type=int, help="цена до, ₽ (все площадки)")
    filters.add_argument("--ym-rating4", action="store_true", help="Маркет: рейтинг от 4.0")
    filters.add_argument("--ym-delivery", type=int, choices=[3, 7], default=0, help="Маркет: срок доставки, дней")
    filters.add_argument("--avito-city", action="append", metavar="ГОРОД",
                         help="Авито: город или регион (можно несколько раз), по умолчанию вся Россия")
    filters.add_argument("--avito-seller", choices=[s.value for s in SellerType], default=SellerType.ALL.value)
    filters.add_argument("--avito-delivery", action="store_true", help="Авито: только с Авито Доставкой")
    filters.add_argument("--avito-title-only", action="store_true", help="Авито: искать только в названиях")
    parser.add_argument("-n", "--max-products", type=int, default=100)
    parser.add_argument("-r", "--reviews", type=int, default=0, metavar="N", help="отзывов на товар (0 — не собирать)")
    parser.add_argument("--sort", choices=[s.value for s in SortOrder], default=SortOrder.POPULAR.value)
    parser.add_argument("--region", choices=list(WB_REGIONS), default=DEFAULT_REGION, metavar="ГОРОД")
    parser.add_argument("--product-fields",
                        help="ключи колонок через запятую: " + ",".join(f.key for f in PRODUCT_FIELDS))
    parser.add_argument("--review-fields",
                        help="ключи колонок через запятую: " + ",".join(f.key for f in REVIEW_FIELDS))
    parser.add_argument("-o", "--output-dir", help="папка для Excel-файла")
    parser.add_argument("--show-browser", action="store_true", help="показывать окно браузера")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_tasks:
        for task in load_tasks():
            print(f"{task.name}: {task.describe()}")
        return 0
    if args.task:
        return run_task(args.task, show_browser=args.show_browser, verbose=args.verbose)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    settings = ParseSettings(
        marketplaces=[k for k in MARKETPLACES if getattr(args, k)] or ["wb", "ozon"],
        mode=InputMode.QUERY if args.query else InputMode.IDS,
        query=args.query or "",
        wb_ids=args.wb_ids,
        ozon_ids=args.ozon_ids,
        ym_ids=args.ym_ids,
        avito_ids=args.avito_ids,
        price_min=args.price_min,
        price_max=args.price_max,
        ym_rating_4=args.ym_rating4,
        ym_delivery_days=args.ym_delivery,
        avito_seller=SellerType(args.avito_seller),
        avito_delivery=args.avito_delivery,
        avito_title_only=args.avito_title_only,
        max_products=args.max_products,
        sort=SortOrder(args.sort),
        collect_reviews=args.reviews > 0,
        max_reviews=max(args.reviews, 1),
        region=args.region,
        show_browser=args.show_browser,
        open_when_done=False,
    )
    if args.product_fields:
        settings.product_fields = args.product_fields.split(",")
    if args.review_fields:
        settings.review_fields = args.review_fields.split(",")
    if args.output_dir:
        settings.output_dir = args.output_dir
    if args.avito_city:
        settings.avito_locations = args.avito_city
    if problems := settings.validate():
        print("\n".join(problems), file=sys.stderr)
        return 2
    result = asyncio.run(run(settings, console_reporter()))
    print()
    return 0 if result.path and not result.errors else 1


def console_reporter() -> Reporter:
    last_text = ""

    def on_progress(fraction: float, text: str) -> None:
        nonlocal last_text
        if text != last_text:
            print(f"\r[{fraction:6.1%}] {text:<60}", end="", flush=True)
            last_text = text

    def on_log(level: str, message: str) -> None:
        print(f"\r{'!' if level in ('warning', 'error') else '•'} {message:<70}", flush=True)
        log.log(logging.WARNING if level in ("warning", "error") else logging.INFO, message)

    return Reporter(on_log, on_progress)


def run_task(name: str, show_browser: bool = False, verbose: bool = False) -> int:
    """Run a saved monitoring task; everything is logged to logs/tasks.log."""
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / "tasks.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[handler])

    task = get_task(name)
    if task is None:
        log.error("Задание «%s» не найдено", name)
        print(f"Задание «{name}» не найдено. Сохранённые задания: python -m mpparser --list-tasks", file=sys.stderr)
        return 2
    settings = task.settings
    settings.open_when_done = False
    settings.show_browser = settings.show_browser or show_browser
    if problems := settings.validate():
        log.error("Задание «%s» не запущено: %s", name, " ".join(problems))
        print("\n".join(problems), file=sys.stderr)
        return 2
    log.info("Запуск задания «%s»", task.name)
    result = asyncio.run(run(settings, console_reporter()))
    print()
    mark_run(task.name, datetime.now(), result.path)
    log.info("Задание «%s» завершено: %s", task.name, result.path or "файл не создан")
    return 0 if result.path and not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
