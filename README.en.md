# Marketplace Parser — products, prices and reviews from Wildberries and Ozon into Excel

A Windows desktop app that collects product cards, prices and reviews from the two largest Russian
marketplaces — Wildberries and Ozon — into a single formatted Excel report. It ships as an `.exe`, so the
client does not need Python.

[Русская версия](README.md) · [Example report](docs/example/example-report-wb-ozon.xlsx)

![Application window](docs/screenshots/app-light.png)

## Features

- **Both marketplaces in one report.** Wildberries and Ozon separately or together, with the same columns.
- **Two input modes.** A search query ("wireless headphones") or a list of article numbers and product links —
  for tracking the prices of specific products, e.g. a competitor's catalogue.
- **Reviews.** Up to 1000 recent reviews per product: date, score, text, pros, cons, seller reply.
- **Selectable columns.** 18 product fields and 13 review fields, switched on and off with checkboxes.
- **Delivery region.** 25 cities for Wildberries, whose prices and delivery times depend on the region.
- **Formatted Excel.** "Summary", "Products" and "Reviews" sheets: auto-filters, frozen headers, currency
  formats, clickable product and photo links, per-marketplace and per-brand statistics, a price-distribution chart.
- **Unattended runs.** Progress, log, stop at any moment (whatever was collected is still saved), a new file per run.
- **Command line** for scheduled runs — the same `.exe` accepts arguments.

| Collecting | Finished |
|---|---|
| ![Collecting](docs/screenshots/app-running.png) | ![Finished](docs/screenshots/app-done.png) |

## What the report contains

**Products sheet:** marketplace, article, position in search results, name, brand, seller, seller rating, price,
card price (Ozon), price before discount, discount %, rating, review count, stock (Ozon), category, photo, link,
collection timestamp.

**Reviews sheet:** marketplace, article, product, date, score, author, text, pros, cons, product variant,
photo count, "useful" votes, seller reply (Wildberries).

**Summary sheet:** run parameters; per marketplace — product count, minimum, average, median and maximum price,
average discount and rating; top 10 brands and sellers; a price-distribution chart.

## How it works

Both marketplaces put their internal APIs behind anti-bot protection bound to the browser fingerprint, so the
parser does not forge requests — it works through a real browser:

1. **Patchright** (a Playwright build that resists automation detection) launches the Chrome or Edge already
   installed on the machine in headless mode and opens the site.
2. The browser passes the site's check: the `x_wbaas_token` cookie on Wildberries, the Antibot Challenge page on Ozon.
3. API calls are then issued with `fetch` from inside the opened page, so they carry the right cookies, headers
   and TLS fingerprint. Wildberries returns 100 products per page and up to 50 articles per request; Ozon returns
   the JSON of its own pages.
4. Open endpoints are called directly: Wildberries reviews (`feedbacks*.wb.ru`) and the CDN map for photo links.
5. Everything is normalised into single `Product` and `Review` models and exported with **openpyxl**.

The browser profile is kept between runs, so the checks pass faster and the Ozon delivery address only has to be
chosen once (the "Выбрать адрес Ozon…" button).

## Installation

### Built application

1. Download `MarketplaceParser-*-windows.zip` from [Releases](https://github.com/krein15/marketplace-parser/releases/latest) and unpack it anywhere.
2. Run `MarketplaceParser.exe`.

Requires Google Chrome or Microsoft Edge (preinstalled on Windows) and access to the marketplaces from a Russian IP.

### From source

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

### Command line

```bash
python -m mpparser --wb --ozon -q "wireless headphones" -n 100 -r 20 --region Москва -o "D:\Reports"
```

The built `.exe` takes the same arguments, which makes it usable from the Windows Task Scheduler:

```bash
MarketplaceParser.exe --wb -q "чайник электрический" -n 300 -o "D:\Reports"
```

See `python -m mpparser --help` for all options.

### Building the .exe

```bash
pip install -r requirements-dev.txt
python -m PyInstaller MarketplaceParser.spec --noconfirm
```

The result is the `dist/MarketplaceParser` folder (about 140 MB; no browser is bundled).

## Tests

Parsing is tested against saved marketplace responses, the export against a real Excel file:

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

When a marketplace changes its response format, refresh the fixtures with `python tools/dump_fixtures.py`.

## Project layout

```
mpparser/
  browser.py            Chrome/Edge startup, requests from inside a page
  marketplaces/
    base.py             parser interface, progress reporting, cancellation
    wildberries.py      search, product cards, reviews, photo links
    ozon.py             search, product cards, reviews
  export/excel.py       "Products", "Reviews" and "Summary" sheets
  fields.py             registry of Excel columns
  regions.py            cities and WB region codes
  runner.py             the whole collection scenario
  settings.py           settings and their persistence
  gui/                  CustomTkinter window
tools/                  icon, screenshots, fixture and region refresh
tests/                  tests and fixtures
```

## Limitations

- **Wildberries stock** is not exported: for anonymous visitors the site reports the same placeholder quantity
  for every product instead of the real stock.
- **Ozon brand, seller, category and card price** only exist on the product page. With those columns enabled the
  parser opens every card, which slows collection down to roughly 1–2 seconds per product.
- **Wildberries reviews** are served in batches of up to 1000 most recent per product.
- Marketplaces change their APIs without notice. The parser adapts where it can (for example, it picks up the
  current search API version from the site itself), but large changes require code updates — which is what the
  fixture-based tests are for.
- Only publicly visible data is collected, with pauses between requests. Reviews contain the public display names
  of their authors, so use the exported data lawfully and for its intended purpose.

## Roadmap

- Collecting by category link (the parser interface is ready: `MarketplaceParser.category`).
- Keeping price history in one file to track changes over time.
- Proxy support and built-in scheduling.

## Author

**Nikolay Bondarenko** — Python developer: web scraping and automation.
Need a similar scraper or a custom version? Get in touch.

- Telegram: [@krein1](https://t.me/krein1)
- Email: [reinkolya@gmail.com](mailto:reinkolya@gmail.com)
- GitHub: [krein15](https://github.com/krein15)

Licensed under the [MIT License](LICENSE).
