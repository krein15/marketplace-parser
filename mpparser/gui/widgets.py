"""Reusable widgets of the main window."""

from __future__ import annotations

import re
from collections.abc import Callable

import customtkinter as ctk

from . import theme


class SectionCard(ctk.CTkFrame):
    """A rounded card with a title; put content into ``self.body``. ``number`` adds a step badge."""

    def __init__(self, master: ctk.CTkBaseClass, number: int | None, title: str, hint: str = "") -> None:
        super().__init__(master, fg_color=theme.CARD_BG, corner_radius=14, border_width=1,
                         border_color=theme.CARD_BORDER)
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=18, pady=(12, 0))
        if number is not None:
            ctk.CTkLabel(header, text=str(number), width=24, height=24, corner_radius=12, fg_color=theme.ACCENT,
                         text_color="#FFFFFF", font=theme.font(12, "bold")).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(header, text=title, font=theme.font(15, "bold"), text_color=theme.TEXT).pack(side="left")
        if hint:
            ctk.CTkLabel(self, text=hint, font=theme.font(12), text_color=theme.TEXT_MUTED, justify="left",
                         anchor="w", wraplength=560).pack(fill="x", padx=18, pady=(4, 0))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=18, pady=(8, 14))


class MarketplaceToggle(ctk.CTkFrame):
    """A marketplace on/off card in the marketplace's brand colour."""

    def __init__(self, master: ctk.CTkBaseClass, key: str, title: str,
                 variable: ctk.BooleanVar, command: Callable[[], None]) -> None:
        color = theme.MARKETPLACE_COLORS[key]
        super().__init__(master, fg_color=theme.INPUT_BG, corner_radius=12, border_width=2, border_color=color)
        self._color = color
        self._variable = variable
        self.checkbox = ctk.CTkCheckBox(self, text=title, variable=variable, command=self._changed,
                                        font=theme.font(13, "bold"), text_color=theme.TEXT,
                                        fg_color=color, hover_color=color, border_color=color,
                                        checkbox_width=20, checkbox_height=20, corner_radius=6)
        self.checkbox.pack(side="left", padx=(10, 4), pady=10)
        self._command = command
        self._refresh()

    def _changed(self) -> None:
        self._refresh()
        self._command()

    def _refresh(self) -> None:
        self.configure(border_color=self._color if self._variable.get() else theme.CARD_BORDER)


class NumberField(ctk.CTkFrame):
    """Label + editable combobox with preset values."""

    def __init__(self, master: ctk.CTkBaseClass, label: str, presets: list[int], value: int, width: int = 110) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=label, font=theme.font(12), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.combo = ctk.CTkComboBox(self, values=[str(v) for v in presets], width=width, height=34,
                                     font=theme.font(13), dropdown_font=theme.font(13), border_width=1,
                                     fg_color=theme.INPUT_BG, border_color=theme.CARD_BORDER,
                                     button_color=theme.NEUTRAL_BUTTON, button_hover_color=theme.NEUTRAL_BUTTON_HOVER,
                                     text_color=theme.TEXT)
        self.combo.set(str(value))
        self.combo.pack(anchor="w", pady=(4, 0))

    def get(self) -> int | None:
        text = self.combo.get().strip().replace(" ", "")
        return int(text) if text.isdigit() else None

    def set_enabled(self, enabled: bool) -> None:
        self.combo.configure(state="normal" if enabled else "disabled")


class OptionField(ctk.CTkFrame):
    """Label + option menu."""

    def __init__(self, master: ctk.CTkBaseClass, label: str, values: list[str], value: str, width: int = 200) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=label, font=theme.font(12), text_color=theme.TEXT_MUTED).pack(anchor="w")
        self.menu = ctk.CTkOptionMenu(self, values=values, width=width, height=34, font=theme.font(13),
                                      dropdown_font=theme.font(13), fg_color=theme.INPUT_BG,
                                      button_color=theme.NEUTRAL_BUTTON, button_hover_color=theme.NEUTRAL_BUTTON_HOVER,
                                      text_color=theme.TEXT, dynamic_resizing=False)
        self.menu.set(value if value in values else values[0])
        self.menu.pack(anchor="w", pady=(4, 0))

    def get(self) -> str:
        return self.menu.get()


def neutral_button(master: ctk.CTkBaseClass, text: str, command: Callable[[], None], width: int = 120,
                   **kwargs: object) -> ctk.CTkButton:
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, font=theme.font(13),
                         fg_color=theme.NEUTRAL_BUTTON, hover_color=theme.NEUTRAL_BUTTON_HOVER,
                         text_color=theme.TEXT, corner_radius=8, **kwargs)


class LocationPicker(ctk.CTkToplevel):
    """A dialog with checkboxes for Avito cities and regions, a search box and a field for any other city."""

    SLUG = re.compile(r"^[a-z0-9_-]{2,60}$")

    def __init__(self, master: ctk.CTk, locations: list[str], selected: list[str],
                 on_done: Callable[[list[str]], None]) -> None:
        super().__init__(master, fg_color=theme.APP_BG)
        self.title("Города и регионы Авито")
        self.geometry("440x600")
        self.transient(master)
        self.after(50, self.grab_set)  # grab only after the window is shown, otherwise Tk refuses it
        self._on_done = on_done
        self._items: dict[str, tuple[ctk.BooleanVar, ctk.CTkCheckBox]] = {}

        self.search = ctk.CTkEntry(self, height=36, font=theme.font(13), border_width=1, fg_color=theme.INPUT_BG,
                                   border_color=theme.CARD_BORDER, text_color=theme.TEXT,
                                   placeholder_text="Найти город или регион…")
        self.search.pack(fill="x", padx=16, pady=(16, 8))
        self.search.bind("<KeyRelease>", lambda _: self._filter())
        self.list = ctk.CTkScrollableFrame(self, fg_color=theme.CARD_BG, corner_radius=10, border_width=1,
                                           border_color=theme.CARD_BORDER)
        self.list.pack(fill="both", expand=True, padx=16)
        for name in locations + [s for s in selected if s not in locations]:
            self._add(name, name in selected)

        custom = ctk.CTkFrame(self, fg_color="transparent")
        custom.pack(fill="x", padx=16, pady=(10, 0))
        ctk.CTkLabel(custom, text="Другой город — как в адресе avito.ru/…, например berezovskiy",
                     font=theme.font(12), text_color=theme.TEXT_MUTED, anchor="w").pack(fill="x")
        row = ctk.CTkFrame(custom, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        self.custom = ctk.CTkEntry(row, height=34, font=theme.font(13), border_width=1, fg_color=theme.INPUT_BG,
                                   border_color=theme.CARD_BORDER, text_color=theme.TEXT)
        self.custom.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.custom.bind("<Return>", lambda _: self._add_custom())
        neutral_button(row, "Добавить", self._add_custom, width=100).pack(side="left")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=16)
        ctk.CTkButton(buttons, text="Готово", command=self._done, height=36, font=theme.font(13, "bold"),
                      fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER).pack(side="right")
        neutral_button(buttons, "Снять все", lambda: self._set_all(False), width=110).pack(side="left")

    def _add(self, name: str, checked: bool) -> None:
        var = ctk.BooleanVar(value=checked)
        box = ctk.CTkCheckBox(self.list, text=name, variable=var, font=theme.font(13), text_color=theme.TEXT,
                              fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, checkbox_width=18,
                              checkbox_height=18, corner_radius=5)
        box.pack(anchor="w", padx=8, pady=3)
        self._items[name] = (var, box)

    def _filter(self) -> None:
        text = self.search.get().strip().lower()
        for _, box in self._items.values():
            box.pack_forget()
        for name, (var, box) in self._items.items():
            if not text or text in name.lower() or var.get():
                box.pack(anchor="w", padx=8, pady=3)

    def _add_custom(self) -> None:
        slug = self.custom.get().strip().lower().strip("/")
        if not self.SLUG.match(slug):
            self.custom.configure(border_color=theme.DANGER)
            return
        self.custom.configure(border_color=theme.CARD_BORDER)
        self.custom.delete(0, "end")
        if slug in self._items:
            self._items[slug][0].set(True)
        else:
            self._add(slug, True)

    def _set_all(self, value: bool) -> None:
        for var, _ in self._items.values():
            var.set(value)

    def _done(self) -> None:
        self._on_done([name for name, (var, _) in self._items.items() if var.get()])
        self.grab_release()
        self.destroy()
