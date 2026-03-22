from __future__ import annotations

import configparser
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import GLib, Gtk, Pango, PangoCairo
from tvheadend import channelGrid, configure, epgEventsOnChannel
from tvheadend.tvh import TVHError

CONFIG_PATH = Path.home() / ".config" / "tvhgtk" / "config"
ICON_CACHE_DIR = Path.home() / ".cache" / "tvhgtk" / "icons"
ICON_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp")

# ── EPG grid layout ────────────────────────────────────────────────────────────
WINDOW_WIDTH: int = 1400
LEFT_SPLIT_RATIO: float = 0.20
PIXELS_PER_MINUTE: int = 4  # 4 px = 1 min → 240 px/hr
CHANNEL_COL_WIDTH: int = int(WINDOW_WIDTH * LEFT_SPLIT_RATIO)
ROW_HEIGHT: int = 50  # height of each channel row (px)
HEADER_HEIGHT: int = 36  # height of the timeline header (px)
MIN_PROGRAM_MINUTES: int = 15  # programmes shorter than this are discarded
TOTAL_HOURS: int = 24  # schedule window length
TOTAL_WIDTH: int = TOTAL_HOURS * 60 * PIXELS_PER_MINUTE  # 5760 px


class AppConfigError(Exception):
    """Raised when the local tvhgtk configuration is invalid."""


def load_server_config() -> tuple[str, str, str]:
    parser = configparser.ConfigParser()
    if not CONFIG_PATH.exists():
        raise AppConfigError(f"config file not found: {CONFIG_PATH}")
    parser.read(CONFIG_PATH)

    if "server" not in parser:
        raise AppConfigError("missing [server] section in config")

    section = parser["server"]
    url = section.get("url", "").strip()
    username = section.get("username", "").strip()
    password = section.get("password", "").strip()

    if not url:
        raise AppConfigError("server url is required")
    if not username:
        raise AppConfigError("server username is required")
    if not password:
        raise AppConfigError("server password is required")

    return url, username, password


def configure_tvheadend(url: str, username: str, password: str) -> None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        raise AppConfigError("server url must include scheme and host")

    configure(
        host=parsed.hostname,
        username=username,
        password=password,
        scheme=parsed.scheme,
        port=parsed.port,
    )


def normalize_channel_name(name: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    return normalized.strip("-")


class TVHGtkApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.ccdale.tvhgtk")
        self._window_start: int = 0
        self._channels: list[dict[str, object]] = []
        self._epg_data: dict[str, list[dict[str, object]]] = {}
        self._program_scroll: Gtk.ScrolledWindow | None = None
        self._channel_rows: list[Gtk.Widget] = []
        self._channel_scroll: Gtk.ScrolledWindow | None = None
        self._corner_label: Gtk.Label | None = None
        self._last_outer_width: int = -1

    def do_activate(self) -> None:
        if self.props.active_window is not None:
            self.props.active_window.present()
            return

        window = Gtk.ApplicationWindow(application=self)
        window.set_title("tvhgtk – Schedule")
        window.set_default_size(WINDOW_WIDTH, 800)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # header bar
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header.set_margin_top(8)
        header.set_margin_bottom(8)
        header.set_margin_start(12)
        header.set_margin_end(12)

        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.connect("clicked", self._on_refresh_clicked)

        self.title_label = Gtk.Label(label="TVHeadend Schedule")
        self.title_label.add_css_class("title-2")
        self.title_label.set_hexpand(True)
        self.title_label.set_xalign(0.0)

        self.status_label = Gtk.Label(label="")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_xalign(1.0)

        header.append(refresh_btn)
        header.append(self.title_label)
        header.append(self.status_label)

        # EPG container (rebuilt on each load/refresh)
        self.epg_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.epg_container.set_hexpand(True)
        self.epg_container.set_vexpand(True)

        content.append(header)
        content.append(self.epg_container)
        window.set_child(content)

        ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._load_epg()
        window.present()

    # ── data loading ────────────────────────────────────────────────────────

    def _load_epg(self) -> None:
        self.status_label.set_text("Loading...")

        now = int(time.time())
        self._window_start = now - (now % 1800)  # snap to previous 30-min mark
        window_end = self._window_start + TOTAL_HOURS * 3600

        try:
            url, username, password = load_server_config()
            configure_tvheadend(url, username, password)
            channels, _ = channelGrid()
        except (AppConfigError, TVHError, RuntimeError) as err:
            self.status_label.set_text(f"Error: {err}")
            return

        self._channels = sorted(
            channels,
            key=lambda c: (c.get("number", 99999), c.get("name", "")),
        )

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
            f"{start_dt:%H:%M} - {end_dt:%H:%M %d %b}"
        )
        self._build_epg_grid()

    def _on_refresh_clicked(self, _btn: Gtk.Button) -> None:
        self._load_epg()

    # ── grid construction ────────────────────────────────────────────────────

    def _build_epg_grid(self) -> None:
        child = self.epg_container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.epg_container.remove(child)
            child = nxt

        outer = Gtk.Grid()
        outer.set_row_spacing(0)
        outer.set_column_spacing(0)
        outer.add_tick_callback(self._on_outer_tick)

        # [0,0] corner
        corner = Gtk.Label(label="Channels")
        corner.add_css_class("dim-label")
        corner.set_size_request(CHANNEL_COL_WIDTH, HEADER_HEIGHT)
        corner.set_hexpand(False)
        self._corner_label = corner
        outer.attach(corner, 0, 0, 1, 1)

        # [0,1] timeline header – shares hadjustment with program_scroll
        timeline_da = Gtk.DrawingArea()
        timeline_da.set_size_request(TOTAL_WIDTH, HEADER_HEIGHT)
        timeline_da.set_draw_func(self._draw_timeline, None)

        timeline_scroll = Gtk.ScrolledWindow()
        timeline_scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        timeline_scroll.set_hexpand(True)
        timeline_scroll.set_size_request(-1, HEADER_HEIGHT)
        timeline_scroll.set_child(timeline_da)
        outer.attach(timeline_scroll, 1, 0, 1, 1)

        # [1,0] channel names – shares vadjustment with program_scroll
        channel_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        channel_box.set_size_request(CHANNEL_COL_WIDTH, -1)
        self._channel_rows = []

        # [1,1] programme rows
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
            prog_da.set_draw_func(self._make_program_draw_func(events, bg), None)
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

        outer.attach(channel_scroll, 0, 1, 1, 1)
        outer.attach(program_scroll, 1, 1, 1, 1)

        self.epg_container.append(outer)
        self._program_scroll = program_scroll
        self._apply_split_width(outer.get_allocated_width())

        # After layout, scroll so current time is near the left edge (1 hr before now)
        GLib.idle_add(self._scroll_to_now)

    def _scroll_to_now(self) -> bool:
        if self._program_scroll is None:
            return False
        now = int(time.time())
        now_x = (now - self._window_start) / 60 * PIXELS_PER_MINUTE
        offset = max(0.0, now_x - 60 * PIXELS_PER_MINUTE)
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

        if self._corner_label is not None:
            self._corner_label.set_size_request(left_width, HEADER_HEIGHT)

        if self._channel_scroll is not None:
            self._channel_scroll.set_size_request(left_width, -1)

        for row in self._channel_rows:
            row.set_size_request(left_width, ROW_HEIGHT)

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
        events: list[dict[str, object]],
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

            for event in events:
                start = event.get("start")
                stop = event.get("stop")
                if not isinstance(start, int) or not isinstance(stop, int):
                    continue

                x = (start - window_start) / 60 * PIXELS_PER_MINUTE
                cell_w = (stop - start) / 60 * PIXELS_PER_MINUTE

                if x + cell_w < 0 or x > TOTAL_WIDTH:
                    continue

                # Cell fill
                cr.set_source_rgb(0.18, 0.38, 0.65)
                cr.rectangle(x + 1, 1, cell_w - 2, height - 2)
                cr.fill()

                # Cell border
                cr.set_source_rgb(0.06, 0.06, 0.06)
                cr.set_line_width(1.0)
                cr.rectangle(x + 0.5, 0.5, cell_w - 1, height - 1)
                cr.stroke()

                # Programme title (skip if cell too narrow)
                title = str(event.get("title") or "")
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
