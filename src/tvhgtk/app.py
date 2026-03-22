from __future__ import annotations

import configparser
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk
from tvheadend import channelGrid, configure, epgEventsOnChannel
from tvheadend.tvh import TVHError

CONFIG_PATH = Path.home() / ".config" / "tvhgtk" / "config"
ICON_CACHE_DIR = Path.home() / ".cache" / "tvhgtk" / "icons"
ICON_EXTENSIONS = (".png", ".jpg", ".jpeg", ".svg", ".webp")


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
        self._channels_status_text = ""
        self._channel_by_index: list[dict[str, object]] = []
        self._current_channel_index: int | None = None

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = Gtk.ApplicationWindow(application=self)
            window.set_title("tvhgtk")
            window.set_default_size(900, 560)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            content.set_margin_top(24)
            content.set_margin_bottom(24)
            content.set_margin_start(24)
            content.set_margin_end(24)

            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            self.back_button = Gtk.Button(label="Back")
            self.back_button.connect("clicked", self._on_back_clicked)
            self.back_button.set_sensitive(False)

            self.prev_button = Gtk.Button(label="Previous")
            self.prev_button.connect("clicked", self._on_previous_clicked)
            self.prev_button.set_sensitive(False)

            self.next_button = Gtk.Button(label="Next")
            self.next_button.connect("clicked", self._on_next_clicked)
            self.next_button.set_sensitive(False)

            header_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

            self.title_label = Gtk.Label(label="Channels")
            self.title_label.add_css_class("title-1")
            self.title_label.set_xalign(0.0)

            self.subtitle_label = Gtk.Label(label=f"Source: {CONFIG_PATH}")
            self.subtitle_label.add_css_class("dim-label")
            self.subtitle_label.set_xalign(0.0)

            cache_subtitle = Gtk.Label(label=f"Icons: {ICON_CACHE_DIR}")
            cache_subtitle.add_css_class("dim-label")
            cache_subtitle.set_xalign(0.0)

            self.status_label = Gtk.Label(label="Loading channels...")
            self.status_label.add_css_class("dim-label")
            self.status_label.set_xalign(0.0)

            self.channel_list = Gtk.ListBox()
            self.channel_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.channel_list.set_activate_on_single_click(True)
            self.channel_list.connect("row-activated", self._on_channel_row_activated)

            channels_scroller = Gtk.ScrolledWindow()
            channels_scroller.set_vexpand(True)
            channels_scroller.set_hexpand(True)
            channels_scroller.set_child(self.channel_list)

            self.schedule_list = Gtk.ListBox()
            self.schedule_list.set_selection_mode(Gtk.SelectionMode.NONE)

            schedule_scroller = Gtk.ScrolledWindow()
            schedule_scroller.set_vexpand(True)
            schedule_scroller.set_hexpand(True)
            schedule_scroller.set_child(self.schedule_list)

            self.stack = Gtk.Stack()
            self.stack.set_hexpand(True)
            self.stack.set_vexpand(True)
            self.stack.add_named(channels_scroller, "channels")
            self.stack.add_named(schedule_scroller, "schedule")
            self.stack.set_visible_child_name("channels")

            header_text.append(self.title_label)
            header_text.append(self.subtitle_label)
            header_text.append(cache_subtitle)

            header.append(self.prev_button)
            header.append(self.back_button)
            header.append(self.next_button)
            header.append(header_text)

            content.append(header)
            content.append(self.status_label)
            content.append(self.stack)
            window.set_child(content)

            ICON_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._load_channels()

        window.present()

    def _clear_channel_rows(self) -> None:
        self._channel_by_index = []
        row = self.channel_list.get_first_child()
        while row is not None:
            next_row = row.get_next_sibling()
            self.channel_list.remove(row)
            row = next_row

    def _clear_schedule_rows(self) -> None:
        row = self.schedule_list.get_first_child()
        while row is not None:
            next_row = row.get_next_sibling()
            self.schedule_list.remove(row)
            row = next_row

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

    def _add_channel_row(
        self, channel: dict[str, object], number: str, name: str
    ) -> None:
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)

        icon_path = self._resolve_channel_icon_path(channel)
        if icon_path is None:
            icon = Gtk.Image.new_from_icon_name("image-missing")
        else:
            icon = Gtk.Image.new_from_file(str(icon_path))
        icon.set_pixel_size(24)

        number_label = Gtk.Label(label=number)
        number_label.set_width_chars(6)
        number_label.set_xalign(0.0)
        number_label.add_css_class("numeric")

        name_label = Gtk.Label(label=name)
        name_label.set_xalign(0.0)
        name_label.set_hexpand(True)

        row_box.append(icon)
        row_box.append(number_label)
        row_box.append(name_label)

        row = Gtk.ListBoxRow()
        row.set_child(row_box)
        self.channel_list.append(row)
        self._channel_by_index.append(channel)

    def _add_schedule_row(self, start_label: str, title: str, subtitle: str) -> None:
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row_box.set_margin_top(6)
        row_box.set_margin_bottom(6)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)

        heading = Gtk.Label(label=f"{start_label}  {title}")
        heading.set_xalign(0.0)

        row_box.append(heading)
        if subtitle:
            details = Gtk.Label(label=subtitle)
            details.add_css_class("dim-label")
            details.set_xalign(0.0)
            details.set_wrap(True)
            row_box.append(details)

        row = Gtk.ListBoxRow()
        row.set_child(row_box)
        self.schedule_list.append(row)

    def _format_time_range(
        self, start_epoch: int | None, stop_epoch: int | None
    ) -> str:
        if start_epoch is None or stop_epoch is None:
            return "Unknown time"
        start_dt = datetime.fromtimestamp(start_epoch)
        stop_dt = datetime.fromtimestamp(stop_epoch)
        return f"{start_dt:%a %H:%M} - {stop_dt:%H:%M}"

    def _show_channels_view(self) -> None:
        self._current_channel_index = None
        self.stack.set_visible_child_name("channels")
        self.back_button.set_sensitive(False)
        self.prev_button.set_sensitive(False)
        self.next_button.set_sensitive(False)
        self.title_label.set_text("Channels")
        self.subtitle_label.set_text(f"Source: {CONFIG_PATH}")
        if self._channels_status_text:
            self.status_label.set_text(self._channels_status_text)

    def _show_schedule_view(self, channel_name: str, channel_uuid: str) -> None:
        self._clear_schedule_rows()
        self.stack.set_visible_child_name("schedule")
        self.back_button.set_sensitive(True)
        self.title_label.set_text(channel_name)
        self.subtitle_label.set_text(f"Schedule for next 24 hours ({channel_uuid})")
        self.status_label.set_text("Loading schedule...")

        now = int(time.time())
        window_end = now + 24 * 60 * 60

        try:
            events, _ = epgEventsOnChannel(channel_uuid, start=now, stop=window_end)
        except TVHError as err:
            self.status_label.set_text(f"Unable to load schedule: {err}")
            return

        if not events:
            self.status_label.set_text("No schedule entries in the next 24 hours")
            return

        sorted_events = sorted(events, key=lambda event: event.get("start", now))
        for event in sorted_events:
            title = str(event.get("title") or "<untitled>")
            subtitle = str(event.get("subtitle") or "")
            start_value = event.get("start")
            stop_value = event.get("stop")
            start_epoch = start_value if isinstance(start_value, int) else None
            stop_epoch = stop_value if isinstance(stop_value, int) else None
            time_range = self._format_time_range(start_epoch, stop_epoch)
            self._add_schedule_row(time_range, title, subtitle)

        self.status_label.set_text(f"Loaded {len(sorted_events)} entries")

    def _show_schedule_for_index(self, index: int) -> None:
        if index < 0 or index >= len(self._channel_by_index):
            return

        channel = self._channel_by_index[index]
        channel_uuid = str(channel.get("uuid", "")).strip()
        channel_name = str(channel.get("name", "<unnamed channel>"))
        if not channel_uuid:
            self.status_label.set_text("Selected channel does not have a UUID")
            return

        self._current_channel_index = index
        self.prev_button.set_sensitive(index > 0)
        self.next_button.set_sensitive(index < (len(self._channel_by_index) - 1))
        self._show_schedule_view(channel_name=channel_name, channel_uuid=channel_uuid)

    def _on_back_clicked(self, _button: Gtk.Button) -> None:
        self._show_channels_view()

    def _on_previous_clicked(self, _button: Gtk.Button) -> None:
        if self._current_channel_index is None:
            return
        self._show_schedule_for_index(self._current_channel_index - 1)

    def _on_next_clicked(self, _button: Gtk.Button) -> None:
        if self._current_channel_index is None:
            return
        self._show_schedule_for_index(self._current_channel_index + 1)

    def _on_channel_row_activated(
        self, _list_box: Gtk.ListBox, row: Gtk.ListBoxRow
    ) -> None:
        index = row.get_index()
        if index < 0 or index >= len(self._channel_by_index):
            return

        self._show_schedule_for_index(index)

    def _load_channels(self) -> None:
        self._clear_channel_rows()

        try:
            url, username, password = load_server_config()
            configure_tvheadend(url, username, password)
            channels, total = channelGrid()
        except (AppConfigError, TVHError, RuntimeError) as err:
            self.status_label.set_text(f"Unable to load channels: {err}")
            return

        rendered = 0
        for channel in sorted(
            channels,
            key=lambda item: (item.get("number", 999999), item.get("name", "")),
        ):
            name = str(channel.get("name", "<unnamed channel>"))
            number_value = channel.get("number")
            number = "-" if number_value is None else str(number_value)
            self._add_channel_row(channel=channel, number=number, name=name)
            rendered += 1

        self._channels_status_text = f"Loaded {rendered} channels (total: {total})"
        self.status_label.set_text(self._channels_status_text)
        self._show_channels_view()


def run() -> int:
    app = TVHGtkApplication()
    return app.run(None)
