from __future__ import annotations

import configparser
from pathlib import Path
from urllib.parse import urlparse

from tvheadend import configure

from .types import RGB, CategoryColorRule

CONFIG_PATH = Path.home() / ".config" / "tvhgtk" / "config"

DEFAULT_CATEGORY_COLOR_RULES: list[CategoryColorRule] = [
    (
        "news",
        ("news", "current affairs", "weather"),
        (0.22, 0.40, 0.70),
        (0.08, 0.16, 0.30),
    ),
    (
        "sport",
        ("sport", "football", "rugby", "tennis", "cricket", "athletics"),
        (0.18, 0.56, 0.30),
        (0.06, 0.22, 0.12),
    ),
    ("film", ("film", "movie", "cinema"), (0.56, 0.22, 0.22), (0.26, 0.10, 0.10)),
    (
        "drama",
        ("drama", "crime", "mystery", "thriller"),
        (0.42, 0.26, 0.58),
        (0.18, 0.10, 0.26),
    ),
    (
        "comedy",
        ("comedy", "sitcom", "entertainment"),
        (0.64, 0.46, 0.18),
        (0.28, 0.18, 0.06),
    ),
    (
        "documentary",
        ("documentary", "history", "science", "nature", "factual"),
        (0.18, 0.50, 0.50),
        (0.06, 0.20, 0.20),
    ),
    (
        "children",
        ("children", "kids", "animation"),
        (0.62, 0.36, 0.18),
        (0.24, 0.12, 0.06),
    ),
    ("music", ("music", "arts", "culture"), (0.50, 0.30, 0.22), (0.22, 0.12, 0.08)),
]


class AppConfigError(Exception):
    """Raised when the local tvhgtk configuration is invalid."""


def _hex_to_rgb(value: str) -> RGB | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        r = int(text[0:2], 16)
        g = int(text[2:4], 16)
        b = int(text[4:6], 16)
    except ValueError:
        return None
    return (r / 255.0, g / 255.0, b / 255.0)


def _darken(color: RGB, factor: float = 0.45) -> RGB:
    return (color[0] * factor, color[1] * factor, color[2] * factor)


def load_category_color_rules(
    parser: configparser.ConfigParser,
) -> list[CategoryColorRule]:
    overrides: dict[str, tuple[RGB, RGB]] = {}

    if "category_colors" in parser:
        section = parser["category_colors"]
        for key, raw in section.items():
            palette_key = key.strip().lower()
            parts = [part.strip() for part in raw.split(",") if part.strip()]
            if not parts:
                continue

            fill = _hex_to_rgb(parts[0])
            if fill is None:
                continue

            border = _hex_to_rgb(parts[1]) if len(parts) > 1 else _darken(fill)
            if border is None:
                border = _darken(fill)

            overrides[palette_key] = (fill, border)

    rules: list[CategoryColorRule] = []
    for (
        palette_key,
        keywords,
        default_fill,
        default_border,
    ) in DEFAULT_CATEGORY_COLOR_RULES:
        fill, border = overrides.get(palette_key, (default_fill, default_border))
        rules.append((palette_key, keywords, fill, border))

    return rules


def load_server_config() -> tuple[str, str, str, configparser.ConfigParser]:
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

    return url, username, password, parser


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
