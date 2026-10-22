"""Telegram notifications: what a finished run found, and the report file itself.

A task started by Windows Task Scheduler has nobody watching it, so the news goes to a Telegram chat:
what got cheaper, who undercuts me, how the places in search moved — and the Excel file as an attachment.

The bot token and the chat are stored next to the settings (``telegram.json``), not inside every saved task,
so changing the bot once is enough for all of them.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from .monitoring import STATUS_ORDER
from .settings import ParseSettings, app_data_dir

if TYPE_CHECKING:  # importing the runner here would close a circle: runner → notify → runner
    from .runner import RunResult

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 30
MAX_TEXT = 4000  # Telegram cuts a message at 4096 characters
MAX_FILE = 50 * 1024 * 1024  # bots may send files up to 50 MB
TOP_CHANGES = 5  # how many price changes the message lists by name
BOT_HELP = ("Токен выдаёт @BotFather, Chat ID — @userinfobot. "
            "Своему боту напишите «/start», иначе он не сможет вам писать.")


class NotifyError(Exception):
    """Telegram did not accept the message; the run itself is fine."""


@dataclass
class Telegram:
    """Where to send the news. Empty token or chat means notifications are not set up yet."""

    token: str = ""
    chat: str = ""

    @classmethod
    def path(cls) -> Path:
        return app_data_dir() / "telegram.json"

    @classmethod
    def load(cls) -> Telegram:
        try:
            raw = json.loads(cls.path().read_text(encoding="utf-8"))
            return cls(token=str(raw.get("token", "")), chat=str(raw.get("chat", "")))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("Telegram settings are unreadable: %s", exc)
            return cls()

    def save(self) -> None:
        try:
            self.path().write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not save Telegram settings: %s", exc)

    def configured(self) -> bool:
        return bool(self.token.strip() and self.chat.strip())

    # --- sending ---

    def send(self, text: str) -> None:
        self._call("sendMessage", {"chat_id": self.chat.strip(), "text": text[:MAX_TEXT],
                                   "parse_mode": "HTML", "disable_web_page_preview": "true"})

    def send_file(self, path: Path, caption: str = "") -> None:
        if path.stat().st_size > MAX_FILE:
            raise NotifyError("Файл больше 50 МБ — Telegram такой не примет, отправляю только текст.")
        self._call("sendDocument", {"chat_id": self.chat.strip(), "caption": caption[:1000],
                                    "parse_mode": "HTML"}, document=path)

    def _call(self, method: str, fields: dict[str, str], document: Path | None = None) -> None:
        url = API.format(token=self.token.strip(), method=method)
        if document is None:
            request = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
        else:
            body, content_type = _multipart(fields, document)
            request = urllib.request.Request(url, data=body, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as answer:
                payload = json.loads(answer.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raise NotifyError(f"Telegram отказал: {_reason(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise NotifyError(f"Telegram недоступен: {exc}") from exc
        if not payload.get("ok"):
            raise NotifyError(f"Telegram отказал: {payload.get('description') or 'без объяснений'}")


def _reason(error: urllib.error.HTTPError) -> str:
    """Telegram explains a refusal in the body of the error answer: a wrong token, a wrong chat."""
    try:
        return str(json.loads(error.read().decode("utf-8", "replace")).get("description") or error)
    except (OSError, ValueError):
        return str(error)


def _multipart(fields: dict[str, str], document: Path) -> tuple[bytes, str]:
    boundary = "mpparser" + uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{document.name}"\r\n'
             "Content-Type: application/octet-stream\r\n\r\n").encode()
    body += document.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


# --- what the message says ---


def _money(value: float | None) -> str:
    return f"{round(value):,} ₽".replace(",", " ") if value is not None else "—"


def _line(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return escape(text if len(text) <= limit else text[: limit - 1].rstrip() + "…")


def subject(settings: ParseSettings) -> str:
    return settings.task_name or settings.subject()


def message(settings: ParseSettings, result: RunResult, started_at: datetime | None = None) -> str:
    """The text of the notification: the run, then changes, my products and places in search."""
    started = started_at or result.started_at
    lines = [f"<b>{_line(subject(settings), 80)}</b>",
             f"{started:%d.%m в %H:%M} · товаров — {len(result.products)}"
             + (f" · отзывов — {len(result.reviews)}" if result.reviews else "")]
    if result.cancelled:
        lines.append("Сбор остановлен — в отчёте только то, что успели собрать.")

    comparison = result.comparison
    if comparison is not None and comparison.previous_at is not None:
        counts = " · ".join(f"{comparison.title(status).lower()} — {comparison.count(status)}"
                            for status in STATUS_ORDER if comparison.count(status))
        lines += ["", f"<b>Изменения</b> с {comparison.previous_at:%d.%m %H:%M}",
                  counts or "цены и состав товаров не изменились"]
        named = [c for c in comparison.changes if c.delta and c.delta_pct is not None]
        for change in named[:TOP_CHANGES]:
            lines.append(f"• {_line(change.name)} — {_money(change.old_price)} → {_money(change.new_price)} "
                         f"({change.delta_pct:+.1f}%)")

    competition = result.competition
    if competition:
        lines += ["", f"<b>Мои товары</b>: {len(competition.spots)} · дешевле меня — {competition.cheaper}"
                      + (f" · никто не дешевле — {competition.leading}" if competition.leading else "")]
        for spot in competition.spots[:TOP_CHANGES]:
            place = f"{spot.rank} из {spot.total}" if spot.rank else "цены нет"
            cheapest = f"; дешевле всех — {_money(spot.cheapest.price)}" if spot.cheapest else ""
            lines.append(f"• {_line(spot.product.name)} — {_money(spot.price)}, место по цене {place}{cheapest}")

    if result.positions is not None and result.positions.rows:
        rises = [result.positions.rise(places) for _, places in result.positions.rows]
        up = sum(1 for rise in rises if rise and rise > 0)
        down = sum(1 for rise in rises if rise and rise < 0)
        if up or down:
            lines += ["", f"<b>Позиции</b>: поднялись — {up} · опустились — {down}"]

    if result.errors:
        lines += ["", "<b>Ошибки</b>"] + [f"• {_line(error, 120)}" for error in result.errors[:3]]
    if result.path:
        lines += ["", f"Файл: {escape(result.path.name)}"]
    return "\n".join(lines)


def notify(settings: ParseSettings, result: RunResult, warn: Callable[[str], None] | None = None) -> bool:
    """Send the news about a finished run. Never breaks the run: problems are reported through ``warn``."""
    if not settings.notify:
        return False
    telegram = Telegram.load()
    if not telegram.configured():
        _say(warn, "Уведомления включены, но бот не настроен. " + BOT_HELP)
        return False
    text = message(settings, result)
    try:
        telegram.send(text)
        if settings.notify_file and result.path and result.path.exists():
            telegram.send_file(result.path)
    except NotifyError as exc:
        _say(warn, str(exc))
        return False
    log.info("Telegram notification sent")
    return True


def _say(warn: Callable[[str], None] | None, text: str) -> None:
    log.warning(text)
    if warn is not None:
        warn(text)
