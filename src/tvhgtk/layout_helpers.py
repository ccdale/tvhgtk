from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable


def scroll_to_now(app: Any, pixels_per_minute: int) -> bool:
    if app._program_scroll is None:
        return False

    if app._selected_day_index == 0:
        now = int(time.time())
        now_x = (now - app._window_start) / 60 * pixels_per_minute
        offset = max(0.0, now_x - 60 * pixels_per_minute)
    else:
        offset = 0.0

    app._program_scroll.get_hadjustment().set_value(offset)
    return False  # do not repeat


def on_outer_tick(
    app: Any,
    widget: Any,
    *,
    left_split_ratio: float,
    row_height: int,
    day_button_row_height: int,
    header_height: int,
) -> bool:
    width = widget.get_allocated_width()
    if width == app._last_outer_width:
        return True

    app._last_outer_width = width
    apply_split_width(
        app,
        width,
        left_split_ratio=left_split_ratio,
        row_height=row_height,
        day_button_row_height=day_button_row_height,
        header_height=header_height,
    )
    return True


def apply_split_width(
    app: Any,
    total_width: int,
    *,
    left_split_ratio: float,
    row_height: int,
    day_button_row_height: int,
    header_height: int,
) -> None:
    if total_width <= 0:
        return

    left_width = max(1, int(total_width * left_split_ratio))

    if app._day_corner_label is not None:
        app._day_corner_label.set_size_request(left_width, day_button_row_height)

    if app._corner_label is not None:
        app._corner_label.set_size_request(left_width, header_height)

    if app._channel_scroll is not None:
        app._channel_scroll.set_size_request(left_width, -1)

    for row in app._channel_rows:
        row.set_size_request(left_width, row_height)


def update_day_controls(app: Any, total_days: int) -> None:
    app._update_header_title()

    for index, button in enumerate(app._day_buttons):
        if index == app._selected_day_index:
            button.add_css_class("suggested-action")
        else:
            button.remove_css_class("suggested-action")

    if app._previous_day_button is not None:
        app._previous_day_button.set_sensitive(app._selected_day_index > 0)

    if app._next_day_button is not None:
        app._next_day_button.set_sensitive(app._selected_day_index < total_days - 1)

    if app._now_button is not None:
        app._now_button.set_sensitive(app._selected_day_index != 0)


def resolve_channel_icon_path(
    channel: dict[str, object],
    icon_cache_dir: Path,
    icon_extensions: tuple[str, ...],
    normalize_name: Callable[[str], str],
) -> Path | None:
    candidate_stems: list[str] = []

    channel_uuid = channel.get("uuid")
    if isinstance(channel_uuid, str) and channel_uuid.strip():
        candidate_stems.append(channel_uuid.strip())

    channel_name = channel.get("name")
    if isinstance(channel_name, str) and channel_name.strip():
        normalized = normalize_name(channel_name)
        if normalized:
            candidate_stems.append(normalized)

    for stem in candidate_stems:
        for extension in icon_extensions:
            candidate = icon_cache_dir / f"{stem}{extension}"
            if candidate.is_file():
                return candidate

    return None
