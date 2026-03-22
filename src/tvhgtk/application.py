from __future__ import annotations

from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")

from gi.repository import Gdk, Gtk  # noqa: E402
from tvheadend import channelGrid, epgEventsOnChannel  # noqa: E402
from tvheadend.tvh import TVHError  # noqa: E402

from .config import (  # noqa: E402
    DEFAULT_CATEGORY_COLOR_RULES,
    AppConfigError,
    configure_tvheadend,
    load_category_color_rules,
    load_server_config,
)
from .epg_helpers import build_program_regions  # noqa: E402
from .grid_builder import build_epg_grid  # noqa: E402
from .interactions import (  # noqa: E402
    attach_program_hover,
    clear_hover_state,
    dismiss_active_popovers,
    on_program_clicked,
)
from .layout_helpers import (  # noqa: E402
    apply_split_width,
    on_outer_tick,
    resolve_channel_icon_path,
    scroll_to_now,
    update_day_controls,
)
from .navigation import (  # noqa: E402
    on_key_pressed,
    on_next_day_clicked,
    on_now_clicked,
    on_previous_day_clicked,
    scroll_schedule,
    scroll_schedule_to_end,
    select_day,
)
from .types import CategoryColorRule, ProgramRegion  # noqa: E402

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
        self._category_color_rules: list[CategoryColorRule] = list(
            DEFAULT_CATEGORY_COLOR_RULES
        )
        self._program_regions: dict[Gtk.DrawingArea, list[ProgramRegion]] = {}
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
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect(
            "key-pressed",
            lambda _controller, keyval, _keycode, state: (
                dismiss_active_popovers(self) or on_key_pressed(self, keyval, state)
            ),
        )
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
        previous_day_btn.connect("clicked", lambda _btn: on_previous_day_clicked(self))
        self._previous_day_button = previous_day_btn

        now_btn = Gtk.Button(label="Now")
        now_btn.connect("clicked", lambda _btn: on_now_clicked(self))
        self._now_button = now_btn

        next_day_btn = Gtk.Button(label="Next day")
        next_day_btn.connect("clicked", lambda _btn: on_next_day_clicked(self))
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

    def _scroll_schedule(self, direction: int) -> None:
        scroll_schedule(
            self,
            direction,
            SCHEDULE_SCROLL_STEP_MINUTES,
            PIXELS_PER_MINUTE,
        )

    def _scroll_schedule_to_end(self) -> None:
        scroll_schedule_to_end(self)

    def _select_day(self, day_index: int) -> None:
        select_day(self, day_index, TOTAL_DAYS)

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
        clear_hover_state(self)

    def _build_program_regions(
        self, events: list[dict[str, object]]
    ) -> list[ProgramRegion]:
        return build_program_regions(
            events,
            window_start=self._window_start,
            pixels_per_minute=PIXELS_PER_MINUTE,
            total_width=TOTAL_WIDTH,
            category_color_rules=self._category_color_rules,
        )

    def _attach_program_hover(
        self, area: Gtk.DrawingArea, regions: list[ProgramRegion]
    ) -> None:
        attach_program_hover(self, area, regions)

    def _on_program_clicked(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        x: float,
        y: float,
        area: Gtk.DrawingArea,
    ) -> None:
        on_program_clicked(self, x, y, area)

    # ── grid construction ────────────────────────────────────────────────────

    def _build_epg_grid(self) -> None:
        build_epg_grid(
            self,
            total_days=TOTAL_DAYS,
            channel_col_width=CHANNEL_COL_WIDTH,
            day_button_row_height=DAY_BUTTON_ROW_HEIGHT,
            header_height=HEADER_HEIGHT,
            total_width=TOTAL_WIDTH,
            row_height=ROW_HEIGHT,
            total_hours=TOTAL_HOURS,
            pixels_per_minute=PIXELS_PER_MINUTE,
        )

    def _scroll_to_now(self) -> bool:
        return scroll_to_now(self, PIXELS_PER_MINUTE)

    def _on_outer_tick(self, widget: Gtk.Widget, _frame_clock: object) -> bool:
        return on_outer_tick(
            self,
            widget,
            left_split_ratio=LEFT_SPLIT_RATIO,
            row_height=ROW_HEIGHT,
            day_button_row_height=DAY_BUTTON_ROW_HEIGHT,
            header_height=HEADER_HEIGHT,
        )

    def _apply_split_width(self, total_width: int) -> None:
        apply_split_width(
            self,
            total_width,
            left_split_ratio=LEFT_SPLIT_RATIO,
            row_height=ROW_HEIGHT,
            day_button_row_height=DAY_BUTTON_ROW_HEIGHT,
            header_height=HEADER_HEIGHT,
        )

    def _update_day_controls(self) -> None:
        update_day_controls(self, TOTAL_DAYS)

    # ── channel icon resolution ──────────────────────────────────────────────

    def _resolve_channel_icon_path(self, channel: dict[str, object]) -> Path | None:
        return resolve_channel_icon_path(
            channel,
            ICON_CACHE_DIR,
            ICON_EXTENSIONS,
            normalize_channel_name,
        )


def run() -> int:
    app = TVHGtkApplication()
    return app.run(None)
