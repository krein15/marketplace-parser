"""Render the main window with demo input and save a screenshot (used for README images).

Usage: python tools/screenshot.py OUTPUT.png [light|dark] [query|ids] [tab name]

Point LOCALAPPDATA at an empty folder to shoot with clean settings and no saved tasks of your own:

    LOCALAPPDATA=%TEMP%\\mp-demo python tools/screenshot.py docs/screenshots/app-light.png
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import customtkinter as ctk
import window_capture

from mpparser import plugins
from mpparser.gui.app import App, setup_logging
from mpparser.settings import InputMode, ParseSettings
from mpparser.tasks import Task, upsert_task

DEMO_QUERY = "беспроводные наушники"


def demo_task() -> None:
    """A saved monitoring task, so the "Мониторинг" tab is not empty on the screenshot."""
    settings = ParseSettings(query=DEMO_QUERY, marketplaces=list(plugins.keys()), mode=InputMode.QUERY,
                             max_products=100, collect_reviews=False, price_min=500, price_max=5000)
    upsert_task(Task(name="Наушники каждый день", settings=settings, time="09:00"))


def main() -> None:
    output = Path(sys.argv[1])
    appearance = sys.argv[2] if len(sys.argv) > 2 else "light"
    mode = sys.argv[3] if len(sys.argv) > 3 else "query"
    tab = sys.argv[4] if len(sys.argv) > 4 else None
    setup_logging()
    if tab == "Мониторинг":
        demo_task()
    ctk.set_appearance_mode(appearance)
    app = App()
    app.appearance.set("Светлая" if appearance == "light" else "Тёмная")
    app._apply_log_colors()
    for key, variable in app.mp_vars.items():
        variable.set(True)
        app.mp_toggles[key]._refresh()
    app._refresh_marketplace_state()
    app.query.delete(0, "end")
    app.query.insert(0, DEMO_QUERY)
    app.max_products.combo.set("100")
    app.max_reviews.combo.set("20")
    app._set_entry(app.price_min, 500)
    app._set_entry(app.price_max, 5000)
    app.output_dir.delete(0, "end")
    app.output_dir.insert(0, str(Path.home() / "Documents" / "Marketplace Parser"))
    app.open_when_done.select()
    if mode == "ids":
        app.mode.set("Артикулы и ссылки")
        app._refresh_mode()
        for key, box in app.ids_boxes.items():
            marketplace = plugins.get(key)
            box.delete("1.0", "end")
            box.insert("1.0", marketplace.ids_placeholder if marketplace else "")
            app._update_ids_counter(key)
    if tab:
        app.tabs.set(tab)
        if tab == "Мониторинг":
            app._refresh_tasks()

    def capture() -> None:
        app.update()
        window_capture.save(app, output)
        app.destroy()

    app.after(2000, capture)
    app.mainloop()


if __name__ == "__main__":
    main()
