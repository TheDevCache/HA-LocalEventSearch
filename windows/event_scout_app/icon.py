"""The app icon, drawn with Pillow (used for the tray and the .exe)."""

from __future__ import annotations

from PIL import Image, ImageDraw


def icon_image(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.rounded_rectangle([6 * s, 10 * s, 58 * s, 58 * s], radius=8 * s, fill=(11, 127, 171))
    d.rounded_rectangle([6 * s, 10 * s, 58 * s, 24 * s], radius=6 * s, fill=(7, 90, 122))
    d.ellipse([29 * s, 31 * s, 47 * s, 49 * s], outline="white", width=max(2, int(5 * s)))
    d.line([44 * s, 46 * s, 52 * s, 54 * s], fill="white", width=max(2, int(5 * s)))
    return img
