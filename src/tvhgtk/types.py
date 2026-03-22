from __future__ import annotations

from typing import TypedDict

RGB = tuple[float, float, float]
CategoryColorRule = tuple[str, tuple[str, ...], RGB, RGB]


class ProgramRegion(TypedDict):
    x: float
    w: float
    title: str
    hover: str
    fill: RGB
    border: RGB
    recording_scheduled: bool
