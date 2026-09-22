"""Main window."""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import subprocess
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import APP_NAME, __version__
from ..browser import Browser, BrowserError
from ..fields import PRODUCT_FIELDS, REVIEW_FIELDS, Field
from ..inputs import parse_ids
from ..marketplaces import Reporter
from ..regions import WB_REGIONS
from ..runner import RunResult, run
from ..settings import (
    MARKETPLACES,
    SORT_TITLES,
    InputMode,
    ParseSettings,
    app_data_dir,
)
from . import theme
from .widgets import MarketplaceToggle, NumberField, OptionField, SectionCard, neutral_button

log = logging.getLogger(__name__)

MODE_TITLES = {InputMode.QUERY: "Поисковый запрос", InputMode.IDS: "Артикулы и ссылки"}
MARKETPLACE_SUBTITLES = {"wb": "wildberries.ru", "ozon": "ozon.ru"}
IDS_PLACEHOLDERS = {
    "wb": "145726284\nhttps://www.wildberries.ru/catalog/839226871/detail.aspx",
    "ozon": "3627230434\nhttps://www.ozon.ru/product/…-5413455528/",
}
LOG_COLORS = {"warning": theme.WARNING, "error": theme.DANGER, "success": theme.SUCCESS}


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__(fg_color=theme.APP_BG)
        self.settings = ParseSettings.load()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_result: RunResult | None = None
        self._closing = False

        self.title(f"{APP_NAME} {__version__}")
        self._size_window(1240, 840)
        icon = theme.resource_path("assets/icon.ico")
        if icon.exists():
            self.after(250, lambda: self.iconbitmap(str(icon)))  # CTk resets the icon right after start

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
        fields_tab = self.tabs.add("Колонки Excel")
        output_tab = self.tabs.add("Сохранение")
        self._build_input(collect_tab)
        self._build_options(collect_tab)
        self._build_fields(fields_tab)
        self._build_output(output_tab)

        self._build_run_panel(content)
        self._refresh_marketplace_state()
        self._refresh_mode()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_events)

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
        ctk.CTkLabel(titles, text="Сбор товаров, цен и отзывов с Wildberries и Ozon в Excel",
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
        row.grid_columnconfigure((0, 1), weight=1, uniform="mp")
        self.mp_vars: dict[str, ctk.BooleanVar] = {}
        for column, (key, title) in enumerate(MARKETPLACES.items()):
            var = ctk.BooleanVar(value=key in self.settings.marketplaces)
            self.mp_vars[key] = var
            toggle = MarketplaceToggle(row, key, title, MARKETPLACE_SUBTITLES[key], var,
                                       self._refresh_marketplace_state)
            toggle.grid(row=0, column=column, sticky="ew", padx=(0, 10) if column == 0 else (10, 0))

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
        self.ids_frame.grid_columnconfigure((0, 1), weight=1, uniform="ids")
        self.ids_boxes: dict[str, ctk.CTkTextbox] = {}
        self.ids_counters: dict[str, ctk.CTkLabel] = {}
        self.ids_columns: dict[str, ctk.CTkFrame] = {}
        for key, title in MARKETPLACES.items():
            frame = ctk.CTkFrame(self.ids_frame, fg_color="transparent")
            self.ids_columns[key] = frame
            top = ctk.CTkFrame(frame, fg_color="transparent")
            top.pack(fill="x")
            ctk.CTkLabel(top, text=title, font=theme.font(13, "bold"),
                         text_color=theme.MARKETPLACE_COLORS[key]).pack(side="left")
            counter = ctk.CTkLabel(top, text="", font=theme.font(12), text_color=theme.TEXT_MUTED)
            counter.pack(side="right")
            self.ids_counters[key] = counter
            box = ctk.CTkTextbox(frame, height=120, font=theme.font(13), border_width=1, fg_color=theme.INPUT_BG,
                                 border_color=theme.CARD_BORDER, text_color=theme.TEXT, wrap="none")
            box.pack(fill="both", expand=True, pady=(4, 0))
            text = getattr(self.settings, f"{key}_ids")
            if text:
                box.insert("1.0", text)
            box.bind("<KeyRelease>", lambda _, k=key: self._update_ids_counter(k))
            box.bind("<<Paste>>", lambda _, k=key: self.after(50, lambda: self._update_ids_counter(k)))
            self.ids_boxes[key] = box
            ctk.CTkLabel(frame, text="По одному на строку: артикул или ссылка\nпример: " +
                         IDS_PLACEHOLDERS[key].replace("\n", ", "), font=theme.font(11),
                         text_color=theme.TEXT_MUTED, justify="left", anchor="w", wraplength=300).pack(fill="x")
            self._update_ids_counter(key)
        self.mode_container = card.body
        self._refresh_mode()

    def _build_options(self, parent: ctk.CTkBaseClass) -> None:
        card = SectionCard(parent, 2, "Параметры")
        card.pack(fill="x", pady=(0, 12))
        row1 = ctk.CTkFrame(card.body, fg_color="transparent")
        row1.pack(fill="x")
        self.max_products = NumberField(row1, "Товаров с каждой площадки", [20, 50, 100, 300, 500, 1000],
                                        self.settings.max_products, width=150)
        self.max_products.pack(side="left", padx=(0, 18))
        self.sort = OptionField(row1, "Сортировка", list(SORT_TITLES.values()), SORT_TITLES[self.settings.sort], 180)
        self.sort.pack(side="left", padx=(0, 18))
        self.region = OptionField(row1, "Регион доставки (WB)", list(WB_REGIONS), self.settings.region, 180)
        self.region.pack(side="left")

        row2 = ctk.CTkFrame(card.body, fg_color="transparent")
        row2.pack(fill="x", pady=(16, 0))
        self.collect_reviews = ctk.CTkSwitch(row2, text="Собирать отзывы", font=theme.font(13), text_color=theme.TEXT,
                                             progress_color=theme.ACCENT, command=self._refresh_reviews_state)
        if self.settings.collect_reviews:
            self.collect_reviews.select()
        self.collect_reviews.pack(side="left", anchor="s", pady=(0, 6))
        self.max_reviews = NumberField(row2, "Отзывов на товар", [5, 10, 20, 50, 100, 300], self.settings.max_reviews)
        self.max_reviews.pack(side="left", padx=(24, 0))
        # Ozon takes its region from the address saved in the parser's browser profile, not from the menu above.
        ozon_box = ctk.CTkFrame(row2, fg_color="transparent")
        ozon_box.pack(side="right")
        ctk.CTkLabel(ozon_box, text="Регион Ozon", font=theme.font(12), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.ozon_setup_button = neutral_button(ozon_box, "Выбрать адрес…", self._open_ozon_setup, width=150)
        self.ozon_setup_button.pack(anchor="w", pady=(4, 0))
        self._refresh_reviews_state()

    def _build_fields(self, parent: ctk.CTkBaseClass) -> None:
        card = SectionCard(parent, None, "Какие колонки попадут в файл",
                           hint="Бренд, продавец, категория и цена по карте для Ozon есть только в карточке товара: "
                                "парсер откроет каждую карточку, сбор займёт больше времени.")
        card.pack(fill="x", pady=(0, 14))
        tabs = ctk.CTkTabview(card.body, height=10, fg_color=theme.INPUT_BG, corner_radius=10,
                              segmented_button_selected_color=theme.ACCENT,
                              segmented_button_selected_hover_color=theme.ACCENT_HOVER,
                              segmented_button_unselected_color=theme.NEUTRAL_BUTTON,
                              segmented_button_unselected_hover_color=theme.NEUTRAL_BUTTON_HOVER,
                              segmented_button_fg_color=theme.NEUTRAL_BUTTON, text_color=theme.TEXT)
        tabs.pack(fill="x")
        self.product_field_vars = self._field_checkboxes(tabs.add("Товары"), PRODUCT_FIELDS,
                                                         self.settings.product_fields)
        self.review_field_vars = self._field_checkboxes(tabs.add("Отзывы"), REVIEW_FIELDS,
                                                        self.settings.review_fields)

    def _field_checkboxes(
        self, tab: ctk.CTkFrame, fields: list[Field], selected: list[str]
    ) -> dict[str, ctk.BooleanVar]:
        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="x", padx=6, pady=(4, 0))
        grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="fields")
        variables: dict[str, ctk.BooleanVar] = {}
        for index, spec in enumerate(fields):
            var = ctk.BooleanVar(value=spec.required or spec.key in selected)
            suffix = {"wb": "  · WB", "ozon": "  · Ozon"}.get(spec.only or "", "")
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
        visible = [k for k in MARKETPLACES if k in selected] or list(MARKETPLACES)
        for column, key in enumerate(visible):
            self.ids_columns[key].grid(row=0, column=column, columnspan=2 if len(visible) == 1 else 1, sticky="nsew",
                                       padx=(0, 8) if column == 0 and len(visible) > 1 else (8, 0) if column else 0)
        self.region.menu.configure(state="normal" if "wb" in selected else "disabled")

    def _refresh_mode(self) -> None:
        self.query_frame.pack_forget()
        self.ids_frame.pack_forget()
        if self._current_mode() == InputMode.QUERY:
            self.query_frame.pack(fill="x", pady=(12, 0))
        else:
            self.ids_frame.pack(fill="x", pady=(12, 0))
        if hasattr(self, "sort"):
            self.sort.menu.configure(state="normal" if self._current_mode() == InputMode.QUERY else "disabled")
            self.max_products.set_enabled(self._current_mode() == InputMode.QUERY)

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
        settings = ParseSettings(
            marketplaces=self._selected_marketplaces(),
            mode=self._current_mode(),
            query=self.query.get().strip(),
            wb_ids=self.ids_boxes["wb"].get("1.0", "end").strip(),
            ozon_ids=self.ids_boxes["ozon"].get("1.0", "end").strip(),
            max_products=max_products or self.settings.max_products,
            sort=next(order for order, title in SORT_TITLES.items() if title == self.sort.get()),
            collect_reviews=bool(self.collect_reviews.get()),
            max_reviews=max_reviews or self.settings.max_reviews,
            region=self.region.get(),
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
        self.ozon_setup_button.configure(state="disabled" if running else "normal")

    # ------------------------------------------------------------------ actions

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        settings = self._collect_settings()
        if settings is None:
            return
        self.result_card.pack_forget()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        self.progress.set(0)
        self.percent.configure(text="0%")
        self.cancel_event.clear()
        self._set_running(True)
        self._log("info", "Старт: " + (f"запрос «{settings.query}»" if settings.mode == InputMode.QUERY
                                       else "список артикулов"))

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

    def _open_ozon_setup(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        messagebox.showinfo(APP_NAME, "Откроется браузер парсера с сайтом Ozon.\n\nУкажите адрес доставки "
                                      "(вверху страницы) и закройте окно браузера — адрес сохранится "
                                      "для следующих запусков.", parent=self)

        def work() -> None:
            async def session() -> None:
                async with Browser(app_data_dir() / "browser-profile", headless=False) as browser:
                    page = await browser.new_page()
                    await page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=60_000)
                    await browser.context.wait_for_event("close", timeout=0)

            try:
                asyncio.run(session())
                self.events.put(("log", ("success", "Браузер закрыт, адрес Ozon сохранён в профиле.")))
            except BrowserError as exc:
                self.events.put(("log", ("error", str(exc))))
            except Exception as exc:
                log.debug("Ozon setup browser closed: %s", exc)
            self.events.put(("done", None))

        self._set_running(True)
        self.stop_button.configure(state="disabled")
        self.status.configure(text="Браузер открыт: выберите адрес на Ozon и закройте окно")
        self.worker = threading.Thread(target=work, name="ozon-setup", daemon=True)
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
        if result.path:
            reviews = f", отзывов: {len(result.reviews)}" if self.settings.collect_reviews else ""
            prefix = "Остановлено, сохранено частично" if result.cancelled else "Готово"
            self.result_title.configure(text=f"{prefix}: товаров {len(result.products)}{reviews}")
            self.result_path.configure(text=result.path.name)
            self.result_card.pack(fill="x", padx=20, pady=(12, 20), before=self.result_bottom)
            if self.settings.open_when_done and not result.cancelled:
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
