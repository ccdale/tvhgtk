from __future__ import annotations

from datetime import datetime
from typing import Any


def color_for_event_category(
    event: dict[str, object],
    category_color_rules: list[
        tuple[
            str, tuple[str, ...], tuple[float, float, float], tuple[float, float, float]
        ]
    ],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    category = event.get("category")
    category_text = ""

    if isinstance(category, list):
        category_text = " ".join(str(item) for item in category).lower()
    elif isinstance(category, str):
        category_text = category.lower()

    for _palette_key, keywords, fill, border in category_color_rules:
        if any(keyword in category_text for keyword in keywords):
            return fill, border

    return (0.18, 0.38, 0.65), (0.06, 0.06, 0.06)


def build_program_regions(
    events: list[dict[str, object]],
    *,
    window_start: int,
    pixels_per_minute: int,
    total_width: int,
    category_color_rules: list[
        tuple[
            str, tuple[str, ...], tuple[float, float, float], tuple[float, float, float]
        ]
    ],
) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for event in events:
        start = event.get("start")
        stop = event.get("stop")
        if not isinstance(start, int) or not isinstance(stop, int):
            continue

        x = (start - window_start) / 60 * pixels_per_minute
        width = (stop - start) / 60 * pixels_per_minute
        if x + width < 0 or x > total_width:
            continue

        title = str(event.get("title") or "Untitled")
        subtitle = str(event.get("subtitle") or "").strip()
        summary = str(event.get("summary") or "").strip()
        description = str(event.get("description") or "").strip()
        fill, border = color_for_event_category(event, category_color_rules)

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
