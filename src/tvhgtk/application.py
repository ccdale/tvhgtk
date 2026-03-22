from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402
from tvheadend import channelGrid, epgEventsOnChannel  # noqa: E402
from tvheadend.tvh import TVHError  # noqa: E402

from .config import (  # noqa: E402
    DEFAULT_CATEGORY_COLOR_RULES,
    AppConfigError,
    configure_tvheadend,
    load_category_color_rules,
    load_server_config,
)

ICON_CACHE_DIR = Path.home() / ".cache" / "tvhgtk" / "icons"
ICON_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp")

# ── EPG grid layout ────────────────────────────────────────────────────────────
WINDOW_WIDTH: int = 1400
LEFT_SPLIT_RATIO: float = 0.20
PIXELS_PER_MINUTE: int = 4  # 4 px = 1 min → 240 px/hr
CHANNEL_COL_WIDTH: int = int(WINDOW_WIDTH * LEFT_SPLIT_RATIO)
ROW_HEIGHT: int = 50  # height of each channel row (px)
HEADER_HEIGHT: int = 36  # height of the timeline header (px)
DAY_BUTTON_ROW_HEIGHT: int = 44
MIN_PROGRAM_MINUTES: int = 15  # programmes shorter than this are discarded
TOTAL_HOURS: int = 24  # schedule window length
TOTAL_DAYS: int = 8
SCHEDULE_SCROLL_STEP_MINUTES: int = 60
TOTAL_WIDTH: int = TOTAL_HOURS * 60 * PIXELS_PER_MINUTE  # 5760 px


def normalize_channel_name(name: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return normalized.strip("-")


class TVHGtkApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.ccdale.tvhgtk")
        self._today_start: int = 0
        self._selected_day_index: int = 0
        self._window_start: int = 0
        self._channels: list[dict[str, object]] = []
        self._epg_data: dict[str, list[dict[str, object]]] = {}
        self._program_scroll: Gtk.ScrolledWindow | None = None
        self._title_label: Gtk.Label | None = None
        self._category_color_rules = list(DEFAULT_CATEGORY_COLOR_RULES)
        self._program_regions: dict[Gtk.DrawingArea, list[dict[str, object]]] = {}
        self._program_popovers: dict[Gtk.DrawingArea, Gtk.Popover] = {}
        self._channel_rows: list[Gtk.Widget] = []
        self._channel_scroll: Gtk.ScrolledWindow | None = None
        self._day_corner_label: Gtk.Label | None = None
        self._corner_label: Gtk.Label | None = None
        self._day_buttons: list[Gtk.Button] = []
        self._previous_day_button: Gtk.Button | None = None
        self._next_day_button: Gtk.Button | None = None
        self._now_button: Gtk.Button | None = None
        self._last_outer_width: int = -1
        self._css_loaded: bool = False

    def do_activate(self) -> None:
        if self.props.active_window is not None:
            self.props.active_window.present()
            return

        window = Gtk.ApplicationWindow(application=self)
        window.set_title("tvhgtk – Schedule")
        window.set_default_size(WINDOW_WIDTH, 800)
        self._ensure_css_loaded()

        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        window.add_controller(key_controller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        header.set_margin_start(12)
        header.set_margin_end(12)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", self._on_refresh_clicked)

        previous_day_btn = Gtk.Button(label="Previous day")
        previous_day_btn.connect("clicked", self._on_previous_day_clicked)
        self._previous_day_button = previous_day_btn

        now_btn = Gtk.Button(label="Now")
        now_btn.connect("clicked", self._on_now_clicked)
        self._now_button = now_btn

        next_day_btn = Gtk.Button(label="Next day")
        next_day_btn.connect("clicked", self._on_next_day_clicked)
        self._next_day_button = next_day_btn

        self.title_label = Gtk.Label(label="TVHeadend Schedule")
        self.title_label.add_css_class("title-2")
        self.title_label.set_hexpand(True)
        self.title_label.set_xalign(0.0)
        self._title_label = self.title_label

        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_xalign(1.0)

        header.append(refresh_btn)
        header.append(previous_day_btn)
        header.append(now_btn)
        header.append(next_day_btn)
        header.append(self.title_label)
        header.append(self.status_label)

        # EPG container (rebuilt on each load/refresh)
        self.epg_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.epg_container.set_hexpand(True)
        self.epg_container.set_vexpand(True)

        key_commands_frame = Gtk.Frame(label="Key commands")
        key_commands_frame.add_css_class("key-commands-panel")
        key_commands_frame.set_margin_start(12)
        key_commands_frame.set_margin_end(12)
        key_commands_frame.set_margin_bottom(10)

        key_commands_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        key_commands_box.set_margin_top(8)
        key_commands_box.set_margin_bottom(8)
        key_commands_box.set_margin_start(10)
        key_commands_box.set_margin_end(10)

        key_commands_line_1 = Gtk.Label(
            label="h / Left: scroll left    l / Right: scroll right    End: end of day    q / Q: quit"
        )
        key_commands_line_1.set_xalign(0.0)

        key_commands_line_2 = Gtk.Label(
            label="H / Shift+Left: previous day    L / Shift+Right: next day    Home: today"
        )
        key_commands_line_2.set_xalign(0.0)

        key_commands_box.append(key_commands_line_1)
        key_commands_box.append(key_commands_line_2)
        key_commands_frame.set_child(key_commands_box)

        content.append(header)
        content.append(self.epg_container)
        content.append(key_commands_frame)
        window.set_child(content)

        ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_epg()
        window.present()

    # ── data loading ────────────────────────────────────────────────────────

    def _load_epg(self, reload_channels: bool = True) -> None:
        self.status_label.set_text("Loading...")

        now_dt = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self._today_start = int(now_dt.timestamp())
        self._window_start = self._today_start + (self._selected_day_index * 86400)
        window_end = self._window_start + TOTAL_HOURS * 3600

        try:
            url, username, password, parser = load_server_config()
            self._category_color_rules = load_category_color_rules(parser)
            configure_tvheadend(url, username, password)
            if reload_channels or not self._channels:
                channels, _ = channelGrid()
                self._channels = sorted(
                    channels,
                    key=lambda c: (c.get("number", 99999), c.get("name", "")),
                )
        except (AppConfigError, TVHError, RuntimeError) as err:
            self.status_label.set_text(f"Error: {err}")
            return

        self._epg_data = {}
        for ch in self._channels:
            uuid = str(ch.get("uuid", "")).strip()
            if not uuid:
                continue
            try:
                events, _ = epgEventsOnChannel(
                    uuid, start=self._window_start, stop=window_end
                )
                self._epg_data[uuid] = [
                    e
                    for e in events
                    if isinstance(e.get("start"), int)
                    and isinstance(e.get("stop"), int)
                    and (e["stop"] - e["start"]) >= MIN_PROGRAM_MINUTES * 60  # type: ignore[operator]
                ]
            except TVHError:
                self._epg_data[uuid] = []

        start_dt = datetime.fromtimestamp(self._window_start)
        end_dt = datetime.fromtimestamp(window_end)
        self.status_label.set_text(
            f"{len(self._channels)} channels  *  "
            f"{start_dt:%a %d %b}  *  {start_dt:%H:%M} - {end_dt:%H:%M}"
        )
        self._update_header_title()
        self._build_epg_grid()
        self._update_day_controls()

    def _on_refresh_clicked(self, _btn: Gtk.Button) -> None:
        self._load_epg(reload_channels=True)

    def _on_day_selected(self, _btn: Gtk.Button, day_index: int) -> None:
        self._select_day(day_index)

    def _on_previous_day_clicked(self, _btn: Gtk.Button) -> None:
        self._select_day(self._selected_day_index - 1)

    def _on_next_day_clicked(self, _btn: Gtk.Button) -> None:
        self._select_day(self._selected_day_index + 1)

    def _on_now_clicked(self, _btn: Gtk.Button) -> None:
        self._select_day(0)

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK
        ):
            return False

        shift_pressed = bool(state & Gdk.ModifierType.SHIFT_MASK)

        if keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.quit()
            return True

        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self._select_day(0)
            return True

        if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            self._scroll_schedule_to_end()
            return True

        if shift_pressed and keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left, Gdk.KEY_H):
            self._select_day(self._selected_day_index - 1)
            return True

        if shift_pressed and keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right, Gdk.KEY_L):
            self._select_day(self._selected_day_index + 1)
            return True

        if not shift_pressed and keyval in (Gdk.KEY_h, Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self._scroll_schedule(-1)
            return True

        if not shift_pressed and keyval in (Gdk.KEY_l, Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self._scroll_schedule(1)
            return True

        return False

    def _scroll_schedule(self, direction: int) -> None:
        if self._program_scroll is None:
            return

        adjustment = self._program_scroll.get_hadjustment()
        step = direction * SCHEDULE_SCROLL_STEP_MINUTES * PIXELS_PER_MINUTE
        max_value = max(
            adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()
        )
        new_value = min(
            max(adjustment.get_value() + step, adjustment.get_lower()), max_value
        )
        adjustment.set_value(new_value)

    def _scroll_schedule_to_end(self) -> None:
        if self._program_scroll is None:
            return

        adjustment = self._program_scroll.get_hadjustment()
        max_value = max(
            adjustment.get_lower(), adjustment.get_upper() - adjustment.get_page_size()
        )
        adjustment.set_value(max_value)

    def _select_day(self, day_index: int) -> None:
        day_index = max(0, min(day_index, TOTAL_DAYS - 1))
        if day_index == self._selected_day_index:
            self._update_day_controls()
            return
        self._selected_day_index = day_index
        self._load_epg(reload_channels=False)

    def _format_selected_day_label(self) -> str:
        selected_dt = datetime.fromtimestamp(self._window_start)
        if self._selected_day_index == 0:
            return f"Today • {selected_dt:%a %d %b}"
        return selected_dt.strftime("%A %d %b")

    def _update_header_title(self) -> None:
        if self._title_label is None:
            return
        self._title_label.set_text(
            f"TVHeadend Schedule  •  {self._format_selected_day_label()}"
        )

    def _ensure_css_loaded(self) -> None:
        if self._css_loaded:
            return

        provider = Gtk.CssProvider()
        provider.load_from_string(
            """
            .key-commands-panel > border {
                background-color: rgba(0, 0, 0, 0.34);
                border: 1px solid rgba(0, 0, 0, 0.56);
                border-radius: 8px;
            }

            .key-commands-panel > label {
                font-weight: 700;
                padding: 2px 6px;
            }
            """
        )

        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        self._css_loaded = True

    def _clear_hover_state(self) -> None:
        for popover in self._program_popovers.values():
            popover.popdown()
            if popover.get_parent() is not None:
                popover.unparent()

        self._program_popovers.clear()
        self._program_regions.clear()

    def _build_program_regions(
        self, events: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        regions: list[dict[str, object]] = []
        for event in events:
            start = event.get("start")
            stop = event.get("stop")
            if not isinstance(start, int) or not isinstance(stop, int):
                continue

            x = (start - self._window_start) / 60 * PIXELS_PER_MINUTE
            width = (stop - start) / 60 * PIXELS_PER_MINUTE
            if x + width < 0 or x > TOTAL_WIDTH:
                continue

            title = str(event.get("title") or "Untitled")
            subtitle = str(event.get("subtitle") or "").strip()
            summary = str(event.get("summary") or "").strip()
            description = str(event.get("description") or "").strip()
            fill, border = self._color_for_event_category(event)

            detail = description or summary or subtitle
            time_text = (
                f"{datetime.fromtimestamp(start):%H:%M} - "
                f"{datetime.fromtimestamp(stop):%H:%M}"
            )
            hover_text = f"{title}\n{time_text}"
            if detail:
                hover_text = f"{hover_text}\n{detail}"

            regions.append(
                {
                    "x": x,
                    "w": width,
                    "title": title,
                    "hover": hover_text,
                    "fill": fill,
                    "border": border,
                }
            )

        return regions

    def _color_for_event_category(
        self, event: dict[str, object]
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        category = event.get("category")
        category_text = ""

        if isinstance(category, list):
            category_text = " ".join(str(item) for item in category).lower()
        elif isinstance(category, str):
            category_text = category.lower()

        for _palette_key, keywords, fill, border in self._category_color_rules:
            if any(keyword in category_text for keyword in keywords):
                return fill, border

        return (0.18, 0.38, 0.65), (0.06, 0.06, 0.06)

    def _attach_program_hover(
        self, area: Gtk.DrawingArea, regions: list[dict[str, object]]
    ) -> None:
        self._program_regions[area] = regions

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
        self._program_popovers[area] = popover

        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_program_clicked, area)
        area.add_controller(click)

    def _on_program_clicked(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
        area: Gtk.DrawingArea,
    ) -> None:
        regions = self._program_regions.get(area, [])
        region = self._find_region_at_x(regions, x)

        popover = self._program_popovers.get(area)
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

    def _find_region_at_x(
        self, regions: list[dict[str, object]], x: float
    ) -> dict[str, object] | None:
        for region in regions:
            left = float(region.get("x", 0.0))
            width = float(region.get("w", 0.0))
            if left <= x <= (left + width):
                return region
        return None

    # ── grid construction ────────────────────────────────────────────────────

    def _build_epg_grid(self) -> None:
        self._clear_hover_state()

        child = self.epg_container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.epg_container.remove(child)
            child = nxt

        outer = Gtk.Grid()
        outer.set_row_spacing(0)
        outer.set_column_spacing(0)
        outer.add_tick_callback(self._on_outer_tick)

        day_corner = Gtk.Label(label="Days")
        day_corner.add_css_class("dim-label")
        day_corner.set_size_request(CHANNEL_COL_WIDTH, DAY_BUTTON_ROW_HEIGHT)
        day_corner.set_hexpand(False)
        self._day_corner_label = day_corner
        outer.attach(day_corner, 0, 0, 1, 1)

        day_button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        day_button_box.set_margin_top(6)
        day_button_box.set_margin_bottom(6)
        day_button_box.set_margin_start(6)
        day_button_box.set_margin_end(6)
        day_button_box.set_halign(Gtk.Align.START)
        self._day_buttons = []

        for day_index in range(TOTAL_DAYS):
            day_start = self._today_start + (day_index * 86400)
            day_dt = datetime.fromtimestamp(day_start)
            label = "Today" if day_index == 0 else day_dt.strftime("%a %d %b")
            button = Gtk.Button(label=label)
            button.connect("clicked", self._on_day_selected, day_index)
            day_button_box.append(button)
            self._day_buttons.append(button)

        day_scroll = Gtk.ScrolledWindow()
        day_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        day_scroll.set_propagate_natural_height(True)
        day_scroll.set_hexpand(True)
        day_scroll.set_size_request(-1, DAY_BUTTON_ROW_HEIGHT)
        day_scroll.set_child(day_button_box)

        self._update_day_controls()
        outer.attach(day_scroll, 1, 0, 1, 1)

        # [1,0] corner
        corner = Gtk.Label(label="Channels")
        corner.add_css_class("dim-label")
        corner.set_size_request(CHANNEL_COL_WIDTH, HEADER_HEIGHT)
        corner.set_hexpand(False)
        self._corner_label = corner
        outer.attach(corner, 0, 1, 1, 1)

        # [1,1] timeline header – shares hadjustment with program_scroll
        timeline_da = Gtk.DrawingArea()
        timeline_da.set_size_request(TOTAL_WIDTH, HEADER_HEIGHT)
        timeline_da.set_draw_func(self._draw_timeline, None)

        timeline_scroll = Gtk.ScrolledWindow()
        timeline_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        timeline_scroll.set_hexpand(True)
        timeline_scroll.set_size_request(-1, HEADER_HEIGHT)
        timeline_scroll.set_child(timeline_da)
        outer.attach(timeline_scroll, 1, 1, 1, 1)

        # [2,0] channel names – shares vadjustment with program_scroll
        channel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        channel_box.set_size_request(CHANNEL_COL_WIDTH, -1)
        self._channel_rows = []

        # [2,1] programme rows
        program_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        row_colours = [
            (0.13, 0.13, 0.13),
            (0.10, 0.10, 0.10),
        ]

        for i, ch in enumerate(self._channels):
            uuid = str(ch.get("uuid", "")).strip()
            name = str(ch.get("name", "<unnamed>"))
            events = self._epg_data.get(uuid, [])
            bg = row_colours[i % 2]
            regions = self._build_program_regions(events)

            # Channel label row (must be exactly ROW_HEIGHT to stay aligned)
            ch_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            ch_row.set_size_request(CHANNEL_COL_WIDTH, ROW_HEIGHT)

            icon_path = self._resolve_channel_icon_path(ch)
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
            self._channel_rows.append(ch_row)

            # Programme DrawingArea (same ROW_HEIGHT keeps rows aligned)
            prog_da = Gtk.DrawingArea()
            prog_da.set_size_request(TOTAL_WIDTH, ROW_HEIGHT)
            prog_da.set_draw_func(self._make_program_draw_func(regions, bg), None)
            self._attach_program_hover(prog_da, regions)
            program_box.append(prog_da)

        program_scroll = Gtk.ScrolledWindow()
        program_scroll.set_policy(Gtk.PolicyType.ALWAYS, Gtk.PolicyType.ALWAYS)
        program_scroll.set_hexpand(True)
        program_scroll.set_vexpand(True)
        program_scroll.set_child(program_box)

        channel_scroll = Gtk.ScrolledWindow()
        channel_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.EXTERNAL)
        channel_scroll.set_size_request(CHANNEL_COL_WIDTH, -1)
        channel_scroll.set_hexpand(False)
        channel_scroll.set_vexpand(True)
        channel_scroll.set_child(channel_box)
        self._channel_scroll = channel_scroll

        # Share adjustments so all three panels scroll in lock-step
        timeline_scroll.set_hadjustment(program_scroll.get_hadjustment())
        channel_scroll.set_vadjustment(program_scroll.get_vadjustment())

        outer.attach(channel_scroll, 0, 2, 1, 1)
        outer.attach(program_scroll, 1, 2, 1, 1)

        self.epg_container.append(outer)
        self._program_scroll = program_scroll
        self._apply_split_width(outer.get_allocated_width())

        # After layout, scroll so current time is near the left edge (1 hr before now)
        GLib.idle_add(self._scroll_to_now)

    def _scroll_to_now(self) -> bool:
        if self._program_scroll is None:
            return False
        if self._selected_day_index == 0:
            now = int(time.time())
            now_x = (now - self._window_start) / 60 * PIXELS_PER_MINUTE
            offset = max(0.0, now_x - 60 * PIXELS_PER_MINUTE)
        else:
            offset = 0.0
        self._program_scroll.get_hadjustment().set_value(offset)
        return False  # do not repeat

    def _on_outer_tick(self, widget: Gtk.Widget, _frame_clock: object) -> bool:
        width = widget.get_allocated_width()
        if width == self._last_outer_width:
            return True
        self._last_outer_width = width
        self._apply_split_width(width)
        return True

    def _apply_split_width(self, total_width: int) -> None:
        if total_width <= 0:
            return

        left_width = max(1, int(total_width * LEFT_SPLIT_RATIO))

        if self._day_corner_label is not None:
            self._day_corner_label.set_size_request(left_width, DAY_BUTTON_ROW_HEIGHT)

        if self._corner_label is not None:
            self._corner_label.set_size_request(left_width, HEADER_HEIGHT)

        if self._channel_scroll is not None:
            self._channel_scroll.set_size_request(left_width, -1)

        for row in self._channel_rows:
            row.set_size_request(left_width, ROW_HEIGHT)

    def _update_day_controls(self) -> None:
        self._update_header_title()

        for index, button in enumerate(self._day_buttons):
            if index == self._selected_day_index:
                button.add_css_class("suggested-action")
            else:
                button.remove_css_class("suggested-action")

        if self._previous_day_button is not None:
            self._previous_day_button.set_sensitive(self._selected_day_index > 0)

        if self._next_day_button is not None:
            self._next_day_button.set_sensitive(
                self._selected_day_index < TOTAL_DAYS - 1
            )

        if self._now_button is not None:
            self._now_button.set_sensitive(self._selected_day_index != 0)

    # ── channel icon resolution ──────────────────────────────────────────────

    def _resolve_channel_icon_path(self, channel: dict[str, object]) -> Path | None:
        candidate_stems: list[str] = []

        channel_uuid = channel.get("uuid")
        if isinstance(channel_uuid, str) and channel_uuid.strip():
            candidate_stems.append(channel_uuid.strip())

        channel_name = channel.get("name")
        if isinstance(channel_name, str) and channel_name.strip():
            normalized = normalize_channel_name(channel_name)
            if normalized:
                candidate_stems.append(normalized)

        for stem in candidate_stems:
            for extension in ICON_EXTENSIONS:
                candidate = ICON_CACHE_DIR / f"{stem}{extension}"
                if candidate.is_file():
                    return candidate

        return None

    # ── Cairo drawing ────────────────────────────────────────────────────────

    def _draw_timeline(
        self,
        _da: Gtk.DrawingArea,
        cr: object,
        _width: int,
        height: int,
        _data: None,
    ) -> None:
        cr.set_source_rgb(0.08, 0.08, 0.08)
        cr.paint()

        now = int(time.time())

        for h in range(TOTAL_HOURS + 1):
            x = h * 60 * PIXELS_PER_MINUTE
            ts = self._window_start + h * 3600

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
            if h < TOTAL_HOURS:
                half_x = x + 30 * PIXELS_PER_MINUTE
                cr.set_source_rgb(0.28, 0.28, 0.28)
                cr.set_line_width(1.0)
                cr.move_to(half_x + 0.5, height - 5)
                cr.line_to(half_x + 0.5, height)
                cr.stroke()

        # Current-time red marker
        now_x = (now - self._window_start) / 60 * PIXELS_PER_MINUTE
        if 0 <= now_x <= TOTAL_WIDTH:
            cr.set_source_rgb(0.9, 0.2, 0.2)
            cr.set_line_width(2.0)
            cr.move_to(now_x, 0)
            cr.line_to(now_x, height)
            cr.stroke()

    def _make_program_draw_func(
        self,
        regions: list[dict[str, object]],
        bg_colour: tuple[float, float, float],
    ):
        window_start = self._window_start

        def draw(
            _da: Gtk.DrawingArea,
            cr: object,
            _width: int,
            height: int,
            _data: None,
        ) -> None:
            cr.set_source_rgb(*bg_colour)
            cr.paint()

            now = int(time.time())
            now_x = (now - window_start) / 60 * PIXELS_PER_MINUTE
            if 0 <= now_x <= TOTAL_WIDTH:
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


def run() -> int:
    app = TVHGtkApplication()
    return app.run(None)
