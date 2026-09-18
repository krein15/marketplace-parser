"""Generate assets/icon.ico (run once; the result is committed).

Usage: python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 512
ACCENT_TOP = (99, 102, 241)
ACCENT_BOTTOM = (67, 56, 202)
BARS = [(0.34, 0.55, (255, 255, 255)), (0.50, 0.38, (255, 255, 255)), (0.66, 0.66, (203, 17, 171))]
OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"


def main() -> None:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        ratio = y / SIZE
        color = tuple(round(top + (bottom - top) * ratio)
                      for top, bottom in zip(ACCENT_TOP, ACCENT_BOTTOM, strict=True))
        draw.line([(0, y), (SIZE, y)], fill=(*color, 255))

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=int(SIZE * 0.22), fill=255)
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)
    bar_width = int(SIZE * 0.1)
    base = int(SIZE * 0.74)
    for center, height, color in BARS:
        x = int(SIZE * center)
        top = base - int(SIZE * height * 0.62)
        draw.rounded_rectangle([x - bar_width // 2, top, x + bar_width // 2, base], radius=bar_width // 2, fill=color)
    draw.line([(int(SIZE * 0.26), base + int(SIZE * 0.045)), (int(SIZE * 0.74), base + int(SIZE * 0.045))],
              fill=(255, 255, 255, 190), width=int(SIZE * 0.035))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.resize((256, 256), Image.LANCZOS).save(OUTPUT.with_suffix(".png"))
    print(f"saved {OUTPUT}")


if __name__ == "__main__":
    main()
