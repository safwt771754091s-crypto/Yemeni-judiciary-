"""Generate the PWA icons for the Yemeni Legal AI Platform.

The icons are drawn programmatically so the set stays reproducible and no binary
artwork has to be hand-maintained. Run from the repository root:

    .venv/bin/python scripts/make_icons.py

It writes 192/512 px "any" icons and a 512 px maskable icon (the maskable one
keeps the glyph inside the safe zone so Android can crop it to any shape).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "app" / "static" / "icons"
BG_TOP = (19, 32, 48)
BG_BOTTOM = (27, 43, 60)
GOLD = (200, 162, 74)
GOLD_LIGHT = (224, 190, 106)


def _gradient(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), BG_TOP)
    draw = ImageDraw.Draw(image)
    for y in range(size):
        t = y / max(size - 1, 1)
        colour = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * t) for i in range(3)
        )
        draw.line([(0, y), (size, y)], fill=colour)
    return image


def _scales(draw: ImageDraw.ImageDraw, size: int, scale: float) -> None:
    """Draw a balance-of-justice glyph centred in the square.

    ``scale`` shrinks the glyph for the maskable icon's safe zone.
    """
    cx = size / 2
    unit = size * scale
    top = size / 2 - unit * 0.30
    bottom = size / 2 + unit * 0.34
    beam_y = top + unit * 0.10
    arm = unit * 0.30

    # Central column and base.
    draw.line([(cx, top), (cx, bottom)], fill=GOLD_LIGHT, width=max(2, int(size * 0.018)))
    draw.line(
        [(cx - unit * 0.16, bottom), (cx + unit * 0.16, bottom)],
        fill=GOLD_LIGHT,
        width=max(2, int(size * 0.018)),
    )
    # Cross beam.
    draw.line(
        [(cx - arm, beam_y), (cx + arm, beam_y)],
        fill=GOLD,
        width=max(2, int(size * 0.016)),
    )
    # Two pans, hung from the beam ends.
    pan_r = unit * 0.10
    hang = unit * 0.16
    for side in (-1, 1):
        x = cx + side * arm
        draw.line([(x, beam_y), (x, beam_y + hang)], fill=GOLD, width=max(1, int(size * 0.010)))
        draw.arc(
            [x - pan_r, beam_y + hang - pan_r * 0.4, x + pan_r, beam_y + hang + pan_r * 1.4],
            start=0,
            end=180,
            fill=GOLD_LIGHT,
            width=max(2, int(size * 0.014)),
        )
    # Crown finial.
    finial = unit * 0.05
    draw.ellipse(
        [cx - finial, top - finial, cx + finial, top + finial], fill=GOLD_LIGHT
    )


def make_icon(size: int, *, maskable: bool = False) -> Image.Image:
    image = _gradient(size).convert("RGBA")
    draw = ImageDraw.Draw(image)
    # A subtle ring frames the emblem; the maskable variant keeps a wide margin.
    margin = size * (0.22 if maskable else 0.10)
    draw.ellipse(
        [margin, margin, size - margin, size - margin],
        outline=GOLD,
        width=max(2, int(size * 0.012)),
    )
    _scales(draw, size, 0.72 if maskable else 1.0)
    return image


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_icon(192).save(OUT / "icon-192.png")
    make_icon(512).save(OUT / "icon-512.png")
    make_icon(512, maskable=True).save(OUT / "icon-maskable-512.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
