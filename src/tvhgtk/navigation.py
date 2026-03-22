from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk


def on_day_selected(app: Any, day_index: int) -> None:
    app._select_day(day_index)


def on_previous_day_clicked(app: Any) -> None:
    app._select_day(app._selected_day_index - 1)


def on_next_day_clicked(app: Any) -> None:
    app._select_day(app._selected_day_index + 1)


def on_now_clicked(app: Any) -> None:
    app._select_day(0)


def on_key_pressed(app: Any, keyval: int, state: Gdk.ModifierType) -> bool:
    if state & (
        Gdk.ModifierType.CONTROL_MASK
        | Gdk.ModifierType.ALT_MASK
        | Gdk.ModifierType.SUPER_MASK
    ):
        return False

    shift_pressed = bool(state & Gdk.ModifierType.SHIFT_MASK)

    if keyval in (Gdk.KEY_q, Gdk.KEY_Q):
        app.quit()
        return True

    if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
        app._select_day(0)
        return True

    if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
        app._scroll_schedule_to_end()
        return True

    if shift_pressed and keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_H):
        app._select_day(app._selected_day_index - 1)
        return True

    if shift_pressed and keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_L):
        app._select_day(app._selected_day_index + 1)
        return True

    if not shift_pressed and keyval in (Gdk.KEY_h, Gdk.KEY_Left, Gdk.KEY_KP_Left):
        app._scroll_schedule(-1)
        return True

    if not shift_pressed and keyval in (Gdk.KEY_l, Gdk.KEY_Right, Gdk.KEY_KP_Right):
        app._scroll_schedule(1)
        return True

    return False


def scroll_schedule(
    app: Any,
    direction: int,
    schedule_scroll_step_minutes: int,
    pixels_per_minute: int,
) -> None:
    if app._program_scroll is None:
        return

    adjustment = app._program_scroll.get_hadjustment()
    step = direction * schedule_scroll_step_minutes * pixels_per_minute
    max_value = max(
        adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()
    )
    new_value = min(
        max(adjustment.get_value() + step, adjustment.get_lower()), max_value
    )
    adjustment.set_value(new_value)


def scroll_schedule_to_end(app: Any) -> None:
    if app._program_scroll is None:
        return

    adjustment = app._program_scroll.get_hadjustment()
    max_value = max(
        adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()
    )
    adjustment.set_value(max_value)


def select_day(app: Any, day_index: int, total_days: int) -> None:
    day_index = max(0, min(day_index, total_days - 1))
    if day_index == app._selected_day_index:
        app._update_day_controls()
        return
    app._selected_day_index = day_index
    app._load_epg(reload_channels=False)
