"""Render the main window with demo input and save a screenshot (used for README images).

Usage: python tools/screenshot.py OUTPUT.png [light|dark] [query|ids] [tab name]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import customtkinter as ctk
import window_capture

from mpparser.gui.app import App, setup_logging


def main() -> None:
    output = Path(sys.argv[1])
    appearance = sys.argv[2] if len(sys.argv) > 2 else "light"
    mode = sys.argv[3] if len(sys.argv) > 3 else "query"
    tab = sys.argv[4] if len(sys.argv) > 4 else None
    setup_logging()
    ctk.set_appearance_mode(appearance)
    app = App()
    app.appearance.set("Светлая" if appearance == "light" else "Тёмная")
    app._apply_log_colors()
    app.query.delete(0, "end")
    app.query.insert(0, "беспроводные наушники")
    app.max_products.combo.set("100")
    app.max_reviews.combo.set("20")
    app.output_dir.delete(0, "end")
    app.output_dir.insert(0, str(Path.home() / "Documents" / "Marketplace Parser"))
    app.open_when_done.select()
    if mode == "ids":
        app.mode.set("Артикулы и ссылки")
        app._refresh_mode()
    if tab:
        app.tabs.set(tab)

    def capture() -> None:
        app.update()
        window_capture.save(app, output)
        app.destroy()

    app.after(2000, capture)
    app.mainloop()


if __name__ == "__main__":
    main()
