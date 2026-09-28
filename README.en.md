# Marketplace Parser — products, prices and reviews from marketplaces into Excel

[![Tests](https://github.com/krein15/marketplace-parser/actions/workflows/tests.yml/badge.svg)](https://github.com/krein15/marketplace-parser/actions/workflows/tests.yml)

A Windows desktop app that collects product cards, prices and reviews from **Wildberries, Ozon and Yandex
Market** into a single formatted Excel report — and, when run daily, shows what went up, what went down and
what disappeared from the search results. It ships as an `.exe`, so the client does not need Python.

[Русская версия](README.md) · [Example report](docs/example/example-report.xlsx) · [Download](https://github.com/krein15/marketplace-parser/releases/latest)

![Application window](docs/screenshots/app-light.png)

## Features

- **Three marketplaces in one report.** Wildberries, Ozon and Yandex Market separately or together, with the
  same set of columns.
- **Three ways to say what to collect.** A search query, a list of article numbers and product links, or a link
  to search results with the filters already set on the site.
- **Price monitoring.** A task remembers the settings and runs every day through Windows Task Scheduler.
  The report then gains the "Изменения" (cheaper, dearer, new, gone) and "Динамика цен" (price history) sheets.
- **Filters.** Price for every marketplace; WB delivery region (25 cities); Market rating from 4.0 and delivery
  time; the delivery address for Ozon and Market is chosen once in the parser's browser.
- **Reviews.** Up to 1000 recent reviews per product: date, score, text, pros, cons, seller reply.
- **Selectable columns.** 18 product fields and 13 review fields, switched on and off with checkboxes.
- **Extensible.** A marketplace is a module: a new one can be added without touching the program
  (see "Adding a marketplace").
- **Unattended runs.** Progress, log, stop at any moment (whatever was collected is still saved), a new file per run.
- **Command line** for scheduled runs — the same `.exe` accepts arguments.

![The program at work](docs/screenshots/run.gif)

| Filters | Monitoring |
|---|---|
| ![Filters](docs/screenshots/app-filters.png) | ![Monitoring](docs/screenshots/app-monitoring.png) |

Excel columns are picked with checkboxes; the dark theme is switched in the header:

![Column picker](docs/screenshots/app-fields.png)

## What the report contains

**Products** — marketplace, article, position in search results, name, brand, seller and seller rating, price,
card price (Ozon and Market), price before discount, discount %, rating, review count, stock (Ozon), category,
photo, link, collection timestamp.

**Reviews** — marketplace, article, product, date, score, author, text, pros, cons, product variant, photo
count, "useful" votes, seller reply (WB).

**Summary** — run parameters and active filters, per-marketplace totals (count, minimum, average, median and
maximum price, average discount and rating), top 10 brands and sellers, a price-distribution chart.

**Changes** and **Price history** appear from the second run of a task: what got cheaper or dearer (with the
difference in ₽ and %), what appeared, what disappeared, and the prices of the last 14 runs.

## How it works

The marketplaces put their internal APIs behind anti-bot protection bound to the browser fingerprint, so the
parser does not forge requests — it works through a real browser:

1. **Patchright** (a Playwright build that resists automation detection) launches the Chrome or Edge already
   installed on the machine in headless mode and opens the site.
2. The browser passes the site's check: a cookie token on Wildberries, the Antibot Challenge page on Ozon,
   a plain page load on Market.
3. Requests are then issued from inside the opened page, so they carry the right cookies, headers and TLS
   fingerprint. Wildberries returns 100 products per page, Ozon returns the JSON of its own pages, Market
   returns the HTML of search results and cards.
4. Everything is normalised into single `Product` and `Review` models and exported with **openpyxl**.

The browser profile is kept between runs: checks pass faster and the delivery address is chosen only once.

## Adding a marketplace

The core knows no marketplace by name. A marketplace is described by one object: key, title, colour, parser
class, how its links are recognised, the Excel columns it fills and the filters it offers. The window, the
command-line flags, the report and the monitoring history key are all built from that description.

```python
MARKETPLACE = Marketplace(
    key="wb",
    title="Wildberries",
    parser=WildberriesParser,
    item_patterns=(ITEM_LINK,),
    options=(Option(key="region", title="Регион доставки", kind="choice", default="Москва", choices=REGIONS),),
)
```

Built-in marketplaces declare themselves in their own modules. A separate package plugs in through an entry
point — install it and the marketplace appears in the program, remove it and it is gone while everything else
keeps working:

```toml
[project.entry-points."mpparser.marketplaces"]
avito = "mpparser_avito:MARKETPLACE"
```

**Avito** is built as such a module and is shipped separately, on request: its sellers are private persons,
and their names and reviews are personal data under Russian law. Get in touch if you need it.

## System requirements

- Windows 10 or 11, 64-bit. The app does not run on Windows 7 or 8: it is built on Python 3.13 and a current
  Chrome, neither of which supports those versions any more.
- Google Chrome or Microsoft Edge (preinstalled on Windows 10 and 11) — the app drives the browser that is
  already installed instead of bundling one.
- Access to the marketplaces from a Russian IP address.
- About 400 MB of free disk space.

## First launch: Windows warning and antivirus

The build is not signed with a developer certificate (those cost money), so Windows SmartScreen shows
"Windows protected your PC" on the first launch: click "More info" → "Run anyway". Once is enough.

An antivirus may ask for permission on every action, because the app starts a browser (`chrome.exe` or
`msedge.exe`) and the `node.exe` helper that drives it. To allow everything once, add the application folder
to the exclusions (Windows Security → Virus & threat protection → Manage settings → Exclusions → Add → Folder).

## Installation

### Built application

1. Download `MarketplaceParser-*-windows.zip` from [Releases](https://github.com/krein15/marketplace-parser/releases/latest) and unpack it anywhere.
2. Run `MarketplaceParser.exe`.

### From source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### Command line

```bash
python -m mpparser --wb --ozon --ym -q "wireless headphones" -n 100 -r 20 --price-min 500 --wb-region Казань
```

A saved monitoring task runs by its name — this is exactly how Windows Task Scheduler starts it:

```bash
MarketplaceParser.exe --task "Наушники каждый день"
```

See `python -m mpparser --help` for all options. Flags for marketplaces and their filters are generated from
the descriptions, so an installed Avito module brings its own `--avito`, `--avito-city` and the rest.

### Building the .exe

```bash
pip install -r requirements-dev.txt
python -m PyInstaller MarketplaceParser.spec --noconfirm
```

The result is the `dist/MarketplaceParser` folder (about 145 MB; no browser is bundled).

## Tests

Parsing is tested against saved marketplace responses, the export against a real Excel file, and the
marketplace contract against a made-up marketplace registered as an external module:

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

Tests and the linter run on every push in GitHub Actions — the badge at the top shows the result. When a
marketplace changes its responses, refresh the fixtures with `python tools/dump_fixtures.py <marketplace>`.

## Project layout

```
mpparser/
  plugins.py            the marketplace contract and registry: description, columns, filters, discovery
  browser.py            Chrome/Edge startup, requests from inside a page
  marketplaces/
    base.py             parser interface, progress reporting, cancellation
    wildberries.py      search, cards, reviews, photos, region
    ozon.py             search, cards, reviews, card price
    yandex_market.py    search results, cards, reviews, filters
  monitoring.py         price history, run comparison, "Changes" and "Price history"
  tasks.py, scheduler.py  tasks and Windows Task Scheduler
  export/excel.py       report sheets
  fields.py             registry of columns
  runner.py             the whole collection scenario
  settings.py           settings and their persistence
  gui/                  CustomTkinter window
tools/                  icon, screenshots, GIF recording, fixture and region refresh
tests/                  tests and fixtures
```

## Limitations

- **Wildberries stock** is not exported: for anonymous visitors the site reports the same placeholder quantity
  for every product.
- **Brand, seller, category and card price** on Ozon and Market only exist on the product page. With those
  columns enabled the parser opens every card, which is slower.
- **Market's region** is detected by IP unless you sign in; prices barely depend on it, availability and
  delivery times do.
- **"Gone from search results"** often means a change of promoted placements rather than a delisting — the
  report says so.
- **Marketplaces change their markup.** Every parser is covered by tests on saved responses, so a break shows
  up as a failing test.
- Only publicly visible data is collected, with pauses between requests. Reviews contain the public display
  names of their authors, so use the exported data lawfully and for its intended purpose.

## Roadmap

- Telegram notifications about notable price changes.
- Export to Google Sheets.
- More marketplaces as separate modules.

## Author

**Nikolay Bondarenko** — Python developer: web scraping and automation.
Need a similar scraper or a custom version? Get in touch.

- Telegram: [@krein1](https://t.me/krein1)
- Email: [reinkolya@gmail.com](mailto:reinkolya@gmail.com)
- GitHub: [krein15](https://github.com/krein15)

Licensed under the [MIT License](LICENSE).
