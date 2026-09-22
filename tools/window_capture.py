"""Capture a Tk window by asking Windows to render it, not by copying screen pixels.

``ImageGrab.grab`` returns whatever is on screen inside the given rectangle, so any window that happens to
overlap ours lands in the screenshot. ``PrintWindow`` with PW_RENDERFULLCONTENT renders the window itself,
which works even when it is covered by other windows.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from PIL import Image

PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2
DIB_RGB_COLORS = 0
BI_RGB = 0

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


def capture(widget) -> Image.Image:  # widget: any Tk widget of the window to capture
    """Return the window's client area as an image."""
    hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("window has no client area yet")

    window_dc = user32.GetDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    try:
        gdi32.SelectObject(memory_dc, bitmap)
        if not user32.PrintWindow(hwnd, memory_dc, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            raise RuntimeError("PrintWindow failed")
        header = BITMAPINFOHEADER(
            biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,  # negative: top-down
            biPlanes=1, biBitCount=32, biCompression=BI_RGB,
        )
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(header), DIB_RGB_COLORS):
            raise RuntimeError("GetDIBits failed")
        return Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def save(widget, path: Path) -> None:  # widget: any Tk widget of the window to capture
    path.parent.mkdir(parents=True, exist_ok=True)
    capture(widget).save(path)
