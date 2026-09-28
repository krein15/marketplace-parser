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

from . import plugins
from .fields import product_fields, review_fields
from .marketplaces import Reporter
from .runner import run
from .settings import DEFAULT_MARKETPLACES, InputMode, ParseSettings, SortOrder, app_data_dir
from .tasks import get_task, load_tasks, mark_run

log = logging.getLogger("mpparser.cli")


def build_parser() -> argparse.ArgumentParser:
    """Flags for the marketplaces and filters that are installed right now."""
    marketplaces = plugins.registry()
    names = ", ".join(mp.title for mp in marketplaces.values())
    parser = argparse.ArgumentParser(
        prog="mpparser", description=f"Сбор товаров, цен и отзывов в Excel. Площадки: {names}"
    )
    for key, marketplace in marketplaces.items():
        parser.add_argument(f"--{key}", action="store_true", help=f"собирать с {marketplace.title}")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-q", "--query", help="поисковый запрос")
    ids_flags = " / ".join(f"--{key}-ids" for key in marketplaces)
    source.add_argument("--ids", action="store_true", help=f"режим артикулов (см. {ids_flags})")
    source.add_argument("--task", metavar="НАЗВАНИЕ", help="запустить сохранённое задание мониторинга")
    source.add_argument("--list-tasks", action="store_true", help="показать сохранённые задания")
    for key, marketplace in marketplaces.items():
        parser.add_argument(f"--{key}-ids", default="", dest=f"ids_{key}",
                            help=f"артикулы или ссылки {marketplace.title} через запятую")

    filters = parser.add_argument_group("фильтры")
    filters.add_argument("--price-min", type=int, help="цена от, ₽ (все площадки)")
    filters.add_argument("--price-max", type=int, help="цена до, ₽ (все площадки)")
    for key, marketplace in marketplaces.items():
        for option in marketplace.options:
            _add_option(filters, key, marketplace.title, option)

    parser.add_argument("-n", "--max-products", type=int, default=100)
    parser.add_argument("-r", "--reviews", type=int, default=0, metavar="N", help="отзывов на товар (0 — не собирать)")
    parser.add_argument("--sort", choices=[s.value for s in SortOrder], default=SortOrder.POPULAR.value)
    parser.add_argument("--product-fields",
                        help="ключи колонок через запятую: " + ",".join(f.key for f in product_fields()))
    parser.add_argument("--review-fields",
                        help="ключи колонок через запятую: " + ",".join(f.key for f in review_fields()))
    parser.add_argument("-o", "--output-dir", help="папка для Excel-файла")
    parser.add_argument("--show-browser", action="store_true", help="показывать окно браузера")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def option_dest(marketplace: str, option_key: str) -> str:
    return f"opt_{marketplace}_{option_key}"


def _add_option(group: argparse._ArgumentGroup, key: str, title: str, option: plugins.Option) -> None:
    """One command-line flag for one marketplace filter. Buttons ("setup") have nothing to pass."""
    if option.kind == "setup":
        return
    flag, dest = option.flag(key), option_dest(key, option.key)
    help_text = f"{title}: {option.title.lower()}"
    if option.kind == "switch":
        group.add_argument(flag, dest=dest, action="store_true", help=help_text)
    elif option.kind == "choice":
        values = [value for value, _ in option.choices]
        group.add_argument(flag, dest=dest, default=None, choices=values,
                           type=int if isinstance(option.default, int) else str,
                           metavar=option.cli_metavar or None, help=help_text)
    elif option.kind == "multi":
        group.add_argument(flag, dest=dest, action="append", metavar=option.cli_metavar or "ЗНАЧЕНИЕ",
                           help=f"{help_text} (можно указать несколько раз)")


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

    marketplaces = plugins.registry()
    settings = ParseSettings(
        marketplaces=[k for k in marketplaces if getattr(args, k)] or list(DEFAULT_MARKETPLACES),
        mode=InputMode.QUERY if args.query else InputMode.IDS,
        query=args.query or "",
        price_min=args.price_min,
        price_max=args.price_max,
        max_products=args.max_products,
        sort=SortOrder(args.sort),
        collect_reviews=args.reviews > 0,
        max_reviews=max(args.reviews, 1),
        show_browser=args.show_browser,
        open_when_done=False,
    )
    for key, marketplace in marketplaces.items():
        if text := getattr(args, f"ids_{key}", ""):
            settings.set_ids(key, text)
        for option in marketplace.options:
            value = getattr(args, option_dest(key, option.key), None)
            if value not in (None, False):
                settings.set_option(key, option.key, value)
    if args.product_fields:
        settings.product_fields = args.product_fields.split(",")
    if args.review_fields:
        settings.review_fields = args.review_fields.split(",")
    if args.output_dir:
        settings.output_dir = args.output_dir
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
