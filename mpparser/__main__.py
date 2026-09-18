"""Command-line interface: ``python -m mpparser --query "наушники" --wb --ozon``.

Handy for automation and scheduled runs; the GUI (``app.py``) covers the same options.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .fields import PRODUCT_FIELDS, REVIEW_FIELDS
from .marketplaces import Reporter
from .regions import DEFAULT_REGION, WB_REGIONS
from .runner import run
from .settings import InputMode, ParseSettings, SortOrder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mpparser", description="Сбор товаров, цен и отзывов с Wildberries и Ozon в Excel"
    )
    parser.add_argument("--wb", action="store_true", help="собирать с Wildberries")
    parser.add_argument("--ozon", action="store_true", help="собирать с Ozon")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("-q", "--query", help="поисковый запрос")
    source.add_argument("--ids", action="store_true", help="режим артикулов (см. --wb-ids / --ozon-ids)")
    parser.add_argument("--wb-ids", default="", help="артикулы или ссылки WB через запятую")
    parser.add_argument("--ozon-ids", default="", help="артикулы или ссылки Ozon через запятую")
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
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    settings = ParseSettings(
        marketplaces=[k for k, on in (("wb", args.wb), ("ozon", args.ozon)) if on] or ["wb", "ozon"],
        mode=InputMode.QUERY if args.query else InputMode.IDS,
        query=args.query or "",
        wb_ids=args.wb_ids,
        ozon_ids=args.ozon_ids,
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
    if problems := settings.validate():
        print("\n".join(problems), file=sys.stderr)
        return 2

    last_text = ""

    def on_progress(fraction: float, text: str) -> None:
        nonlocal last_text
        if text != last_text:
            print(f"\r[{fraction:6.1%}] {text:<60}", end="", flush=True)
            last_text = text

    def on_log(level: str, message: str) -> None:
        print(f"\r{'!' if level in ('warning', 'error') else '•'} {message:<70}", flush=True)

    result = asyncio.run(run(settings, Reporter(on_log, on_progress)))
    print()
    return 0 if result.path and not result.errors else 1


if __name__ == "__main__":
    sys.exit(main())
