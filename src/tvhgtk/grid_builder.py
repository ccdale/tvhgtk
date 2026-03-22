from __future__ import annotations

from datetime import datetime
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import GLib, Gtk, Pango

from .drawing import draw_timeline, make_program_draw_func
from .navigation import on_day_selected


def build_epg_grid(
    app: Any,
    *,
    total_days: int,
    channel_col_width: int,
    day_button_row_height: int,
    header_height: int,
    total_width: int,
    row_height: int,
    total_hours: int,
    pixels_per_minute: int,
) -> None:
    app._clear_hover_state()

    child = app.epg_container.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        app.epg_container.remove(child)
        child = nxt

    outer = Gtk.Grid()
    outer.set_row_spacing(0)
    outer.set_column_spacing(0)
    outer.add_tick_callback(app._on_outer_tick)

    day_corner = Gtk.Label(label="Days")
    day_corner.add_css_class("dim-label")
    day_corner.set_size_request(channel_col_width, day_button_row_height)
    day_corner.set_hexpand(False)
    app._day_corner_label = day_corner
    outer.attach(day_corner, 0, 0, 1, 1)

    day_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    day_button_box.set_margin_top(6)
    day_button_box.set_margin_bottom(6)
    day_button_box.set_margin_start(6)
    day_button_box.set_margin_end(6)
    day_button_box.set_halign(Gtk.Align.START)
    app._day_buttons = []

    for day_index in range(total_days):
        day_start = app._today_start + (day_index * 86400)
        day_dt = datetime.fromtimestamp(day_start)
        label = "Today" if day_index == 0 else day_dt.strftime("%a %d %b")
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda _btn, idx=day_index: on_day_selected(app, idx))
        day_button_box.append(button)
        app._day_buttons.append(button)

    day_scroll = Gtk.ScrolledWindow()
    day_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
    day_scroll.set_propagate_natural_height(True)
    day_scroll.set_hexpand(True)
    day_scroll.set_size_request(-1, day_button_row_height)
    day_scroll.set_child(day_button_box)

    app._update_day_controls()
    outer.attach(day_scroll, 1, 0, 1, 1)

    # [1,0] corner
    corner = Gtk.Label(label="Channels")
    corner.add_css_class("dim-label")
    corner.set_size_request(channel_col_width, header_height)
    corner.set_hexpand(False)
    app._corner_label = corner
    outer.attach(corner, 0, 1, 1, 1)

    # [1,1] timeline header – shares hadjustment with program_scroll
    timeline_da = Gtk.DrawingArea()
    timeline_da.set_size_request(total_width, header_height)
    timeline_da.set_draw_func(
        lambda da, cr, width, height, data: draw_timeline(
            da,
            cr,
            width,
            height,
            data,
            window_start=app._window_start,
            total_hours=total_hours,
            total_width=total_width,
            pixels_per_minute=pixels_per_minute,
        ),
        None,
    )

    timeline_scroll = Gtk.ScrolledWindow()
    timeline_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
    timeline_scroll.set_hexpand(True)
    timeline_scroll.set_size_request(-1, header_height)
    timeline_scroll.set_child(timeline_da)
    outer.attach(timeline_scroll, 1, 1, 1, 1)

    # [2,0] channel names – shares vadjustment with program_scroll
    channel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    channel_box.set_size_request(channel_col_width, -1)
    app._channel_rows = []

    # [2,1] programme rows
    program_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

    row_colours = [
        (0.13, 0.13, 0.13),
        (0.10, 0.10, 0.10),
    ]

    for i, ch in enumerate(app._channels):
        uuid = str(ch.get("uuid", "")).strip()
        name = str(ch.get("name", "<unnamed>"))
        events = app._epg_data.get(uuid, [])
        bg = row_colours[i % 2]
        regions = app._build_program_regions(events)

        # Channel label row (must be exactly ROW_HEIGHT to stay aligned)
        ch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        ch_row.set_size_request(channel_col_width, row_height)

        icon_path = app._resolve_channel_icon_path(ch)
        icon = (
            Gtk.Image.new_from_file(str(icon_path))
            if icon_path is not None
            else Gtk.Image.new_from_icon_name("image-missing")
        )
        icon.set_pixel_size(24)
        icon.set_margin_start(8)

        lbl = Gtk.Label(label=name)
        lbl.set_xalign(0.0)
        lbl.set_hexpand(True)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(18)
        lbl.set_margin_end(4)

        ch_row.append(icon)
        ch_row.append(lbl)
        channel_box.append(ch_row)
        app._channel_rows.append(ch_row)

        # Programme DrawingArea (same ROW_HEIGHT keeps rows aligned)
        prog_da = Gtk.DrawingArea()
        prog_da.set_size_request(total_width, row_height)
        prog_da.set_draw_func(
            make_program_draw_func(
                window_start=app._window_start,
                regions=regions,
                bg_colour=bg,
                total_width=total_width,
                pixels_per_minute=pixels_per_minute,
            ),
            None,
        )
        app._attach_program_hover(prog_da, regions)
        program_box.append(prog_da)

    program_scroll = Gtk.ScrolledWindow()
    program_scroll.set_policy(Gtk.PolicyType.ALWAYS, Gtk.PolicyType.ALWAYS)
    program_scroll.set_hexpand(True)
    program_scroll.set_vexpand(True)
    program_scroll.set_child(program_box)

    channel_scroll = Gtk.ScrolledWindow()
    channel_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
    channel_scroll.set_size_request(channel_col_width, -1)
    channel_scroll.set_hexpand(False)
    channel_scroll.set_vexpand(True)
    channel_scroll.set_child(channel_box)
    app._channel_scroll = channel_scroll

    # Share adjustments so all three panels scroll in lock-step
    timeline_scroll.set_hadjustment(program_scroll.get_hadjustment())
    channel_scroll.set_vadjustment(program_scroll.get_vadjustment())

    outer.attach(channel_scroll, 0, 2, 1, 1)
    outer.attach(program_scroll, 1, 2, 1, 1)

    app.epg_container.append(outer)
    app._program_scroll = program_scroll
    app._apply_split_width(outer.get_allocated_width())

    # After layout, scroll so current time is near the left edge (1 hr before now)
    GLib.idle_add(app._scroll_to_now)
