"""Refresh tests/fixtures from live responses.

Marketplaces change their JSON from time to time; run this script after such a change, review the diff
and fix the parsers until the tests pass again.

Usage: python tools/dump_fixtures.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpparser.browser import Browser
from mpparser.marketplaces import OzonParser, WildberriesParser
from mpparser.marketplaces.base import Reporter
from mpparser.marketplaces.ozon import widgets
from mpparser.marketplaces.wildberries import DETAIL_PATH, FEEDBACK_HOSTS
from mpparser.settings import ParseSettings, app_data_dir

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
QUERY = "наушники"
OZON_WIDGET_PREFIXES = ("webProductHeading", "webPrice", "webReviewProductScore", "webCurrentSeller",
                        "webGallery", "breadCrumbs", "webListReviews", "tileGridDesktop",
                        "infiniteVirtualPaginator")


def save(name: str, data: object) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {name}")


def trim_ozon_page(page: dict, reviews_limit: int = 2, tiles_limit: int = 2) -> dict:
    states = {}
    for key, raw in (page.get("widgetStates") or {}).items():
        if not key.startswith(OZON_WIDGET_PREFIXES):
            continue
        state = json.loads(raw) if isinstance(raw, str) else raw
        if "reviews" in state:
            state["reviews"] = state["reviews"][:reviews_limit]
        if "items" in state and isinstance(state["items"], list):
            state["items"] = state["items"][:tiles_limit]
        states[key] = json.dumps(state, ensure_ascii=False)
    return {"widgetStates": states, "layoutTrackingInfo": page.get("layoutTrackingInfo"), "seo": page.get("seo")}


async def main() -> None:
    settings = ParseSettings(query=QUERY)
    reporter = Reporter(on_log=lambda _level, message: print(f"  {message}"))
    async with Browser(app_data_dir() / "browser-profile", headless=True) as browser:
        wb = WildberriesParser(browser, settings, reporter)
        await wb.prepare()
        search = await wb._api(wb.search_path, {"dest": -1257403, "page": 1, "query": QUERY,
                                                "resultset": "catalog", "sort": "popular"})
        save("wb_search.json", {"total": search.get("total"), "products": search["products"][:3]})
        article = str(search["products"][0]["id"])
        detail = await wb._api(DETAIL_PATH, {"dest": -1257403, "nm": article})
        save("wb_detail.json", {"products": detail["products"][:1]})
        root = search["products"][0]["root"]
        _, feedbacks = await browser.get_json(f"{FEEDBACK_HOSTS[0]}/feedbacks/v1/{root}")
        save("wb_feedbacks.json", {"feedbackCount": feedbacks.get("feedbackCount"),
                                   "feedbacks": (feedbacks.get("feedbacks") or [])[:3]})

        ozon = OzonParser(browser, settings, reporter)
        await ozon.prepare()
        ozon_search = await ozon._page_json(f"/search/?text={quote(QUERY)}&from_global=true")
        save("ozon_search.json", trim_ozon_page(ozon_search))
        grid = (widgets(ozon_search, "tileGridDesktop") or [{}])[0]
        sku = grid["items"][0]["sku"]
        save("ozon_product.json", trim_ozon_page(await ozon._page_json(f"/product/{sku}/")))
        save("ozon_reviews.json", trim_ozon_page(await ozon._page_json(f"/product/{sku}/reviews/")))


if __name__ == "__main__":
    asyncio.run(main())
