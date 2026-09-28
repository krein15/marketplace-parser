"""Main window."""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import subprocess
import threading
from dataclasses import replace
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, ClassVar

import customtkinter as ctk

from .. import APP_NAME, __version__, plugins
from ..browser import Browser, BrowserError
from ..fields import Field, product_fields, review_fields
from ..inputs import parse_ids
from ..marketplaces import Reporter
from ..plugins import Marketplace, Option
from ..runner import RunResult, run
from ..scheduler import SchedulerError, schedule, unschedule
from ..settings import SORT_TITLES, InputMode, ParseSettings, app_data_dir
from ..tasks import Task, check_name, delete_task, get_task, load_tasks, mark_run, normalize_time, upsert_task
from . import theme
from .widgets import LocationPicker, MarketplaceToggle, NumberField, OptionField, SectionCard, neutral_button

log = logging.getLogger(__name__)

MODE_TITLES = {InputMode.QUERY: "Поисковый запрос", InputMode.IDS: "Артикулы и ссылки"}
LOG_COLORS = {"warning": theme.WARNING, "error": theme.DANGER, "success": theme.SUCCESS}


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=theme.APP_BG)
        self.settings = ParseSettings.load()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_result: RunResult | None = None
        self.running_settings: ParseSettings | None = None
        self._closing = False

        self.title(f"{APP_NAME} {__version__}")
        self._size_window(1240, 840)
        icon = theme.resource_path("assets/icon.ico")
        if icon.exists():
            self.after(250, lambda: self.iconbitmap(str(icon)))  # CTk resets the icon right after start

        self.marketplaces = plugins.registry()
        self._build_header()
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        content.grid_columnconfigure(0, weight=3, uniform="columns")
        content.grid_columnconfigure(1, weight=2, uniform="columns")
        content.grid_rowconfigure(0, weight=1)

        # Tabs instead of one long scrollable column: everything fits on screen, and Tk does not have to
        # repaint nested rounded frames while scrolling (which left visual artefacts).
        self.tabs = ctk.CTkTabview(
            content, fg_color="transparent", corner_radius=12, anchor="nw",
            segmented_button_selected_color=theme.ACCENT, segmented_button_selected_hover_color=theme.ACCENT_HOVER,
            segmented_button_unselected_color=theme.NEUTRAL_BUTTON,
            segmented_button_unselected_hover_color=theme.NEUTRAL_BUTTON_HOVER,
            segmented_button_fg_color=theme.NEUTRAL_BUTTON, text_color=theme.TEXT)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        collect_tab = self.tabs.add("Сбор")
        filters_tab = self.tabs.add("Фильтры")
        monitoring_tab = self.tabs.add("Мониторинг")
        fields_tab = self.tabs.add("Колонки Excel")
        output_tab = self.tabs.add("Сохранение")
        self._build_input(collect_tab)
        self._build_options(collect_tab)
        self._build_filters(filters_tab)
        self._build_monitoring(monitoring_tab)
        self._build_fields(fields_tab)
        self._build_output(output_tab)

        self.setup_addresses = [(mp.key, option.key) for mp in plugins.with_options()
                                for option in mp.options if option.kind == "setup"]
        self._build_run_panel(content)
        self._refresh_marketplace_state()
        self._refresh_mode()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)
        for message in plugins.load_errors():
            self.after(200, lambda text=message: self._log("warning", text))

    # ------------------------------------------------------------------ layout

    def _size_window(self, width: int, height: int) -> None:
        """Fit the window into the screen: with Windows display scaling the requested size grows."""
        try:
            scaling = ctk.ScalingTracker.get_window_scaling(self)
        except Exception:
            scaling = 1.0
        screen_width, screen_height = self.winfo_screenwidth(), self.winfo_screenheight()
        width = min(width, int(screen_width / scaling) - 40)
        height = min(height, int(screen_height / scaling) - 90)  # leave room for the taskbar
        x = max((screen_width - int(width * scaling)) // 2, 0)
        y = max((screen_height - int(height * scaling)) // 3, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(1040, width), min(640, height))

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=24, pady=(18, 14))
        logo = ctk.CTkLabel(header, text="MP", width=44, height=44, corner_radius=12, fg_color=theme.ACCENT,
                            text_color="#FFFFFF", font=theme.font(17, "bold"))
        logo.pack(side="left")
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=12)
        ctk.CTkLabel(titles, text=APP_NAME, font=theme.font(21, "bold"), text_color=theme.TEXT).pack(anchor="w")
        names = ", ".join(mp.title for mp in self.marketplaces.values())
        ctk.CTkLabel(titles, text=f"Товары, цены и отзывы в Excel: {names}",
                     font=theme.font(13), text_color=theme.TEXT_MUTED).pack(anchor="w")

        self.appearance = ctk.CTkSegmentedButton(
            header, values=["Светлая", "Тёмная", "Системная"], command=self._set_appearance, font=theme.font(12),
            selected_color=theme.ACCENT, selected_hover_color=theme.ACCENT_HOVER,
            unselected_color=theme.NEUTRAL_BUTTON, unselected_hover_color=theme.NEUTRAL_BUTTON_HOVER,
            text_color=theme.TEXT, fg_color=theme.NEUTRAL_BUTTON)
        self.appearance.set("Системная")
        self.appearance.pack(side="right")

    def _build_marketplaces(self, parent: ctk.CTkBaseClass) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))
        row.grid_columnconfigure(tuple(range(len(self.marketplaces))), weight=1, uniform="mp")
        self.mp_vars: dict[str, ctk.BooleanVar] = {}
        self.mp_toggles: dict[str, MarketplaceToggle] = {}
        for column, (key, marketplace) in enumerate(self.marketplaces.items()):
            var = ctk.BooleanVar(value=key in self.settings.marketplaces)
            self.mp_vars[key] = var
            toggle = MarketplaceToggle(row, marketplace.color, marketplace.title, var,
                                       self._refresh_marketplace_state)
            toggle.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
            self.mp_toggles[key] = toggle

    def _build_input(self, parent: ctk.CTkBaseClass) -> None:
        card = SectionCard(parent, 1, "Площадки и что собираем")
        card.pack(fill="x", pady=(0, 12))
        self._build_marketplaces(card.body)
        self.mode = ctk.CTkSegmentedButton(
            card.body, values=list(MODE_TITLES.values()), command=lambda _: self._refresh_mode(),
            font=theme.font(13), height=34, selected_color=theme.ACCENT, selected_hover_color=theme.ACCENT_HOVER,
            unselected_color=theme.NEUTRAL_BUTTON, unselected_hover_color=theme.NEUTRAL_BUTTON_HOVER,
            text_color=theme.TEXT, fg_color=theme.NEUTRAL_BUTTON)
        self.mode.set(MODE_TITLES[self.settings.mode])
        self.mode.pack(anchor="w")

        self.query_frame = ctk.CTkFrame(card.body, fg_color="transparent")
        self.query = ctk.CTkEntry(self.query_frame, height=42, font=theme.font(15), border_width=1,
                                  fg_color=theme.INPUT_BG, border_color=theme.CARD_BORDER, text_color=theme.TEXT,
                                  placeholder_text="Например: беспроводные наушники")
        if self.settings.query:
            self.query.insert(0, self.settings.query)
        self.query.pack(fill="x")
        self.query.bind("<Return>", lambda _: self._start())

        self.ids_frame = ctk.CTkFrame(card.body, fg_color="transparent")
        self.ids_frame.grid_columnconfigure(tuple(range(len(self.marketplaces))), weight=1, uniform="ids")
        self.ids_boxes: dict[str, ctk.CTkTextbox] = {}
        self.ids_counters: dict[str, ctk.CTkLabel] = {}
        self.ids_columns: dict[str, ctk.CTkFrame] = {}
        for key, marketplace in self.marketplaces.items():
            frame = ctk.CTkFrame(self.ids_frame, fg_color="transparent")
            self.ids_columns[key] = frame
            top = ctk.CTkFrame(frame, fg_color="transparent")
            top.pack(fill="x")
            ctk.CTkLabel(top, text=marketplace.title, font=theme.font(13, "bold"),
                         text_color=marketplace.color).pack(side="left")
            counter = ctk.CTkLabel(top, text="", font=theme.font(12), text_color=theme.TEXT_MUTED)
            counter.pack(side="right")
            self.ids_counters[key] = counter
            box = ctk.CTkTextbox(frame, height=120, font=theme.font(13), border_width=1, fg_color=theme.INPUT_BG,
                                 border_color=theme.CARD_BORDER, text_color=theme.TEXT, wrap="none")
            box.pack(fill="both", expand=True, pady=(4, 0))
            if text := self.settings.ids_text(key):
                box.insert("1.0", text)
            box.bind("<KeyRelease>", lambda _, k=key: self._update_ids_counter(k))
            box.bind("<<Paste>>", lambda _, k=key: self.after(50, lambda: self._update_ids_counter(k)))
            self.ids_boxes[key] = box
            example = marketplace.ids_placeholder.replace("\n", ", ")
            ctk.CTkLabel(frame, text=f"По одному на строку: артикул или ссылка\nпример: {example}",
                         font=theme.font(11), text_color=theme.TEXT_MUTED, justify="left", anchor="w",
                         wraplength=230).pack(fill="x")
            self._update_ids_counter(key)
        self.mode_container = card.body
        self._refresh_mode()

    def _build_options(self, parent: ctk.CTkBaseClass) -> None:
        card = SectionCard(parent, 2, "Параметры")
        card.pack(fill="x", pady=(0, 12))
        row1 = ctk.CTkFrame(card.body, fg_color="transparent")
        row1.pack(fill="x")
        self.max_products = NumberField(row1, "Товаров на запрос / ссылку", [20, 50, 100, 300, 500, 1000],
                                        self.settings.max_products, width=150)
        self.max_products.pack(side="left", padx=(0, 18))
        self.sort = OptionField(row1, "Сортировка", list(SORT_TITLES.values()), SORT_TITLES[self.settings.sort], 180)
        self.sort.pack(side="left", padx=(0, 18))

        row2 = ctk.CTkFrame(card.body, fg_color="transparent")
        row2.pack(fill="x", pady=(16, 0))
        self.collect_reviews = ctk.CTkSwitch(row2, text="Собирать отзывы", font=theme.font(13), text_color=theme.TEXT,
                                             progress_color=theme.ACCENT, command=self._refresh_reviews_state)
        if self.settings.collect_reviews:
            self.collect_reviews.select()
        self.collect_reviews.pack(side="left", anchor="s", pady=(0, 6))
        self.max_reviews = NumberField(row2, "Отзывов на товар", [5, 10, 20, 50, 100, 300], self.settings.max_reviews)
        self.max_reviews.pack(side="left", padx=(24, 0))
        self._refresh_reviews_state()

    def _build_filters(self, tab: ctk.CTkBaseClass) -> None:
        # The number of marketplaces is not fixed, so this tab is the one place where scrolling is needed.
        parent = ctk.CTkScrollableFrame(tab, fg_color="transparent", scrollbar_button_color=theme.CARD_BORDER)
        parent.pack(fill="both", expand=True)
        common = SectionCard(parent, None, "Для всех площадок",
                             hint="Бренд, категорию или магазин задайте на сайте и вставьте ссылку на выдачу "
                                  "на вкладке «Сбор».")
        common.pack(fill="x", pady=(0, 8))
        row = ctk.CTkFrame(common.body, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Цена, ₽", font=theme.font(13), text_color=theme.TEXT).pack(side="left", padx=(0, 12))
        self.price_min = self._entry(row, "от", self.settings.price_min)
        self.price_min.pack(side="left", padx=(0, 8))
        self.price_max = self._entry(row, "до", self.settings.price_max)
        self.price_max.pack(side="left")

        # A card per marketplace, built from the filters it declares — the window knows none of them by name.
        # Two cards per row, so that four marketplaces still fit on screen without scrolling.
        self.option_widgets: dict[tuple[str, str], Any] = {}
        self.option_values: dict[tuple[str, str], list[str]] = {}
        self.option_labels: dict[tuple[str, str], ctk.CTkLabel] = {}
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="filters")
        cell = 0

        # Buttons that open the site live together: for the user it is one and the same action.
        setups = [(mp, option) for mp in plugins.with_options() for option in mp.options if option.kind == "setup"]
        if setups:
            card = SectionCard(grid, None, "Адрес доставки на сайте",
                               hint="Берётся из адреса в браузере парсера; Маркет — после входа в аккаунт.")
            card.grid(row=0, column=0, columnspan=2, sticky="new", pady=(0, 8))
            row = ctk.CTkFrame(card.body, fg_color="transparent")
            row.pack(fill="x")
            for marketplace, option in setups:
                self._build_option(row, marketplace, option, with_title=True)
            cell = 2  # the card spans both columns, so the marketplaces start on the next row

        with_filters = [mp for mp in plugins.with_options()
                        if any(option.kind != "setup" for option in mp.options)]
        for number, marketplace in enumerate(with_filters, start=cell):
            self._build_marketplace_filters(grid, marketplace, row=number // 2, column=number % 2)

    # A filter card takes half of the window width; this is how much of it one control needs.
    CARD_WIDTH = 340
    OPTION_WIDTHS: ClassVar[dict[str, int]] = {"multi": 320, "switch": 150}

    def _build_marketplace_filters(self, parent: ctk.CTkBaseClass, marketplace: Marketplace,
                                   row: int, column: int) -> None:
        card = SectionCard(parent, None, marketplace.title, hint=marketplace.notes)
        card.grid(row=row, column=column, sticky="new", padx=(0, 5) if column == 0 else (5, 0), pady=(0, 8))
        # Controls flow left to right and wrap to the next line when the card runs out of width.
        options = [o for o in marketplace.options if o.kind != "setup"]
        line, used = None, 0.0
        for option in options:
            width = self.OPTION_WIDTHS.get(option.kind, option.width + 24)
            if line is None or used + width > self.CARD_WIDTH:
                line = ctk.CTkFrame(card.body, fg_color="transparent")
                line.pack(fill="x", pady=(0, 0) if used == 0 else (8, 0))
                used = 0
            self._build_option(line, marketplace, option)
            used += width

    def _build_option(self, row: ctk.CTkFrame, marketplace: Marketplace, option: Option,
                      with_title: bool = False) -> None:
        """One filter widget: a switch, a menu, a list of places, or a button that opens the site."""
        address = (marketplace.key, option.key)
        value = self.settings.option(marketplace.key, option.key)
        if option.kind == "switch":
            switch = self._switch(row, option.title, bool(value))
            switch.pack(side="left", padx=(0, 24))
            self.option_widgets[address] = switch
        elif option.kind == "choice":
            menu = OptionField(row, option.title, option.choice_titles(), option.title_of(value), option.width)
            menu.pack(side="left", padx=(0, 18))
            self.option_widgets[address] = menu
        elif option.kind == "multi":
            box = ctk.CTkFrame(row, fg_color="transparent")
            box.pack(side="left", fill="x", expand=True, padx=(0, 18))
            ctk.CTkLabel(box, text=option.hint or f"{option.title} (поиск идёт по каждому)", justify="left",
                         font=theme.font(12), wraplength=230, text_color=theme.TEXT_MUTED).pack(anchor="w")
            pick = ctk.CTkFrame(box, fg_color="transparent")
            pick.pack(fill="x", pady=(4, 0))
            button = neutral_button(pick, "Выбрать…", lambda: self._pick_locations(marketplace, option), width=110)
            button.pack(side="left", padx=(0, 10))
            label = ctk.CTkLabel(pick, text="", font=theme.font(13), text_color=theme.TEXT, anchor="w",
                                 justify="left", wraplength=200)
            label.pack(side="left", fill="x", expand=True)
            self.option_widgets[address] = button
            self.option_labels[address] = label
            self.option_values[address] = list(value or [])
            self._show_locations(marketplace, option)
        elif option.kind == "setup":
            box = ctk.CTkFrame(row, fg_color="transparent")
            box.pack(side="left", padx=(0, 28))
            # In the shared card the marketplace name is enough; on its own the option explains itself.
            caption = marketplace.title if with_title else (option.hint or option.title)
            color = marketplace.color if with_title else theme.TEXT_MUTED
            font = theme.font(13, "bold") if with_title else theme.font(12)
            ctk.CTkLabel(box, text=caption, font=font, justify="left", wraplength=230,
                         text_color=color).pack(anchor="w")
            button = neutral_button(box, option.button, lambda: self._open_option_setup(marketplace, option),
                                    width=option.width)
            button.pack(anchor="w", pady=(4, 0))
            self.option_widgets[address] = button

    def _entry(self, master: ctk.CTkBaseClass, placeholder: str, value: int | None) -> ctk.CTkEntry:
        entry = ctk.CTkEntry(master, width=110, height=34, font=theme.font(13), border_width=1,
                             fg_color=theme.INPUT_BG, border_color=theme.CARD_BORDER, text_color=theme.TEXT,
                             placeholder_text=placeholder)
        if value is not None:
            entry.insert(0, str(value))
        return entry

    def _switch(self, master: ctk.CTkBaseClass, text: str, value: bool) -> ctk.CTkSwitch:
        switch = ctk.CTkSwitch(master, text=text, font=theme.font(13), text_color=theme.TEXT,
                               progress_color=theme.ACCENT)
        if value:
            switch.select()
        return switch

    @staticmethod
    def _price(entry: ctk.CTkEntry) -> int | str | None:
        text = entry.get().strip().replace(" ", "")
        if not text:
            return None
        return int(text) if text.isdigit() else "error"

    def _pick_locations(self, marketplace: Marketplace, option: Option) -> None:
        address = (marketplace.key, option.key)

        def done(selected: list[str]) -> None:
            self.option_values[address] = selected
            self._show_locations(marketplace, option)

        LocationPicker(self, [name for name, _ in option.catalogue], self.option_values[address], done)

    def _show_locations(self, marketplace: Marketplace, option: Option) -> None:
        address = (marketplace.key, option.key)
        names = self.option_values[address]
        more = f" и ещё {len(names) - 6}" if len(names) > 6 else ""
        self.option_labels[address].configure(text=", ".join(names[:6]) + more if names else "не выбраны")

    def _build_monitoring(self, parent: ctk.CTkBaseClass) -> None:
        save = SectionCard(parent, None, "Сохранить текущие настройки как задание",
                           hint="Задание запоминает вкладки «Сбор», «Фильтры», «Колонки Excel» и «Сохранение». "
                                "Каждый запуск сравнивается с прошлым: в отчёте появятся листы «Изменения» "
                                "и «Динамика цен».")
        save.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(save.body, fg_color="transparent")
        row.pack(fill="x")
        name_box = ctk.CTkFrame(row, fg_color="transparent")
        name_box.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ctk.CTkLabel(name_box, text="Название", font=theme.font(12), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.task_name = ctk.CTkEntry(name_box, height=34, font=theme.font(13), border_width=1,
                                      fg_color=theme.INPUT_BG, border_color=theme.CARD_BORDER, text_color=theme.TEXT,
                                      placeholder_text="Например: Чайники Екатеринбург")
        self.task_name.pack(fill="x", pady=(4, 0))
        time_box = ctk.CTkFrame(row, fg_color="transparent")
        time_box.pack(side="left", padx=(0, 12))
        ctk.CTkLabel(time_box, text="Каждый день в", font=theme.font(12), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.task_time = self._entry(time_box, "09:00", None)
        self.task_time.configure(width=90)
        self.task_time.pack(pady=(4, 0))
        self.save_task_button = ctk.CTkButton(row, text="Сохранить", command=self._save_task, height=34, width=120,
                                              font=theme.font(13, "bold"), fg_color=theme.ACCENT,
                                              hover_color=theme.ACCENT_HOVER)
        self.save_task_button.pack(side="left", anchor="s")
        ctk.CTkLabel(save.body, text="Без времени задание запускается только вручную. По расписанию программа "
                                     "запустится сама, если компьютер включён и вы вошли в Windows.",
                     font=theme.font(11), text_color=theme.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=560).pack(fill="x", pady=(8, 0))

        saved = SectionCard(parent, None, "Задания")
        saved.pack(fill="both", expand=True)
        self.tasks_list = ctk.CTkScrollableFrame(saved.body, height=200, fg_color="transparent")
        self.tasks_list.pack(fill="both", expand=True)
        self._refresh_tasks()

    def _refresh_tasks(self, running: bool = False) -> None:
        if not hasattr(self, "tasks_list"):
            return
        for child in self.tasks_list.winfo_children():
            child.destroy()
        tasks = load_tasks()
        if not tasks:
            ctk.CTkLabel(self.tasks_list, text="Пока нет заданий. Настройте сбор и сохраните его здесь.",
                         font=theme.font(13), text_color=theme.TEXT_MUTED).pack(anchor="w", pady=6)
            return
        state = "disabled" if running else "normal"
        for task in tasks:
            row = ctk.CTkFrame(self.tasks_list, fg_color="transparent")
            row.pack(fill="x", pady=(0, 10))
            # Buttons are packed first so that a long description wraps instead of pushing them out.
            neutral_button(row, "Удалить", lambda t=task: self._delete_task(t), width=80,
                           state=state).pack(side="right", padx=(6, 0))
            neutral_button(row, "Открыть", lambda t=task: self._open_task(t), width=80,
                           state=state).pack(side="right", padx=(6, 0))
            ctk.CTkButton(row, text="Запустить", command=lambda t=task: self._run_task(t), width=96, height=34,
                          font=theme.font(13), fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                          state=state).pack(side="right", padx=(12, 0))
            texts = ctk.CTkFrame(row, fg_color="transparent")
            texts.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(texts, text=task.name, font=theme.font(13, "bold"), text_color=theme.TEXT,
                         anchor="w").pack(fill="x")
            last = ""
            if task.last_run:
                last = f" · последний запуск {datetime.fromisoformat(task.last_run):%d.%m %H:%M}"
            ctk.CTkLabel(texts, text=task.describe() + last, font=theme.font(12), text_color=theme.TEXT_MUTED,
                         anchor="w", justify="left", wraplength=280).pack(fill="x")

    def _save_task(self) -> None:
        name = self.task_name.get().strip()
        time = normalize_time(self.task_time.get())
        problem = check_name(name) or ("Время — в формате ЧЧ:ММ, например 09:00." if time is None else None)
        if problem:
            messagebox.showwarning(APP_NAME, problem, parent=self)
            return
        settings = self._collect_settings()
        if settings is None:
            return
        existing = get_task(name)
        try:
            if time:
                schedule(name, time)
            elif existing and existing.time:
                unschedule(name)
        except SchedulerError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        task = Task(name, replace(settings, task_name=name), time or "",
                    existing.last_run if existing else "", existing.last_file if existing else "")
        upsert_task(task)
        self._log("success", f"Задание «{name}» сохранено" + (f": каждый день в {time}" if time else ""))
        self._refresh_tasks()

    def _run_task(self, task: Task) -> None:
        if self.worker and self.worker.is_alive():
            return
        if problems := task.settings.validate():
            messagebox.showwarning(APP_NAME, "\n".join(problems), parent=self)
            return
        self._launch(task.settings, f"Старт задания «{task.name}»")

    def _open_task(self, task: Task) -> None:
        """Put the task's settings into the form, to look at them or change and save them again."""
        self._apply_settings(task.settings)
        self.task_name.delete(0, "end")
        self.task_name.insert(0, task.name)
        self.task_time.delete(0, "end")
        if task.time:
            self.task_time.insert(0, task.time)
        self._log("info", f"Настройки задания «{task.name}» открыты. Измените их и нажмите «Сохранить» "
                          "на вкладке «Мониторинг», чтобы обновить задание.")
        self.tabs.set("Сбор")

    def _delete_task(self, task: Task) -> None:
        if not messagebox.askyesno(APP_NAME, f"Удалить задание «{task.name}» и его расписание?", parent=self):
            return
        try:
            unschedule(task.name)
        except SchedulerError as exc:
            messagebox.showwarning(APP_NAME, str(exc), parent=self)
        delete_task(task.name)
        self._log("info", f"Задание «{task.name}» удалено")
        self._refresh_tasks()

    @staticmethod
    def _set_switch(switch: ctk.CTkSwitch, value: bool) -> None:
        if value:
            switch.select()
        else:
            switch.deselect()

    @staticmethod
    def _set_entry(entry: ctk.CTkEntry, value: object) -> None:
        entry.delete(0, "end")
        if value not in (None, ""):
            entry.insert(0, str(value))

    def _apply_settings(self, settings: ParseSettings) -> None:
        """Show ``settings`` in every widget of the form."""
        for key, var in self.mp_vars.items():
            var.set(key in settings.marketplaces)
            self.mp_toggles[key]._refresh()
        self.mode.set(MODE_TITLES[settings.mode])
        self._set_entry(self.query, settings.query)
        for key, box in self.ids_boxes.items():
            box.delete("1.0", "end")
            box.insert("1.0", settings.ids_text(key))
            self._update_ids_counter(key)
        self.max_products.combo.set(str(settings.max_products))
        self.sort.menu.set(SORT_TITLES[settings.sort])
        self._set_switch(self.collect_reviews, settings.collect_reviews)
        self.max_reviews.combo.set(str(settings.max_reviews))
        self._set_entry(self.price_min, settings.price_min)
        self._set_entry(self.price_max, settings.price_max)
        for marketplace in plugins.with_options():
            for option in marketplace.options:
                self._apply_option(marketplace, option, settings.option(marketplace.key, option.key))
        for fields, variables, selected in ((product_fields(), self.product_field_vars, settings.product_fields),
                                            (review_fields(), self.review_field_vars, settings.review_fields)):
            for spec in fields:
                variables[spec.key].set(spec.required or spec.key in selected)
        self._set_entry(self.output_dir, settings.output_dir)
        self._set_switch(self.open_when_done, settings.open_when_done)
        self._set_switch(self.show_browser, settings.show_browser)
        self._refresh_mode()
        self._refresh_reviews_state()
        self._refresh_marketplace_state()

    def _apply_option(self, marketplace: Marketplace, option: Option, value: Any) -> None:
        widget = self.option_widgets.get((marketplace.key, option.key))
        if widget is None:
            return
        if option.kind == "switch":
            self._set_switch(widget, bool(value))
        elif option.kind == "choice":
            widget.menu.set(option.title_of(value))
        elif option.kind == "multi":
            self.option_values[(marketplace.key, option.key)] = list(value or [])
            self._show_locations(marketplace, option)

    def _read_option(self, marketplace: Marketplace, option: Option) -> Any:
        widget = self.option_widgets.get((marketplace.key, option.key))
        if option.kind == "switch":
            return bool(widget.get())
        if option.kind == "choice":
            return option.value_of(widget.get())
        if option.kind == "multi":
            return list(self.option_values[(marketplace.key, option.key)])
        return None

    def _build_fields(self, parent: ctk.CTkBaseClass) -> None:
        card = SectionCard(parent, None, "Какие колонки попадут в файл",
                           hint="Бренд, продавец и категория для Ozon и Маркета, а также адрес, просмотры и тип "
                                "продавца для Авито есть только на странице товара: парсер откроет каждую, "
                                "сбор займёт больше времени. На Авито — около 6 секунд на объявление.")
        card.pack(fill="x", pady=(0, 14))
        tabs = ctk.CTkTabview(card.body, height=10, fg_color=theme.INPUT_BG, corner_radius=10,
                              segmented_button_selected_color=theme.ACCENT,
                              segmented_button_selected_hover_color=theme.ACCENT_HOVER,
                              segmented_button_unselected_color=theme.NEUTRAL_BUTTON,
                              segmented_button_unselected_hover_color=theme.NEUTRAL_BUTTON_HOVER,
                              segmented_button_fg_color=theme.NEUTRAL_BUTTON, text_color=theme.TEXT)
        tabs.pack(fill="x")
        self.product_field_vars = self._field_checkboxes(tabs.add("Товары"), product_fields(),
                                                         self.settings.product_fields)
        self.review_field_vars = self._field_checkboxes(tabs.add("Отзывы"), review_fields(),
                                                        self.settings.review_fields)

    def _field_checkboxes(
        self, tab: ctk.CTkFrame, fields: list[Field], selected: list[str]
    ) -> dict[str, ctk.BooleanVar]:
        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="x", padx=6, pady=(4, 0))
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="fields")
        variables: dict[str, ctk.BooleanVar] = {}
        short = plugins.short_titles()
        for index, spec in enumerate(fields):
            var = ctk.BooleanVar(value=spec.required or spec.key in selected)
            suffix = "  · " + ", ".join(short.get(k, k) for k in spec.only) if spec.only else ""
            box = ctk.CTkCheckBox(grid, text=spec.title + suffix, variable=var, font=theme.font(13),
                                  text_color=theme.TEXT, fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                  checkbox_width=20, checkbox_height=20, corner_radius=5,
                                  state="disabled" if spec.required else "normal")
            box.grid(row=index // 3, column=index % 3, sticky="w", pady=5)
            variables[spec.key] = var

        buttons = ctk.CTkFrame(tab, fg_color="transparent")
        buttons.pack(fill="x", padx=6, pady=(10, 6))

        def set_all(value: bool) -> None:
            for spec in fields:
                variables[spec.key].set(value or spec.required)

        def set_default() -> None:
            for spec in fields:
                variables[spec.key].set(spec.default or spec.required)

        neutral_button(buttons, "Выбрать все", lambda: set_all(True), width=110).pack(side="left")
        neutral_button(buttons, "Снять все", lambda: set_all(False), width=110).pack(side="left", padx=8)
        neutral_button(buttons, "По умолчанию", set_default, width=120).pack(side="left")
        return variables

    def _build_output(self, parent: ctk.CTkBaseClass) -> None:
        card = SectionCard(parent, None, "Куда сохранять отчёт")
        card.pack(fill="x", pady=(0, 4))
        row = ctk.CTkFrame(card.body, fg_color="transparent")
        row.pack(fill="x")
        self.output_dir = ctk.CTkEntry(row, height=34, font=theme.font(13), border_width=1, fg_color=theme.INPUT_BG,
                                       border_color=theme.CARD_BORDER, text_color=theme.TEXT)
        self.output_dir.insert(0, self.settings.output_dir)
        self.output_dir.pack(side="left", fill="x", expand=True)
        neutral_button(row, "Обзор…", self._choose_dir, width=100).pack(side="left", padx=(8, 0))

        switches = ctk.CTkFrame(card.body, fg_color="transparent")
        switches.pack(fill="x", pady=(14, 0))
        self.open_when_done = ctk.CTkSwitch(switches, text="Открыть файл после сбора", font=theme.font(13),
                                            text_color=theme.TEXT, progress_color=theme.ACCENT)
        if self.settings.open_when_done:
            self.open_when_done.select()
        self.open_when_done.pack(side="left")
        self.show_browser = ctk.CTkSwitch(switches, text="Показывать окно браузера", font=theme.font(13),
                                          text_color=theme.TEXT, progress_color=theme.ACCENT)
        if self.settings.show_browser:
            self.show_browser.select()
        self.show_browser.pack(side="left", padx=(28, 0))

    def _build_run_panel(self, master: ctk.CTkFrame) -> None:
        panel = ctk.CTkFrame(master, fg_color=theme.CARD_BG, corner_radius=14, border_width=1,
                             border_color=theme.CARD_BORDER)
        panel.grid(row=0, column=1, sticky="nsew")

        self.start_button = ctk.CTkButton(panel, text="Начать сбор", height=50, corner_radius=12,
                                          font=theme.font(17, "bold"), fg_color=theme.ACCENT,
                                          hover_color=theme.ACCENT_HOVER, command=self._start)
        self.start_button.pack(fill="x", padx=20, pady=(20, 8))
        # Packed only while a job is running (see _set_running).
        self.stop_button = ctk.CTkButton(panel, text="Остановить", height=38, corner_radius=10, font=theme.font(14),
                                         fg_color=theme.DANGER, hover_color=theme.DANGER_HOVER, command=self._stop)

        self.progress = ctk.CTkProgressBar(panel, height=10, corner_radius=5, progress_color=theme.ACCENT,
                                           fg_color=theme.NEUTRAL_BUTTON)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=20, pady=(22, 6))
        status_row = ctk.CTkFrame(panel, fg_color="transparent")
        status_row.pack(fill="x", padx=20)
        self.status = ctk.CTkLabel(status_row, text="Готов к работе", font=theme.font(13), text_color=theme.TEXT_MUTED,
                                   anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.percent = ctk.CTkLabel(status_row, text="", font=theme.font(13, "bold"), text_color=theme.TEXT)
        self.percent.pack(side="right")

        ctk.CTkLabel(panel, text="Журнал", font=theme.font(14, "bold"), text_color=theme.TEXT, anchor="w").pack(
            fill="x", padx=20, pady=(18, 4))
        self.log_box = ctk.CTkTextbox(panel, height=150, font=ctk.CTkFont(family="Consolas", size=12),
                                      fg_color=theme.INPUT_BG, text_color=theme.TEXT, border_width=0,
                                      corner_radius=10, wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=20)
        self.log_box.insert("1.0", "Здесь появится ход сбора: страницы выдачи, карточки, отзывы.", "time")
        self.log_box.configure(state="disabled")
        self._apply_log_colors()

        self.result_card = ctk.CTkFrame(panel, fg_color=theme.INPUT_BG, corner_radius=12)
        self.result_title = ctk.CTkLabel(self.result_card, text="", font=theme.font(14, "bold"), text_color=theme.TEXT,
                                         anchor="w", justify="left")
        self.result_title.pack(fill="x", padx=14, pady=(12, 0))
        self.result_path = ctk.CTkLabel(self.result_card, text="", font=theme.font(12), text_color=theme.TEXT_MUTED,
                                        anchor="w", justify="left", wraplength=380)
        self.result_path.pack(fill="x", padx=14)
        buttons = ctk.CTkFrame(self.result_card, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(8, 12))
        self.open_file_button = ctk.CTkButton(buttons, text="Открыть файл", height=34, font=theme.font(13),
                                              fg_color=theme.SUCCESS, hover_color=theme.SUCCESS, corner_radius=8,
                                              command=self._open_result_file)
        self.open_file_button.pack(side="left")
        neutral_button(buttons, "Показать в папке", self._open_result_folder, width=140).pack(side="left", padx=8)
        self.result_bottom = ctk.CTkFrame(panel, fg_color="transparent", height=20)
        self.result_bottom.pack(fill="x")

    # ------------------------------------------------------------------ state

    def _set_appearance(self, value: str) -> None:
        ctk.set_appearance_mode({"Светлая": "light", "Тёмная": "dark"}.get(value, "system"))
        self._apply_log_colors()

    def _apply_log_colors(self) -> None:
        index = 1 if ctk.get_appearance_mode() == "Dark" else 0
        for level, colors in LOG_COLORS.items():
            self.log_box.tag_config(level, foreground=colors[index])
        self.log_box.tag_config("time", foreground=theme.TEXT_MUTED[index])

    def _selected_marketplaces(self) -> list[str]:
        return [key for key, var in self.mp_vars.items() if var.get()]

    def _refresh_marketplace_state(self) -> None:
        selected = self._selected_marketplaces()
        for frame in self.ids_columns.values():
            frame.grid_forget()
        visible = [k for k in self.marketplaces if k in selected] or list(self.marketplaces)
        # Visible boxes share the whole width; columns of hidden marketplaces collapse.
        for column in range(len(self.marketplaces)):
            shown = column < len(visible)
            self.ids_frame.grid_columnconfigure(column, weight=1 if shown else 0, uniform="ids" if shown else "")
        for column, key in enumerate(visible):
            self.ids_columns[key].grid(row=0, column=column, sticky="nsew", padx=(8 if column else 0, 0))
        # Filters of a marketplace that is switched off are greyed out.
        for (marketplace_key, _), widget in self.option_widgets.items():
            state = "normal" if marketplace_key in selected else "disabled"
            target = widget.menu if isinstance(widget, OptionField) else widget
            target.configure(state=state)

    def _refresh_mode(self) -> None:
        self.query_frame.pack_forget()
        self.ids_frame.pack_forget()
        if self._current_mode() == InputMode.QUERY:
            self.query_frame.pack(fill="x", pady=(12, 0))
        else:
            self.ids_frame.pack(fill="x", pady=(12, 0))
        if hasattr(self, "sort"):
            self.sort.menu.configure(state="normal" if self._current_mode() == InputMode.QUERY else "disabled")

    def _current_mode(self) -> InputMode:
        return next(mode for mode, title in MODE_TITLES.items() if title == self.mode.get())

    def _refresh_reviews_state(self) -> None:
        self.max_reviews.set_enabled(bool(self.collect_reviews.get()))

    def _update_ids_counter(self, key: str) -> None:
        parsed = parse_ids(self.ids_boxes[key].get("1.0", "end"), key)
        count = len(parsed.for_marketplace(key))
        text = f"распознано: {count}" if count else ""
        if parsed.invalid:
            text += f"{' · ' if text else ''}не распознано: {len(parsed.invalid)}"
        self.ids_counters[key].configure(text=text)

    def _collect_settings(self) -> ParseSettings | None:
        problems = []
        max_products = self.max_products.get()
        max_reviews = self.max_reviews.get()
        if max_products is None:
            problems.append("Количество товаров должно быть числом.")
        if self.collect_reviews.get() and max_reviews is None:
            problems.append("Количество отзывов должно быть числом.")
        price_min, price_max = (self._price(entry) for entry in (self.price_min, self.price_max))
        if "error" in (price_min, price_max):
            problems.append("Цена в фильтре должна быть целым числом рублей.")
            price_min = price_max = None
        settings = ParseSettings(
            marketplaces=self._selected_marketplaces(),
            mode=self._current_mode(),
            query=self.query.get().strip(),
            ids={key: box.get("1.0", "end").strip() for key, box in self.ids_boxes.items()},
            max_products=max_products or self.settings.max_products,
            sort=next(order for order, title in SORT_TITLES.items() if title == self.sort.get()),
            collect_reviews=bool(self.collect_reviews.get()),
            max_reviews=max_reviews or self.settings.max_reviews,
            price_min=price_min,
            price_max=price_max,
            options={marketplace.key: {option.key: self._read_option(marketplace, option)
                                       for option in marketplace.options if option.kind != "setup"}
                     for marketplace in plugins.with_options()},
            product_fields=[k for k, v in self.product_field_vars.items() if v.get()],
            review_fields=[k for k, v in self.review_field_vars.items() if v.get()],
            output_dir=self.output_dir.get().strip(),
            open_when_done=bool(self.open_when_done.get()),
            show_browser=bool(self.show_browser.get()),
        )
        problems += settings.validate()
        self.settings = settings
        settings.save()
        if problems:
            messagebox.showwarning(APP_NAME, "\n".join(problems), parent=self)
            return None
        return settings

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal",
                                    text="Идёт сбор…" if running else "Начать сбор")
        if running:
            self.stop_button.configure(state="normal", text="Остановить")
            self.stop_button.pack(fill="x", padx=20, before=self.progress)
        else:
            self.stop_button.pack_forget()
        setup_buttons = [self.option_widgets[address] for address in self.setup_addresses]
        for button in (*setup_buttons, self.save_task_button):
            button.configure(state="disabled" if running else "normal")
        self._refresh_tasks(running)

    # ------------------------------------------------------------------ actions

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        settings = self._collect_settings()
        if settings is None:
            return
        self._launch(settings, "Старт: " + (f"запрос «{settings.query}»" if settings.mode == InputMode.QUERY
                                            else "список артикулов и ссылок"))

    def _launch(self, settings: ParseSettings, title: str) -> None:
        self.running_settings = settings
        self.result_card.pack_forget()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.progress.set(0)
        self.percent.configure(text="0%")
        self.cancel_event.clear()
        self._set_running(True)
        self._log("info", title)

        def work() -> None:
            reporter = Reporter(
                on_log=lambda level, message: self.events.put(("log", (level, message))),
                on_progress=lambda fraction, text: self.events.put(("progress", (fraction, text))),
                cancel_event=self.cancel_event,
            )
            try:
                result = asyncio.run(run(settings, reporter))
            except Exception as exc:
                log.exception("Run failed")
                self.events.put(("log", ("error", f"Ошибка: {exc}")))
                result = RunResult(errors=[str(exc)])
            self.events.put(("done", result))

        self.worker = threading.Thread(target=work, name="parser", daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.cancel_event.set()
        self.stop_button.configure(state="disabled", text="Останавливаю…")

    def _open_option_setup(self, marketplace: Marketplace, option: Option) -> None:
        self._open_site_setup(marketplace.title, option.url or f"https://{marketplace.site}/", option.instruction)

    def _open_site_setup(self, title: str, url: str, instruction: str) -> None:
        """Open the parser's browser profile on ``url`` so that the user sets the delivery region by hand."""
        if self.worker and self.worker.is_alive():
            return
        messagebox.showinfo(APP_NAME, f"Откроется браузер парсера с сайтом {title}.\n\n{instruction}", parent=self)

        def work() -> None:
            async def session() -> None:
                async with Browser(app_data_dir() / "browser-profile", headless=False) as browser:
                    page = await browser.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                    await browser.context.wait_for_event("close", timeout=0)

            try:
                asyncio.run(session())
                self.events.put(("log", ("success", f"Браузер закрыт, адрес {title} сохранён в профиле.")))
            except BrowserError as exc:
                self.events.put(("log", ("error", str(exc))))
            except Exception as exc:
                log.debug("%s setup browser closed: %s", title, exc)
            self.events.put(("done", None))

        self._set_running(True)
        self.stop_button.configure(state="disabled")
        self.status.configure(text=f"Браузер открыт: выберите адрес на сайте {title} и закройте окно")
        self.worker = threading.Thread(target=work, name="site-setup", daemon=True)
        self.worker.start()

    def _choose_dir(self) -> None:
        path = filedialog.askdirectory(parent=self, initialdir=self.output_dir.get() or str(Path.home()))
        if path:
            self.output_dir.delete(0, "end")
            self.output_dir.insert(0, str(Path(path)))

    def _open_result_file(self) -> None:
        if self.last_result and self.last_result.path and self.last_result.path.exists():
            os.startfile(self.last_result.path)

    def _open_result_folder(self) -> None:
        if self.last_result and self.last_result.path:
            subprocess.Popen(["explorer", "/select,", str(self.last_result.path)])

    # ------------------------------------------------------------------ events

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    level, message = payload  # type: ignore[misc]
                    self._log(level, message)
                elif kind == "progress":
                    fraction, text = payload  # type: ignore[misc]
                    self.progress.set(fraction)
                    self.percent.configure(text=f"{fraction:.0%}")
                    self.status.configure(text=text)
                elif kind == "done":
                    self._finish(payload)  # type: ignore[arg-type]
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _log(self, level: str, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", datetime.now().strftime("%H:%M:%S  "), "time")
        self.log_box.insert("end", message + "\n", level if level in LOG_COLORS else ())
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        getattr(log, "warning" if level == "warning" else "error" if level == "error" else "info")(message)

    def _finish(self, result: RunResult | None) -> None:
        self._set_running(False)
        if self._closing:
            self.destroy()
            return
        if result is None:  # the Ozon address browser was closed
            self.status.configure(text="Готов к работе")
            return
        self.last_result = result
        settings = self.running_settings or self.settings
        if settings.task_name:
            mark_run(settings.task_name, datetime.now(), result.path)
            self._refresh_tasks()
        if result.path:
            reviews = f", отзывов: {len(result.reviews)}" if settings.collect_reviews else ""
            prefix = "Остановлено, сохранено частично" if result.cancelled else "Готово"
            self.result_title.configure(text=f"{prefix}: товаров {len(result.products)}{reviews}")
            self.result_path.configure(text=result.path.name)
            self.result_card.pack(fill="x", padx=20, pady=(12, 20), before=self.result_bottom)
            if settings.open_when_done and not result.cancelled:
                self._open_result_file()
        elif result.errors:
            self.status.configure(text="Сбор завершился с ошибками — подробности в журнале")
        if result.errors and result.path:
            self.status.configure(text="Готово, но с ошибками — подробности в журнале")

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "Сбор ещё идёт. Остановить и выйти?", parent=self):
                return
            self._closing = True
            self.cancel_event.set()
            self.status.configure(text="Завершаю работу…")
            self.after(15_000, self.destroy)  # do not hang forever on a stuck browser
            return
        self._collect_settings_silently()
        self.destroy()

    def _collect_settings_silently(self) -> None:
        try:
            self.settings.query = self.query.get().strip()
            self.settings.output_dir = self.output_dir.get().strip()
            self.settings.save()
        except Exception:
            log.debug("Could not save settings on exit", exc_info=True)


def setup_logging() -> None:
    log_dir = app_data_dir() / "logs"
    log_dir.mkdir(exist_ok=True)
    handler = RotatingFileHandler(log_dir / "parser.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def main() -> None:
    setup_logging()
    ctk.set_appearance_mode("system")
    app = App()
    app.mainloop()
