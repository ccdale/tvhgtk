from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk

from .types import ProgramRegion


def clear_hover_state(app: Any) -> None:
    for popover in app._program_popovers.values():
        popover.popdown()
        if popover.get_parent() is not None:
            popover.unparent()

    app._program_popovers.clear()
    app._program_regions.clear()


def attach_program_hover(
    app: Any, area: Gtk.DrawingArea, regions: list[ProgramRegion]
) -> None:
    app._program_regions[area] = regions

    popover = Gtk.Popover.new()
    popover.set_has_arrow(True)
    popover.set_autohide(True)
    label = Gtk.Label()
    label.set_wrap(True)
    label.set_xalign(0.0)
    label.set_max_width_chars(56)
    label.set_margin_top(8)
    label.set_margin_bottom(8)
    label.set_margin_start(10)
    label.set_margin_end(10)
    popover.set_child(label)
    popover.set_parent(area)
    app._program_popovers[area] = popover

    click = Gtk.GestureClick.new()
    click.set_button(Gdk.BUTTON_PRIMARY)
    click.connect("pressed", app._on_program_clicked, area)
    area.add_controller(click)


def on_program_clicked(app: Any, x: float, y: float, area: Gtk.DrawingArea) -> None:
    regions = app._program_regions.get(area, [])
    region = find_region_at_x(regions, x)

    popover = app._program_popovers.get(area)
    if popover is None:
        return

    if region is None:
        popover.popdown()
        return

    child = popover.get_child()
    if not isinstance(child, Gtk.Label):
        return

    child.set_text(str(region.get("hover", "")))

    rect = Gdk.Rectangle()
    rect.x = int(x)
    rect.y = int(y)
    rect.width = 1
    rect.height = 1
    popover.set_pointing_to(rect)
    popover.popup()


def find_region_at_x(regions: list[ProgramRegion], x: float) -> ProgramRegion | None:
    for region in regions:
        left = float(region.get("x", 0.0))
        width = float(region.get("w", 0.0))
        if left <= x <= (left + width):
            return region
    return None
