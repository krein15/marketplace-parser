"""Saved tasks and Windows Task Scheduler commands (schtasks is replaced with a fake)."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

import mpparser.scheduler as scheduler
import mpparser.tasks as tasks
from mpparser.settings import ParseSettings
from mpparser.tasks import Task, check_name, normalize_time


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(tasks, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(scheduler, "app_data_dir", lambda: tmp_path)
    return tmp_path


class FakeSchtasks:
    def __init__(self, existing: bool = False, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.existing = existing
        self.fail = fail

    def __call__(self, args: list[str], **_: object) -> subprocess.CompletedProcess:
        self.calls.append(args)
        code = 0
        if args[1] == "/Query":
            code = 0 if self.existing else 1
        elif self.fail:
            code = 1
        return subprocess.CompletedProcess(args, code, stdout="", stderr="ОШИБКА: отказано в доступе." if code else "")


def test_task_roundtrip_and_replace_by_name():
    tasks.upsert_task(Task("Чайники", ParseSettings(query="чайник", marketplaces=["ym", "avito"]), "09:00"))
    tasks.upsert_task(Task("Наушники", ParseSettings(query="наушники")))
    tasks.upsert_task(Task("чайники", ParseSettings(query="электрочайник", marketplaces=["ym"]), "08:30"))
    loaded = tasks.load_tasks()
    assert [t.name for t in loaded] == ["Наушники", "чайники"]
    kettles = tasks.get_task("ЧАЙНИКИ")
    assert kettles.settings.query == "электрочайник"
    assert kettles.settings.task_name == "чайники"
    assert kettles.describe() == "ЯМ · «электрочайник» · каждый день в 08:30"
    assert tasks.get_task("Наушники").describe() == "WB+Ozon · «наушники» · вручную"


def test_delete_and_mark_run():
    tasks.upsert_task(Task("Чайники", ParseSettings(query="чайник")))
    tasks.mark_run("чайники", datetime(2026, 9, 27, 9, 0), Path("D:/Отчёты/ЯМ_Чайники.xlsx"))
    task = tasks.get_task("Чайники")
    assert task.last_run == "2026-09-27T09:00:00" and task.last_file.endswith("ЯМ_Чайники.xlsx")
    tasks.delete_task("Чайники")
    assert tasks.load_tasks() == []


def test_broken_tasks_file_is_ignored(data_dir):
    (data_dir / "tasks.json").write_text("{oops", encoding="utf-8")
    assert tasks.load_tasks() == []


@pytest.mark.parametrize(("name", "ok"), [("Чайники Екатеринбург", True), ("", False), ("a/b", False),
                                          ('кавычки "так"', False), ("x" * 61, False)])
def test_task_names(name, ok):
    assert (check_name(name) is None) == ok


@pytest.mark.parametrize(("text", "expected"), [("9:00", "09:00"), ("09.30", "09:30"), ("", ""), ("25:00", None),
                                                ("9", None), ("12:60", None)])
def test_normalize_time(text, expected):
    assert normalize_time(text) == expected


def test_schedule_creates_a_daily_entry(monkeypatch):
    monkeypatch.setattr(scheduler, "launch_command", lambda name: f'"C:\\MP\\MarketplaceParser.exe" --task "{name}"')
    fake = FakeSchtasks()
    scheduler.schedule("Чайники", "09:00", run=fake)
    assert fake.calls == [["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", "09:00",
                           "/TN", "Marketplace Parser v2\\Чайники",
                           "/TR", '"C:\\MP\\MarketplaceParser.exe" --task "Чайники"']]


def test_long_command_goes_through_a_hidden_launcher(monkeypatch, data_dir):
    long_command = '"C:\\' + "очень длинный путь\\" * 20 + 'pythonw.exe" "app.py" --task "Чайники"'
    monkeypatch.setattr(scheduler, "launch_command", lambda _name: long_command)
    fake = FakeSchtasks()
    scheduler.schedule("Чайники", "09:00", run=fake)
    command = fake.calls[0][-1]
    assert command.startswith("wscript.exe ") and len(command) < scheduler.MAX_COMMAND
    script = next((data_dir / "launchers").glob("*.vbs"))
    assert "--task" in script.read_text(encoding="utf-16")
    scheduler.unschedule("Чайники", run=FakeSchtasks(existing=True))
    assert not script.exists()


def test_schedule_errors_are_readable():
    with pytest.raises(scheduler.SchedulerError, match="Не удалось создать задачу"):
        scheduler.schedule("Чайники", "09:00", run=FakeSchtasks(fail=True))
    with pytest.raises(scheduler.SchedulerError, match="нельзя использовать"):
        scheduler.schedule('имя с "кавычками"', "09:00", run=FakeSchtasks())


def test_unschedule_skips_missing_entries():
    fake = FakeSchtasks(existing=False)
    scheduler.unschedule("Чайники", run=fake)
    assert [call[1] for call in fake.calls] == ["/Query"]


def test_launch_command_from_sources():
    command = scheduler.launch_command("Чайники")
    assert command.endswith('app.py" --task "Чайники"')
