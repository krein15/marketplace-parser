"""Refresh tests/fixtures from live responses.

Marketplaces change their JSON from time to time; run this script after such a change, review the diff
and fix the parsers until the tests pass again.

Usage: python tools/dump_fixtures.py [wb] [ozon] [ym] [avito]   (all marketplaces when none is given)

Avito needs a browser window; names of sellers and review authors are replaced, as they are private persons.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpparser.browser import Browser
from mpparser.marketplaces import AvitoParser, OzonParser, WildberriesParser, YandexMarketParser
from mpparser.marketplaces.avito import _ITEM_JS, _PAGE_JS, _REVIEWS_JS, _SEARCH_JS, absolute
from mpparser.marketplaces.base import Reporter
from mpparser.marketplaces.ozon import widgets
from mpparser.marketplaces.wildberries import DETAIL_PATH, FEEDBACK_HOSTS
from mpparser.marketplaces.yandex_market import _SNIPPETS_JS, FOREIGN_BLOCKS
from mpparser.marketplaces.yandex_market import search_url as ym_search_url
from mpparser.settings import ParseSettings, app_data_dir

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
QUERY = "наушники"
OZON_WIDGET_PREFIXES = ("webProductHeading", "webPrice", "webReviewProductScore", "webCurrentSeller",
                        "webGallery", "breadCrumbs", "webListReviews", "tileGridDesktop",
                        "infiniteVirtualPaginator")
YM_COLLECTIONS = {"productCardMeta", "price", "shopInfo", "reviewV2"}
YM_REVIEW_KEYS = {"id", "author", "rating", "descriptor", "media", "votes", "comment", "pro", "contra",
                  "liteOffer", "analyticsData"}


def _strip_ym_entity(name: str, entity: dict) -> dict:
    """Reviews carry kilobytes of tracking data; keep the keys the parser reads."""
    if name != "reviewV2":
        return entity
    stripped = {key: value for key, value in entity.items() if key in YM_REVIEW_KEYS}
    stripped["author"] = {"nickname": (entity.get("author") or {}).get("nickname", "")}
    return stripped


def save(name: str, data: object) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
    (FIXTURES / name).write_text(text, encoding="utf-8")
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


def trim_ym_page(page_html: str, entities_limit: int = 3) -> str:
    """Keep only JSON-LD and the apiary collections the parser reads: 2 MB of HTML → a few KB."""
    parts = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>.*?</script>', page_html, re.S)
    taken: dict[str, int] = {}
    for raw in re.findall(r'<noframes data-apiary="patch">(.*?)</noframes>', page_html, re.S):
        try:
            patch = json.loads(html.unescape(raw))
        except ValueError:
            continue
        kept = {}
        for name, entities in (patch.get("collections") or {}).items():
            if name not in YM_COLLECTIONS or not isinstance(entities, dict):
                continue
            room = entities_limit - taken.get(name, 0)
            chosen = {key: _strip_ym_entity(name, value) for key, value in list(entities.items())[:max(room, 0)]}
            if chosen:
                kept[name] = chosen
                taken[name] = taken.get(name, 0) + len(chosen)
        if kept:
            payload = html.escape(json.dumps({"collections": kept}, ensure_ascii=False), quote=False)
            parts.append(f'<noframes data-apiary="patch">{payload}</noframes>')
    return "<html><body>\n" + "\n".join(parts) + "\n</body></html>\n"


async def dump_wb(browser: Browser, settings: ParseSettings, reporter: Reporter) -> None:
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


async def dump_ozon(browser: Browser, settings: ParseSettings, reporter: Reporter) -> None:
    ozon = OzonParser(browser, settings, reporter)
    await ozon.prepare()
    ozon_search = await ozon._page_json(f"/search/?text={quote(QUERY)}&from_global=true")
    save("ozon_search.json", trim_ozon_page(ozon_search))
    grid = (widgets(ozon_search, "tileGridDesktop") or [{}])[0]
    sku = grid["items"][0]["sku"]
    save("ozon_product.json", trim_ozon_page(await ozon._page_json(f"/product/{sku}/")))
    save("ozon_reviews.json", trim_ozon_page(await ozon._page_json(f"/product/{sku}/reviews/")))


async def dump_ym(browser: Browser, settings: ParseSettings, reporter: Reporter) -> None:
    ym = YandexMarketParser(browser, settings, reporter)
    await ym.prepare()
    assert ym.page is not None
    await ym._goto(ym_search_url(QUERY, settings.sort, settings))
    await ym.page.wait_for_selector('[data-zone-name="productSnippet"]', timeout=30_000)
    snippets = await ym.page.evaluate(_SNIPPETS_JS, FOREIGN_BLOCKS)
    save("ym_snippets.json", snippets[:3])
    rated = next((s for s in snippets if "Оценок" in s["rating"]), snippets[0])
    sku = re.search(r"/card/[^/?#]+/(\d+)", rated["href"]).group(1)
    save("ym_card.html", trim_ym_page(await ym._html(f"/card/x/{sku}")))
    save("ym_reviews.html", trim_ym_page(await ym._html(f"/card/x/{sku}/reviews")))


async def dump_avito(browser: Browser, settings: ParseSettings, reporter: Reporter) -> None:
    avito = AvitoParser(browser, settings, reporter)
    await avito.prepare()
    assert avito.page is not None
    await avito._open(f"https://www.avito.ru/all?q={quote(QUERY)}")
    await avito._scroll_through()
    items = (await avito.page.evaluate(_SEARCH_JS))[:3]
    for number, item in enumerate(items, 1):
        name = item["seller"].split("\n", 1)[0]
        item["seller"] = item["seller"].replace(name, f"Продавец {number}", 1) if name else ""
    save("avito_search.json", {"page": await avito.page.evaluate(_PAGE_JS), "items": items})
    await avito.pause()
    await avito._open(absolute(items[0]["href"]))
    item = await avito.page.evaluate(_ITEM_JS)
    if item["seller"]:
        item["seller_info"] = item["seller_info"].replace(item["seller"], "Продавец 1")
        item["seller"] = "Продавец 1"
    save("avito_item.json", item)
    if item["seller_href"]:
        await avito.pause()
        await avito._open(absolute(item["seller_href"]))
        reviews = (await avito.page.evaluate(_REVIEWS_JS))[:3]
        for number, review in enumerate(reviews, 1):
            review["author"] = f"Покупатель {number}"
        save("avito_reviews.json", reviews)


DUMPERS = {"wb": dump_wb, "ozon": dump_ozon, "ym": dump_ym, "avito": dump_avito}


async def main(keys: list[str]) -> None:
    settings = ParseSettings(query=QUERY)
    reporter = Reporter(on_log=lambda _level, message: print(f"  {message}"))
    async with Browser(app_data_dir() / "browser-profile", headless="avito" not in keys) as browser:
        for key in keys:
            await DUMPERS[key](browser, settings, reporter)


if __name__ == "__main__":
    wanted = [key for key in sys.argv[1:] if key in DUMPERS] or list(DUMPERS)
    asyncio.run(main(wanted))
