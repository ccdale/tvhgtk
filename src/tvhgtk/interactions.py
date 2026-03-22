from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, Gtk
from tvheadend import sendToTvh
from tvheadend.tvh import TVHError

from .navigation import on_key_pressed
from .types import ProgramRegion


def _set_popover_attr(popover: Gtk.Popover, name: str, value: object) -> None:
    setattr(popover, name, value)


def _get_popover_attr(popover: Gtk.Popover, name: str) -> object | None:
    return getattr(popover, name, None)


def clear_hover_state(app: Any) -> None:
    for popover in app._program_popovers.values():
        popover.popdown()
        if popover.get_parent() is not None:
            popover.unparent()

    app._program_popovers.clear()
    app._program_regions.clear()


def dismiss_active_popovers(app: Any) -> bool:
    dismissed = False
    for popover in app._program_popovers.values():
        if popover.get_visible():
            popover.popdown()
            dismissed = True
    return dismissed


def attach_program_hover(
    app: Any, area: Gtk.DrawingArea, regions: list[ProgramRegion]
) -> None:
    app._program_regions[area] = regions

    popover = Gtk.Popover.new()
    popover.set_has_arrow(True)
    popover.set_autohide(True)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_top(8)
    box.set_margin_bottom(8)
    box.set_margin_start(10)
    box.set_margin_end(10)

    label = Gtk.Label()
    label.set_wrap(True)
    label.set_xalign(0.0)
    label.set_max_width_chars(56)

    actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    record_btn = Gtk.Button(label="Record")
    series_btn = Gtk.Button(label="Record Series")
    series_btn.set_visible(False)
    actions_row.append(record_btn)
    actions_row.append(series_btn)

    box.append(label)
    box.append(actions_row)
    popover.set_child(box)
    popover.set_parent(area)

    _set_popover_attr(popover, "_tvhgtk_detail_label", label)
    _set_popover_attr(popover, "_tvhgtk_record_btn", record_btn)
    _set_popover_attr(popover, "_tvhgtk_series_btn", series_btn)
    _set_popover_attr(popover, "_tvhgtk_active_region", None)

    app._program_popovers[area] = popover

    record_btn.connect("clicked", lambda _btn: _on_record_clicked(app, area, False))
    series_btn.connect("clicked", lambda _btn: _on_record_clicked(app, area, True))

    popover_key_controller = Gtk.EventControllerKey()
    popover_key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    popover_key_controller.connect(
        "key-pressed",
        lambda _controller, keyval, _keycode, state: (
            dismiss_active_popovers(app) or on_key_pressed(app, keyval, state)
        ),
    )
    popover.add_controller(popover_key_controller)

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

    detail_label = _get_popover_attr(popover, "_tvhgtk_detail_label")
    record_btn = _get_popover_attr(popover, "_tvhgtk_record_btn")
    series_btn = _get_popover_attr(popover, "_tvhgtk_series_btn")
    if not isinstance(detail_label, Gtk.Label):
        return

    detail_label.set_text(str(region.get("hover", "")))
    _set_popover_attr(popover, "_tvhgtk_active_region", region)

    event_id = region.get("event_id")
    recording_scheduled = bool(region.get("recording_scheduled", False))

    if isinstance(record_btn, Gtk.Button):
        record_btn.set_sensitive(isinstance(event_id, int) and not recording_scheduled)
        if recording_scheduled:
            record_btn.set_label("Scheduled")
        else:
            record_btn.set_label("Record")

    if isinstance(series_btn, Gtk.Button):
        series_id = region.get("series_id")
        has_series = isinstance(series_id, str) and bool(series_id.strip())
        series_btn.set_visible(has_series)
        series_btn.set_sensitive(has_series)

    rect = Gdk.Rectangle()
    rect.x = int(x)
    rect.y = int(y)
    rect.width = 1
    rect.height = 1
    popover.set_pointing_to(rect)
    popover.popup()


def _send_record_request(
    event_id: int, series_id: str | None, use_series: bool
) -> None:
    attempts: list[tuple[str, dict[str, object]]] = []

    if use_series and isinstance(series_id, str) and series_id.strip():
        sid = series_id.strip()
        attempts.extend(
            [
                ("dvr/autorec/create", {"seriesid": sid}),
                ("dvr/autorec/create", {"seriesId": sid}),
                ("dvr/autorec/create_by_series", {"seriesid": sid}),
                ("dvr/autorec/create_by_series", {"seriesId": sid}),
            ]
        )
    else:
        attempts.extend(
            [
                ("dvr/entry/create_by_event", {"event_id": event_id}),
                ("dvr/entry/create_by_event", {"eventId": event_id}),
                ("dvr/entry/create", {"event_id": event_id}),
                ("dvr/entry/create", {"eventId": event_id}),
            ]
        )

    last_error: Exception | None = None
    for route, payload in attempts:
        try:
            sendToTvh(route, payload)
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    if last_error is None:
        raise TVHError("no valid recording API call could be attempted")
    raise TVHError(str(last_error))


def _on_record_clicked(app: Any, area: Gtk.DrawingArea, use_series: bool) -> None:
    popover = app._program_popovers.get(area)
    if popover is None:
        return

    region = _get_popover_attr(popover, "_tvhgtk_active_region")
    if not isinstance(region, dict):
        return

    event_id = region.get("event_id")
    if not isinstance(event_id, int):
        app.status_label.set_text("Cannot schedule recording: missing event ID")
        return

    series_id = region.get("series_id")
    if series_id is not None and not isinstance(series_id, str):
        series_id = str(series_id)

    try:
        _send_record_request(event_id, series_id, use_series)
        popover.popdown()
        app._load_epg(reload_channels=False)
        if use_series:
            app.status_label.set_text("Series recording scheduled")
        else:
            app.status_label.set_text("Recording scheduled")
    except TVHError as exc:
        app.status_label.set_text(f"Recording request failed: {exc}")


def find_region_at_x(regions: list[ProgramRegion], x: float) -> ProgramRegion | None:
    for region in regions:
        left = float(region.get("x", 0.0))
        width = float(region.get("w", 0.0))
        if left <= x <= (left + width):
            return region
    return None
