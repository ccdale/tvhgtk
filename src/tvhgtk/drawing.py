from __future__ import annotations

import time
from datetime import datetime

import gi

from .types import RGB, ProgramRegion

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Pango, PangoCairo  # noqa: E402


def draw_timeline(
    _da: object,
    cr: object,
    _width: int,
    height: int,
    _data: object,
    *,
    window_start: int,
    total_hours: int,
    total_width: int,
    pixels_per_minute: int,
) -> None:
    cr.set_source_rgb(0.08, 0.08, 0.08)
    cr.paint()

    now = int(time.time())

    for h in range(total_hours + 1):
        x = h * 60 * pixels_per_minute
        ts = window_start + h * 3600

        # Major hour tick
        cr.set_source_rgb(0.4, 0.4, 0.4)
        cr.set_line_width(1.0)
        cr.move_to(x + 0.5, height - 10)
        cr.line_to(x + 0.5, height)
        cr.stroke()

        # Hour label
        label = datetime.fromtimestamp(ts).strftime("%H:%M")
        layout = PangoCairo.create_layout(cr)
        layout.set_text(label, -1)
        layout.set_font_description(Pango.FontDescription("Sans Bold 8"))
        _, logical = layout.get_pixel_extents()
        cr.set_source_rgb(0.85, 0.85, 0.85)
        cr.move_to(x + 4, (height - logical.height) // 2)
        PangoCairo.show_layout(cr, layout)

        # Half-hour minor tick
        if h < total_hours:
            half_x = x + 30 * pixels_per_minute
            cr.set_source_rgb(0.28, 0.28, 0.28)
            cr.set_line_width(1.0)
            cr.move_to(half_x + 0.5, height - 5)
            cr.line_to(half_x + 0.5, height)
            cr.stroke()

    # Current-time red marker
    now_x = (now - window_start) / 60 * pixels_per_minute
    if 0 <= now_x <= total_width:
        cr.set_source_rgb(0.9, 0.2, 0.2)
        cr.set_line_width(2.0)
        cr.move_to(now_x, 0)
        cr.line_to(now_x, height)
        cr.stroke()


def make_program_draw_func(
    *,
    window_start: int,
    regions: list[ProgramRegion],
    bg_colour: RGB,
    total_width: int,
    pixels_per_minute: int,
):
    def draw(
        _da: object,
        cr: object,
        _width: int,
        height: int,
        _data: object,
    ) -> None:
        cr.set_source_rgb(*bg_colour)
        cr.paint()

        now = int(time.time())
        now_x = (now - window_start) / 60 * pixels_per_minute
        if 0 <= now_x <= total_width:
            cr.set_source_rgba(0.9, 0.2, 0.2, 0.45)
            cr.set_line_width(1.5)
            cr.move_to(now_x, 0)
            cr.line_to(now_x, height)
            cr.stroke()

        for region in regions:
            x = float(region.get("x", 0.0))
            cell_w = float(region.get("w", 0.0))
            title = str(region.get("title", ""))
            fill = region.get("fill", (0.18, 0.38, 0.65))
            border = region.get("border", (0.06, 0.06, 0.06))

            if (
                not isinstance(fill, tuple)
                or len(fill) != 3
                or not isinstance(border, tuple)
                or len(border) != 3
            ):
                fill = (0.18, 0.38, 0.65)
                border = (0.06, 0.06, 0.06)

            # Cell fill
            cr.set_source_rgb(*fill)
            cr.rectangle(x + 1, 1, cell_w - 2, height - 2)
            cr.fill()

            # Cell border
            cr.set_source_rgb(*border)
            cr.set_line_width(1.0)
            cr.rectangle(x + 0.5, 0.5, cell_w - 1, height - 1)
            cr.stroke()

            # Programme title (skip if cell too narrow)
            if title and cell_w > 24:
                layout = PangoCairo.create_layout(cr)
                layout.set_text(title, -1)
                layout.set_font_description(Pango.FontDescription("Sans 8"))
                layout.set_width(int((cell_w - 8) * Pango.SCALE))
                layout.set_ellipsize(Pango.EllipsizeMode.END)
                _, logical = layout.get_pixel_extents()
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.move_to(x + 4, (height - logical.height) // 2)
                PangoCairo.show_layout(cr, layout)

    return draw
