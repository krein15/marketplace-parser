"""Record the program at work as an animated GIF (used for README).

The window is captured with PrintWindow, so nothing that happens to be on top of it can get into the frames.

Usage: python tools/record_gif.py docs/screenshots/run.gif [seconds] [query]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import customtkinter as ctk
import window_capture
from PIL import Image

from mpparser.gui.app import App, setup_logging

FRAME_MS = 500  # one frame every half second
WIDTH = 900  # frames are scaled down to this width
COLORS = 128  # fewer colours — smaller file


def main() -> None:
    output = Path(sys.argv[1])
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 40
    query = sys.argv[3] if len(sys.argv) > 3 else "беспроводные наушники"
    output_dir = Path(sys.argv[4]) if len(sys.argv) > 4 else Path.home() / "Documents" / "Marketplace Parser"

    setup_logging()
    ctk.set_appearance_mode("light")
    app = App()
    app.appearance.set("Светлая")
    app._apply_log_colors()
    for key, variable in app.mp_vars.items():
        variable.set(True)
        app.mp_toggles[key]._refresh()
    app._refresh_marketplace_state()
    app.query.delete(0, "end")
    app.query.insert(0, query)
    app.max_products.combo.set("10")
    app.max_reviews.combo.set("3")
    app.output_dir.delete(0, "end")
    app.output_dir.insert(0, str(output_dir))
    app.open_when_done.deselect()

    frames: list[Image.Image] = []

    def grab() -> None:
        # Keep the query visible: the entry may lose it when the window is not focused.
        if app.query.get().strip() != query:
            app.query.delete(0, "end")
            app.query.insert(0, query)
        app.update()
        image = window_capture.capture(app)
        frames.append(image.resize((WIDTH, round(WIDTH * image.height / image.width)), Image.LANCZOS))

    def tick() -> None:
        grab()
        if len(frames) * FRAME_MS < seconds * 1000 and not (app.last_result and len(frames) > 6):
            app.after(FRAME_MS, tick)
        else:
            for _ in range(6):  # hold the final screen a little
                frames.append(frames[-1])
            save(frames, output)
            app.destroy()

    app.after(1200, app._start)
    app.after(1500, tick)
    app.mainloop()


def save(frames: list[Image.Image], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    palette = [frame.convert("P", palette=Image.ADAPTIVE, colors=COLORS) for frame in frames]
    palette[0].save(output, save_all=True, append_images=palette[1:], duration=FRAME_MS, loop=0, optimize=True)
    size = output.stat().st_size / 1024 / 1024
    print(f"saved {output} — {len(frames)} кадров, {size:.1f} МБ")


if __name__ == "__main__":
    main()
