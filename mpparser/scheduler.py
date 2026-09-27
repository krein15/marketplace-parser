"""Daily runs through Windows Task Scheduler (``schtasks.exe``).

Each task gets an entry in the "Marketplace Parser v2" folder of Task Scheduler that starts the program with
``--task "<name>"`` every day at the chosen time. The entry runs as the current user and only while the user is
signed in to Windows: the browser needs the user's desktop (Avito works only in a visible window).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import APP_NAME
from .settings import app_data_dir
from .tasks import check_name

MAX_COMMAND = 250  # schtasks refuses a /TR command longer than 261 characters
Runner = Callable[..., subprocess.CompletedProcess]


class SchedulerError(RuntimeError):
    """A problem with Task Scheduler, with a message that can be shown to the user."""


def entry_name(task_name: str) -> str:
    return f"{APP_NAME}\\{task_name}"


def launch_command(task_name: str) -> str:
    """The command that runs a saved task without opening the window."""
    if getattr(sys, "frozen", False):  # the built .exe
        return f'"{sys.executable}" --task "{task_name}"'
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    app = Path(__file__).resolve().parents[1] / "app.py"
    return f'"{windowless if windowless.exists() else python}" "{app}" --task "{task_name}"'


def _launcher_path(task_name: str) -> Path:
    digest = hashlib.sha1(task_name.casefold().encode()).hexdigest()[:12]
    return app_data_dir() / "launchers" / f"{digest}.vbs"


def _hidden_launcher(task_name: str, command: str) -> str:
    """A long command does not fit into Task Scheduler: start it from a small VBScript that hides the console."""
    script = _launcher_path(task_name)
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f'CreateObject("WScript.Shell").Run "{command.replace(chr(34), chr(34) * 2)}", 0, False\n',
                      encoding="utf-16")
    return f'wscript.exe "{script}"'


def _run(run: Runner, args: list[str]) -> subprocess.CompletedProcess:
    try:
        return run(args, capture_output=True, text=True, encoding="cp866", errors="replace",
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError as exc:
        raise SchedulerError("Планировщик задач Windows недоступен на этом компьютере.") from exc


def schedule(task_name: str, time: str, run: Runner = subprocess.run) -> None:
    """Create or replace the daily entry of ``task_name`` at ``time`` ("HH:MM")."""
    if problem := check_name(task_name):  # the name goes into a command line: no quotes allowed
        raise SchedulerError(problem)
    command = launch_command(task_name)
    if len(command) > MAX_COMMAND:
        command = _hidden_launcher(task_name, command)
    result = _run(run, ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", time,
                        "/TN", entry_name(task_name), "/TR", command])
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise SchedulerError(f"Не удалось создать задачу в Планировщике Windows. {details}".strip())


def unschedule(task_name: str, run: Runner = subprocess.run) -> None:
    """Remove the daily entry; a missing entry is not an error."""
    if is_scheduled(task_name, run):
        result = _run(run, ["schtasks", "/Delete", "/F", "/TN", entry_name(task_name)])
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            raise SchedulerError(f"Не удалось удалить задачу из Планировщика Windows. {details}".strip())
    _launcher_path(task_name).unlink(missing_ok=True)


def is_scheduled(task_name: str, run: Runner = subprocess.run) -> bool:
    return _run(run, ["schtasks", "/Query", "/TN", entry_name(task_name)]).returncode == 0
