"""Render the main window with demo input and save a screenshot (used for README images).

Usage: python tools/screenshot.py OUTPUT.png [light|dark] [query|ids]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import customtkinter as ctk
from PIL import ImageGrab

from mpparser.gui.app import App, setup_logging


def main() -> None:
    output = Path(sys.argv[1])
    appearance = sys.argv[2] if len(sys.argv) > 2 else "light"
    mode = sys.argv[3] if len(sys.argv) > 3 else "query"
    setup_logging()
    ctk.set_appearance_mode(appearance)
    app = App()
    app.appearance.set("Светлая" if appearance == "light" else "Тёмная")
    app._apply_log_colors()
    app.query.delete(0, "end")
    app.query.insert(0, "беспроводные наушники")
    app.max_products.combo.set("100")
    app.max_reviews.combo.set("20")
    if mode == "ids":
        app.mode.set("Артикулы и ссылки")
        app._refresh_mode()

    def capture() -> None:
        app.update()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        output.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(output)
        app.destroy()

    def bring_to_front() -> None:
        # ImageGrab copies screen pixels, so any window on top of ours would end up in the image.
        app.attributes("-topmost", True)
        app.lift()
        app.focus_force()
        app.after(500, capture)

    app.after(2000, bring_to_front)
    app.mainloop()


if __name__ == "__main__":
    main()
