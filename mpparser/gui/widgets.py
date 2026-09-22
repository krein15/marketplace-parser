"""Reusable widgets of the main window."""

from __future__ import annotations

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

    def __init__(self, master: ctk.CTkBaseClass, key: str, title: str, subtitle: str,
                 variable: ctk.BooleanVar, command: Callable[[], None]) -> None:
        color = theme.MARKETPLACE_COLORS[key]
        super().__init__(master, fg_color=theme.INPUT_BG, corner_radius=12, border_width=2, border_color=color)
        self._color = color
        self._variable = variable
        self.checkbox = ctk.CTkCheckBox(self, text=title, variable=variable, command=self._changed,
                                        font=theme.font(14, "bold"), text_color=theme.TEXT,
                                        fg_color=color, hover_color=color, border_color=color,
                                        checkbox_width=20, checkbox_height=20, corner_radius=6)
        self.checkbox.pack(side="left", padx=(12, 8), pady=10)
        ctk.CTkLabel(self, text=subtitle, font=theme.font(12), text_color=theme.TEXT_MUTED).pack(
            side="left", pady=10)
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
