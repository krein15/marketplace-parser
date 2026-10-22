"""History between runs: what changed since the previous run of the same job — prices and places in search.

A job is a saved task (``task:<name>``) or, for a run started by hand, the combination of what is collected —
marketplaces, query or links, filters — so that running the same search again compares it with the last time.
History lives in SQLite next to the settings. Only marketplaces that finished without errors are recorded
and compared: a failed marketplace must not turn into "all products disappeared" the next day.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import plugins
from .inputs import collect_ids
from .models import Product
from .settings import InputMode, ParseSettings

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    job TEXT NOT NULL,
    started_at TEXT NOT NULL,
    marketplaces TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_job ON runs (job, started_at);
CREATE TABLE IF NOT EXISTS prices (
    run_id INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    marketplace TEXT NOT NULL,
    article TEXT NOT NULL,
    name TEXT,
    price REAL,
    url TEXT,
    position INTEGER,
    PRIMARY KEY (run_id, marketplace, article)
);
"""
# Databases written by version 2.1 and earlier have no "position" column.
MIGRATIONS = {"position": "ALTER TABLE prices ADD COLUMN position INTEGER"}

CHEAPER, PRICIER, NEW, GONE = "Подешевел", "Подорожал", "Новый", "Пропал"
STATUS_ORDER = (CHEAPER, PRICIER, NEW, GONE)
# How statuses read in the report. In search results "gone" means "dropped out of the first N results"
# (often just rotating ads), not "removed from sale", so the wording depends on how products were collected.
LABELS = {
    True: {CHEAPER: ("Подешевел", "Подешевели"), PRICIER: ("Подорожал", "Подорожали"),
           NEW: ("Новый в выдаче", "Новые в выдаче"), GONE: ("Выпал из выдачи", "Выпали из выдачи")},
    False: {CHEAPER: ("Подешевел", "Подешевели"), PRICIER: ("Подорожал", "Подорожали"),
            NEW: ("Новый", "Новые"), GONE: ("Не найден", "Не найдены")},
}
MIN_DELTA = 1.0  # rubles; smaller differences are rounding noise
DYNAMICS_RUNS = 14

Key = tuple[str, str]  # (marketplace title, article)


def job_key(settings: ParseSettings) -> str:
    """Identify what is collected, so that repeated runs of the same thing are compared with each other."""
    if settings.task_name:
        return f"task:{settings.task_name}"
    what: dict[str, object] = {"marketplaces": sorted(settings.marketplaces), "mode": str(settings.mode)}
    if settings.mode == InputMode.QUERY:
        what |= {
            "query": " | ".join(" ".join(q.lower().split()) for q in settings.queries),
            "sort": str(settings.sort),
            "limit": settings.max_products,
        }
    else:
        what["ids"] = {key: " ".join(settings.ids_text(key).split()) for key in settings.marketplaces}
        what["limit"] = settings.max_products
    # Filters of the selected marketplaces, whatever they are: a different filter means a different history.
    options = {}
    for key in settings.marketplaces:
        marketplace = plugins.get(key)
        for option in marketplace.options if marketplace else ():
            if option.kind == "setup" or (option.query_only and settings.mode != InputMode.QUERY):
                continue
            value = settings.option(key, option.key)
            options[f"{key}.{option.key}"] = sorted(value) if isinstance(value, list) else value
    what["filters"] = [settings.price_min, settings.price_max, options]
    digest = hashlib.sha1(json.dumps(what, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return f"run:{digest[:16]}"


@dataclass
class Snapshot:
    """An item as it was recorded in a previous run."""

    name: str
    price: float | None
    url: str
    position: int | None = None  # place in search results, 1 is the top


@dataclass
class PreviousRun:
    started_at: datetime
    marketplaces: list[str]
    items: dict[Key, Snapshot]


@dataclass
class Change:
    status: str
    marketplace: str
    article: str
    name: str
    url: str
    old_price: float | None = None
    new_price: float | None = None
    old_position: int | None = None
    new_position: int | None = None

    @property
    def rise(self) -> int | None:
        """How many places the product moved up; negative means it moved down."""
        if self.old_position is None or self.new_position is None:
            return None
        return self.old_position - self.new_position

    @property
    def delta(self) -> float | None:
        if self.old_price is None or self.new_price is None:
            return None
        return self.new_price - self.old_price

    @property
    def delta_pct(self) -> float | None:
        if self.delta is None or not self.old_price:
            return None
        return round(self.delta / self.old_price * 100, 1)


@dataclass
class Comparison:
    previous_at: datetime | None
    marketplaces: list[str] = field(default_factory=list)  # titles compared (present in both runs)
    changes: list[Change] = field(default_factory=list)
    search: bool = True  # products come from search results or listing links, not from a list of articles

    def count(self, status: str) -> int:
        return sum(1 for change in self.changes if change.status == status)

    def label(self, status: str) -> str:
        return LABELS[self.search][status][0]

    def title(self, status: str) -> str:
        return LABELS[self.search][status][1]


@dataclass
class Dynamics:
    """Prices of the current products over the last runs, oldest first."""

    dates: list[datetime]
    rows: list[tuple[Product, list[float | None]]]


@dataclass
class Positions:
    """Places of the current products in search results over the last runs, oldest first.

    Rows are sorted by how far a product moved: the biggest drops first, because that is what a seller
    wants to see. Products collected by article number have no place and are left out.
    """

    dates: list[datetime]
    rows: list[tuple[Product, list[int | None]]]

    def rise(self, places: Sequence[int | None]) -> int | None:
        """Places gained since the earliest run where the product was seen; negative means it dropped."""
        seen = [place for place in places if place is not None]
        if len(seen) < 2:
            return None
        return seen[0] - seen[-1]


def compare(previous: PreviousRun | None, products: Sequence[Product], marketplaces: Sequence[str]) -> Comparison:
    """Changes between the previous run and ``products`` for the marketplaces collected fully both times."""
    if previous is None:
        return Comparison(previous_at=None)
    compared = [m for m in marketplaces if m in previous.marketplaces]
    changes: list[Change] = []
    current: set[Key] = set()
    for product in products:
        if product.marketplace not in compared:
            continue
        key = (product.marketplace, product.article)
        current.add(key)
        before = previous.items.get(key)
        change = Change("", product.marketplace, product.article, product.name, product.url,
                        before.price if before else None, product.price,
                        before.position if before else None, product.position)
        if before is None:
            change.status = NEW
        elif change.delta is not None and abs(change.delta) >= MIN_DELTA:
            change.status = CHEAPER if change.delta < 0 else PRICIER
        else:
            continue
        changes.append(change)
    for (marketplace, article), before in previous.items.items():
        if marketplace in compared and (marketplace, article) not in current:
            changes.append(Change(GONE, marketplace, article, before.name, before.url, before.price, None,
                                  before.position, None))

    def order(change: Change) -> tuple[int, float]:
        # Biggest price drops and rises first; new and gone items keep the marketplace order.
        return STATUS_ORDER.index(change.status), -abs(change.delta_pct or 0)

    changes.sort(key=order)
    return Comparison(previous_at=previous.started_at, marketplaces=compared, changes=changes)


class History:
    def __init__(self, path: Path) -> None:
        self.path = path
        with closing(self._connect()) as db, db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info (prices)")}
            for column, statement in MIGRATIONS.items():
                if column not in columns:
                    log.info("History: adding the %s column", column)
                    db.execute(statement)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def previous(self, job: str) -> PreviousRun | None:
        with closing(self._connect()) as db:
            run = db.execute("SELECT id, started_at, marketplaces FROM runs WHERE job = ? "
                             "ORDER BY started_at DESC LIMIT 1", (job,)).fetchone()
            if run is None:
                return None
            rows = db.execute("SELECT marketplace, article, name, price, url, position FROM prices "
                              "WHERE run_id = ?", (run[0],)).fetchall()
        items = {(m, a): Snapshot(name or "", price, url or "", position)
                 for m, a, name, price, url, position in rows}
        return PreviousRun(datetime.fromisoformat(run[1]), json.loads(run[2]), items)

    def record(self, job: str, started_at: datetime, marketplaces: Sequence[str],
               products: Sequence[Product]) -> int:
        """Store the prices of ``products`` of the given (fully collected) marketplaces."""
        with closing(self._connect()) as db, db:
            cursor = db.execute("INSERT INTO runs (job, started_at, marketplaces) VALUES (?, ?, ?)",
                                (job, started_at.isoformat(timespec="seconds"),
                                 json.dumps(list(marketplaces), ensure_ascii=False)))
            run_id = int(cursor.lastrowid or 0)
            db.executemany(
                "INSERT OR IGNORE INTO prices (run_id, marketplace, article, name, price, url, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(run_id, p.marketplace, p.article, p.name, p.price, p.url, p.position)
                 for p in products if p.marketplace in marketplaces],
            )
        return run_id

    def dynamics(self, job: str, products: Sequence[Product], runs: int = DYNAMICS_RUNS) -> Dynamics:
        with closing(self._connect()) as db:
            recent = db.execute("SELECT id, started_at FROM runs WHERE job = ? ORDER BY started_at DESC LIMIT ?",
                                (job, runs)).fetchall()[::-1]
            ids = [run_id for run_id, _ in recent]
            marks = ",".join("?" * len(ids))
            rows = db.execute(f"SELECT run_id, marketplace, article, price FROM prices WHERE run_id IN ({marks})",
                              ids).fetchall() if ids else []
        prices = {(run_id, m, a): price for run_id, m, a, price in rows}
        table = [(p, [prices.get((run_id, p.marketplace, p.article)) for run_id in ids]) for p in products]
        return Dynamics([datetime.fromisoformat(started) for _, started in recent], table)

    def positions(self, job: str, products: Sequence[Product], runs: int = DYNAMICS_RUNS) -> Positions:
        """Places of ``products`` in search results over the last runs, biggest drops first."""
        with closing(self._connect()) as db:
            recent = db.execute("SELECT id, started_at FROM runs WHERE job = ? ORDER BY started_at DESC LIMIT ?",
                                (job, runs)).fetchall()[::-1]
            ids = [run_id for run_id, _ in recent]
            marks = ",".join("?" * len(ids))
            rows = db.execute(f"SELECT run_id, marketplace, article, position FROM prices "
                              f"WHERE run_id IN ({marks})", ids).fetchall() if ids else []
        places = {(run_id, m, a): position for run_id, m, a, position in rows}
        table = [(p, [places.get((run_id, p.marketplace, p.article)) for run_id in ids])
                 for p in products if p.position is not None]
        dates = [datetime.fromisoformat(started) for _, started in recent]
        result = Positions(dates, table)
        result.rows.sort(key=lambda row: (result.rise(row[1]) is None, result.rise(row[1]) or 0))
        return result

    def forget(self, job: str) -> None:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM runs WHERE job = ?", (job,))


def update_history(path: Path, settings: ParseSettings, started_at: datetime, completed: Sequence[str],
                   products: Sequence[Product]) -> tuple[Comparison, Dynamics, Positions]:
    """Compare with the previous run of the same job, then record this one. ``completed`` — marketplace keys."""
    titles = [plugins.title_of(key) for key in completed]
    history = History(path)
    job = job_key(settings)
    comparison = compare(history.previous(job), products, titles)
    links = collect_ids({key: settings.ids_text(key) for key in settings.marketplaces}).listings
    comparison.search = settings.mode == InputMode.QUERY or any(links.values())
    history.record(job, started_at, titles, products)
    collected = [p for p in products if p.marketplace in titles]
    return comparison, history.dynamics(job, collected), history.positions(job, collected)
