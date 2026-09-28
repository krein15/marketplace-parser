"""Saved monitoring tasks: a name, the collection settings and an optional daily run time.

A task is what a client sets up once — "чайники на Маркете и Авито в Екатеринбурге, каждый день в 9:00" —
and then gets a fresh report with a "Изменения" sheet every morning. Tasks live in ``tasks.json``
next to the settings; the daily run itself is a Windows Task Scheduler entry (see ``scheduler.py``).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import plugins
from .settings import InputMode, ParseSettings, app_data_dir

log = logging.getLogger(__name__)

FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
TIME = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*$")
MAX_NAME = 60


@dataclass
class Task:
    name: str
    settings: ParseSettings = field(default_factory=ParseSettings)
    time: str = ""  # "HH:MM" of the daily run; empty — started by hand only
    last_run: str = ""  # ISO date and time of the last finished run
    last_file: str = ""

    def describe(self) -> str:
        """One line for the task list: "ЯМ+Авито · «чайник» · каждый день в 09:00"."""
        settings = self.settings
        short = plugins.short_titles()
        parts = ["+".join(short.get(k, k) for k in settings.marketplaces)]
        parts.append(f"«{settings.query}»" if settings.mode == InputMode.QUERY else "ссылки и артикулы")
        parts.append(f"каждый день в {self.time}" if self.time else "вручную")
        return " · ".join(parts)


def tasks_path() -> Path:
    return app_data_dir() / "tasks.json"


def check_name(name: str) -> str | None:
    """A problem with the task name, or None. The name is also used in Task Scheduler and file names."""
    if not name.strip():
        return "Введите название задания."
    if len(name.strip()) > MAX_NAME:
        return f"Название задания — не длиннее {MAX_NAME} символов."
    if FORBIDDEN.search(name):
        return 'В названии задания нельзя использовать символы \\ / : * ? " < > |'
    return None


def normalize_time(text: str) -> str | None:
    """ "9:00", "09.30" → "09:00", "09:30"; "" → ""; None if it is not a time of day."""
    if not text.strip():
        return ""
    match = TIME.match(text)
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def load_tasks() -> list[Task]:
    try:
        raw = json.loads(tasks_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("Tasks file is unreadable: %s", exc)
        return []
    tasks = []
    for item in raw if isinstance(raw, list) else []:
        settings = ParseSettings.from_dict(item.get("settings") or {})
        if settings is None or not item.get("name"):
            log.warning("Skipping a broken task: %s", item.get("name"))
            continue
        settings.task_name = item["name"]
        tasks.append(Task(item["name"], settings, item.get("time", ""), item.get("last_run", ""),
                          item.get("last_file", "")))
    return tasks


def save_tasks(tasks: list[Task]) -> None:
    data = [asdict(task) for task in tasks]
    tasks_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_task(name: str) -> Task | None:
    return next((task for task in load_tasks() if task.name.casefold() == name.strip().casefold()), None)


def upsert_task(task: Task) -> None:
    """Add the task or replace the one with the same name (case-insensitive)."""
    task.name = task.name.strip()
    task.settings.task_name = task.name
    tasks = [t for t in load_tasks() if t.name.casefold() != task.name.casefold()]
    save_tasks([*tasks, task])


def delete_task(name: str) -> None:
    save_tasks([t for t in load_tasks() if t.name.casefold() != name.strip().casefold()])


def mark_run(name: str, finished_at: datetime, file: Path | None) -> None:
    tasks = load_tasks()
    for task in tasks:
        if task.name.casefold() == name.casefold():
            task.last_run = finished_at.isoformat(timespec="seconds")
            task.last_file = str(file) if file else task.last_file
    save_tasks(tasks)
