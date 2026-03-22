from __future__ import annotations

from datetime import datetime

from .types import RGB, CategoryColorRule, ProgramRegion

RecordingMatchKey = tuple[str, int, int]


def _normalize_title(value: str) -> str:
    return " ".join(value.lower().split())


def build_upcoming_recording_index(
    recordings: list[dict[str, object]],
) -> set[RecordingMatchKey]:
    index: set[RecordingMatchKey] = set()
    for recording in recordings:
        title = str(recording.get("disp_title") or "").strip()
        start = recording.get("start")
        stop = recording.get("stop")
        if not title or not isinstance(start, int) or not isinstance(stop, int):
            continue
        index.add((_normalize_title(title), start, stop))
    return index


def is_scheduled_recording(
    event: dict[str, object],
    recording_index: set[RecordingMatchKey],
    tolerance_seconds: int = 120,
) -> bool:
    title = str(event.get("title") or "").strip()
    start = event.get("start")
    stop = event.get("stop")
    if not title or not isinstance(start, int) or not isinstance(stop, int):
        return False

    normalized_title = _normalize_title(title)
    for title_key, start_key, stop_key in recording_index:
        if title_key != normalized_title:
            continue
        if (
            abs(start_key - start) <= tolerance_seconds
            and abs(stop_key - stop) <= tolerance_seconds
        ):
            return True
    return False


def color_for_event_category(
    event: dict[str, object],
    category_color_rules: list[CategoryColorRule],
) -> tuple[RGB, RGB]:
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
    category_color_rules: list[CategoryColorRule],
    recording_index: set[RecordingMatchKey] | None = None,
) -> list[ProgramRegion]:
    regions: list[ProgramRegion] = []
    for event in events:
        start = event.get("start")
        stop = event.get("stop")
        if not isinstance(start, int) or not isinstance(stop, int):
            continue

        event_id_raw = event.get("eventId")
        event_id = event_id_raw if isinstance(event_id_raw, int) else None

        series_id_raw = event.get("seriesid")
        if series_id_raw is None:
            series_id_raw = event.get("seriesId")
        series_id = (
            str(series_id_raw).strip() if series_id_raw not in (None, "") else None
        )

        x = (start - window_start) / 60 * pixels_per_minute
        width = (stop - start) / 60 * pixels_per_minute
        if x + width < 0 or x > total_width:
            continue

        title = str(event.get("title") or "Untitled")
        subtitle = str(event.get("subtitle") or "").strip()
        summary = str(event.get("summary") or "").strip()
        description = str(event.get("description") or "").strip()
        fill, border = color_for_event_category(event, category_color_rules)
        recording_scheduled = (
            is_scheduled_recording(event, recording_index)
            if recording_index is not None
            else bool(event.get("recording_scheduled", False))
        )

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
                "recording_scheduled": recording_scheduled,
                "event_id": event_id,
                "series_id": series_id,
            }
        )

    return regions
