#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

W, H = 896, 635

BG = (255, 238, 212)
PAPER = (255, 249, 239)
PAPER_2 = (255, 244, 228)
ORANGE = (244, 160, 82)
ORANGE_DARK = (226, 137, 62)
DOT = (232, 184, 125)

S = 22


def rect(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, color=ORANGE) -> None:
    draw.rectangle([x, y, x + w - 1, y + h - 1], fill=color)


def px(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], size: int = S, color=ORANGE) -> None:
    for x, y in points:
        rect(draw, x, y, size, size, color)


def draw_background(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle([0, 0, W, H], fill=BG)
    draw.rounded_rectangle([40, 34, W - 40, H - 64], radius=18, fill=(246, 210, 165))
    draw.rounded_rectangle([32, 24, W - 48, H - 74], radius=18, fill=PAPER)
    draw.rectangle([32, H - 118, W - 48, H - 104], fill=(241, 181, 108))
    for x in range(68, W - 82, 24):
        rect(draw, x, H - 48, 5, 5, DOT)


def idle_plate() -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_background(draw)
    draw.rounded_rectangle([372, H - 134, 524, H - 94], radius=20, fill=(255, 244, 230))
    for x in [407, 446, 485]:
        rect(draw, x, H - 114, 16, 5, ORANGE_DARK)
    return image


def face_canvas(draw_face) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_background(draw)
    draw_face(draw)
    return image


def draw_happy(draw: ImageDraw.ImageDraw) -> None:
    px(draw, [(170, 260), (192, 226), (214, 192), (236, 192), (258, 226), (280, 260)], 22)
    px(draw, [(612, 260), (634, 226), (656, 192), (678, 192), (700, 226), (722, 260)], 22)
    px(draw, [(392, 406), (414, 428), (436, 450), (458, 450), (480, 428), (502, 406)], 22)
    for x in [72, 99, 126, 734, 761, 788, 815]:
        rect(draw, x, 354, 20, 20)


def draw_standing(draw: ImageDraw.ImageDraw) -> None:
    for x in [248, 278, 308, 338, 564, 594, 624, 654]:
        rect(draw, x, 276, 24, 20)
    rect(draw, 412, 416, 96, 24)


def draw_working(draw: ImageDraw.ImageDraw) -> None:
    px(draw, [(302, 176), (324, 198), (346, 220), (378, 220), (400, 242)], 22)
    px(draw, [(602, 242), (624, 220), (656, 220), (678, 198), (700, 176)], 22)
    draw.rectangle([358, 356, 538, 474], outline=ORANGE, width=5)
    rect(draw, 440, 408, 24, 24)
    rect(draw, 336, 486, 224, 9)
    rect(draw, 328, 496, 240, 9)


def draw_error(draw: ImageDraw.ImageDraw) -> None:
    for base_x in [238, 590]:
        for i in range(4):
            rect(draw, base_x + i * 28, 210 + i * 28, 24, 24)
            rect(draw, base_x + i * 28, 294 - i * 28, 24, 24)
    px(draw, [(396, 464), (418, 436), (440, 414), (462, 414), (484, 436), (506, 464)], 22)


def draw_pending(draw: ImageDraw.ImageDraw) -> None:
    for cx in [306, 590]:
        rect(draw, cx - 22, 270, 44, 88)
        rect(draw, cx - 44, 292, 88, 44)
        rect(draw, cx - 18, 274, 36, 80, PAPER)
        rect(draw, cx - 40, 296, 80, 36, PAPER)
    rect(draw, 720, 214, 18, 54)
    rect(draw, 740, 178, 18, 54)
    rect(draw, 760, 196, 18, 18)
    for x in [396, 430, 464]:
        rect(draw, x, 430, 26, 24)


def draw_sleeping(draw: ImageDraw.ImageDraw) -> None:
    for x in [220, 252, 284, 590, 622, 654]:
        rect(draw, x, 294, 30, 16)
    for x in [252, 284, 622, 654]:
        rect(draw, x, 310, 30, 16)
    rect(draw, 410, 434, 98, 24)
    rect(draw, 716, 196, 60, 12)
    rect(draw, 762, 160, 14, 42)
    rect(draw, 716, 160, 60, 12)


DRAWERS = [draw_happy, draw_standing, draw_working, draw_error, draw_pending, draw_sleeping]


def flip_frame(draw_face, scale: float, show_face: bool, marks: int) -> Image.Image:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw_background(draw)
    if show_face:
        draw_face(draw)
        return image

    band_h = max(10, int(360 * scale))
    y = 112 + (360 - band_h) // 2
    draw.rounded_rectangle([58, y + 7, W - 74, y + band_h + 7], radius=12, fill=(244, 202, 151))
    draw.rounded_rectangle([50, y, W - 82, y + band_h], radius=12, fill=PAPER_2)
    draw.rectangle([50, y + band_h - 8, W - 82, y + band_h], fill=(241, 181, 108))
    for i in range(marks):
        rect(draw, 408 + i * 34, 316, 16, 5, ORANGE_DARK)
    return image


def make_assets(index: int, draw_face) -> None:
    static = idle_plate()
    static.save(ASSETS / f"face_{index}.png")

    final = face_canvas(draw_face)
    frames = [
        idle_plate(),
        flip_frame(draw_face, 0.88, False, 3),
        flip_frame(draw_face, 0.38, False, 3),
        flip_frame(draw_face, 0.10, False, 2),
        flip_frame(draw_face, 0.54, False, 2),
        final,
        flip_frame(draw_face, 0.18, False, 2),
        flip_frame(draw_face, 0.72, False, 1),
        final,
    ]
    durations = [90, 70, 70, 70, 70, 150, 70, 70, 900]
    frames[0].save(
        ASSETS / f"face_{index}.gif",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )


def main() -> None:
    for i, drawer in enumerate(DRAWERS, 1):
        make_assets(i, drawer)


if __name__ == "__main__":
    main()
